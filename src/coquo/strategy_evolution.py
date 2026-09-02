"""Bounded automatic Prompt and Workflow strategy evolution.

The strategy service mines repeated Host traces into declarative candidates.
It deliberately does not alter the canonical system prompt, ToolSet,
PermissionGate, sandbox, AgentLoop, or provider contracts.  Candidates are
ordinary EvolutionController records and therefore remain quarantined until
independent evaluation and explicit approval.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from coquo.evolution import (
    EvaluationResult,
    EvolutionCandidate,
    EvolutionController,
    EvolutionMode,
    EvolutionOutcome,
    EvolutionTarget,
    EvolutionTrace,
)


MIN_STRATEGY_TRACES = 3
MAX_STRATEGY_TRACES = 32
MAX_STRATEGY_CONTENT_BYTES = 16 * 1024
_SAFE_TARGETS = {EvolutionTarget.PROMPT, EvolutionTarget.WORKFLOW}
_UNSAFE = re.compile(
    r"(?i)(?:api[_-]?key|authorization\s*:\s*bearer|\bpassword\b|\bsecret\b|"
    r"bypass(?:es|ed|ing)?|disable(?:s|d|ing)?\s+(?:the\s+)?(?:sandbox|approval|audit)|"
    r"override(?:s|d|ing)?\s+(?:the\s+)?(?:permission|policy|sandbox)|"
    r"modify\s+(?:the\s+)?(?:system\s+prompt|agentloop|tool\s+schema))"
)


class StrategyEvolutionError(ValueError):
    """Raised for an invalid Prompt or Workflow strategy transition."""


def _normalise(summary: str) -> str:
    return " ".join(re.sub(r"\b\d+\b", "#", summary.casefold()).split())


def _fingerprint(target: EvolutionTarget, summary: str, workflow: tuple[str, ...]) -> str:
    payload = json.dumps(
        {"target": target.value, "summary": _normalise(summary), "workflow": list(workflow)},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return "strategy-v1-" + hashlib.sha256(b"coquo-strategy-v1\0" + payload).hexdigest()


@dataclass(frozen=True)
class StrategyPattern:
    target: EvolutionTarget
    fingerprint: str
    summary: str
    workflow: tuple[str, ...]
    trace_ids: tuple[str, ...]
    success_count: int
    failure_count: int

    def __post_init__(self) -> None:
        if self.target not in _SAFE_TARGETS:
            raise StrategyEvolutionError("strategy target must be prompt or workflow")
        if not re.fullmatch(r"strategy-v1-[0-9a-f]{64}", self.fingerprint):
            raise StrategyEvolutionError("strategy fingerprint is invalid")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise StrategyEvolutionError("strategy summary is invalid")
        if not self.trace_ids or len(self.trace_ids) > MAX_STRATEGY_TRACES:
            raise StrategyEvolutionError("strategy provenance is invalid")
        if type(self.success_count) is not int or self.success_count < 1:
            raise StrategyEvolutionError("strategy success count is invalid")
        if type(self.failure_count) is not int or self.failure_count < 0:
            raise StrategyEvolutionError("strategy failure count is invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "target": self.target.value,
            "fingerprint": self.fingerprint,
            "summary": self.summary,
            "workflow": list(self.workflow),
            "trace_ids": list(self.trace_ids),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }


@dataclass(frozen=True)
class StrategySafetyReport:
    candidate_id: str
    passed: bool
    reasons: tuple[str, ...]

    def as_mapping(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "passed": self.passed,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class StrategyEvolutionProposal:
    pattern: StrategyPattern
    candidate: EvolutionCandidate
    safety: StrategySafetyReport

    def as_mapping(self) -> dict[str, object]:
        return {
            "pattern": self.pattern.as_mapping(),
            "candidate": self.candidate.as_mapping(),
            "safety": self.safety.as_mapping(),
        }


class StrategyEvolutionService:
    """Mine and govern Prompt/Workflow candidates through one Host lifecycle."""

    def __init__(self, workspace: Path, *, evolution: EvolutionController | None = None) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.evolution = evolution or EvolutionController(self.workspace)

    def patterns(
        self,
        target: EvolutionTarget,
        *,
        min_successes: int = MIN_STRATEGY_TRACES,
    ) -> tuple[StrategyPattern, ...]:
        if target not in _SAFE_TARGETS:
            raise StrategyEvolutionError("strategy target must be prompt or workflow")
        if type(min_successes) is not int or not 1 <= min_successes <= MAX_STRATEGY_TRACES:
            raise StrategyEvolutionError("minimum strategy trace count is invalid")
        groups: dict[tuple[str, tuple[str, ...]], list[EvolutionTrace]] = {}
        for trace in self.evolution.traces(target):
            groups.setdefault((_normalise(trace.summary), trace.workflow), []).append(trace)
        result: list[StrategyPattern] = []
        for (summary, workflow), traces in groups.items():
            successes = [item for item in traces if item.outcome is EvolutionOutcome.SUCCESS]
            if len(successes) < min_successes:
                continue
            selected = traces[-MAX_STRATEGY_TRACES:]
            result.append(
                StrategyPattern(
                    target,
                    _fingerprint(target, summary, workflow),
                    summary,
                    workflow,
                    tuple(item.trace_id for item in selected),
                    sum(item.outcome is EvolutionOutcome.SUCCESS for item in selected),
                    sum(item.outcome is not EvolutionOutcome.SUCCESS for item in selected),
                )
            )
        return tuple(sorted(result, key=lambda item: item.fingerprint))

    def ingest_trace(self, trace: EvolutionTrace) -> StrategyEvolutionProposal | None:
        if not isinstance(trace, EvolutionTrace) or trace.target not in _SAFE_TARGETS:
            return None
        if self.evolution.mode() is EvolutionMode.OFF:
            return None
        for pattern in self.patterns(trace.target):
            if trace.trace_id in pattern.trace_ids:
                existing = self._for_pattern(pattern.fingerprint)
                return existing or self.propose(pattern)
        return None

    def pattern(self, target: EvolutionTarget, fingerprint: str) -> StrategyPattern:
        if target not in _SAFE_TARGETS:
            raise StrategyEvolutionError("strategy target must be prompt or workflow")
        if not isinstance(fingerprint, str) or not re.fullmatch(
            r"strategy-v1-[0-9a-f]{64}", fingerprint
        ):
            raise StrategyEvolutionError("strategy fingerprint is invalid")
        for pattern in self.patterns(target):
            if pattern.fingerprint == fingerprint:
                return pattern
        raise StrategyEvolutionError("strategy pattern does not exist")

    def propose(self, pattern: StrategyPattern) -> StrategyEvolutionProposal:
        if not isinstance(pattern, StrategyPattern):
            raise StrategyEvolutionError("strategy pattern is invalid")
        if self.evolution.mode() is EvolutionMode.OFF:
            raise StrategyEvolutionError("evolution is disabled; configure propose or supervised")
        existing = self._for_pattern(pattern.fingerprint)
        if existing is not None:
            return existing
        if pattern.target is EvolutionTarget.WORKFLOW:
            steps = " -> ".join(step.split(":", 1)[0] for step in pattern.workflow)
            content = (
                "Untrusted workflow strategy candidate. For matching tasks, follow this observed "
                f"order: {steps}. Stop on any failed, denied, cancelled, partial, or uncertain result."
            )
        else:
            content = (
                "Untrusted prompt strategy candidate. For matching tasks, state the observed "
                "evidence first, distinguish unknowns from facts, and keep the final response concise."
            )
        report = self._safety_content(content)
        if not report.passed:
            raise StrategyEvolutionError("generated strategy failed safety checks")
        candidate = self.evolution.propose(
            pattern.target,
            f"Repeated strategy: {pattern.fingerprint}",
            content,
            pattern.trace_ids,
            expected_metrics={"success_rate": 1.0},
        )
        passed, reasons = self.evolution.safety_check(candidate.candidate_id)
        if not passed:
            raise StrategyEvolutionError("generated strategy failed Evolution safety checks")
        return StrategyEvolutionProposal(
            pattern,
            self.evolution._latest_candidate(candidate.candidate_id),
            StrategySafetyReport(candidate.candidate_id, passed, reasons),
        )

    def safety_check(self, candidate_id: str) -> StrategySafetyReport:
        candidate = self.evolution._latest_candidate(candidate_id)
        if candidate.target not in _SAFE_TARGETS:
            raise StrategyEvolutionError("candidate is not a Prompt/Workflow strategy")
        passed, reasons = self.evolution.safety_check(candidate_id)
        return StrategySafetyReport(candidate_id, passed, reasons)

    def evaluate(
        self,
        candidate_id: str,
        baseline_metrics: Mapping[str, Any],
        candidate_metrics: Mapping[str, Any],
        *,
        validation_set: str = "strategy-validation-v1",
        test_set: str = "strategy-test-v1",
    ) -> EvaluationResult:
        report = self.safety_check(candidate_id)
        if not report.passed:
            raise StrategyEvolutionError("strategy safety checks must pass before evaluation")
        return self.evolution.evaluate(
            candidate_id,
            baseline_metrics,
            candidate_metrics,
            validation_set=validation_set,
            test_set=test_set,
        )

    def approve(self, candidate_id: str) -> EvolutionCandidate:
        return self.evolution.approve(candidate_id)

    def activate(self, candidate_id: str) -> EvolutionCandidate:
        return self.evolution.activate(candidate_id)

    def observe(
        self, candidate_id: str, metrics: Mapping[str, Any], *, used: bool = True
    ) -> EvolutionCandidate:
        return self.evolution.observe(candidate_id, metrics, used=used)

    def rollback(self, candidate_id: str) -> EvolutionCandidate:
        return self.evolution.rollback(candidate_id)

    def deprecate(self, candidate_id: str) -> EvolutionCandidate:
        return self.evolution.deprecate(candidate_id)

    def archive(self, *, before: str) -> tuple[EvolutionCandidate, ...]:
        return tuple(
            candidate
            for candidate in self.evolution.archive(before=before)
            if candidate.target in _SAFE_TARGETS
        )

    def _for_pattern(self, fingerprint: str) -> StrategyEvolutionProposal | None:
        for candidate in self.evolution.candidates():
            if candidate.target not in _SAFE_TARGETS:
                continue
            # Candidate summaries contain the fingerprint, so this is a stable
            # link without a second mutable registry.
            if fingerprint not in candidate.summary:
                continue
            pattern = next(
                (
                    item
                    for item in self.patterns(candidate.target)
                    if item.fingerprint == fingerprint
                ),
                None,
            )
            if pattern is None:
                continue
            return StrategyEvolutionProposal(
                pattern,
                candidate,
                self.safety_check(candidate.candidate_id),
            )
        return None

    @staticmethod
    def _safety_content(content: str) -> StrategySafetyReport:
        reasons = ("unsafe_content",) if _UNSAFE.search(content) else ()
        return StrategySafetyReport("00000000-0000-4000-8000-000000000000", not reasons, reasons)


__all__ = [
    "MAX_STRATEGY_CONTENT_BYTES",
    "MAX_STRATEGY_TRACES",
    "MIN_STRATEGY_TRACES",
    "StrategyEvolutionError",
    "StrategyEvolutionProposal",
    "StrategyEvolutionService",
    "StrategyPattern",
    "StrategySafetyReport",
]
