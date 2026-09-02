"""Host-owned evolution of repeated successful experience into Memory facts.

Memory evolution is intentionally a separate service from ordinary memory
capture.  It mines only bounded, content-free EvolutionTrace summaries,
keeps the resulting Memory record quarantined until evaluation and explicit
approval, and reuses the existing EvolutionController lifecycle.  Generated
content is untrusted evidence and never grants a runtime capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from coquo.evolution import (
    CandidateStatus,
    EvaluationResult,
    EvolutionCandidate,
    EvolutionController,
    EvolutionError,
    EvolutionMode,
    EvolutionOutcome,
    EvolutionTarget,
    EvolutionTrace,
)
from coquo.memory import MemoryRecord, MemoryScope, MemoryStatus
from coquo.memory_store import MemoryStore, MemoryStoreError


MIN_SUCCESSFUL_MEMORY_TRACES = 3
MAX_MEMORY_PATTERN_TRACES = 32
MAX_MEMORY_PATTERN_SUMMARY_BYTES = 2 * 1024
_UNSAFE_MEMORY = re.compile(
    r"(?i)(?:api[_-]?key|authorization\s*:\s*bearer|\bpassword\b|\bsecret\b|"
    r"bypass(?:es|ed|ing)?|disable(?:s|d|ing)?\s+(?:the\s+)?(?:sandbox|approval|audit)|"
    r"override(?:s|d|ing)?\s+(?:the\s+)?(?:permission|policy|sandbox)|"
    r"grant(?:s|ed|ing)?\s+(?:itself|the\s+agent|permission)|"
    r"modify\s+(?:the\s+)?(?:system\s+prompt|agentloop|tool\s+schema))"
)


class MemoryEvolutionError(ValueError):
    """Raised when the bounded Memory evolution lifecycle is invalid."""


def _normalise(value: str) -> str:
    return " ".join(re.sub(r"\b\d+\b", "#", value.casefold()).split())


def _fingerprint(summary: str, workflow: tuple[str, ...]) -> str:
    payload = json.dumps(
        {"summary": _normalise(summary), "workflow": list(workflow)},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return "memory-v1-" + hashlib.sha256(b"coquo-memory-evolution-v1\0" + payload).hexdigest()


@dataclass(frozen=True)
class MemoryPattern:
    """Repeated successful Memory experience with bounded provenance."""

    fingerprint: str
    summary: str
    workflow: tuple[str, ...]
    trace_ids: tuple[str, ...]
    success_count: int
    failure_count: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"memory-v1-[0-9a-f]{64}", self.fingerprint):
            raise MemoryEvolutionError("memory pattern fingerprint is invalid")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise MemoryEvolutionError("memory pattern summary is invalid")
        if len(self.summary.encode("utf-8")) > MAX_MEMORY_PATTERN_SUMMARY_BYTES:
            raise MemoryEvolutionError("memory pattern summary is too large")
        if not isinstance(self.workflow, tuple) or len(self.workflow) > 64:
            raise MemoryEvolutionError("memory pattern workflow is invalid")
        if not self.trace_ids or len(self.trace_ids) > MAX_MEMORY_PATTERN_TRACES:
            raise MemoryEvolutionError("memory pattern provenance is invalid")
        if type(self.success_count) is not int or self.success_count < 1:
            raise MemoryEvolutionError("memory pattern success count is invalid")
        if type(self.failure_count) is not int or self.failure_count < 0:
            raise MemoryEvolutionError("memory pattern failure count is invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "summary": self.summary,
            "workflow": list(self.workflow),
            "trace_ids": list(self.trace_ids),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }


@dataclass(frozen=True)
class MemorySafetyReport:
    evolution_candidate_id: str
    passed: bool
    reasons: tuple[str, ...]

    def as_mapping(self) -> dict[str, object]:
        return {
            "evolution_candidate_id": self.evolution_candidate_id,
            "passed": self.passed,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class MemoryEvolutionState:
    evolution: EvolutionCandidate
    memory: MemoryRecord

    def as_mapping(self) -> dict[str, object]:
        return {
            "evolution": self.evolution.as_mapping(),
            "memory": self.memory.to_mapping(),
        }


@dataclass(frozen=True)
class MemoryEvolutionProposal:
    pattern: MemoryPattern
    state: MemoryEvolutionState
    safety: MemorySafetyReport

    def as_mapping(self) -> dict[str, object]:
        return {
            "pattern": self.pattern.as_mapping(),
            "state": self.state.as_mapping(),
            "safety": self.safety.as_mapping(),
        }


class MemoryEvolutionService:
    """Mine, evaluate, activate, observe and roll back Memory candidates."""

    def __init__(
        self,
        workspace: Path,
        *,
        evolution: EvolutionController | None = None,
        memory: MemoryStore | None = None,
        scope_id: str = "workspace",
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        if not isinstance(scope_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", scope_id
        ):
            raise MemoryEvolutionError("memory evolution scope ID is invalid")
        self.scope_id = scope_id
        self.evolution = evolution or EvolutionController(self.workspace)
        self.memory = memory or MemoryStore(self.workspace)

    def patterns(
        self, *, min_successes: int = MIN_SUCCESSFUL_MEMORY_TRACES
    ) -> tuple[MemoryPattern, ...]:
        if type(min_successes) is not int or not 1 <= min_successes <= MAX_MEMORY_PATTERN_TRACES:
            raise MemoryEvolutionError("minimum Memory trace count is invalid")
        groups: dict[tuple[str, tuple[str, ...]], list[EvolutionTrace]] = {}
        for trace in self.evolution.traces(EvolutionTarget.MEMORY):
            key = (_normalise(trace.summary), trace.workflow)
            groups.setdefault(key, []).append(trace)
        found: list[MemoryPattern] = []
        for (summary, workflow), traces in groups.items():
            successes = [item for item in traces if item.outcome is EvolutionOutcome.SUCCESS]
            if len(successes) < min_successes:
                continue
            selected = traces[-MAX_MEMORY_PATTERN_TRACES:]
            found.append(
                MemoryPattern(
                    _fingerprint(summary, workflow),
                    summary,
                    workflow,
                    tuple(item.trace_id for item in selected),
                    sum(item.outcome is EvolutionOutcome.SUCCESS for item in selected),
                    sum(item.outcome is not EvolutionOutcome.SUCCESS for item in selected),
                )
            )
        return tuple(sorted(found, key=lambda item: item.fingerprint))

    def ingest_trace(self, trace: EvolutionTrace) -> MemoryEvolutionProposal | None:
        if not isinstance(trace, EvolutionTrace) or trace.target is not EvolutionTarget.MEMORY:
            return None
        if self.evolution.mode() is EvolutionMode.OFF:
            return None
        for pattern in self.patterns():
            if trace.trace_id in pattern.trace_ids:
                existing = self._for_pattern(pattern.fingerprint)
                return existing or self.propose(pattern)
        return None

    def pattern(self, fingerprint: str) -> MemoryPattern:
        if not isinstance(fingerprint, str) or not re.fullmatch(
            r"memory-v1-[0-9a-f]{64}", fingerprint
        ):
            raise MemoryEvolutionError("memory pattern fingerprint is invalid")
        for pattern in self.patterns():
            if pattern.fingerprint == fingerprint:
                return pattern
        raise MemoryEvolutionError("memory pattern does not exist")

    def propose(
        self,
        pattern: MemoryPattern,
        *,
        category: str = "evolved_experience",
        confidence: float = 0.75,
    ) -> MemoryEvolutionProposal:
        if not isinstance(pattern, MemoryPattern):
            raise MemoryEvolutionError("memory pattern is invalid")
        if self.evolution.mode() is EvolutionMode.OFF:
            raise MemoryEvolutionError("evolution is disabled; configure propose or supervised")
        existing = self._for_pattern(pattern.fingerprint)
        if existing is not None:
            return existing
        content = "Host-observed repeated successful experience: " + pattern.summary
        safety = self._content_safety(content)
        if not safety.passed:
            raise MemoryEvolutionError("generated Memory candidate failed safety checks")
        candidate = self.evolution.propose(
            EvolutionTarget.MEMORY,
            f"Repeated Memory experience: {pattern.fingerprint}",
            content,
            pattern.trace_ids,
            expected_metrics={"success_rate": 1.0},
        )
        passed, reasons = self._candidate_safety(candidate)
        if not passed:
            raise MemoryEvolutionError("generated Memory candidate failed safety checks")
        try:
            record = self.memory.create_candidate(
                content,
                scope=MemoryScope.WORKSPACE,
                scope_id=self.scope_id,
                category=category,
                confidence=confidence,
                source_session_id=pattern.trace_ids[0],
                source_turn=None,
            )
            self.evolution.store._append(
                "memory_link",
                {
                    "evolution_candidate_id": candidate.candidate_id,
                    "memory_id": record.memory_id,
                    "pattern_fingerprint": pattern.fingerprint,
                    "linked_at": candidate.created_at,
                },
            )
        except (MemoryStoreError, EvolutionError) as error:
            raise MemoryEvolutionError(f"Memory candidate persistence failed: {error}") from error
        report = MemorySafetyReport(candidate.candidate_id, passed, reasons)
        return MemoryEvolutionProposal(
            pattern,
            MemoryEvolutionState(candidate, record),
            report,
        )

    def safety_check(self, candidate_id: str) -> MemorySafetyReport:
        state = self._state(candidate_id)
        passed, reasons = self._candidate_safety(state.evolution)
        self.evolution.store._append(
            "memory_safety",
            {"candidate_id": candidate_id, "passed": passed, "reasons": list(reasons)},
        )
        if passed:
            self.evolution.safety_check(candidate_id)
        return MemorySafetyReport(candidate_id, passed, reasons)

    def evaluate(
        self,
        candidate_id: str,
        baseline_metrics: Mapping[str, Any],
        candidate_metrics: Mapping[str, Any],
        *,
        validation_set: str = "memory-validation-v1",
        test_set: str = "memory-test-v1",
    ) -> EvaluationResult:
        state = self._state(candidate_id)
        report = self.safety_check(candidate_id)
        if not report.passed:
            raise MemoryEvolutionError("Memory safety checks must pass before evaluation")
        if state.memory.status is not MemoryStatus.CANDIDATE:
            raise MemoryEvolutionError("Memory candidate is no longer quarantined")
        return self.evolution.evaluate(
            state.evolution.candidate_id,
            baseline_metrics,
            candidate_metrics,
            validation_set=validation_set,
            test_set=test_set,
        )

    def approve(self, candidate_id: str) -> MemoryEvolutionState:
        state = self._state(candidate_id)
        if state.memory.status is not MemoryStatus.CANDIDATE:
            raise MemoryEvolutionError("Memory candidate must remain quarantined before approval")
        return MemoryEvolutionState(self.evolution.approve(candidate_id), state.memory)

    def activate(self, candidate_id: str) -> MemoryEvolutionState:
        state = self._state(candidate_id)
        if state.evolution.status is not CandidateStatus.APPROVED:
            raise MemoryEvolutionError(
                "Memory candidate must be explicitly approved before activation"
            )
        active = self.evolution.activate(candidate_id)
        try:
            record = self.memory.confirm(state.memory.memory_id)
        except Exception as error:
            try:
                self.evolution.rollback(candidate_id)
            except Exception as rollback_error:
                raise MemoryEvolutionError(
                    "Memory activation is uncertain; manual recovery required"
                ) from rollback_error
            raise MemoryEvolutionError("Memory activation failed and was rolled back") from error
        return MemoryEvolutionState(active, record)

    def observe(
        self, candidate_id: str, metrics: Mapping[str, Any], *, used: bool = True
    ) -> MemoryEvolutionState:
        state = self._state(candidate_id)
        evolved = self.evolution.observe(candidate_id, metrics, used=used)
        if used and state.memory.status is MemoryStatus.CONFIRMED:
            try:
                record = self.memory.reinforce(state.memory.memory_id)
            except MemoryStoreError:
                record = state.memory
        else:
            record = state.memory
        return MemoryEvolutionState(evolved, record)

    def rollback(self, candidate_id: str) -> MemoryEvolutionState:
        state = self._state(candidate_id)
        rolled = self.evolution.rollback(candidate_id)
        record = state.memory
        if record.status is MemoryStatus.CONFIRMED:
            record = self.memory.transition(
                record.memory_id, MemoryStatus.STALE, reason="evolution_rollback"
            )
        return MemoryEvolutionState(rolled, record)

    def archive(self, *, before: str) -> tuple[MemoryEvolutionState, ...]:
        archived = self.evolution.archive(before=before)
        result: list[MemoryEvolutionState] = []
        for candidate in archived:
            try:
                result.append(self._state(candidate.candidate_id))
            except MemoryEvolutionError:
                continue
        return tuple(result)

    def _state(self, candidate_id: str) -> MemoryEvolutionState:
        candidate = self.evolution._latest_candidate(candidate_id)
        if candidate.target is not EvolutionTarget.MEMORY:
            raise MemoryEvolutionError("candidate is not a Memory evolution candidate")
        links = [
            event
            for event in self.evolution.store.events()
            if event.get("event") == "memory_link"
            and event.get("evolution_candidate_id") == candidate_id
        ]
        if not links:
            raise MemoryEvolutionError("Memory candidate provenance link does not exist")
        try:
            record = self.memory.get(links[-1]["memory_id"])
        except (KeyError, MemoryStoreError) as error:
            raise MemoryEvolutionError("linked Memory record does not exist") from error
        return MemoryEvolutionState(candidate, record)

    def _for_pattern(self, fingerprint: str) -> MemoryEvolutionProposal | None:
        for event in self.evolution.store.events():
            if (
                event.get("event") != "memory_link"
                or event.get("pattern_fingerprint") != fingerprint
            ):
                continue
            candidate_id = event.get("evolution_candidate_id")
            if not isinstance(candidate_id, str):
                continue
            try:
                state = self._state(candidate_id)
            except MemoryEvolutionError:
                continue
            pattern = next(
                (item for item in self.patterns() if item.fingerprint == fingerprint), None
            )
            if pattern is not None:
                return MemoryEvolutionProposal(pattern, state, self.safety_check(candidate_id))
        return None

    @staticmethod
    def _content_safety(content: str) -> MemorySafetyReport:
        reasons = ("unsafe_content",) if _UNSAFE_MEMORY.search(content) else ()
        return MemorySafetyReport("00000000-0000-4000-8000-000000000000", not reasons, reasons)

    @staticmethod
    def _candidate_safety(candidate: EvolutionCandidate) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        if _UNSAFE_MEMORY.search(candidate.content):
            reasons.append("unsafe_content")
        if len(candidate.content.encode("utf-8")) > 16 * 1024:
            reasons.append("content_size_limit")
        return not reasons, tuple(reasons)


__all__ = [
    "MAX_MEMORY_PATTERN_TRACES",
    "MIN_SUCCESSFUL_MEMORY_TRACES",
    "MemoryEvolutionError",
    "MemoryEvolutionProposal",
    "MemoryEvolutionService",
    "MemoryEvolutionState",
    "MemoryPattern",
    "MemorySafetyReport",
]
