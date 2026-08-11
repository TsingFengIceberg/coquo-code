"""Actual coding-task fixtures, isolated scoring, and opt-in provider execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from tempfile import TemporaryDirectory

from coquo.agent.tool_events import AgentPromptEvent
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.permissions import ApprovalMode, PermissionMode
from coquo.evals.baseline import (
    EvalCaseResult,
    EvalCheck,
    EvalError,
    EvalSuiteResult,
    EvalWorkspaceFile,
)
from coquo.session import ProjectSession
from coquo.session_records import ActionAuditStatus
from coquo.session_store import SessionStore
from coquo.tools._workspace_paths import open_parent_directory
from coquo.tools.command_sandbox import LinuxBubblewrapCommandSandbox
from coquo.tools.run_command import RunCommandExecutionResult, RunCommandTool

CODING_TASK_SUITE_ID = "coding-task-v1"
MAX_CODING_TASK_ENTRIES = 100
MAX_CODING_TASK_FILE_BYTES = 1024 * 1024
MAX_CODING_TASK_TOTAL_BYTES = 4 * 1024 * 1024
_SESSION_STATE_DIRECTORY = ".coquo"
_HIDDEN_TEST_DIRECTORY = ".coquo-eval-tests"
_EXPECTED_COMMAND_FACT = "command_succeeded:exited:0"


@dataclass(frozen=True)
class CodingTask:
    """One actual project fixture with protected inputs and Host-private tests."""

    task_id: str
    summary: str
    prompt: str
    initial_files: tuple[EvalWorkspaceFile, ...]
    mutable_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    hidden_test_content: str

    def __post_init__(self) -> None:
        if not self.task_id.isascii() or not self.task_id.replace("-", "").isalnum():
            raise ValueError("coding task ID must use ASCII letters, digits, and hyphens")
        if not self.summary or not self.prompt or not self.hidden_test_content:
            raise ValueError("coding task text fields must be non-empty")
        paths = tuple(item.path for item in self.initial_files)
        if len(paths) != len(set(paths)):
            raise ValueError("coding task file paths must be unique")
        mutable = set(self.mutable_paths)
        protected = set(self.protected_paths)
        if mutable & protected or mutable | protected != set(paths):
            raise ValueError("coding task mutable and protected paths must partition fixture files")


@dataclass(frozen=True)
class CodingTaskRunResult:
    """One provider attempt plus deterministic Host scoring."""

    result: EvalCaseResult
    final_text: str | None
    execution_error: str | None


_INVENTORY_README = """# Inventory reservation

Implement `remaining_stock(stock, requested)` in `inventory.py`.

Requirements:

- `stock` and `requested` represent non-negative integer unit counts.
- Raise `ValueError` when either value is negative.
- Raise `ValueError` when `requested` is greater than `stock`.
- Otherwise return the number of units remaining after the reservation.

Modify only `inventory.py`. Do not modify this file or the tests.
"""

_INVENTORY_SOURCE = '''def remaining_stock(stock: int, requested: int) -> int:
    """Return stock left after reserving requested units."""
    return stock - requested
'''

_INVENTORY_VISIBLE_TESTS = """import unittest

from inventory import remaining_stock


class InventoryVisibleTests(unittest.TestCase):
    def test_normal_reservation(self):
        self.assertEqual(remaining_stock(10, 3), 7)

    def test_reserving_all_stock(self):
        self.assertEqual(remaining_stock(5, 5), 0)

    def test_rejects_over_reservation(self):
        with self.assertRaises(ValueError):
            remaining_stock(3, 4)


if __name__ == "__main__":
    unittest.main()
"""

_INVENTORY_HIDDEN_TESTS = """import unittest

from inventory import remaining_stock


class InventoryHiddenTests(unittest.TestCase):
    def test_rejects_negative_stock(self):
        with self.assertRaises(ValueError):
            remaining_stock(-1, 0)

    def test_rejects_negative_request(self):
        with self.assertRaises(ValueError):
            remaining_stock(5, -1)

    def test_zero_values(self):
        self.assertEqual(remaining_stock(0, 0), 0)

    def test_larger_valid_reservation(self):
        self.assertEqual(remaining_stock(100, 37), 63)
