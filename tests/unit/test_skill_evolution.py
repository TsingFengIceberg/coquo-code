from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
import json

from coquo.cli.main import main
from coquo.core.contracts import ToolResult
from coquo.evolution import (
    EvolutionController,
    EvolutionMode,
    EvolutionOutcome,
    EvolutionTarget,
)
from coquo.skill_evolution import (
    SkillCandidateStatus,
    SkillEvolutionService,
    WorkflowPattern,
)
from coquo.skills.catalog import SkillInventoryLoader


def _service_with_repeated_traces(tmp_path: Path) -> SkillEvolutionService:
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.PROPOSE)
    for turn in range(1, 4):
        controller.record_trace(
            EvolutionTarget.WORKFLOW,
            EvolutionOutcome.SUCCESS,
            "committed turn with 2 tool requests and 0 unsuccessful outcomes",
            source_turn=turn,
            workflow=("read_file:succeeded", "grep:succeeded"),
        )
    return SkillEvolutionService(tmp_path, evolution=controller)


def test_repeated_success_traces_produce_one_deterministic_pattern(tmp_path: Path) -> None:
    service = _service_with_repeated_traces(tmp_path)

    patterns = service.patterns()

    assert len(patterns) == 1
    pattern = patterns[0]
    assert isinstance(pattern, WorkflowPattern)
    assert pattern.success_count == 3
    assert pattern.failure_count == 0
    assert pattern.tool_names == ("read_file", "grep")
    assert pattern.workflow == ("read_file:succeeded", "grep:succeeded")


def test_ingest_trace_automatically_quarantines_only_after_threshold(tmp_path: Path) -> None:
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.PROPOSE)
    service = SkillEvolutionService(tmp_path, evolution=controller)

    proposals = []
    for turn in range(1, 4):
        trace = controller.record_trace(
            EvolutionTarget.WORKFLOW,
            EvolutionOutcome.SUCCESS,
            "same workflow",
            source_turn=turn,
            workflow=("read_file:succeeded", "grep:succeeded"),
        )
        proposals.append(service.ingest_trace(trace))

    assert proposals[:2] == [None, None]
    assert proposals[2] is not None
    assert proposals[2].state.skill.status is SkillCandidateStatus.PENDING


def test_evolution_skill_stays_quarantined_until_eval_approval_and_install(tmp_path: Path) -> None:
    service = _service_with_repeated_traces(tmp_path)
    proposal = service.propose(service.patterns()[0])
    name = proposal.state.skill.manifest.name

    assert proposal.state.skill.status is SkillCandidateStatus.PENDING
    assert proposal.safety.passed
    assert all(
        item.manifest.name != name for item in SkillInventoryLoader(tmp_path, {}).load().active
    )

    result = service.evaluate(
        proposal.state.skill.candidate_id,
        {"success_rate": 0.5, "token_cost": 100},
        {"success_rate": 0.8, "token_cost": 90},
    )
    assert result.passed
    approved = service.approve(proposal.state.skill.candidate_id)
    assert approved.evolution.status.value == "approved"

    installed = service.install(proposal.state.skill.candidate_id)
    assert installed.evolution.status.value == "active"
    assert installed.skill.status is SkillCandidateStatus.INSTALLED
    active = SkillInventoryLoader(tmp_path, {}).load().get(name)
    assert installed.skill.source.value == "evolution"
    assert active.manifest.fingerprint == installed.skill.manifest.fingerprint


def test_observation_rollback_and_archive_revoke_installed_skill(tmp_path: Path) -> None:
    service = _service_with_repeated_traces(tmp_path)
    proposal = service.propose(service.patterns()[0])
    candidate_id = proposal.state.skill.candidate_id
    service.evaluate(candidate_id, {"quality": 0.5}, {"quality": 0.6})
    service.approve(candidate_id)
    service.install(candidate_id)

    payload = json.dumps(
        {
            "kind": "skill_loaded",
            "name": proposal.state.skill.manifest.name,
            "fingerprint": proposal.state.skill.manifest.fingerprint,
            "source": "evolution",
        }
    )
    observed = service.observe_turn(
        SimpleNamespace(
            items=(ToolResult("skill-load", payload),),
            tool_ledger=SimpleNamespace(entries=(), requested=0),
        )
    )
    assert len(observed) == 1
    assert observed[0].evolution.use_count == 1

    rolled_back = service.rollback(candidate_id)
    assert rolled_back.evolution.status.value == "rolled_back"
    assert rolled_back.skill.status is SkillCandidateStatus.REVOKED
    assert all(
        item.manifest.name != proposal.state.skill.manifest.name
        for item in SkillInventoryLoader(tmp_path, {}).load().active
    )

    archived = service.archive(before="9999-12-31T00:00:00Z")
    assert len(archived) == 1
    assert archived[0].skill.status is SkillCandidateStatus.ARCHIVED


