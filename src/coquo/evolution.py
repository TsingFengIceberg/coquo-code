"""Host-owned, bounded self-evolution control plane.

The controller deliberately treats model-produced improvements as untrusted
task data.  It records content-free execution facts, creates quarantined
versioned candidates, evaluates them with deterministic metrics, and requires
an explicit approval before activation.  It never changes permissions,
sandboxing, tools, providers, or the AgentLoop.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import json
import hashlib
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl

MAX_EVOLUTION_FILE_BYTES = 8 * 1024 * 1024
MAX_TRACE_COUNT = 20_000
MAX_CANDIDATE_COUNT = 2_000
MAX_TEXT_BYTES = 16 * 1024
MAX_SUMMARY_BYTES = 2 * 1024
MAX_METRICS = 32
MAX_WORKFLOW_STEPS = 64
MAX_WORKFLOW_STEP_BYTES = 192
_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SECRET_RE = re.compile(r"(?i)(api[_-]?key|authorization\s*:\s*bearer|secret|password|token\s*=)")


class EvolutionError(ValueError):
    """Raised when evolution state or a transition is invalid."""


class EvolutionMode(StrEnum):
    OFF = "off"
    PROPOSE = "propose"
    SUPERVISED = "supervised"


class EvolutionTarget(StrEnum):
    MEMORY = "memory"
    SKILL = "skill"
    PROMPT = "prompt"
    WORKFLOW = "workflow"


class EvolutionOutcome(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXTERNAL_ERROR = "external_error"


class CandidateStatus(StrEnum):
    CANDIDATE = "candidate"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"
    ARCHIVED = "archived"


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise EvolutionError(f"evolution {field} must be non-blank text")
    if "\x00" in value or len(value.encode("utf-8")) > limit:
        raise EvolutionError(f"evolution {field} exceeds its bound")
    return value


def _id(value: Any, field: str) -> str:
    result = _text(value, field, 64)
    if _ID_RE.fullmatch(result) is None:
        raise EvolutionError(f"evolution {field} must be a UUID4")
    return result


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _metrics(value: Mapping[str, Any] | None) -> tuple[tuple[str, float], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping) or len(value) > MAX_METRICS:
        raise EvolutionError("evolution metrics are invalid")
    result: list[tuple[str, float]] = []
    for key, raw in value.items():
        name = _text(key, "metric name", 64)
        if not name.isascii() or isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise EvolutionError("evolution metric must be finite numeric data")
        number = float(raw)
        if not (number == number and abs(number) != float("inf")):
            raise EvolutionError("evolution metric must be finite numeric data")
        result.append((name, number))
    return tuple(sorted(result))


def _mapping_metrics(value: tuple[tuple[str, float], ...]) -> dict[str, float]:
    return {key: number for key, number in value}


def _workflow_fingerprint(workflow: tuple[str, ...]) -> str:
    """Return a stable, content-free identity for one observed tool sequence."""
    raw = json.dumps(list(workflow), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return "workflow-v1-" + hashlib.sha256(b"coquo-workflow-v1\0" + raw).hexdigest()


@dataclass(frozen=True)
class EvolutionTrace:
    trace_id: str
    target: EvolutionTarget
    outcome: EvolutionOutcome
    summary: str
    source_session_id: str | None
    source_turn: int | None
    metrics: tuple[tuple[str, float], ...]
    occurred_at: str
    workflow: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _id(self.trace_id, "trace_id")
        if not isinstance(self.target, EvolutionTarget) or not isinstance(
            self.outcome, EvolutionOutcome
        ):
            raise EvolutionError("evolution trace target or outcome is invalid")
        _text(self.summary, "trace summary", MAX_SUMMARY_BYTES)
        if self.source_session_id is not None:
            _id(self.source_session_id, "source_session_id")
        if self.source_turn is not None and (
            type(self.source_turn) is not int or self.source_turn < 1
        ):
            raise EvolutionError("evolution source_turn is invalid")
        _text(self.occurred_at, "occurred_at", 64)
        if not isinstance(self.workflow, tuple) or len(self.workflow) > MAX_WORKFLOW_STEPS:
            raise EvolutionError("evolution workflow is invalid")
        for step in self.workflow:
            if (
                not isinstance(step, str)
                or not step
                or not step.isascii()
                or "\x00" in step
                or len(step.encode("ascii")) > MAX_WORKFLOW_STEP_BYTES
            ):
                raise EvolutionError("evolution workflow step is invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "target": self.target.value,
            "outcome": self.outcome.value,
            "summary": self.summary,
            "source_session_id": self.source_session_id,
            "source_turn": self.source_turn,
            "metrics": _mapping_metrics(self.metrics),
            "occurred_at": self.occurred_at,
            "workflow": list(self.workflow),
        }


@dataclass(frozen=True)
class TraceAssessment:
    assessment_id: str
    trace_id: str
    label: str
    grader: str
    confidence: float
    reason: str
    assessed_at: str

    def __post_init__(self) -> None:
        _id(self.assessment_id, "assessment_id")
        _id(self.trace_id, "trace_id")
        _text(self.label, "assessment label", 64)
        _text(self.grader, "grader", 64)
        if not 0.0 <= self.confidence <= 1.0:
            raise EvolutionError("assessment confidence is invalid")
        _text(self.reason, "assessment reason", MAX_SUMMARY_BYTES)
        _text(self.assessed_at, "assessed_at", 64)


@dataclass(frozen=True)
class EvolutionCandidate:
    candidate_id: str
    target: EvolutionTarget
    version: int
    summary: str
    content: str
    source_trace_ids: tuple[str, ...]
    expected_metrics: tuple[tuple[str, float], ...]
    status: CandidateStatus
    created_at: str
    updated_at: str
    last_used_at: str | None = None
    use_count: int = 0
    safety_passed: bool = False
    evaluation_id: str | None = None
    evaluation_passed: bool = False

    def __post_init__(self) -> None:
        _id(self.candidate_id, "candidate_id")
        if (
            not isinstance(self.target, EvolutionTarget)
            or type(self.version) is not int
            or self.version < 1
        ):
            raise EvolutionError("candidate identity is invalid")
        _text(self.summary, "candidate summary", MAX_SUMMARY_BYTES)
        _text(self.content, "candidate content", MAX_TEXT_BYTES)
        if not self.source_trace_ids or len(self.source_trace_ids) > 64:
            raise EvolutionError("candidate must have bounded trace provenance")
        for trace_id in self.source_trace_ids:
            _id(trace_id, "candidate source trace")
        if self.status is CandidateStatus.ACTIVE and not self.safety_passed:
            raise EvolutionError("unsafe candidate cannot be active")
        if type(self.use_count) is not int or self.use_count < 0:
            raise EvolutionError("candidate use count is invalid")

    def as_mapping(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "target": self.target.value,
            "version": self.version,
            "summary": self.summary,
            "content": self.content,
            "source_trace_ids": list(self.source_trace_ids),
            "expected_metrics": _mapping_metrics(self.expected_metrics),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
            "safety_passed": self.safety_passed,
            "evaluation_id": self.evaluation_id,
            "evaluation_passed": self.evaluation_passed,
        }


@dataclass(frozen=True)
class EvaluationResult:
    evaluation_id: str
    candidate_id: str
    validation_set: str
    test_set: str
    baseline_metrics: tuple[tuple[str, float], ...]
    candidate_metrics: tuple[tuple[str, float], ...]
    passed: bool
    checks: tuple[str, ...]
    evaluated_at: str

    def as_mapping(self) -> dict[str, object]:
        return {
            "evaluation_id": self.evaluation_id,
            "candidate_id": self.candidate_id,
            "validation_set": self.validation_set,
            "test_set": self.test_set,
            "baseline_metrics": _mapping_metrics(self.baseline_metrics),
            "candidate_metrics": _mapping_metrics(self.candidate_metrics),
            "passed": self.passed,
            "checks": list(self.checks),
            "evaluated_at": self.evaluated_at,
        }


class EvolutionStore:
    """Append-only durable evolution records under ``.coquo/evolution``."""

    def __init__(self, workspace: Path) -> None:
        original = Path(workspace)
        resolved = original.resolve(strict=True)
        if original.is_symlink() or not resolved.is_dir():
            raise EvolutionError("workspace must be an existing non-symlink directory")
        self.workspace = resolved
        self.root = resolved / ".coquo" / "evolution"
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / ".evolution.lock"
        self._lock = RLock()

    def _append(self, event: str, payload: Mapping[str, object]) -> None:
        # ``version`` is the closed event schema version.  Candidates also
        # have a monotonic content version; keep that payload field separate
        # so a second candidate cannot make the event log unreadable.
        body = {"event": event, "version": 1}
        for key, value in payload.items():
            body["candidate_version" if event == "candidate" and key == "version" else key] = value
        raw = (
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        if len(raw) > 128 * 1024:
            raise EvolutionError("evolution event exceeds its size limit")
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if (
                self.events_path.exists()
                and self.events_path.stat().st_size + len(raw) > MAX_EVOLUTION_FILE_BYTES
            ):
                raise EvolutionError("evolution event log exceeds its size limit")
            with self.lock_path.open("a+b") as lock:
                _lock_file(lock)
                try:
                    with self.events_path.open("ab") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    fd = os.open(self.root, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                finally:
                    _unlock_file(lock)

    def events(self) -> tuple[dict[str, object], ...]:
        if not self.events_path.exists():
            return ()
        raw = self.events_path.read_bytes()
        if len(raw) > MAX_EVOLUTION_FILE_BYTES:
            raise EvolutionError("evolution event log exceeds its size limit")
        result: list[dict[str, object]] = []
        for line in raw.splitlines():
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise EvolutionError("evolution event log is invalid") from None
            if not isinstance(value, dict) or value.get("version") != 1:
                raise EvolutionError("evolution event schema is invalid")
            result.append(value)
            if len(result) > MAX_TRACE_COUNT + MAX_CANDIDATE_COUNT * 8:
                raise EvolutionError("evolution event count exceeds its limit")
        return tuple(result)


class EvolutionController:
    """Nine-step controlled evolution lifecycle."""

    def __init__(self, workspace: Path) -> None:
        self.store = EvolutionStore(workspace)

    def mode(self) -> EvolutionMode:
        for event in reversed(self.store.events()):
            if event.get("event") == "mode_changed":
                return EvolutionMode(event["mode"])
        return EvolutionMode.OFF

    def configure(self, mode: EvolutionMode) -> EvolutionMode:
        if not isinstance(mode, EvolutionMode):
            raise EvolutionError("evolution mode is invalid")
        self.store._append("mode_changed", {"mode": mode.value, "changed_at": _now()})
        return mode

    def record_trace(
        self,
        target: EvolutionTarget,
        outcome: EvolutionOutcome,
        summary: str,
        *,
        source_session_id: str | None = None,
        source_turn: int | None = None,
        metrics: Mapping[str, Any] | None = None,
        workflow: tuple[str, ...] = (),
    ) -> EvolutionTrace:
        trace = EvolutionTrace(
            str(uuid4()),
            target,
            outcome,
            summary,
            source_session_id,
            source_turn,
            _metrics(metrics),
            _now(),
            workflow,
        )
        self.store._append("trace", trace.as_mapping())
        return trace

    def traces(self, target: EvolutionTarget | None = None) -> tuple[EvolutionTrace, ...]:
        result = []
        for event in self.store.events():
            if event.get("event") != "trace":
                continue
            result.append(
                EvolutionTrace(
                    event["trace_id"],
                    EvolutionTarget(event["target"]),
                    EvolutionOutcome(event["outcome"]),
                    event["summary"],
                    event.get("source_session_id"),
                    event.get("source_turn"),
                    _metrics(event.get("metrics")),
                    event["occurred_at"],
                    tuple(event.get("workflow", ())),
                )
            )
        return tuple(item for item in result if target is None or item.target is target)

    def assess(
        self, trace_id: str, *, label: str | None = None, grader: str = "deterministic"
    ) -> TraceAssessment:
        trace = next((item for item in self.traces() if item.trace_id == trace_id), None)
        if trace is None:
            raise EvolutionError("trace does not exist")
        selected = label or ("pass" if trace.outcome is EvolutionOutcome.SUCCESS else "failure")
        assessment = TraceAssessment(
            str(uuid4()), trace_id, selected, grader, 1.0, trace.outcome.value, _now()
        )
        self.store._append(
            "assessment",
            {
                "assessment_id": assessment.assessment_id,
                "trace_id": trace_id,
                "label": assessment.label,
                "grader": assessment.grader,
                "confidence": assessment.confidence,
                "reason": assessment.reason,
                "assessed_at": assessment.assessed_at,
            },
        )
        return assessment

    def patterns(self, target: EvolutionTarget | None = None) -> tuple[dict[str, object], ...]:
        groups: dict[tuple[str, str, str, tuple[str, ...]], list[EvolutionTrace]] = {}
        for trace in self.traces(target):
            key = (
                trace.target.value,
                trace.outcome.value,
                trace.summary.casefold(),
                trace.workflow,
            )
            groups.setdefault(key, []).append(trace)
        return tuple(
            {
                "target": key[0],
                "outcome": key[1],
                "summary": key[2],
                "count": len(items),
                "trace_ids": [item.trace_id for item in items],
                "workflow": list(key[3]),
                "workflow_fingerprint": _workflow_fingerprint(key[3]),
            }
            for key, items in sorted(groups.items())
        )

    def propose(
        self,
        target: EvolutionTarget,
        summary: str,
        content: str,
        source_trace_ids: tuple[str, ...],
        *,
        expected_metrics: Mapping[str, Any] | None = None,
    ) -> EvolutionCandidate:
        if self.mode() is EvolutionMode.OFF:
            raise EvolutionError("evolution is disabled; configure propose or supervised")
        if target in {EvolutionTarget.PROMPT, EvolutionTarget.WORKFLOW} and _SECRET_RE.search(
            content
        ):
            raise EvolutionError("candidate contains a possible secret")
        known = {trace.trace_id for trace in self.traces()}
        if not source_trace_ids or any(trace_id not in known for trace_id in source_trace_ids):
            raise EvolutionError("candidate provenance references an unknown trace")
        next_version = max((item.version for item in self.candidates(target)), default=0) + 1
        candidate = EvolutionCandidate(
            str(uuid4()),
            target,
            next_version,
            summary,
            content,
            tuple(dict.fromkeys(source_trace_ids)),
            _metrics(expected_metrics),
            CandidateStatus.CANDIDATE,
            _now(),
            _now(),
        )
        self.store._append("candidate", candidate.as_mapping())
        return candidate

    def candidates(self, target: EvolutionTarget | None = None) -> tuple[EvolutionCandidate, ...]:
        latest: dict[str, EvolutionCandidate] = {}
        for event in self.store.events():
            if event.get("event") != "candidate":
                continue
            item = self._candidate_from_event(event)
            latest[item.candidate_id] = item
        result = sorted(latest.values(), key=lambda item: (item.created_at, item.candidate_id))
        return tuple(item for item in result if target is None or item.target is target)

    @staticmethod
    def _candidate_from_event(event: Mapping[str, object]) -> EvolutionCandidate:
        return EvolutionCandidate(
            event["candidate_id"],
            EvolutionTarget(event["target"]),
            event.get("candidate_version", event["version"]),
            event["summary"],
            event["content"],
            tuple(event["source_trace_ids"]),
            _metrics(event.get("expected_metrics")),
            CandidateStatus(event["status"]),
            event["created_at"],
            event["updated_at"],
            event.get("last_used_at"),
            event.get("use_count", 0),
            event.get("safety_passed", False),
            event.get("evaluation_id"),
            event.get("evaluation_passed", False),
        )

    def _latest_candidate(self, candidate_id: str) -> EvolutionCandidate:
        items = [item for item in self.candidates() if item.candidate_id == candidate_id]
        if not items:
            raise EvolutionError("candidate does not exist")
        return items[-1]

    def safety_check(self, candidate_id: str) -> tuple[bool, tuple[str, ...]]:
        candidate = self._latest_candidate(candidate_id)
        reasons: list[str] = []
        if _SECRET_RE.search(candidate.content):
            reasons.append("possible_secret")
        if candidate.target in {EvolutionTarget.PROMPT, EvolutionTarget.WORKFLOW} and any(
            marker in candidate.content.casefold()
            for marker in ("permissiongate", "sandbox", "tool schema", "agentloop")
        ):
            reasons.append("protected_runtime_boundary")
        passed = not reasons
        self.store._append(
            "safety",
            {
                "candidate_id": candidate_id,
                "passed": passed,
                "reasons": reasons,
                "checked_at": _now(),
            },
        )
        if passed and not candidate.safety_passed:
            self.store._append(
                "candidate",
                replace(candidate, safety_passed=True, updated_at=_now()).as_mapping(),
            )
        return passed, tuple(reasons)

    def evaluate(
        self,
        candidate_id: str,
        baseline_metrics: Mapping[str, Any],
        candidate_metrics: Mapping[str, Any],
        *,
        validation_set: str,
        test_set: str,
    ) -> EvaluationResult:
        candidate = self._latest_candidate(candidate_id)
        _text(validation_set, "validation_set", 128)
        _text(test_set, "test_set", 128)
        if validation_set == test_set:
            raise EvolutionError("validation and test sets must be independent")
        before, after = _metrics(baseline_metrics), _metrics(candidate_metrics)
        base, newer = _mapping_metrics(before), _mapping_metrics(after)
        checks: list[str] = []
        for name in sorted(set(base) | set(newer)):
            if name not in base or name not in newer:
                checks.append(f"{name}:missing")
                continue
            if name in {"success_rate", "quality", "pass_rate"}:
                checks.append(f"{name}:{'ok' if newer[name] >= base[name] else 'regression'}")
            elif name in {"error_rate", "token_cost", "latency_ms", "elapsed_seconds"}:
                checks.append(f"{name}:{'ok' if newer[name] <= base[name] else 'regression'}")
            else:
                checks.append(f"{name}:observed")
        passed = bool(checks) and all(
            not item.endswith("regression") and not item.endswith("missing") for item in checks
        )
        result = EvaluationResult(
            str(uuid4()),
            candidate_id,
            validation_set,
            test_set,
            before,
            after,
            passed,
            tuple(checks),
            _now(),
        )
        self.store._append("evaluation", result.as_mapping())
        updated = replace(
            candidate,
            status=CandidateStatus.EVALUATED,
            updated_at=_now(),
            evaluation_id=result.evaluation_id,
            evaluation_passed=passed,
        )
        self.store._append("candidate", updated.as_mapping())
        return result

    def approve(self, candidate_id: str) -> EvolutionCandidate:
        candidate = self._latest_candidate(candidate_id)
        if candidate.status is not CandidateStatus.EVALUATED:
            raise EvolutionError("candidate must be evaluated before approval")
        if not candidate.evaluation_passed:
            raise EvolutionError("candidate must pass evaluation before approval")
        updated = replace(candidate, status=CandidateStatus.APPROVED, updated_at=_now())
        self.store._append("candidate", updated.as_mapping())
        return updated

    def activate(self, candidate_id: str) -> EvolutionCandidate:
        candidate = self._latest_candidate(candidate_id)
        if candidate.status is not CandidateStatus.APPROVED:
            raise EvolutionError("only an approved candidate can be activated")
        if not candidate.safety_passed:
            passed, _ = self.safety_check(candidate_id)
            if not passed:
                raise EvolutionError("candidate safety check failed")
            candidate = self._latest_candidate(candidate_id)
        for previous in self.candidates(candidate.target):
            if previous.status is CandidateStatus.ACTIVE:
                self.store._append(
                    "candidate",
                    replace(
                        previous, status=CandidateStatus.DEPRECATED, updated_at=_now()
                    ).as_mapping(),
                )
        updated = replace(
            candidate, status=CandidateStatus.ACTIVE, updated_at=_now(), safety_passed=True
        )
        self.store._append("candidate", updated.as_mapping())
        return updated

    def observe(
        self, candidate_id: str, metrics: Mapping[str, Any], *, used: bool = True
    ) -> EvolutionCandidate:
        candidate = self._latest_candidate(candidate_id)
        if candidate.status is not CandidateStatus.ACTIVE:
            raise EvolutionError("only an active candidate can receive observations")
        self.store._append(
            "observation",
            {
                "candidate_id": candidate_id,
                "metrics": _mapping_metrics(_metrics(metrics)),
                "used": used,
                "observed_at": _now(),
            },
        )
        updated = replace(
            candidate,
            last_used_at=_now() if used else candidate.last_used_at,
            use_count=candidate.use_count + (1 if used else 0),
            updated_at=_now(),
        )
        self.store._append("candidate", updated.as_mapping())
        return updated

    def rollback(self, candidate_id: str) -> EvolutionCandidate:
        candidate = self._latest_candidate(candidate_id)
        if candidate.status is not CandidateStatus.ACTIVE:
            raise EvolutionError("only an active candidate can be rolled back")
        updated = replace(candidate, status=CandidateStatus.ROLLED_BACK, updated_at=_now())
        self.store._append("candidate", updated.as_mapping())
        previous = [
            item
            for item in self.candidates(candidate.target)
            if item.candidate_id != candidate_id
            and item.status is CandidateStatus.DEPRECATED
            and item.safety_passed
            and item.evaluation_passed
        ]
        if previous:
            restore = max(previous, key=lambda item: (item.version, item.updated_at))
            self.store._append(
                "candidate",
                replace(restore, status=CandidateStatus.ACTIVE, updated_at=_now()).as_mapping(),
            )
        return updated

    def deprecate(self, candidate_id: str) -> EvolutionCandidate:
        candidate = self._latest_candidate(candidate_id)
        if candidate.status in {CandidateStatus.ARCHIVED, CandidateStatus.REJECTED}:
            raise EvolutionError("candidate is already terminal")
        updated = replace(candidate, status=CandidateStatus.DEPRECATED, updated_at=_now())
        self.store._append("candidate", updated.as_mapping())
        return updated

    def archive(self, *, before: str) -> tuple[EvolutionCandidate, ...]:
        _text(before, "archive timestamp", 64)
        archived: list[EvolutionCandidate] = []
        for candidate in self.candidates():
            if (
                candidate.status in {CandidateStatus.ACTIVE, CandidateStatus.ARCHIVED}
                or candidate.created_at >= before
            ):
                continue
            updated = replace(candidate, status=CandidateStatus.ARCHIVED, updated_at=_now())
            self.store._append("candidate", updated.as_mapping())
            archived.append(updated)
        return tuple(archived)


def _lock_file(stream: Any) -> None:
    if os.name == "nt":
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock_file(stream: Any) -> None:
    if os.name == "nt":
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


__all__ = [
    "CandidateStatus",
    "EvaluationResult",
    "EvolutionCandidate",
    "EvolutionController",
    "EvolutionError",
    "EvolutionMode",
    "EvolutionOutcome",
    "EvolutionStore",
    "EvolutionTarget",
    "EvolutionTrace",
    "TraceAssessment",
]
