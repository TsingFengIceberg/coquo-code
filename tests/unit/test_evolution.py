from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import io
import json

import pytest

from coquo.cli.main import main
from coquo.evolution import (
    CandidateStatus,
    EvolutionController,
    EvolutionError,
    EvolutionMode,
    EvolutionOutcome,
    EvolutionTarget,
)


def test_evolution_defaults_off_and_records_content_free_trace(tmp_path: Path) -> None:
    controller = EvolutionController(tmp_path)
    assert controller.mode() is EvolutionMode.OFF
    trace = controller.record_trace(
        EvolutionTarget.WORKFLOW,
        EvolutionOutcome.FAILED,
        "tool order failed validation",
        metrics={"success_rate": 0.0, "latency_ms": 20},
    )
    assert controller.traces() == (trace,)
    assessment = controller.assess(trace.trace_id)
    assert assessment.label == "failure"
    assert controller.patterns()[0]["count"] == 1


def test_evolution_candidate_requires_provenance_and_independent_eval(tmp_path: Path) -> None:
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.SUPERVISED)
    trace = controller.record_trace(
        EvolutionTarget.SKILL, EvolutionOutcome.SUCCESS, "verified release workflow"
    )
    candidate = controller.propose(
        EvolutionTarget.SKILL,
        "release helper",
        "Run checks, inspect failures, and report verified results.",
        (trace.trace_id,),
    )
    with pytest.raises(EvolutionError, match="independent"):
        controller.evaluate(
            candidate.candidate_id,
            {"success_rate": 0.5},
            {"success_rate": 0.8},
            validation_set="v1",
            test_set="v1",
        )
    result = controller.evaluate(
        candidate.candidate_id,
        {"success_rate": 0.5, "token_cost": 100},
        {"success_rate": 0.8, "token_cost": 90},
        validation_set="validation-v1",
        test_set="test-v1",
    )
    assert result.passed
    approved = controller.approve(candidate.candidate_id)
    assert approved.status is CandidateStatus.APPROVED
    active = controller.activate(candidate.candidate_id)
    assert active.status is CandidateStatus.ACTIVE


def test_evolution_rejects_protected_or_secret_candidates(tmp_path: Path) -> None:
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.PROPOSE)
    trace = controller.record_trace(
        EvolutionTarget.PROMPT, EvolutionOutcome.SUCCESS, "prompt passed"
    )
    with pytest.raises(EvolutionError, match="secret"):
        controller.propose(EvolutionTarget.PROMPT, "bad", "api_key=secret", (trace.trace_id,))
    candidate = controller.propose(
        EvolutionTarget.WORKFLOW,
        "bad boundary",
        "Change the PermissionGate to allow everything.",
        (trace.trace_id,),
    )
    assert controller.safety_check(candidate.candidate_id) == (
        False,
        ("protected_runtime_boundary",),
    )


def test_evolution_observe_rollback_and_archive_lifecycle(tmp_path: Path) -> None:
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.PROPOSE)
    trace = controller.record_trace(
        EvolutionTarget.MEMORY, EvolutionOutcome.SUCCESS, "stable project rule"
    )
    candidate = controller.propose(
        EvolutionTarget.MEMORY, "rule", "Use deterministic checks.", (trace.trace_id,)
    )
    controller.evaluate(
        candidate.candidate_id, {"quality": 0.5}, {"quality": 0.6}, validation_set="v", test_set="t"
    )
    controller.approve(candidate.candidate_id)
    controller.activate(candidate.candidate_id)
    observed = controller.observe(candidate.candidate_id, {"quality": 0.7})
    assert observed.use_count == 1
    rolled_back = controller.rollback(candidate.candidate_id)
    assert rolled_back.status is CandidateStatus.ROLLED_BACK
    old = (
        (datetime.now(timezone.utc) - timedelta(days=2))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    assert controller.archive(before=old) == ()
    assert controller.deprecate(candidate.candidate_id).status is CandidateStatus.DEPRECATED
    assert controller.archive(before="9999-12-31T00:00:00Z")[0].status is CandidateStatus.ARCHIVED


def test_evolution_cli_exposes_host_only_lifecycle(tmp_path: Path) -> None:
    output = io.StringIO()
    assert main(["evolution", "configure", "propose"], cwd=tmp_path, stdout=output) == 0
    output = io.StringIO()
    assert (
        main(
            ["evolution", "trace", "memory", "success", "stable project rule"],
            cwd=tmp_path,
            stdout=output,
        )
        == 0
    )
    trace_id = json.loads(output.getvalue())["trace_id"]
    output = io.StringIO()
    assert (
        main(
            [
                "evolution",
                "propose",
                "memory",
                "rule",
                "Use deterministic checks.",
                "--trace-id",
                trace_id,
            ],
            cwd=tmp_path,
            stdout=output,
        )
        == 0
    )
    candidate_id = json.loads(output.getvalue())["candidate_id"]
    output = io.StringIO()
    assert (
        main(
            [
                "evolution",
                "evaluate",
                candidate_id,
                "--baseline-metrics",
                '{"quality":0.5}',
                "--candidate-metrics",
                '{"quality":0.6}',
                "--validation-set",
                "v1",
                "--test-set",
                "t1",
            ],
            cwd=tmp_path,
            stdout=output,
        )
        == 0
    )
    assert json.loads(output.getvalue())["passed"] is True
    assert not (tmp_path / ".coquo" / "sessions").exists()


def test_committed_project_turn_records_automatic_workflow_trace(tmp_path: Path) -> None:
    output = io.StringIO()
    assert main(["prompt", "Hello"], cwd=tmp_path, stdout=output, stderr=io.StringIO()) == 0
    traces = EvolutionController(tmp_path).traces(EvolutionTarget.WORKFLOW)
    assert len(traces) == 1
    assert traces[0].source_turn == 1
    assert traces[0].outcome is EvolutionOutcome.SUCCESS
