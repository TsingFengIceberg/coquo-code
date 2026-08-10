from __future__ import annotations

from dataclasses import replace
import io
import json

import pytest

from coquo.cli.main import main
from coquo.core.contracts import AssistantText
from coquo.evals.baseline import (
    DETERMINISTIC_BASELINE_ID,
    EvalWorkspaceFile,
    builtin_eval_cases,
    render_eval_result_json,
    run_eval_case,
    run_eval_suite,
)


def test_builtin_eval_case_order_and_baseline_are_stable() -> None:
    assert tuple(case.case_id for case in builtin_eval_cases()) == (
        "read-file-success",
        "controlled-write-success",
        "read-only-write-denied",
        "batch-stops-after-failure",
        "task-admission-lifecycle",
    )

    first = run_eval_suite()
    second = run_eval_suite()

    assert first.suite_id == DETERMINISTIC_BASELINE_ID
    assert first.passed is True
    assert first.passed_cases == 5
    assert first.passed_checks == first.total_checks == 30
    assert render_eval_result_json(first) == render_eval_result_json(second)


def test_host_workspace_fact_fails_a_false_success_claim() -> None:
    denied = next(case for case in builtin_eval_cases() if case.case_id == "read-only-write-denied")
    false_claim = replace(
        denied,
        case_id="false-success-claim",
        provider_script=(denied.provider_script[0], AssistantText("Created denied.txt.")),
        expected_final_text="Created denied.txt.",
        expected_files=(EvalWorkspaceFile("denied.txt", "MUST_NOT_EXIST\n"),),
    )

    result = run_eval_case(false_claim)

    assert result.passed is False
    assert [check.name for check in result.checks if not check.passed] == ["workspace_entries"]


@pytest.mark.parametrize("path", ["../escape", "/absolute", ".coquo/state"])
def test_eval_fixture_paths_reject_escape_and_session_state(path: str) -> None:
    with pytest.raises(ValueError, match="Eval fixture path"):
        EvalWorkspaceFile(path, "content")


def test_eval_cli_lists_and_runs_text_and_json_reports(tmp_path) -> None:
    listed = io.StringIO()
    text_output = io.StringIO()
    json_output = io.StringIO()

    assert main(["eval", "list"], stdout=listed, stderr=io.StringIO(), cwd=tmp_path) == 0
    assert listed.getvalue().splitlines()[0].startswith("read-file-success:")

    assert (
        main(
            ["eval", "run", "controlled-write-success"],
            stdout=text_output,
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        == 0
    )
    assert "PASS controlled-write-success (5/5 checks)" in text_output.getvalue()
    assert "PASS summary: 1/1 cases, 5/5 checks" in text_output.getvalue()

    assert (
        main(
            ["eval", "run", "all", "--format", "json"],
            stdout=json_output,
            stderr=io.StringIO(),
            cwd=tmp_path,
        )
        == 0
    )
    report = json.loads(json_output.getvalue())
    assert report["suite_id"] == DETERMINISTIC_BASELINE_ID
    assert report["summary"] == {
        "passed_cases": 5,
        "passed_checks": 30,
        "total_cases": 5,
        "total_checks": 30,
    }
    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["eval", "run", "missing"], "eval error: unknown Eval case: missing\n"),
        (
            ["-C", "elsewhere", "eval", "run"],
            "eval error: eval uses isolated temporary workspaces and does not accept -C/--cwd\n",
        ),
        (
            ["--model", "anthropic/example", "eval", "run"],
            "eval error: eval is offline and does not accept runtime or provider selection options\n",
        ),
    ],
)
def test_eval_cli_rejects_unknown_cases_and_nonisolated_runtime_options(
    tmp_path, arguments: list[str], message: str
) -> None:
    output = io.StringIO()
    errors = io.StringIO()

    assert main(arguments, stdout=output, stderr=errors, cwd=tmp_path) == 2
    assert output.getvalue() == ""
    assert errors.getvalue() == message
    assert tuple(tmp_path.iterdir()) == ()
