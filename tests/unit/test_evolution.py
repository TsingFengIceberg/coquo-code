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
    HostApprovalReceipt,
    PrivilegedEvolutionBridge,
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


def test_privileged_evolution_requires_host_receipt_and_explicit_rollback(tmp_path: Path) -> None:
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.SUPERVISED)
    trace = controller.record_trace(
        EvolutionTarget.SYSTEM_PROMPT, EvolutionOutcome.SUCCESS, "reviewed prompt experiment"
    )
    candidate = controller.propose(
        EvolutionTarget.SYSTEM_PROMPT,
        "prompt candidate",
        "Use a concise system framing for this controlled experiment.",
        (trace.trace_id,),
    )
    controller.evaluate(
        candidate.candidate_id,
        {"quality": 0.5},
        {"quality": 0.6},
        validation_set="v1",
        test_set="t1",
    )
    with pytest.raises(EvolutionError, match="Host approval bridge"):
        controller.approve(candidate.candidate_id)

    bridge = PrivilegedEvolutionBridge(tmp_path, evolution=controller)
    receipt = bridge.approve(candidate.candidate_id, approved_by="operator")
    assert isinstance(receipt, HostApprovalReceipt)
    applied: list[str] = []
    stage = bridge.apply(
        candidate.candidate_id,
        receipt=receipt,
        applier=lambda value: applied.append(value.content_sha256),
    )
    assert applied == [stage.content_sha256]
    rolled_back: list[str] = []
    bridge.rollback(
        candidate.candidate_id, rollbacker=lambda value: rolled_back.append(value.content_sha256)
    )
    assert rolled_back == [stage.content_sha256]
    assert (
        controller._latest_candidate(candidate.candidate_id).status is CandidateStatus.ROLLED_BACK
    )


def test_privileged_receipt_tampering_and_revoke_after_apply_are_rejected(tmp_path: Path) -> None:
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.SUPERVISED)
    trace = controller.record_trace(
        EvolutionTarget.TOOLSET, EvolutionOutcome.SUCCESS, "reviewed toolset experiment"
    )
    candidate = controller.propose(
        EvolutionTarget.TOOLSET,
        "toolset candidate",
        "Restrict the optional toolset to an explicitly reviewed subset.",
        (trace.trace_id,),
    )
    controller.evaluate(
        candidate.candidate_id,
        {"quality": 0.5},
        {"quality": 0.6},
        validation_set="v1",
        test_set="t1",
    )
    bridge = PrivilegedEvolutionBridge(tmp_path, evolution=controller)
    receipt = bridge.approve(candidate.candidate_id, approved_by="operator")
    tampered = HostApprovalReceipt(
        receipt.receipt_id,
        receipt.candidate_id,
        receipt.target,
        receipt.version,
        "0" * 64,
        receipt.approved_by,
        receipt.approved_at,
    )
    with pytest.raises(EvolutionError, match="does not match"):
        bridge.apply(candidate.candidate_id, receipt=tampered, applier=lambda _stage: None)
    bridge.apply(candidate.candidate_id, receipt=receipt, applier=lambda _stage: None)
    with pytest.raises(EvolutionError, match="rolled back"):
        bridge.revoke(candidate.candidate_id)


def test_privileged_evolution_cli_stages_and_records_host_approval(tmp_path: Path) -> None:
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.SUPERVISED)
    trace = controller.record_trace(
        EvolutionTarget.PERMISSIONS, EvolutionOutcome.SUCCESS, "reviewed permission experiment"
    )
    candidate = controller.propose(
        EvolutionTarget.PERMISSIONS,
        "permission candidate",
        "Require an explicit Host confirmation for this policy experiment.",
        (trace.trace_id,),
    )
    controller.evaluate(
        candidate.candidate_id,
        {"quality": 0.5},
        {"quality": 0.6},
        validation_set="v1",
        test_set="t1",
    )
    output = io.StringIO()
    assert (
        main(
            [
                "evolution",
                "privileged-approve",
                candidate.candidate_id,
                "--approved-by",
                "operator",
            ],
            cwd=tmp_path,
            stdout=output,
        )
        == 0
    )
    assert json.loads(output.getvalue())["candidate_id"] == candidate.candidate_id
    output = io.StringIO()
    assert (
        main(["evolution", "privileged-stage", candidate.candidate_id], cwd=tmp_path, stdout=output)
        == 0
    )
    assert Path(json.loads(output.getvalue())["artifact_path"]).is_file()
