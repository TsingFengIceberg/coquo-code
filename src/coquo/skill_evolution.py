"""Host-owned automatic evolution of repeated workflows into declarative Skills.

This module is deliberately separate from the model-facing Skill authoring tools.  It
mines only bounded Host trace facts, writes inactive candidates, and keeps evaluation,
approval, activation, and rollback explicit.  Generated package text is procedural
guidance only; it never grants a capability or changes a runtime policy.
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
from coquo.skill_candidates import (
    SkillCandidateInfo,
    SkillCandidateSource,
    SkillCandidateStatus,
    SkillCandidateStore,
)
from coquo.skills.catalog import (
    MAX_SKILL_DESCRIPTION_CHARS,
    MAX_SKILL_FILE_BYTES,
    canonical_skill_name,
)


MIN_SUCCESSFUL_TRACES = 3
MAX_PATTERN_TRACES = 32
MAX_PATTERN_STEPS = 64
PATTERN_FINGERPRINT_PREFIX = "workflow-v1-"
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_FINGERPRINT = re.compile(r"workflow-v1-[0-9a-f]{64}\Z")
_UNSAFE_CONTENT = re.compile(
    r"(?i)(?:api[_-]?key|authorization\s*:\s*bearer|\bpassword\b|\bsecret\b|"
    r"bypass(?:es|ed|ing)?|disable(?:s|d|ing)?\s+(?:the\s+)?(?:sandbox|approval|audit)|"
    r"override(?:s|d|ing)?\s+(?:the\s+)?(?:permission|policy|sandbox)|"
    r"grant(?:s|ed|ing)?\s+(?:itself|the\s+agent|permission)|"
    r"ignore(?:s|d|ing)?\s+(?:the\s+)?(?:workspace|sandbox|permission|policy)|"
    r"recursive\s+(?:delegation|agent)|modify\s+(?:the\s+)?(?:system\s+prompt|agentloop|tool\s+schema))"
)


class SkillEvolutionError(ValueError):
    """Raised when automatic Skill evolution cannot proceed safely."""


@dataclass(frozen=True)
class WorkflowPattern:
    """A deterministic repeated sequence of Host-observed tool outcomes."""

    fingerprint: str
    summary: str
    workflow: tuple[str, ...]
    tool_names: tuple[str, ...]
    trace_ids: tuple[str, ...]
    success_count: int
    failure_count: int

    def __post_init__(self) -> None:
        if not _FINGERPRINT.fullmatch(self.fingerprint):
            raise SkillEvolutionError("workflow pattern fingerprint is invalid")
        if not isinstance(self.summary, str) or not self.summary:
            raise SkillEvolutionError("workflow pattern summary is invalid")
        if not isinstance(self.workflow, tuple) or not self.workflow:
            raise SkillEvolutionError("workflow pattern sequence is invalid")
        if len(self.workflow) > MAX_PATTERN_STEPS:
            raise SkillEvolutionError("workflow pattern is too large")
        if not isinstance(self.tool_names, tuple) or not self.tool_names:
            raise SkillEvolutionError("workflow pattern tools are invalid")
        if any(_TOOL_NAME.fullmatch(name) is None for name in self.tool_names):
            raise SkillEvolutionError("workflow pattern contains an invalid tool")
        if (
            not isinstance(self.trace_ids, tuple)
            or not self.trace_ids
            or len(self.trace_ids) > MAX_PATTERN_TRACES
            or any(not isinstance(item, str) or not item for item in self.trace_ids)
        ):
            raise SkillEvolutionError("workflow pattern provenance is invalid")
        if (
            type(self.success_count) is not int
            or self.success_count < 1
            or type(self.failure_count) is not int
            or self.failure_count < 0
        ):
            raise SkillEvolutionError("workflow pattern counts are invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "summary": self.summary,
            "workflow": list(self.workflow),
            "tool_names": list(self.tool_names),
            "trace_ids": list(self.trace_ids),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }


@dataclass(frozen=True)
class SkillSafetyReport:
    """Static, fail-closed checks for one generated Skill package."""

    skill_candidate_id: str
    passed: bool
    reasons: tuple[str, ...]
    checked_fingerprint: str

    def as_mapping(self) -> dict[str, object]:
        return {
            "skill_candidate_id": self.skill_candidate_id,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "checked_fingerprint": self.checked_fingerprint,
        }


@dataclass(frozen=True)
class SkillEvolutionState:
    """Linked Evolution and Skill Store state for one generated Skill."""

    evolution: EvolutionCandidate
    skill: SkillCandidateInfo

    def as_mapping(self) -> dict[str, object]:
        return {
            "evolution": self.evolution.as_mapping(),
            "skill": {
                "candidate_id": self.skill.candidate_id,
                "source": self.skill.source.value,
                "status": self.skill.status.value,
                "name": self.skill.manifest.name,
                "fingerprint": self.skill.manifest.fingerprint,
                "requested_scope": self.skill.requested_scope,
                "evolution_candidate_id": self.skill.evolution_candidate_id,
                "source_trace_ids": list(self.skill.source_trace_ids),
                "pattern_fingerprint": self.skill.pattern_fingerprint,
                "installed_scope": self.skill.installed_scope,
                "installed_lock_digest": self.skill.installed_lock_digest,
            },
        }


@dataclass(frozen=True)
class SkillEvolutionProposal:
    """One quarantined Skill created from a repeated workflow pattern."""

    pattern: WorkflowPattern
    state: SkillEvolutionState
    safety: SkillSafetyReport


def _normalise_summary(summary: str) -> str:
    """Remove volatile numeric counters while retaining a bounded task category."""
    value = re.sub(r"\b\d+\b", "#", summary.casefold())
    return " ".join(value.split())[:512]


def _pattern_fingerprint(summary: str, workflow: tuple[str, ...]) -> str:
    payload = json.dumps(
        {"summary": _normalise_summary(summary), "workflow": list(workflow)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return (
        PATTERN_FINGERPRINT_PREFIX
        + hashlib.sha256(b"coquo-skill-workflow-pattern-v1\0" + payload).hexdigest()
    )


def _tool_names(workflow: tuple[str, ...]) -> tuple[str, ...]:
    names: list[str] = []
    for step in workflow:
        name = step.split(":", 1)[0]
        if _TOOL_NAME.fullmatch(name) is None:
            raise SkillEvolutionError("workflow contains an invalid tool name")
        if name not in names:
            names.append(name)
    return tuple(names)


def _render_skill_file(
    name: str, description: str, instructions: str, tools: tuple[str, ...]
) -> bytes:
    body = instructions if instructions.endswith("\n") else instructions + "\n"
    allowed = "allowed-tools:\n" + "".join(f"  - {tool}\n" for tool in tools)
    raw = (
        "---\n"
        "manifest-version: 1\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        f"{allowed}"
        "---\n"
        f"{body}"
    ).encode("utf-8")
    if len(raw) > MAX_SKILL_FILE_BYTES:
        raise SkillEvolutionError("generated Skill exceeds its byte limit")
    return raw


def _generated_name(fingerprint: str) -> str:
    return canonical_skill_name("evolved-" + fingerprint[-16:])


def _generated_description(pattern: WorkflowPattern) -> str:
    description = "Host-observed repeated workflow using " + ", ".join(pattern.tool_names)
    return description[:MAX_SKILL_DESCRIPTION_CHARS]


def _generated_instructions(pattern: WorkflowPattern) -> str:
    steps = " -> ".join(step.split(":", 1)[0] for step in pattern.workflow)
    return (
        "This is untrusted declarative guidance derived from repeated Host-observed results.\n"
        f"Use it only when the request matches the observed workflow: {pattern.summary}.\n"
        f"Execute the observed tool order exactly: {steps}.\n"
        "After every tool, use the Host result as the source of truth and stop on a failed, "
        "denied, cancelled, partial, or uncertain result.\n"
        "Verify the final result with the available Host evidence and report what was actually "
        "observed; do not claim an action that has no successful result.\n"
    )


class SkillEvolutionService:
    """Orchestrate the complete bounded Trace-to-Skill lifecycle."""

    def __init__(
        self,
        workspace: Path,
        *,
        evolution: EvolutionController | None = None,
        candidates: SkillCandidateStore | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.evolution = evolution or EvolutionController(self.workspace)
        self.candidates = candidates or SkillCandidateStore(self.workspace)

    def patterns(
        self, *, min_successes: int = MIN_SUCCESSFUL_TRACES
    ) -> tuple[WorkflowPattern, ...]:
        if type(min_successes) is not int or not 1 <= min_successes <= MAX_PATTERN_TRACES:
            raise SkillEvolutionError("minimum successful Trace count is invalid")
        groups: dict[tuple[str, tuple[str, ...]], list[EvolutionTrace]] = {}
        for trace in self.evolution.traces(EvolutionTarget.WORKFLOW):
            if not trace.workflow:
                continue
            if len(trace.workflow) > MAX_PATTERN_STEPS:
                continue
            try:
                _tool_names(trace.workflow)
            except SkillEvolutionError:
                continue
            key = (_normalise_summary(trace.summary), trace.workflow)
            groups.setdefault(key, []).append(trace)
        found: list[WorkflowPattern] = []
        for (summary, workflow), traces in groups.items():
            successes = tuple(
                trace for trace in traces if trace.outcome is EvolutionOutcome.SUCCESS
            )
            if len(successes) < min_successes:
                continue
            selected = tuple(trace.trace_id for trace in traces[-MAX_PATTERN_TRACES:])
            selected_successes = sum(
                trace.outcome is EvolutionOutcome.SUCCESS for trace in traces[-MAX_PATTERN_TRACES:]
            )
            found.append(
                WorkflowPattern(
                    fingerprint=_pattern_fingerprint(summary, workflow),
                    summary=summary,
                    workflow=workflow,
                    tool_names=_tool_names(workflow),
                    trace_ids=selected,
                    success_count=selected_successes,
                    failure_count=len(selected) - selected_successes,
                )
            )
        return tuple(sorted(found, key=lambda item: item.fingerprint))

    def ingest_trace(self, trace: EvolutionTrace) -> SkillEvolutionProposal | None:
        """Mine one newly committed trace when the configured mode permits proposals."""
        if not isinstance(trace, EvolutionTrace) or trace.target is not EvolutionTarget.WORKFLOW:
            return None
        if self.evolution.mode() is EvolutionMode.OFF:
            return None
        matches = tuple(item for item in self.patterns() if trace.trace_id in item.trace_ids)
        if not matches:
            return None
        pattern = matches[0]
        existing = self._for_pattern(pattern.fingerprint)
        if existing is not None:
            return existing
        return self.propose(pattern)

    def propose(
        self,
        pattern: WorkflowPattern,
        *,
        scope: str = "project",
    ) -> SkillEvolutionProposal:
        if not isinstance(pattern, WorkflowPattern):
            raise SkillEvolutionError("workflow pattern is invalid")
        if self.evolution.mode() is EvolutionMode.OFF:
            raise SkillEvolutionError("evolution is disabled; configure propose or supervised")
        existing = self._for_pattern(pattern.fingerprint)
        if existing is not None:
            return existing
        name = _generated_name(pattern.fingerprint)
        description = _generated_description(pattern)
        instructions = _generated_instructions(pattern)
        skill_file = _render_skill_file(name, description, instructions, pattern.tool_names)
        self._check_content(name, description, instructions, pattern.tool_names)
        evolution_candidate = self.evolution.propose(
            EvolutionTarget.SKILL,
            f"Repeated workflow: {pattern.fingerprint}",
            skill_file.decode("utf-8").strip(),
            pattern.trace_ids,
            expected_metrics={"success_rate": 1.0, "tool_requests": float(len(pattern.workflow))},
        )
        passed, reasons = self.evolution.safety_check(evolution_candidate.candidate_id)
        if not passed:
            raise SkillEvolutionError("generated Skill failed Evolution safety checks")
        try:
            skill = self.candidates.create_evolution(
                evolution_candidate_id=evolution_candidate.candidate_id,
                name=name,
                description=description,
                instructions=instructions,
                allowed_tools=pattern.tool_names,
                source_trace_ids=pattern.trace_ids,
                pattern_fingerprint=pattern.fingerprint,
                scope=scope,
            )
        except Exception:
            # The generic candidate remains an auditable proposal; it is never activated.
            raise
        safety = self.safety_check(skill.candidate_id)
        if not safety.passed:
            self.candidates.reject(skill.candidate_id)
            raise SkillEvolutionError("generated Skill failed static safety checks")
        return SkillEvolutionProposal(
            pattern,
            SkillEvolutionState(
                self.evolution._latest_candidate(evolution_candidate.candidate_id), skill
            ),
            safety,
        )

    def pattern(self, fingerprint: str) -> WorkflowPattern:
        if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
            raise SkillEvolutionError("workflow pattern fingerprint is invalid")
        for pattern in self.patterns():
            if pattern.fingerprint == fingerprint:
                return pattern
        raise SkillEvolutionError("workflow pattern does not exist")

    def safety_check(self, skill_candidate_id: str) -> SkillSafetyReport:
        skill = self.candidates.inspect(skill_candidate_id)
        reasons: list[str] = []
        if skill.source is not SkillCandidateSource.EVOLUTION:
            reasons.append("not_evolution_source")
        if skill.manifest.allowed_tools is None or not skill.manifest.allowed_tools:
            reasons.append("missing_tool_restriction")
        elif any(_TOOL_NAME.fullmatch(name) is None for name in skill.manifest.allowed_tools):
            reasons.append("invalid_tool_restriction")
        if _UNSAFE_CONTENT.search(skill.manifest.instructions):
            reasons.append("unsafe_instruction")
        if len(skill.manifest.instructions.encode("utf-8")) > MAX_SKILL_FILE_BYTES:
            reasons.append("instruction_size_limit")
        report = SkillSafetyReport(
            skill.candidate_id,
            not reasons,
            tuple(reasons),
            skill.manifest.fingerprint,
        )
        if skill.evolution_candidate_id is not None:
            passed, _ = self.evolution.safety_check(skill.evolution_candidate_id)
            if not passed and report.passed:
                report = SkillSafetyReport(
                    report.skill_candidate_id,
                    False,
                    ("evolution_safety_failed",),
                    report.checked_fingerprint,
                )
        return report

    def evaluate(
        self,
        skill_candidate_id: str,
        baseline_metrics: Mapping[str, Any],
        candidate_metrics: Mapping[str, Any],
        *,
        validation_set: str = "skill-validation-v1",
        test_set: str = "skill-test-v1",
    ) -> EvaluationResult:
        skill = self.candidates.inspect(skill_candidate_id)
        if skill.evolution_candidate_id is None:
            raise SkillEvolutionError("Skill candidate has no Evolution provenance")
        evolution = self.evolution._latest_candidate(skill.evolution_candidate_id)
        if evolution.status not in {CandidateStatus.CANDIDATE, CandidateStatus.EVALUATED}:
            raise SkillEvolutionError("Skill candidate is not in an evaluable quarantine state")
        safety = self.safety_check(skill_candidate_id)
        if not safety.passed:
            raise SkillEvolutionError("Skill safety checks must pass before evaluation")
        return self.evolution.evaluate(
            skill.evolution_candidate_id,
            baseline_metrics,
            candidate_metrics,
            validation_set=validation_set,
            test_set=test_set,
        )

    def approve(self, skill_candidate_id: str) -> SkillEvolutionState:
        state = self._state(skill_candidate_id)
        if state.skill.status is not SkillCandidateStatus.PENDING:
            raise SkillEvolutionError("Skill candidate must remain quarantined before approval")
        evolution = self.evolution.approve(state.evolution.candidate_id)
        return SkillEvolutionState(evolution, state.skill)

    def install(self, skill_candidate_id: str) -> SkillEvolutionState:
        state = self._state(skill_candidate_id)
        if state.evolution.status is not CandidateStatus.APPROVED:
            raise SkillEvolutionError("Skill candidate must be explicitly approved before install")
        try:
            self.candidates.install(
                skill_candidate_id,
                scope=state.skill.requested_scope,
                evolution_approved=True,
            )
        except Exception:
            raise
        try:
            evolution = self.evolution.activate(state.evolution.candidate_id)
        except Exception as error:
            try:
                self.candidates.revoke(skill_candidate_id)
            except Exception as cleanup_error:
                raise SkillEvolutionError(
                    "Skill package installed but activation and rollback cleanup are uncertain"
                ) from cleanup_error
            raise SkillEvolutionError(
                "Skill package installed but Evolution activation could not be committed"
            ) from error
        return SkillEvolutionState(evolution, self.candidates.inspect(skill_candidate_id))

    def observe(
        self,
        skill_candidate_id: str,
        metrics: Mapping[str, Any],
        *,
        used: bool = True,
    ) -> SkillEvolutionState:
        state = self._state(skill_candidate_id)
        evolution = self.evolution.observe(state.evolution.candidate_id, metrics, used=used)
        return SkillEvolutionState(evolution, state.skill)

    def observe_turn(self, turn: Any) -> tuple[SkillEvolutionState, ...]:
        """Record bounded usage facts for Evolution Skills loaded in one committed Turn."""
        ledger = getattr(turn, "tool_ledger", None)
        items = getattr(turn, "items", ())
        failures = sum(
            entry.outcome.value
            in {"error", "denied", "rejected", "cancelled", "failed", "partial", "outcome-unknown"}
            for entry in getattr(ledger, "entries", ())
        )
        requests = getattr(ledger, "requested", 0)
        result: list[SkillEvolutionState] = []
        seen: set[str] = set()
        for item in items:
            content = getattr(item, "content", None)
            if not isinstance(content, str):
                continue
            try:
                payload = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("kind") != "skill_loaded":
                continue
            fingerprint = payload.get("fingerprint")
            if not isinstance(fingerprint, str) or fingerprint in seen:
                continue
            seen.add(fingerprint)
            for skill in self.candidates.list():
                if (
                    skill.manifest.fingerprint != fingerprint
                    or skill.source is not SkillCandidateSource.EVOLUTION
                ):
                    continue
                if skill.evolution_candidate_id is None:
                    continue
                try:
                    result.append(
                        self.observe(
                            skill.candidate_id,
                            {
                                "success_rate": 0.0 if failures else 1.0,
                                "tool_requests": requests,
                                "tool_failures": failures,
                            },
                        )
                    )
                except (EvolutionError, SkillEvolutionError):
                    continue
                break
        return tuple(result)

    def rollback(self, skill_candidate_id: str) -> SkillEvolutionState:
        state = self._state(skill_candidate_id)
        if state.evolution.status is not CandidateStatus.ACTIVE:
            raise SkillEvolutionError("only an active Evolution Skill can be rolled back")
        if state.skill.status is SkillCandidateStatus.INSTALLED:
            self.candidates.revoke(skill_candidate_id)
        evolution = self.evolution.rollback(state.evolution.candidate_id)
        return SkillEvolutionState(evolution, self.candidates.inspect(skill_candidate_id))

    def deprecate(self, skill_candidate_id: str) -> SkillEvolutionState:
        state = self._state(skill_candidate_id)
        evolution = self.evolution.deprecate(state.evolution.candidate_id)
        return SkillEvolutionState(evolution, state.skill)

    def archive(self, *, before: str) -> tuple[SkillEvolutionState, ...]:
        archived = self.evolution.archive(before=before)
        states: list[SkillEvolutionState] = []
        for evolution in archived:
            for skill in self.candidates.list():
                if skill.evolution_candidate_id != evolution.candidate_id:
                    continue
                if skill.status in {SkillCandidateStatus.PENDING, SkillCandidateStatus.REVOKED}:
                    skill = self.candidates.archive(skill.candidate_id)
                states.append(SkillEvolutionState(evolution, skill))
        return tuple(states)

    def _state(self, skill_candidate_id: str) -> SkillEvolutionState:
        skill = self.candidates.inspect(skill_candidate_id)
        if skill.evolution_candidate_id is None:
            raise SkillEvolutionError("Skill candidate has no Evolution provenance")
        evolution = self.evolution._latest_candidate(skill.evolution_candidate_id)
        return SkillEvolutionState(evolution, skill)

    def _for_pattern(self, fingerprint: str) -> SkillEvolutionProposal | None:
        for skill in self.candidates.list():
            if (
                skill.source is SkillCandidateSource.EVOLUTION
                and skill.pattern_fingerprint == fingerprint
                and skill.evolution_candidate_id is not None
            ):
                evolution = self.evolution._latest_candidate(skill.evolution_candidate_id)
                pattern = next(
                    (item for item in self.patterns() if item.fingerprint == fingerprint), None
                )
                if pattern is None:
                    continue
                safety = self.safety_check(skill.candidate_id)
                return SkillEvolutionProposal(
                    pattern, SkillEvolutionState(evolution, skill), safety
                )
        return None

    @staticmethod
    def _check_content(
        name: str, description: str, instructions: str, tools: tuple[str, ...]
    ) -> None:
        if _UNSAFE_CONTENT.search("\n".join((name, description, instructions))):
            raise SkillEvolutionError("generated Skill contains unsafe instructions")
        if not tools or any(_TOOL_NAME.fullmatch(tool) is None for tool in tools):
            raise SkillEvolutionError("generated Skill tool restriction is invalid")


__all__ = [
    "MAX_PATTERN_STEPS",
    "MAX_PATTERN_TRACES",
    "MIN_SUCCESSFUL_TRACES",
    "SkillEvolutionError",
    "SkillEvolutionProposal",
    "SkillEvolutionService",
    "SkillEvolutionState",
    "SkillSafetyReport",
    "WorkflowPattern",
]
