from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from leonervis_code.cli.main import main
from leonervis_code.core.contracts import AssistantText, ToolArguments, ToolUse
import leonervis_code.evals.coding_tasks as coding_tasks_module
from leonervis_code.evals.coding_tasks import (
    CODING_TASK_SUITE_ID,
    MAX_CODING_TASK_FILE_BYTES,
    builtin_coding_tasks,
    get_coding_task,
    materialize_coding_task,
    render_coding_task_result_json,
    run_coding_task,
    score_coding_task,
)
from leonervis_code.providers.fake import ScriptedFakeProvider
from leonervis_code.tools.command_sandbox import CommandSandboxLaunch
from leonervis_code.tools.run_command import RunCommandTool


class _DirectTestSandbox:
    def prepare_launch(self, *, workspace, cwd, argv, environment) -> CommandSandboxLaunch:
        return CommandSandboxLaunch(argv=argv, cwd=cwd, environment=dict(environment))


def _command_tool(workspace: Path, environment, *, command_sandbox=None) -> RunCommandTool:
    return RunCommandTool(
        workspace,
        environment,
        command_sandbox=_DirectTestSandbox(),
    )


def _prepared_task(tmp_path: Path, task_id: str) -> tuple[object, Path]:
    task = get_coding_task(task_id)
    workspace = tmp_path / task_id
    materialize_coding_task(task, workspace)
    return task, workspace


def _correct_solution(task_id: str) -> str:
    if task_id == "inventory-validation":
        return '''def remaining_stock(stock: int, requested: int) -> int:
    """Return stock left after reserving requested units."""
    if stock < 0 or requested < 0:
        raise ValueError("unit counts must be non-negative")
    if requested > stock:
        raise ValueError("requested units exceed stock")
    return stock - requested
'''
    return '''def slugify(text: str) -> str:
    """Return a URL-safe ASCII slug."""
    output = []
    separator_pending = False
    for character in text:
        if character.isascii() and character.isalnum():
            if separator_pending and output:
                output.append("-")
            output.append(character.lower())
            separator_pending = False
        else:
            separator_pending = True
    slug = "".join(output)
    if not slug:
        raise ValueError("normalized slug is empty")
    return slug
'''


def test_builtin_coding_task_order_is_stable() -> None:
    assert tuple(task.task_id for task in builtin_coding_tasks()) == (
        "inventory-validation",
        "slug-normalization",
    )


@pytest.mark.parametrize("task_id", ["inventory-validation", "slug-normalization"])
def test_materialized_tasks_hide_private_tests_and_initial_implementations_fail(
    tmp_path: Path,
    task_id: str,
) -> None:
    task, workspace = _prepared_task(tmp_path, task_id)

    result = score_coding_task(task, workspace, command_tool_factory=_command_tool)

    assert not any("hidden" in path.name for path in workspace.rglob("*"))
    assert [check.name for check in result.checks] == [
        "workspace_shape",
        "protected_files",
        "visible_tests",
        "hidden_tests",
    ]
    assert [check.passed for check in result.checks] == [True, True, False, False]


@pytest.mark.parametrize("task_id", ["inventory-validation", "slug-normalization"])
def test_correct_solution_passes_visible_and_hidden_host_checks(
    tmp_path: Path,
    task_id: str,
) -> None:
    task, workspace = _prepared_task(tmp_path, task_id)
    mutable = workspace / task.mutable_paths[0]
    mutable.write_text(_correct_solution(task_id), encoding="utf-8")

    first = score_coding_task(task, workspace, command_tool_factory=_command_tool)
    second = score_coding_task(task, workspace, command_tool_factory=_command_tool)

    assert first.passed is True
    assert render_coding_task_result_json(first) == render_coding_task_result_json(second)
    assert json.loads(render_coding_task_result_json(first))["suite_id"] == CODING_TASK_SUITE_ID
    assert not (workspace / ".leonervis-eval-tests").exists()


def test_protected_changes_and_extra_entries_fail_host_facts(tmp_path: Path) -> None:
    task, workspace = _prepared_task(tmp_path, "inventory-validation")
    (workspace / "README.md").write_text("changed\n", encoding="utf-8")
    (workspace / "extra.txt").write_text("unexpected\n", encoding="utf-8")

    result = score_coding_task(task, workspace, command_tool_factory=_command_tool)

    assert [check.name for check in result.checks if not check.passed][:2] == [
        "workspace_shape",
        "protected_files",
    ]


@pytest.mark.parametrize("unsafe_kind", ["symlink", "oversize"])
def test_unsafe_declared_files_fail_closed_scoring(tmp_path: Path, unsafe_kind: str) -> None:
    task, workspace = _prepared_task(tmp_path, "inventory-validation")
    source = workspace / "inventory.py"
    source.unlink()
    if unsafe_kind == "symlink":
        source.symlink_to("README.md")
    else:
        source.write_bytes(b"x" * (MAX_CODING_TASK_FILE_BYTES + 1))

    result = score_coding_task(task, workspace, command_tool_factory=_command_tool)

    assert result.passed is False
    assert next(check for check in result.checks if check.name == "workspace_shape").passed is (
        unsafe_kind == "oversize"
    )
    assert next(
        check for check in result.checks if check.name == "visible_tests"
    ).actual.startswith("evaluator-error:")