def test_skill_evolution_cli_covers_quarantine_to_archive_without_provider(tmp_path: Path) -> None:
    environment = {"XDG_CONFIG_HOME": str(tmp_path / "config")}

    assert (
        main(
            ["evolution", "configure", "propose"],
            cwd=tmp_path,
            environment=environment,
            stdout=io.StringIO(),
        )
        == 0
    )
    for turn in range(1, 4):
        output = io.StringIO()
        assert (
            main(
                [
                    "evolution",
                    "trace",
                    "workflow",
                    "success",
                    "same release workflow",
                    "--turn",
                    str(turn),
                    "--workflow",
                    "read_file:succeeded",
                    "--workflow",
                    "grep:succeeded",
                ],
                cwd=tmp_path,
                environment=environment,
                stdout=output,
            )
            == 0
        )

    patterns = io.StringIO()
    assert (
        main(
            ["evolution", "skill-patterns"],
            cwd=tmp_path,
            environment=environment,
            stdout=patterns,
        )
        == 0
    )
    pattern = json.loads(patterns.getvalue())
    assert pattern["success_count"] == 3

    proposed = io.StringIO()
    assert (
        main(
            ["evolution", "skill-propose", pattern["fingerprint"]],
            cwd=tmp_path,
            environment=environment,
            stdout=proposed,
        )
        == 0
    )
    proposal = json.loads(proposed.getvalue())
    skill_id = proposal["state"]["skill"]["candidate_id"]
    assert proposal["state"]["skill"]["status"] == "pending"
    assert proposal["safety"]["passed"] is True

    checked = io.StringIO()
    assert (
        main(
            ["evolution", "skill-safety-check", skill_id],
            cwd=tmp_path,
            environment=environment,
            stdout=checked,
        )
        == 0
    )
    assert json.loads(checked.getvalue())["passed"] is True

    rejected_install = io.StringIO()
    rejected_errors = io.StringIO()
    assert (
        main(
            ["skills", "install", skill_id],
            cwd=tmp_path,
            environment=environment,
            stdout=rejected_install,
            stderr=rejected_errors,
        )
        == 2
    )
    assert "evolution-approval-required" in rejected_errors.getvalue()
    assert rejected_install.getvalue() == ""

    evaluated = io.StringIO()
    assert (
        main(
            [
                "evolution",
                "skill-evaluate",
                skill_id,
                "--baseline-metrics",
                '{"success_rate":0.5,"token_cost":100}',
                "--candidate-metrics",
                '{"success_rate":0.8,"token_cost":90}',
            ],
            cwd=tmp_path,
            environment=environment,
            stdout=evaluated,
        )
        == 0
    )
    assert json.loads(evaluated.getvalue())["passed"] is True

    approved = io.StringIO()
    assert (
        main(
            ["evolution", "skill-approve", skill_id],
            cwd=tmp_path,
            environment=environment,
            stdout=approved,
        )
        == 0
    )
    assert json.loads(approved.getvalue())["evolution"]["status"] == "approved"

    installed = io.StringIO()
    assert (
        main(
            ["evolution", "skill-install", skill_id],
            cwd=tmp_path,
            environment=environment,
            stdout=installed,
        )
        == 0
    )
    installed_state = json.loads(installed.getvalue())
    assert installed_state["evolution"]["status"] == "active"
    assert installed_state["skill"]["status"] == "installed"

    listed = io.StringIO()
    assert (
        main(
            ["skills", "list"],
            cwd=tmp_path,
            environment=environment,
            stdout=listed,
        )
        == 0
    )
    assert json.loads(listed.getvalue())["name"] == installed_state["skill"]["name"]

    observed = io.StringIO()
    assert (
        main(
            [
                "evolution",
                "skill-observe",
                skill_id,
                "--metrics",
                '{"success_rate":1.0,"tool_failures":0}',
            ],
            cwd=tmp_path,
            environment=environment,
            stdout=observed,
        )
        == 0
    )
    assert json.loads(observed.getvalue())["evolution"]["use_count"] == 1

    rolled_back = io.StringIO()
    assert (
        main(
            ["evolution", "skill-rollback", skill_id],
            cwd=tmp_path,
            environment=environment,
            stdout=rolled_back,
        )
        == 0
    )
    assert json.loads(rolled_back.getvalue())["evolution"]["status"] == "rolled_back"

    archived = io.StringIO()
    assert (
        main(
            ["evolution", "skill-archive", "9999-12-31T00:00:00Z"],
            cwd=tmp_path,
            environment=environment,
            stdout=archived,
        )
        == 0
    )
    archived_state = json.loads(archived.getvalue())
    assert archived_state["skill"]["status"] == "archived"