"""

_SLUG_README = """# Slug normalization

Implement `slugify(text)` in `slugify.py`.

Requirements:

- Convert ASCII letters to lowercase.
- Preserve ASCII digits.
- Replace each run of every other character with one `-`.
- Remove leading and trailing `-` characters.
- Raise `ValueError` when the normalized slug is empty.

Modify only `slugify.py`. Do not modify this file or the tests.
"""

_SLUG_SOURCE = '''def slugify(text: str) -> str:
    """Return a URL-safe ASCII slug."""
    return text.lower().replace(" ", "-")
'''

_SLUG_VISIBLE_TESTS = """import unittest

from slugify import slugify


class SlugVisibleTests(unittest.TestCase):
    def test_words_and_case(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_preserves_digits(self):
        self.assertEqual(slugify("Release 2026"), "release-2026")

    def test_collapses_punctuation(self):
        self.assertEqual(slugify("  Hello,   World!  "), "hello-world")


if __name__ == "__main__":
    unittest.main()
"""

_SLUG_HIDDEN_TESTS = """import unittest

from slugify import slugify


class SlugHiddenTests(unittest.TestCase):
    def test_collapses_mixed_separator_runs(self):
        self.assertEqual(slugify("alpha___...beta"), "alpha-beta")

    def test_treats_unicode_as_separator(self):
        self.assertEqual(slugify("cafe cafe".replace("e ", "é ")), "caf-cafe")

    def test_strips_leading_and_trailing_separators(self):
        self.assertEqual(slugify("---alpha---"), "alpha")

    def test_rejects_empty_normalized_slug(self):
        with self.assertRaises(ValueError):
            slugify(" !!! ")
"""


_CODING_TASKS = (
    CodingTask(
        task_id="inventory-validation",
        summary="Repair reservation validation without changing requirements or visible tests.",
        prompt=(
            "Fix the implementation according to README.md and the visible tests. Modify only "
            "inventory.py; do not change README.md or tests/test_inventory.py. Run "
            "/usr/bin/python3 -m unittest discover -s tests -p test_*.py -v and report the "
            "actual result. Host-private tests will check additional documented edge cases."
        ),
        initial_files=(
            EvalWorkspaceFile("README.md", _INVENTORY_README),
            EvalWorkspaceFile("inventory.py", _INVENTORY_SOURCE),
            EvalWorkspaceFile("tests/test_inventory.py", _INVENTORY_VISIBLE_TESTS),
        ),
        mutable_paths=("inventory.py",),
        protected_paths=("README.md", "tests/test_inventory.py"),
        hidden_test_content=_INVENTORY_HIDDEN_TESTS,
    ),
    CodingTask(
        task_id="slug-normalization",
        summary="Implement documented ASCII slug normalization against visible and hidden cases.",
        prompt=(
            "Fix the implementation according to README.md and the visible tests. Modify only "
            "slugify.py; do not change README.md or tests/test_slugify.py. Run /usr/bin/python3 "
            "-m unittest discover -s tests -p test_*.py -v and report the actual result. "
            "Host-private tests will check additional documented edge cases."
        ),
        initial_files=(
            EvalWorkspaceFile("README.md", _SLUG_README),
            EvalWorkspaceFile("slugify.py", _SLUG_SOURCE),
            EvalWorkspaceFile("tests/test_slugify.py", _SLUG_VISIBLE_TESTS),
        ),
        mutable_paths=("slugify.py",),
        protected_paths=("README.md", "tests/test_slugify.py"),
        hidden_test_content=_SLUG_HIDDEN_TESTS,
    ),
)


def builtin_coding_tasks() -> tuple[CodingTask, ...]:
    """Return actual coding tasks in canonical suite order."""
    return _CODING_TASKS


def get_coding_task(task_id: str) -> CodingTask:
    """Resolve one exact built-in task ID."""
    matches = tuple(task for task in _CODING_TASKS if task.task_id == task_id)
    if not matches:
        raise EvalError(f"unknown coding task: {task_id}")
    return matches[0]


def materialize_coding_task(task: CodingTask, workspace: Path) -> Path:
    """Create one absent task workspace without exposing Host-private tests."""
    if type(task) is not CodingTask:
        raise ValueError("coding task is invalid")
    target = Path(workspace)
    if target.exists() or target.is_symlink():
        raise EvalError("coding task output path must be absent")
    parent = target.parent
    if not parent.exists() or parent.is_symlink() or not parent.is_dir():
        raise EvalError("coding task output parent must be an existing non-symlink directory")
    try:
        target.mkdir(mode=0o700)
        for item in task.initial_files:
            path = target.joinpath(*PurePosixPath(item.path).parts)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_exclusive(path, item.content.encode("utf-8"))
    except OSError as error:
        raise EvalError(
            f"coding task materialization failed with {type(error).__name__}; inspect the output path"
        ) from None
    return target


def score_coding_task(
    task: CodingTask,
    workspace: Path,
    *,
    environment: Mapping[str, str] | None = None,
    command_tool_factory: Callable[[Path, Mapping[str, str]], RunCommandTool] | None = None,
) -> EvalCaseResult:
    """Score protected inputs and visible/hidden tests without modifying the candidate workspace."""
    if type(task) is not CodingTask:
        raise ValueError("coding task is invalid")
    candidate = Path(workspace)
    if not candidate.exists() or candidate.is_symlink() or not candidate.is_dir():
        raise EvalError("coding task workspace must be an existing non-symlink directory")

    expected_shape = _expected_shape(task)
    actual_shape = _observed_shape(candidate)
    expected_protected = _expected_protected_fact(task)
    actual_protected = _observed_protected_fact(task, candidate)
    visible_fact = "not-run"
    hidden_fact = "not-run"
    try:
        with TemporaryDirectory(prefix="coquo-task-score-") as temporary:
            scoring_workspace = Path(temporary) / "workspace"
            scoring_workspace.mkdir()
            _copy_declared_files(task, candidate, scoring_workspace)
            hidden_directory = scoring_workspace / _HIDDEN_TEST_DIRECTORY
            hidden_directory.mkdir()
            _write_exclusive(
                hidden_directory / "test_hidden.py",
                task.hidden_test_content.encode("utf-8"),
            )
            factory = command_tool_factory or RunCommandTool
            command_tool = factory(
                scoring_workspace,
                environment or {"PATH": "/usr/bin", "LANG": "C.UTF-8"},
            )
            visible = _execute_test_command(command_tool, "visible-tests", "tests")
            hidden = _execute_test_command(
                command_tool,
                "hidden-tests",
                _HIDDEN_TEST_DIRECTORY,
            )
            visible_fact = _command_fact(visible)
            hidden_fact = _command_fact(hidden)
    except Exception as error:
        failure = f"evaluator-error:{type(error).__name__}"
        if visible_fact == "not-run":
            visible_fact = failure
        if hidden_fact == "not-run":
            hidden_fact = failure

    checks = (
        _check("workspace_shape", expected_shape, actual_shape),
        _check("protected_files", expected_protected, actual_protected),
        _check("visible_tests", _EXPECTED_COMMAND_FACT, visible_fact),
        _check("hidden_tests", _EXPECTED_COMMAND_FACT, hidden_fact),
    )
    return EvalCaseResult(task.task_id, task.summary, checks)


def run_coding_task(
    task: CodingTask,
    workspace: Path,
    *,
    environment: Mapping[str, str],
    profile: str | None = None,
    profile_id: str | None = None,
    model: str | None = None,
    custom_protocol: str | None = None,
    custom_base_url: str | None = None,
    custom_api_key_env: str | None = None,
    max_output_tokens: int | None = None,
    user_profile_path: Path | None = None,
    provider_project_profile_path: Path | None = None,
    provider_factory=None,
    event_sink: Callable[[AgentPromptEvent], None] | None = None,
    command_tool_factory: Callable[[Path, Mapping[str, str]], RunCommandTool] | None = None,
) -> CodingTaskRunResult:
    """Materialize, run one explicitly selected provider, then score Host facts."""
    materialize_coding_task(task, workspace)
    session: ProjectSession | None = None
    session_id: str | None = None
    final_text: str | None = None
    execution_error: Exception | None = None
    try:
        session = ProjectSession.open(
            workspace,
            profile=profile,
            profile_id=profile_id,
            model=model,
            custom_protocol=custom_protocol,
            custom_base_url=custom_base_url,
            custom_api_key_env=custom_api_key_env,
            max_output_tokens=max_output_tokens,
            environment=environment,
            user_profile_path=user_profile_path,
            project_profile_path=(
                provider_project_profile_path
                or workspace / _SESSION_STATE_DIRECTORY / "eval-provider.json"
            ),
            provider_factory=provider_factory,
            run_command_factory=_private_eval_command_tool,
            permission_mode=PermissionMode.DANGER_FULL_ACCESS,
            approval_mode=ApprovalMode.AUTO,
        )
        session_id = session.session_id
        final_text = session.prompt(task.prompt, event_sink=event_sink)
    except Exception as error:
        execution_error = error
    finally:
        if session is not None:
            try:
                session.close()
            except Exception as error:
                if execution_error is None:
                    execution_error = error

    base = score_coding_task(
        task,
        workspace,
        environment=environment,
        command_tool_factory=command_tool_factory,
    )
    turn_count = 0
    action_fact = "unavailable"
    if session_id is not None:
        try:
            store = SessionStore(workspace)
            turn_count = store.inspect(session_id).turn_count
            action_fact = _action_certainty_fact(store.action_audits(session_id))
        except Exception:
            action_fact = "replay-error"
    execution_fact = (
        "completed"
        if execution_error is None and final_text is not None
        else (type(execution_error).__name__ if execution_error is not None else "incomplete")
    )
    checks = (
        _check("agent_turn", "completed", execution_fact),
        _check("committed_turns", "1", str(turn_count)),
        _check("action_certainty", "terminal-known", action_fact),
        *base.checks,
    )
    result = EvalCaseResult(task.task_id, task.summary, checks)
    return CodingTaskRunResult(
        result,
        final_text,
        type(execution_error).__name__ if execution_error is not None else None,
    )


def render_coding_task_result_text(result: EvalCaseResult) -> str:
    """Render one actual-task score with failed Host checks expanded."""
    status = "PASS" if result.passed else "FAIL"
    lines = [
        f"Coding Task Eval: {CODING_TASK_SUITE_ID}",
        f"{status} {result.case_id} ({result.passed_checks}/{len(result.checks)} checks) - {result.summary}",
    ]
    for check in result.checks:
        if not check.passed:
            lines.append(f"  FAIL {check.name}: expected={check.expected} actual={check.actual}")
    return "\n".join(lines)


def render_coding_task_result_json(result: EvalCaseResult) -> str:
    """Render stable task scoring without provider text or workspace identity."""
    suite = EvalSuiteResult(CODING_TASK_SUITE_ID, (result,))
    return json.dumps(
        suite.as_mapping(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _check(name: str, expected: str, actual: str) -> EvalCheck:
    return EvalCheck(name, expected == actual, expected, actual)


def _private_eval_command_tool(
    workspace: Path,
    environment: Mapping[str, str],
) -> RunCommandTool:
    sandbox = LinuxBubblewrapCommandSandbox(masked_read_paths=_evaluator_private_paths())
    return RunCommandTool(workspace, environment, command_sandbox=sandbox)


def _evaluator_private_paths() -> tuple[Path, ...]:
    source = Path(__file__).resolve()
    for parent in source.parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").is_file():
            return (parent,)
    paths = [source]
    bytecode = source.parent / "__pycache__"
    if bytecode.is_dir():
        paths.append(bytecode)
    return tuple(paths)


def _write_exclusive(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short fixture write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _declared_paths(task: CodingTask) -> tuple[str, ...]:
    return tuple(item.path for item in task.initial_files)


def _expected_shape(task: CodingTask) -> str:
    entries: set[str] = set()
    for path in _declared_paths(task):
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            entries.add(f"{'/'.join(parts[:index])}:directory")
        entries.add(f"{path}:file")
    return json.dumps(sorted(entries), separators=(",", ":"))


def _observed_shape(workspace: Path) -> str:
    entries: list[str] = []
    scanned = 0
    for root, directories, files in os.walk(workspace, topdown=True, followlinks=False):
        root_path = Path(root)
        directories[:] = sorted(
            name
            for name in directories
            if name != _SESSION_STATE_DIRECTORY and name != "__pycache__"
        )
        files = sorted(name for name in files if not name.endswith(".pyc"))
        for name in directories:
            scanned += 1
            path = root_path / name
            info = path.lstat()
            kind = "directory" if stat.S_ISDIR(info.st_mode) else "symlink"
            entries.append(f"{path.relative_to(workspace).as_posix()}:{kind}")
        for name in files:
            scanned += 1
            path = root_path / name
            info = path.lstat()
            kind = (
                "file"
                if stat.S_ISREG(info.st_mode)
                else ("symlink" if stat.S_ISLNK(info.st_mode) else "other")
            )
            entries.append(f"{path.relative_to(workspace).as_posix()}:{kind}")
        if scanned > MAX_CODING_TASK_ENTRIES:
            return "scan-limit-exceeded"
    return json.dumps(sorted(entries), separators=(",", ":"))


def _expected_protected_fact(task: CodingTask) -> str:
    files = {item.path: item for item in task.initial_files}
    values = [
        f"{path}:{hashlib.sha256(files[path].content.encode('utf-8')).hexdigest()}"
        for path in sorted(task.protected_paths)
    ]
    return json.dumps(values, separators=(",", ":"))


def _observed_protected_fact(task: CodingTask, workspace: Path) -> str:
    values: list[str] = []
    for path in sorted(task.protected_paths):
        try:
            content = _read_declared_file(workspace, path)
            values.append(f"{path}:{hashlib.sha256(content).hexdigest()}")
        except Exception as error:
            values.append(f"{path}:error:{type(error).__name__}")
    return json.dumps(values, separators=(",", ":"))


def _read_declared_file(workspace: Path, relative_path: str) -> bytes:
    parts = PurePosixPath(relative_path).parts
    parent_fd, name = open_parent_directory(workspace, parts, tool_name="coding task scorer")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise EvalError("coding task declared path is not a regular file")
            if info.st_size > MAX_CODING_TASK_FILE_BYTES:
                raise EvalError("coding task file exceeds the scoring limit")
            chunks: list[bytes] = []
            remaining = info.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    raise EvalError("coding task file changed while scoring")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise EvalError("coding task file changed while scoring")
            after = os.fstat(descriptor)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
            ) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise EvalError("coding task file changed while scoring")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _copy_declared_files(task: CodingTask, source: Path, destination: Path) -> None:
    total = 0
    for relative_path in _declared_paths(task):
        content = _read_declared_file(source, relative_path)
        total += len(content)
        if total > MAX_CODING_TASK_TOTAL_BYTES:
            raise EvalError("coding task files exceed the total scoring limit")
        target = destination.joinpath(*PurePosixPath(relative_path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_exclusive(target, content)


def _execute_test_command(
    tool: RunCommandTool,
    tool_use_id: str,
    test_directory: str,
) -> RunCommandExecutionResult:
    request = ToolUse(
        tool_use_id,
        "run_command",
        ToolArguments.from_mapping(
            {
                "argv": [
                    "/usr/bin/python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    test_directory,
                    "-p",
                    "test_*.py",
                    "-v",
                ],
                "cwd": ".",
                "timeout_seconds": 30,
            }
        ),
    )
    return tool.execute_detailed(tool.prepare(request))


def _command_fact(result: RunCommandExecutionResult) -> str:
    exit_code = result.observation.exit_code
    return f"{result.result_code}:{result.observation.status.value}:{exit_code if exit_code is not None else '-'}"


def _action_certainty_fact(audits) -> str:
    uncertain = {
        ActionAuditStatus.REQUESTED,
        ActionAuditStatus.AWAITING_APPROVAL,
        ActionAuditStatus.AUTHORIZED,
        ActionAuditStatus.APPROVED,
        ActionAuditStatus.EXECUTING,
        ActionAuditStatus.PARTIAL,
        ActionAuditStatus.ABANDONED,
        ActionAuditStatus.OUTCOME_UNKNOWN,
    }
    observed = sorted(audit.status.value for audit in audits if audit.status in uncertain)
    return "terminal-known" if not observed else json.dumps(observed, separators=(",", ":"))
