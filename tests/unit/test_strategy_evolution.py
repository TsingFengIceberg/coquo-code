from __future__ import annotations

import pytest

from coquo.evolution import EvolutionController, EvolutionMode, EvolutionOutcome, EvolutionTarget
from coquo.strategy_evolution import StrategyEvolutionError, StrategyEvolutionService


def _controller(tmp_path, target):
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.PROPOSE)
    for turn in range(1, 4):
        controller.record_trace(
            target,
            EvolutionOutcome.SUCCESS,
            "inspect deployment configuration",
            source_turn=turn,
            workflow=("read_file:succeeded", "grep:succeeded"),
        )
    return controller


@pytest.mark.parametrize("target", [EvolutionTarget.PROMPT, EvolutionTarget.WORKFLOW])
def test_strategy_patterns_and_quarantined_proposal(tmp_path, target):
    controller = _controller(tmp_path, target)
    service = StrategyEvolutionService(tmp_path, evolution=controller)
    pattern = service.patterns(target)[0]
    proposal = service.propose(pattern)

    assert proposal.candidate.target is target
    assert proposal.candidate.status.value == "candidate"
    assert proposal.safety.passed
    assert service.ingest_trace(controller.traces(target)[-1]) is not None


def test_workflow_strategy_lifecycle_uses_common_evolution_gate(tmp_path):
    controller = _controller(tmp_path, EvolutionTarget.WORKFLOW)
    service = StrategyEvolutionService(tmp_path, evolution=controller)
    candidate_id = service.propose(
        service.patterns(EvolutionTarget.WORKFLOW)[0]
    ).candidate.candidate_id

    result = service.evaluate(
        candidate_id,
        {"pass_rate": 0.5, "latency_ms": 100},
        {"pass_rate": 0.8, "latency_ms": 90},
    )
    assert result.passed
    service.approve(candidate_id)
    active = service.activate(candidate_id)
    assert active.status.value == "active"
    observed = service.observe(candidate_id, {"pass_rate": 1.0})
    assert observed.use_count == 1
    rolled = service.rollback(candidate_id)
    assert rolled.status.value == "rolled_back"


def test_strategy_does_not_accept_memory_or_skill_targets(tmp_path):
    controller = EvolutionController(tmp_path)
    controller.configure(EvolutionMode.PROPOSE)
    with pytest.raises(StrategyEvolutionError, match="prompt or workflow"):
        StrategyEvolutionService(tmp_path, evolution=controller).patterns(EvolutionTarget.MEMORY)
