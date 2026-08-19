"""Host-owned, fail-closed orchestration for a bounded multi-agent workflow.

This module is intentionally a thin coordinator.  Task, Child, Team, and
verification ledgers remain authoritative for execution facts; this layer
stores only the workflow phase, immutable packet, and explicitly supplied
untrusted evidence.  It never invokes a Provider and never performs Git
integration, commit, push, or workspace mutation on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from uuid import UUID, uuid4

from coquo.task_bridge import TaskBridgeResult
from coquo.task_store import TaskStore, TaskStoreError


class WorkflowError(RuntimeError):
    """Raised when a workflow request would violate its Host state machine."""


class WorkflowRole(StrEnum):
    ARCHITECT = "architect"
    EXPLORER = "explorer"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"
    INTEGRATOR = "integrator"


class WorkflowPhase(StrEnum):
    ARCHITECTURE = "architecture"
    EXPLORATION = "exploration"
    EXECUTION = "execution"
    REVIEW = "review"
    INTEGRATION = "integration"
    COMPLETED = "completed"
    NEEDS_REWORK = "needs-rework"
    BLOCKED = "blocked"
    RECOVERY_REQUIRED = "recovery-required"


class WorkflowVerdict(StrEnum):
    PASSED = "passed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


_TERMINAL_PHASES = {
    WorkflowPhase.COMPLETED,
    WorkflowPhase.BLOCKED,
    WorkflowPhase.RECOVERY_REQUIRED,
}
_NEXT_PHASE = {
    WorkflowPhase.ARCHITECTURE: WorkflowPhase.EXPLORATION,
    WorkflowPhase.EXPLORATION: WorkflowPhase.EXECUTION,
    WorkflowPhase.EXECUTION: WorkflowPhase.REVIEW,
    WorkflowPhase.REVIEW: WorkflowPhase.INTEGRATION,
}
_PHASE_ROLE = {
    WorkflowPhase.ARCHITECTURE: WorkflowRole.ARCHITECT,
    WorkflowPhase.EXPLORATION: WorkflowRole.EXPLORER,
    WorkflowPhase.EXECUTION: WorkflowRole.EXECUTOR,
    WorkflowPhase.REVIEW: WorkflowRole.REVIEWER,
    WorkflowPhase.INTEGRATION: WorkflowRole.INTEGRATOR,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value: object, label: str, *, max_chars: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise WorkflowError(f"workflow {label} is invalid")
    value = value.strip()
    if len(value) > max_chars:
        raise WorkflowError(f"workflow {label} is oversized")
    return value


def _tuple_text(values: object, label: str, *, max_items: int = 32) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        try:
            values = tuple(values)  # type: ignore[arg-type]
        except TypeError:
            raise WorkflowError(f"workflow {label} is invalid") from None
    if len(values) > max_items:
        raise WorkflowError(f"workflow {label} has too many entries")
    result = tuple(_text(value, f"{label} entry", max_chars=2048) for value in values)
    if len(set(result)) != len(result):
        raise WorkflowError(f"workflow {label} contains duplicates")
    return result


@dataclass(frozen=True)
class WorkflowPacket:
    """Immutable contract handed from one Host-owned phase to the next."""

    objective: str
    scope: tuple[str, ...]
    resources: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    permission_profile: str
    recovery_policy: str
    verification_plan: tuple[str, ...]
    provider_provenance: Mapping[str, str]

    def __post_init__(self) -> None:
        _text(self.objective, "objective")
        _tuple_text(self.scope, "scope")
        _tuple_text(self.resources, "resources")
        if not self.acceptance_criteria:
            raise WorkflowError("workflow acceptance criteria cannot be empty")
        _tuple_text(self.acceptance_criteria, "acceptance criteria")
        _text(self.permission_profile, "permission profile", max_chars=256)
        _text(self.recovery_policy, "recovery policy", max_chars=2048)
        _tuple_text(self.verification_plan, "verification plan")
        if not isinstance(self.provider_provenance, Mapping):
            raise WorkflowError("workflow provider provenance is invalid")
        for key, value in self.provider_provenance.items():
            _text(key, "provider provenance key", max_chars=128)
            _text(value, "provider provenance value", max_chars=512)


@dataclass(frozen=True)
class WorkflowEvidence:
    role: WorkflowRole
    source_id: str
    status: str
    summary: str
    untrusted: bool = True
    evidence_sha256: str | None = None
    recorded_at: str = ""

    def __post_init__(self) -> None:
        if type(self.role) is not WorkflowRole:
            raise WorkflowError("workflow evidence role is invalid")
        _text(self.source_id, "evidence source ID", max_chars=256)
        _text(self.status, "evidence status", max_chars=128)
        _text(self.summary, "evidence summary", max_chars=8192)
        if self.untrusted is not True:
            raise WorkflowError("workflow evidence must remain untrusted")
        if self.evidence_sha256 is not None and (
            not isinstance(self.evidence_sha256, str)
            or len(self.evidence_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.evidence_sha256)
        ):
            raise WorkflowError("workflow evidence digest is invalid")


@dataclass(frozen=True)
class WorkflowState:
    workflow_id: str
    task_id: str
    phase: WorkflowPhase
    packet: WorkflowPacket
    evidence: tuple[WorkflowEvidence, ...] = ()
    revision: int = 0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        try:
            UUID(self.workflow_id)
            UUID(self.task_id)
        except (ValueError, AttributeError):
            raise WorkflowError("workflow or Task ID is invalid") from None
        if type(self.phase) is not WorkflowPhase:
            raise WorkflowError("workflow phase is invalid")
        if type(self.revision) is not int or self.revision < 0:
            raise WorkflowError("workflow revision is invalid")
        if any(type(item) is not WorkflowEvidence for item in self.evidence):
            raise WorkflowError("workflow evidence is invalid")


class WorkflowOrchestrator:
    """Advance one Host-owned workflow without autonomous side effects."""

    def __init__(self, workspace: Path, *, task_store: TaskStore | None = None) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.tasks = task_store or TaskStore(self.workspace)
        self.root = self.workspace / ".coquo" / "workflows"
        self._states: dict[str, WorkflowState] = {}

    def start(
        self,
        objective: str,
        *,
        owner_session: str = "latest",
        task_id: str | None = None,
        scope: tuple[str, ...] = ("workspace",),
        resources: tuple[str, ...] = (),
        acceptance_criteria: tuple[str, ...] = (),
        permission_profile: str = "read-only-explorer-then-explicit-executor",
        recovery_policy: str = "unknown-or-lost-result requires Host recovery; never auto-retry",
        verification_plan: tuple[str, ...] = ("Host checks", "independent read-only review"),
        provider_provenance: Mapping[str, str] = (),
    ) -> WorkflowState:
        objective = _text(objective, "objective")
        criteria = _tuple_text(acceptance_criteria, "acceptance criteria")
        if not criteria:
            raise WorkflowError("workflow acceptance criteria cannot be empty")
        if task_id is None:
            try:
                task = self.tasks.create(
                    objective,
                    owner_session=owner_session,
                    acceptance_criteria=criteria,
                )
            except (TaskStoreError, OSError) as error:
                raise WorkflowError(f"Task creation failed: {error}") from None
            task_id = task.task_id
        else:
            try:
                task = self.tasks.inspect(task_id)
            except TaskStoreError as error:
                raise WorkflowError(f"Task inspection failed: {error}") from None
            if task.objective != objective:
                raise WorkflowError("workflow objective does not match the existing Task")
        packet = WorkflowPacket(
            objective=objective,
            scope=_tuple_text(scope, "scope"),
            resources=_tuple_text(resources, "resources"),
            acceptance_criteria=criteria,
            permission_profile=_text(permission_profile, "permission profile", max_chars=256),
            recovery_policy=_text(recovery_policy, "recovery policy", max_chars=2048),
            verification_plan=_tuple_text(verification_plan, "verification plan"),
            provider_provenance=dict(provider_provenance),
        )
        now = _now()
        state = WorkflowState(
            str(uuid4()), task_id, WorkflowPhase.ARCHITECTURE, packet, (), 0, now, now
        )
        self._save(state)
        return state

    def inspect(self, workflow_id: str) -> WorkflowState:
        if workflow_id in self._states:
            return self._states[workflow_id]
        path = self._path(workflow_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowError(f"workflow state is unavailable: {error}") from None
        state = self._decode(data)
        self._states[state.workflow_id] = state
        return state

    def advance(
        self, workflow_id: str, *, expected_phase: WorkflowPhase | None = None
    ) -> WorkflowState:
        state = self.inspect(workflow_id)
        self._expect_phase(state, expected_phase)
        try:
            next_phase = _NEXT_PHASE[state.phase]
        except KeyError:
            raise WorkflowError(f"workflow phase {state.phase.value} cannot advance") from None
        return self._replace(state, phase=next_phase)

    def record_exploration(
        self, workflow_id: str, *, source_id: str, status: str, summary: str
    ) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.EXPLORATION)
        evidence = WorkflowEvidence(
            WorkflowRole.EXPLORER, source_id, status, summary, recorded_at=_now()
        )
        return self._replace(
            state, phase=WorkflowPhase.EXECUTION, evidence=state.evidence + (evidence,)
        )

    def record_execution(
        self,
        workflow_id: str,
        *,
        source_id: str,
        status: str,
        summary: str,
        bridge_result: TaskBridgeResult | None = None,
    ) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.EXECUTION)
        if bridge_result is not None:
            if bridge_result.task.task_id != state.task_id:
                raise WorkflowError("execution bridge Task does not match workflow")
            if bridge_result.outcome not in {"committed", "pending", "failed", "recovery-required"}:
                raise WorkflowError("execution bridge outcome is invalid")
            status = bridge_result.outcome
        evidence = WorkflowEvidence(
            WorkflowRole.EXECUTOR, source_id, status, summary, recorded_at=_now()
        )
        return self._replace(
            state, phase=WorkflowPhase.REVIEW, evidence=state.evidence + (evidence,)
        )

    def record_review(
        self,
        workflow_id: str,
        *,
        source_id: str,
        verdict: WorkflowVerdict,
        summary: str,
    ) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.REVIEW)
        if type(verdict) is not WorkflowVerdict:
            raise WorkflowError("review verdict is invalid")
        evidence = WorkflowEvidence(
            WorkflowRole.REVIEWER, source_id, verdict.value, summary, recorded_at=_now()
        )
        target = {
            WorkflowVerdict.PASSED: WorkflowPhase.INTEGRATION,
            WorkflowVerdict.REJECTED: WorkflowPhase.NEEDS_REWORK,
            WorkflowVerdict.UNKNOWN: WorkflowPhase.RECOVERY_REQUIRED,
        }[verdict]
        return self._replace(state, phase=target, evidence=state.evidence + (evidence,))

    def accept(
        self, workflow_id: str, *, summary: str = "Host accepted reviewed workflow"
    ) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.INTEGRATION)
        evidence = WorkflowEvidence(
            WorkflowRole.INTEGRATOR, "host", "accepted", summary, recorded_at=_now()
        )
        return self._replace(
            state, phase=WorkflowPhase.COMPLETED, evidence=state.evidence + (evidence,)
        )

    def rework(self, workflow_id: str, *, summary: str) -> WorkflowState:
        state = self._phase(workflow_id, WorkflowPhase.NEEDS_REWORK)
        evidence = WorkflowEvidence(
            WorkflowRole.ARCHITECT, "host", "rework", summary, recorded_at=_now()
        )
        return self._replace(
            state, phase=WorkflowPhase.EXECUTION, evidence=state.evidence + (evidence,)
        )

    def block(self, workflow_id: str, *, summary: str) -> WorkflowState:
        state = self.inspect(workflow_id)
        if state.phase in _TERMINAL_PHASES:
            raise WorkflowError("terminal workflow cannot be blocked again")
        evidence = WorkflowEvidence(
            _PHASE_ROLE.get(state.phase, WorkflowRole.ARCHITECT),
            "host",
            "blocked",
            summary,
            recorded_at=_now(),
        )
        return self._replace(
            state, phase=WorkflowPhase.BLOCKED, evidence=state.evidence + (evidence,)
        )

    def _phase(self, workflow_id: str, expected: WorkflowPhase) -> WorkflowState:
        state = self.inspect(workflow_id)
        if state.phase is not expected:
            raise WorkflowError(
                f"workflow requires phase {expected.value}, got {state.phase.value}"
            )
        return state

    def _expect_phase(self, state: WorkflowState, expected: WorkflowPhase | None) -> None:
        if expected is not None and state.phase is not expected:
            raise WorkflowError(
                f"workflow phase changed: expected {expected.value}, got {state.phase.value}"
            )

    def _replace(self, state: WorkflowState, **changes: Any) -> WorkflowState:
        next_state = WorkflowState(
            workflow_id=changes.get("workflow_id", state.workflow_id),
            task_id=changes.get("task_id", state.task_id),
            phase=changes.get("phase", state.phase),
            packet=changes.get("packet", state.packet),
            evidence=changes.get("evidence", state.evidence),
            revision=state.revision + 1,
            created_at=state.created_at,
            updated_at=_now(),
        )
        self._save(next_state)
        return next_state

    def _path(self, workflow_id: str) -> Path:
        try:
            value = str(UUID(workflow_id))
        except (ValueError, AttributeError):
            raise WorkflowError("workflow ID is invalid") from None
        return self.root / f"{value}.json"

    def _save(self, state: WorkflowState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(state.workflow_id)
        payload = json.dumps(
            self._encode(state), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.root, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            temp = Path(handle.name)
        temp.replace(path)
        self._states[state.workflow_id] = state

    @staticmethod
    def _encode(state: WorkflowState) -> dict[str, object]:
        return {
            "workflow_id": state.workflow_id,
            "task_id": state.task_id,
            "phase": state.phase.value,
            "packet": {
                "objective": state.packet.objective,
                "scope": state.packet.scope,
                "resources": state.packet.resources,
                "acceptance_criteria": state.packet.acceptance_criteria,
                "permission_profile": state.packet.permission_profile,
                "recovery_policy": state.packet.recovery_policy,
                "verification_plan": state.packet.verification_plan,
                "provider_provenance": dict(state.packet.provider_provenance),
            },
            "evidence": [
                {
                    "role": item.role.value,
                    "source_id": item.source_id,
                    "status": item.status,
                    "summary": item.summary,
                    "untrusted": item.untrusted,
                    "evidence_sha256": item.evidence_sha256,
                    "recorded_at": item.recorded_at,
                }
                for item in state.evidence
            ],
            "revision": state.revision,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }

    @staticmethod
    def _decode(value: object) -> WorkflowState:
        if not isinstance(value, dict):
            raise WorkflowError("workflow state JSON is invalid")
        try:
            packet = value["packet"]
            assert isinstance(packet, dict)
            raw_evidence = value["evidence"]
            assert isinstance(raw_evidence, list)
            evidence = tuple(
                WorkflowEvidence(
                    WorkflowRole(item["role"]),
                    item["source_id"],
                    item["status"],
                    item["summary"],
                    item["untrusted"],
                    item.get("evidence_sha256"),
                    item.get("recorded_at", ""),
                )
                for item in raw_evidence
            )
            return WorkflowState(
                workflow_id=value["workflow_id"],
                task_id=value["task_id"],
                phase=WorkflowPhase(value["phase"]),
                packet=WorkflowPacket(
                    packet["objective"],
                    tuple(packet["scope"]),
                    tuple(packet["resources"]),
                    tuple(packet["acceptance_criteria"]),
                    packet["permission_profile"],
                    packet["recovery_policy"],
                    tuple(packet["verification_plan"]),
                    dict(packet["provider_provenance"]),
                ),
                evidence=evidence,
                revision=value["revision"],
                created_at=value["created_at"],
                updated_at=value["updated_at"],
            )
        except (AssertionError, KeyError, TypeError, ValueError, WorkflowError) as error:
            if isinstance(error, WorkflowError):
                raise
            raise WorkflowError("workflow state JSON is invalid") from None


__all__ = [
    "WorkflowError",
    "WorkflowRole",
    "WorkflowPhase",
    "WorkflowVerdict",
    "WorkflowPacket",
    "WorkflowEvidence",
    "WorkflowState",
    "WorkflowOrchestrator",
]
