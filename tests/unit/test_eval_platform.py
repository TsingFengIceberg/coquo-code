from __future__ import annotations

import io
import json

import pytest

from coquo.cli.main import main
from coquo.evals.platform import (
    EvalDataset,
    EvalPlatform,
    EvalPlatformError,
)


def test_builtin_dataset_run_is_durable_and_stable_projection_is_content_free(tmp_path):
    platform = EvalPlatform(tmp_path)
    dataset = platform.dataset("host-baseline-v3")
    assert dataset.validation_ids and dataset.test_ids
    run = platform.run_builtin(label="baseline")

    assert run.pass_rate == 1.0
    assert run.mean_score == 1.0
    assert run.stable_projection()["dataset_id"] == "host-baseline-v3"
    assert platform.runs()[0].stable_projection() == run.stable_projection()


def test_candidate_grades_compare_and_regression_gate(tmp_path):
    platform = EvalPlatform(tmp_path)
    baseline = platform.run_builtin()
    observations = {
        case_id: {"passed": True, "score": 1.0, "checks": []}
        for case_id in platform.dataset("host-baseline-v3").case_ids
    }
    candidate = platform.grade("host-baseline-v3", observations, label="candidate")
    assert platform.compare(baseline, candidate).passed
    platform.gate(platform.compare(baseline, candidate))

    observations[platform.dataset("host-baseline-v3").case_ids[0]] = {
        "passed": False,
        "score": 0.0,
        "checks": ["workspace"],
    }
    regressed = platform.grade("host-baseline-v3", observations, label="regressed")
    comparison = platform.compare(baseline, regressed)
    assert comparison.passed is False
    assert "suite:pass_rate" in comparison.regressions
    with pytest.raises(EvalPlatformError, match="regression gate failed"):
        platform.gate(comparison)


def test_dataset_validation_and_missing_observation_fail_closed(tmp_path):
    with pytest.raises(EvalPlatformError, match="disjoint"):
        EvalDataset("bad", 1, ("a", "b"), ("a",), ("a",))
    platform = EvalPlatform(tmp_path)
    with pytest.raises(EvalPlatformError, match="missing Eval observation"):
        platform.grade("host-baseline-v3", {})


def test_comparison_rejects_different_dataset_versions(tmp_path):
    platform = EvalPlatform(tmp_path)
    baseline = platform.run_builtin()
    platform.register(EvalDataset("other", 1, ("a", "b"), ("a",), ("b",)))
    other = platform.grade("other", {"a": {"passed": True}, "b": {"passed": True}})
    with pytest.raises(EvalPlatformError, match="same dataset"):
        platform.compare(baseline, other)


def test_eval_platform_cli_persists_and_compares_runs(tmp_path):
    listed = io.StringIO()
    assert main(["eval", "platform", "datasets"], cwd=tmp_path, stdout=listed) == 0
    assert "host-baseline-v3" in listed.getvalue()

    baseline_output = io.StringIO()
    candidate_output = io.StringIO()
    assert main(["eval", "platform", "run"], cwd=tmp_path, stdout=baseline_output) == 0
    assert (
        main(
            ["eval", "platform", "run", "--label", "candidate"],
            cwd=tmp_path,
            stdout=candidate_output,
        )
        == 0
    )
    baseline_id = json.loads(baseline_output.getvalue())["run_id"]
    candidate_id = json.loads(candidate_output.getvalue())["run_id"]
    compared = io.StringIO()
    assert (
        main(
            ["eval", "platform", "compare", baseline_id, candidate_id],
            cwd=tmp_path,
            stdout=compared,
        )
        == 0
    )
    assert json.loads(compared.getvalue())["passed"] is True
