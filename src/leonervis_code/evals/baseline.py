"""Versioned offline task fixtures and Host-fact scoring for the first Eval baseline."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from tempfile import TemporaryDirectory

from leonervis_code.core.contracts import (
    AssistantText,
    AssistantToolBatch,
    ProviderResponse,
    ToolArguments,
    ToolRequestOutcome,
    ToolUse,
)
from leonervis_code.core.permissions import ApprovalMode, PermissionMode
from leonervis_code.providers.fake import ScriptedFakeProvider
from leonervis_code.session import ProjectSession
from leonervis_code.session_records import ActionAuditStatus
from leonervis_code.session_store import SessionStore

DETERMINISTIC_BASELINE_ID = "host-baseline-v1"
_SESSION_STATE_DIRECTORY = ".leonervis-code"


class EvalError(ValueError):
    """Raised for invalid user-facing deterministic Eval selection."""


def _validate_fixture_path(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Eval fixture path must be a portable relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Eval fixture path must be a portable relative path")
    if path.parts[0] == _SESSION_STATE_DIRECTORY:
        raise ValueError("Eval fixture path collides with Session state")


@dataclass(frozen=True)
class EvalWorkspaceFile:
    """One exact UTF-8 regular file in a fixture workspace."""

    path: str
    content: str

    def __post_init__(self) -> None:
        _validate_fixture_path(self.path)
        if not isinstance(self.content, str) or "\x00" in self.content:
            raise ValueError("Eval fixture content must be NUL-free text")
        try:
            self.content.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Eval fixture content must be valid UTF-8") from None


@dataclass(frozen=True)
class EvalToolExpectation:
    """One durable tool-ledger fact expected in request order."""

    tool_name: str
    outcome: ToolRequestOutcome
    result_code: str | None


@dataclass(frozen=True)
class EvalActionExpectation:
    """One durable Action Audit fact expected in request order."""

    tool_name: str
    status: ActionAuditStatus
    permission_decision: str
    result_code: str | None


@dataclass(frozen=True)
class DeterministicEvalCase:
    """One immutable offline task, scripted trajectory, and Host-fact oracle."""

    case_id: str
    summary: str
    prompt: str
    permission_mode: PermissionMode
    approval_mode: ApprovalMode
    initial_files: tuple[EvalWorkspaceFile, ...]
    provider_script: tuple[ProviderResponse, ...]
    expected_final_text: str
    expected_files: tuple[EvalWorkspaceFile, ...]
    expected_tool_outcomes: tuple[EvalToolExpectation, ...]
    expected_action_audits: tuple[EvalActionExpectation, ...]

    def __post_init__(self) -> None:
        if not self.case_id.isascii() or not self.case_id.replace("-", "").isalnum():
            raise ValueError("Eval case ID must use ASCII letters, digits, and hyphens")
        if not self.summary or not self.prompt or not self.expected_final_text:
            raise ValueError("Eval case text fields must be non-empty")
        initial_paths = tuple(item.path for item in self.initial_files)
        expected_paths = tuple(item.path for item in self.expected_files)
        if len(initial_paths) != len(set(initial_paths)):
            raise ValueError("Eval initial file paths must be unique")
        if len(expected_paths) != len(set(expected_paths)):
            raise ValueError("Eval expected file paths must be unique")


@dataclass(frozen=True)
class EvalCheck:
    """One deterministic equality check over a trusted Host observation."""

    name: str
    passed: bool
    expected: str
    actual: str

    def as_mapping(self) -> dict[str, object]:
        return {
            "actual": self.actual,
            "expected": self.expected,
            "name": self.name,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class EvalCaseResult:
    """Bounded result for one isolated deterministic task."""

    case_id: str
    summary: str
    checks: tuple[EvalCheck, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def passed_checks(self) -> int:
        return sum(check.passed for check in self.checks)

    def as_mapping(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "checks": [check.as_mapping() for check in self.checks],
            "passed": self.passed,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class EvalSuiteResult:
    """Aggregate result whose projection excludes random Session and temp-path data."""

    suite_id: str
    cases: tuple[EvalCaseResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.cases) and all(case.passed for case in self.cases)

    @property
    def passed_cases(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def total_checks(self) -> int:
        return sum(len(case.checks) for case in self.cases)

    @property
    def passed_checks(self) -> int:
        return sum(case.passed_checks for case in self.cases)

    def as_mapping(self) -> dict[str, object]:
        return {
            "cases": [case.as_mapping() for case in self.cases],
            "passed": self.passed,
            "summary": {
                "passed_cases": self.passed_cases,
                "passed_checks": self.passed_checks,
                "total_cases": len(self.cases),
                "total_checks": self.total_checks,
            },
            "suite_id": self.suite_id,
        }


def _tool(tool_use_id: str, name: str, arguments: dict[str, object]) -> ToolUse:
    return ToolUse(tool_use_id, name, ToolArguments.from_mapping(arguments))


_BUILTIN_CASES = (
    DeterministicEvalCase(
        case_id="read-file-success",
        summary="Read one bounded workspace file and commit a truthful final answer.",
        prompt="Read note.txt and report its marker.",
        permission_mode=PermissionMode.READ_ONLY,
        approval_mode=ApprovalMode.AUTO,
        initial_files=(EvalWorkspaceFile("note.txt", "READ_OK\n"),),
        provider_script=(
            _tool("read-1", "read_file", {"path": "note.txt"}),
            AssistantText("The marker is READ_OK."),
        ),
        expected_final_text="The marker is READ_OK.",
        expected_files=(EvalWorkspaceFile("note.txt", "READ_OK\n"),),
        expected_tool_outcomes=(
            EvalToolExpectation("read_file", ToolRequestOutcome.SUCCEEDED, "ok"),
        ),
        expected_action_audits=(
            EvalActionExpectation("read_file", ActionAuditStatus.SUCCEEDED, "allow", "ok"),
        ),
    ),
    DeterministicEvalCase(
        case_id="controlled-write-success",
        summary="Create one file through workspace-write auto policy and durable audit.",
        prompt="Create result.txt with the requested marker.",
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
        initial_files=(),
        provider_script=(
            _tool(
                "write-1",
                "write_file",
                {"path": "result.txt", "content": "EVAL_OK\n"},
            ),
            AssistantText("Created result.txt."),
        ),
        expected_final_text="Created result.txt.",
        expected_files=(EvalWorkspaceFile("result.txt", "EVAL_OK\n"),),
        expected_tool_outcomes=(
            EvalToolExpectation("write_file", ToolRequestOutcome.SUCCEEDED, "created"),
        ),
        expected_action_audits=(
            EvalActionExpectation("write_file", ActionAuditStatus.SUCCEEDED, "allow", "created"),
        ),
    ),
    DeterministicEvalCase(
        case_id="read-only-write-denied",
        summary="Deny an out-of-scope write and avoid claiming the file exists.",
        prompt="Try to create denied.txt and report the actual result.",
        permission_mode=PermissionMode.READ_ONLY,
        approval_mode=ApprovalMode.AUTO,
        initial_files=(),
        provider_script=(
            _tool(
                "write-denied",
                "write_file",
                {"path": "denied.txt", "content": "MUST_NOT_EXIST\n"},
            ),
            AssistantText("The file was not created because permission was denied."),
        ),
        expected_final_text="The file was not created because permission was denied.",
        expected_files=(),
        expected_tool_outcomes=(
            EvalToolExpectation(
                "write_file",
                ToolRequestOutcome.DENIED,
                "denied_read_only_mode",
            ),
        ),
        expected_action_audits=(
            EvalActionExpectation("write_file", ActionAuditStatus.DENIED, "deny", None),
        ),
    ),
    DeterministicEvalCase(
        case_id="batch-stops-after-failure",
        summary="Skip later batch actions after the first tool fails and preserve Host truth.",
        prompt="Inspect missing.txt and only write if that action succeeds.",
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.AUTO,
        initial_files=(),
        provider_script=(
            AssistantToolBatch(
                (
                    _tool("read-missing", "read_file", {"path": "missing.txt"}),
                    _tool(
                        "write-skipped",
                        "write_file",
                        {"path": "must-not-exist.txt", "content": "unexpected\n"},
                    ),
                )
            ),
            AssistantText("The read failed, so the write was not executed."),
        ),
        expected_final_text="The read failed, so the write was not executed.",
        expected_files=(),
        expected_tool_outcomes=(
            EvalToolExpectation("read_file", ToolRequestOutcome.FAILED, "tool_error"),
            EvalToolExpectation(
                "write_file",
                ToolRequestOutcome.SKIPPED_AFTER_FAILURE,
                "prior_batch_action_not_succeeded",
            ),
        ),
        expected_action_audits=(
            EvalActionExpectation("read_file", ActionAuditStatus.FAILED, "allow", "tool_error"),
        ),
    ),
)


def builtin_eval_cases() -> tuple[DeterministicEvalCase, ...]:
    """Return the immutable canonical case order for the baseline."""
    return _BUILTIN_CASES


def run_eval_case(case: DeterministicEvalCase) -> EvalCaseResult:
    """Run one case in a fresh temporary workspace and score only Host-observed facts."""
    if type(case) is not DeterministicEvalCase:
        raise ValueError("Eval case is invalid")
    with TemporaryDirectory(prefix="leonervis-eval-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        state = root / "host-state"
        workspace.mkdir()
        state.mkdir()
        _materialize_initial_files(workspace, case.initial_files)
        provider = ScriptedFakeProvider(case.provider_script)
        session: ProjectSession | None = None
        session_id: str | None = None
        final_text: str | None = None
        execution_error: Exception | None = None
        try:
            session = ProjectSession.open(
                workspace,
                environment={},
                user_profile_path=state / "user-profiles.json",
                project_profile_path=state / "project-profile.json",
                fake_provider_factory=lambda: provider,
                permission_mode=case.permission_mode,
                approval_mode=case.approval_mode,
            )
            session_id = session.session_id
            final_text = session.prompt(case.prompt)
        except Exception as error:
            execution_error = error
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception as error:
                    if execution_error is None:
                        execution_error = error

        if execution_error is not None or session_id is None or final_text is None:
            actual = type(execution_error).__name__ if execution_error is not None else "incomplete"
            return EvalCaseResult(
                case.case_id,
                case.summary,
                (EvalCheck("execution", False, "completed", actual),),
            )

        store = SessionStore(workspace)
        info = store.inspect(session_id)
        ledgers = store.tool_ledgers(session_id, 1)
        ledger = ledgers.turns[0].ledger if ledgers.turns else None
        audits = store.action_audits(session_id)
        checks = (
            _check("final_text", _text_fact(case.expected_final_text), _text_fact(final_text)),
            _check("committed_turns", "1", str(info.turn_count)),
            _check(
                "workspace_entries",
                _expected_workspace_fact(case.expected_files),
                _observed_workspace_fact(workspace),
            ),
            _check(
                "tool_ledger",
                _expected_tool_fact(case.expected_tool_outcomes),
                _observed_tool_fact(ledger),
            ),
            _check(
                "action_audit",
                _expected_action_fact(case.expected_action_audits),
                _observed_action_fact(audits),
            ),
        )
        return EvalCaseResult(case.case_id, case.summary, checks)


def run_eval_suite(selector: str = "all") -> EvalSuiteResult:
    """Run one named case or the complete canonical baseline in stable order."""
    if not isinstance(selector, str):
        raise ValueError("Eval selector must be text")
    cases = _BUILTIN_CASES
    if selector != "all":
        cases = tuple(case for case in cases if case.case_id == selector)
        if not cases:
            raise EvalError(f"unknown Eval case: {selector}")
    return EvalSuiteResult(DETERMINISTIC_BASELINE_ID, tuple(run_eval_case(case) for case in cases))


def render_eval_result_text(result: EvalSuiteResult) -> str:
    """Render a concise deterministic report with details only for failed checks."""
    lines = [f"Deterministic Eval: {result.suite_id}"]
    for case in result.cases:
        status = "PASS" if case.passed else "FAIL"
        lines.append(
            f"{status} {case.case_id} ({case.passed_checks}/{len(case.checks)} checks) - {case.summary}"
        )
        for check in case.checks:
            if not check.passed:
                lines.append(
                    f"  FAIL {check.name}: expected={check.expected} actual={check.actual}"
                )
    suite_status = "PASS" if result.passed else "FAIL"
    lines.append(
        f"{suite_status} summary: {result.passed_cases}/{len(result.cases)} cases, "
        f"{result.passed_checks}/{result.total_checks} checks"
    )
    return "\n".join(lines)


def render_eval_result_json(result: EvalSuiteResult) -> str:
    """Return a stable compact JSON report without paths, timestamps, or random IDs."""
    return json.dumps(
        result.as_mapping(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _materialize_initial_files(workspace: Path, files: tuple[EvalWorkspaceFile, ...]) -> None:
    for item in files:
        target = workspace.joinpath(*PurePosixPath(item.path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.content, encoding="utf-8", newline="")


def _check(name: str, expected: str, actual: str) -> EvalCheck:
    return EvalCheck(name, expected == actual, expected, actual)


def _text_fact(value: str) -> str:
    encoded = value.encode("utf-8")
    return f"utf8:{len(encoded)}:{hashlib.sha256(encoded).hexdigest()}"


def _entry_fact(path: str, kind: str, content: bytes | None = None) -> str:
    if content is None:
        return f"{path}:{kind}"
    return f"{path}:{kind}:{len(content)}:{hashlib.sha256(content).hexdigest()}"


def _expected_workspace_fact(files: tuple[EvalWorkspaceFile, ...]) -> str:
    entries: set[str] = set()
    for item in files:
        parts = PurePosixPath(item.path).parts
        for index in range(1, len(parts)):
            entries.add(_entry_fact("/".join(parts[:index]), "directory"))
        entries.add(_entry_fact(item.path, "file", item.content.encode("utf-8")))
    return json.dumps(sorted(entries), separators=(",", ":"))


def _observed_workspace_fact(workspace: Path) -> str:
    entries: list[str] = []
    for root, directories, files in os.walk(workspace, topdown=True, followlinks=False):
        root_path = Path(root)
        if root_path == workspace:
            directories[:] = sorted(
                name for name in directories if name != _SESSION_STATE_DIRECTORY
            )
        else:
            directories.sort()
        files.sort()
        for name in directories:
            path = root_path / name
            relative = path.relative_to(workspace).as_posix()
            info = path.lstat()
            kind = "directory" if stat.S_ISDIR(info.st_mode) else "symlink"
            entries.append(_entry_fact(relative, kind))
        for name in files:
            path = root_path / name
            relative = path.relative_to(workspace).as_posix()
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                entries.append(_entry_fact(relative, "file", path.read_bytes()))
            elif stat.S_ISLNK(info.st_mode):
                entries.append(_entry_fact(relative, "symlink"))
            else:
                entries.append(_entry_fact(relative, "other"))
    return json.dumps(sorted(entries), separators=(",", ":"))


def _expected_tool_fact(expected: tuple[EvalToolExpectation, ...]) -> str:
    values = [
        f"{item.tool_name}:{item.outcome.value}:{item.result_code or '-'}" for item in expected
    ]
    return json.dumps(values, separators=(",", ":"))


def _observed_tool_fact(ledger) -> str:
    if ledger is None:
        return "unavailable"
    values = [
        f"{item.tool_name}:{item.outcome.value}:{item.result_code or '-'}"
        for item in ledger.entries
    ]
    return json.dumps(values, separators=(",", ":"))


def _expected_action_fact(expected: tuple[EvalActionExpectation, ...]) -> str:
    values = [
        f"{item.tool_name}:{item.status.value}:{item.permission_decision}:{item.result_code or '-'}"
        for item in expected
    ]
    return json.dumps(values, separators=(",", ":"))


def _observed_action_fact(audits) -> str:
    values = [
        f"{item.identity.tool_name}:{item.status.value}:"
        f"{item.permission_result.decision.value if item.permission_result is not None else '-'}:"
        f"{item.result_code or '-'}"
        for item in audits
    ]
    return json.dumps(values, separators=(",", ":"))