def test_symlinked_declared_parent_is_not_followed(tmp_path: Path) -> None:
    task, workspace = _prepared_task(tmp_path, "inventory-validation")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "test_inventory.py").write_text("outside\n", encoding="utf-8")
    visible = workspace / "tests" / "test_inventory.py"
    visible.unlink()
    (workspace / "tests").rmdir()
    (workspace / "tests").symlink_to(outside, target_is_directory=True)

    result = score_coding_task(task, workspace, command_tool_factory=_command_tool)

    assert result.passed is False
    protected = next(check for check in result.checks if check.name == "protected_files")
    assert "WorkspacePathFailure" in protected.actual


def test_provider_attempt_is_scored_from_workspace_and_durable_host_facts(tmp_path: Path) -> None:
    task = get_coding_task("inventory-validation")
    corrected = _correct_solution(task.task_id)
    provider = ScriptedFakeProvider(
        (
            ToolUse(
                "write-solution",
                "write_file",
                ToolArguments.from_mapping({"path": "inventory.py", "content": corrected}),
            ),
            AssistantText("Implemented and verified the requested validation."),
        )
    )

    result = run_coding_task(
        task,
        tmp_path / "attempt",
        environment={"PATH": "/usr/bin", "LANG": "C.UTF-8"},
        model="anthropic/test-model",
        provider_factory=lambda route, environment: provider,
        command_tool_factory=_command_tool,
    )

    assert result.result.passed is True
    assert [check.name for check in result.result.checks] == [
        "agent_turn",
        "committed_turns",
        "action_certainty",
        "workspace_shape",
        "protected_files",
        "visible_tests",
        "hidden_tests",
    ]
    assert result.final_text == "Implemented and verified the requested validation."
    assert result.execution_error is None


def test_coding_task_cli_lists_prepares_and_scores_without_hidden_test_leakage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(coding_tasks_module, "RunCommandTool", _command_tool)
    listed = io.StringIO()
    prepared = io.StringIO()
    initial_score = io.StringIO()
    final_score = io.StringIO()
    workspace = tmp_path / "candidate"

    assert main(["eval", "task", "list"], stdout=listed, stderr=io.StringIO(), cwd=tmp_path) == 0
    assert listed.getvalue().splitlines()[0].startswith("inventory-validation:")
    assert (
        main(
            ["eval", "task", "prepare", "inventory-validation", "candidate"],
            stdout=prepared,
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        == 0
    )
    assert not any("hidden" in path.name for path in workspace.rglob("*"))
    assert (
        main(
            ["eval", "task", "score", "inventory-validation", "candidate"],
            stdout=initial_score,
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        == 1
    )
    assert "FAIL inventory-validation (2/4 checks)" in initial_score.getvalue()

    (workspace / "inventory.py").write_text(
        _correct_solution("inventory-validation"), encoding="utf-8"
    )
    assert (
        main(
            [
                "eval",
                "task",
                "score",
                "inventory-validation",
                "candidate",
                "--format",
                "json",
            ],
            stdout=final_score,
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        == 0
    )
    assert json.loads(final_score.getvalue())["passed"] is True
    assert str(tmp_path) not in final_score.getvalue()


@pytest.mark.parametrize(
    "arguments",
    [
        ["eval", "task", "run", "inventory-validation", "--real-provider"],
        ["--model", "custom/test", "eval", "task", "score", "inventory-validation", "."],
    ],
)
def test_coding_task_cli_rejects_missing_or_misplaced_provider_selection(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    output = io.StringIO()
    errors = io.StringIO()

    assert main(arguments, stdout=output, stderr=errors, cwd=tmp_path) == 2
    assert output.getvalue() == ""
    assert errors.getvalue().startswith("eval error:")


def test_coding_task_cli_runs_fake_provider_but_reports_only_stable_host_score(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(coding_tasks_module, "RunCommandTool", _command_tool)
    corrected = _correct_solution("inventory-validation")
    provider = ScriptedFakeProvider(
        (
            ToolUse(
                "write-solution",
                "write_file",
                ToolArguments.from_mapping({"path": "inventory.py", "content": corrected}),
            ),
            AssistantText("PRIVATE MODEL FINAL TEXT"),
        )
    )
    output = io.StringIO()
    errors = io.StringIO()

    status = main(
        [
            "--model",
            "custom/test-model",
            "--provider-protocol",
            "openai-compatible",
            "--base-url",
            "https://example.invalid/v1",
            "eval",
            "task",
            "run",
            "inventory-validation",
            "--real-provider",
            "--format",
            "json",
        ],
        stdout=output,
        stderr=errors,
        cwd=tmp_path,
        environment={"PATH": "/usr/bin", "LANG": "C.UTF-8"},
        provider_factory=lambda route, *, environment: provider,
    )

    report = json.loads(output.getvalue())
    assert status == 0
    assert report["passed"] is True
    assert report["summary"]["total_checks"] == 7
    assert "PRIVATE MODEL FINAL TEXT" not in output.getvalue()
    assert str(tmp_path) not in output.getvalue()
    assert "write_file" in errors.getvalue()
    assert tuple(tmp_path.iterdir()) == ()
