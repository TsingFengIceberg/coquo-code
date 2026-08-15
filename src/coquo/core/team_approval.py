"""Content-free approval identities for parent-owned Team controls."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from coquo.core.permissions import ApprovalMode
from coquo.tools.team_control import TeamControlRequest
from coquo.team_records import canonical_team_id

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CONTEXT_ID = re.compile(r"ctx-v[1-9][0-9]*-[0-9a-f]{64}\Z")
_TOOLSET_ID = re.compile(r"toolset-v[1-9][0-9]*-[0-9a-f]{64}\Z")


def _digest(label: bytes, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(label + b"\0" + encoded).hexdigest()


def canonical_team_arguments_sha256(request: TeamControlRequest) -> str:
    """Hash the parsed request, including nulls and ordered dependencies."""

    if type(request) is not TeamControlRequest:
        raise ValueError("Team control request is invalid")
    return _digest(b"coquo-team-control-arguments-v1", _request_mapping(request))


@dataclass(frozen=True)
class TeamControlApprovalIdentity:
    parent_session_id: str
    context_id: str
    tool_use_id: str
    control_name: str
    canonical_arguments_sha256: str
    target_or_preallocated_team_id: str
    approval_mode: ApprovalMode
    schedule_run_id: str | None = None
    route_fingerprint: str | None = None
    child_tool_set_id: str | None = None
    max_assignments: int | None = None
    max_parallel: int | None = None
    per_child_provider_invocations: int | None = None
    per_child_tool_requests: int | None = None
    per_child_output_tokens: int | None = None
    per_child_deadline_seconds: int | None = None

    def __post_init__(self) -> None:
        from coquo.session_records import canonical_session_id

        canonical_session_id(self.parent_session_id)
        if _CONTEXT_ID.fullmatch(self.context_id) is None:
            raise ValueError("Team approval context identity is invalid")
        if not isinstance(self.tool_use_id, str) or not self.tool_use_id:
            raise ValueError("Team approval ToolUse ID is invalid")
        if not isinstance(self.control_name, str) or not self.control_name:
            raise ValueError("Team approval control name is invalid")
        if _SHA256.fullmatch(self.canonical_arguments_sha256) is None:
            raise ValueError("Team approval argument digest is invalid")
        canonical_team_id(self.target_or_preallocated_team_id)
        if type(self.approval_mode) is not ApprovalMode:
            raise ValueError("Team approval mode is invalid")
        if self.schedule_run_id is not None:
            canonical_team_id(self.schedule_run_id)
        for value, label in (
            (self.route_fingerprint, "route fingerprint"),
            (self.child_tool_set_id, "Child ToolSet ID"),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"Team approval {label} is invalid")
        if (
            self.child_tool_set_id is not None
            and _TOOLSET_ID.fullmatch(self.child_tool_set_id) is None
        ):
            raise ValueError("Team approval Child ToolSet ID is invalid")
        limits = (
            (self.max_assignments, "max assignments"),
            (self.max_parallel, "max parallel"),
            (self.per_child_provider_invocations, "Child provider invocations"),
            (self.per_child_tool_requests, "Child tool requests"),
            (self.per_child_output_tokens, "Child output tokens"),
            (self.per_child_deadline_seconds, "Child deadline"),
        )
        for value, label in limits:
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"Team approval {label} is invalid")
        has_schedule = self.control_name == "team_schedule_start"
        schedule_fields = (
            self.route_fingerprint,
            self.child_tool_set_id,
            self.max_assignments,
            self.max_parallel,
            self.per_child_provider_invocations,
            self.per_child_tool_requests,
            self.per_child_output_tokens,
            self.per_child_deadline_seconds,
        )
        if has_schedule and any(value is None for value in schedule_fields):
            raise ValueError("Team schedule approval must bind route and Child budgets")
        if not has_schedule and any(value is not None for value in schedule_fields):
            raise ValueError("Non-schedule Team approval cannot contain schedule provenance")

    @property
    def digest(self) -> str:
        return _digest(
            b"coquo-team-control-approval-identity-v1",
            {
                "approval_mode": self.approval_mode.value,
                "canonical_arguments_sha256": self.canonical_arguments_sha256,
                "child_tool_set_id": self.child_tool_set_id,
                "control_name": self.control_name,
                "context_id": self.context_id,
                "max_assignments": self.max_assignments,
                "max_parallel": self.max_parallel,
                "parent_session_id": self.parent_session_id,
                "per_child_deadline_seconds": self.per_child_deadline_seconds,
                "per_child_output_tokens": self.per_child_output_tokens,
                "per_child_provider_invocations": self.per_child_provider_invocations,
                "per_child_tool_requests": self.per_child_tool_requests,
                "route_fingerprint": self.route_fingerprint,
                "schedule_run_id": self.schedule_run_id,
                "target_or_preallocated_team_id": self.target_or_preallocated_team_id,
                "tool_use_id": self.tool_use_id,
            },
        )


@dataclass(frozen=True)
class TeamControlApprovalPreview:
    """Bounded UI data for one exact Team decision."""

    control_name: str
    team_id: str
    summary: str
    provider_id: str | None = None
    model: str | None = None
    route_fingerprint: str | None = None
    child_tool_names: tuple[str, ...] = ()
    max_assignments: int | None = None
    max_parallel: int | None = None
    per_child_provider_invocations: int | None = None
    per_child_tool_requests: int | None = None
    per_child_output_tokens: int | None = None
    per_child_deadline_seconds: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.control_name, str) or not self.control_name:
            raise ValueError("Team approval preview control is invalid")
        canonical_team_id(self.team_id)
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("Team approval preview summary is invalid")
        if len(self.summary) > 4096 or len(self.summary.encode("utf-8")) > 16 * 1024:
            raise ValueError("Team approval preview summary exceeds its bound")
        if self.provider_id is not None and (
            not isinstance(self.provider_id, str) or not self.provider_id
        ):
            raise ValueError("Team approval preview Provider is invalid")
        if self.model is not None and not isinstance(self.model, str):
            raise ValueError("Team approval preview model is invalid")
        if len(set(self.child_tool_names)) != len(self.child_tool_names):
            raise ValueError("Team approval preview Child tools are duplicated")
        if any(not isinstance(name, str) or not name for name in self.child_tool_names):
            raise ValueError("Team approval preview Child tools are invalid")


@dataclass(frozen=True)
class TeamControlApprovalRequest:
    identity: TeamControlApprovalIdentity
    preview: TeamControlApprovalPreview

    def __post_init__(self) -> None:
        if type(self.identity) is not TeamControlApprovalIdentity:
            raise ValueError("Team approval identity is invalid")
        if type(self.preview) is not TeamControlApprovalPreview:
            raise ValueError("Team approval preview is invalid")
        if self.preview.team_id != self.identity.target_or_preallocated_team_id:
            raise ValueError("Team approval preview target does not match identity")
        if self.preview.control_name != self.identity.control_name:
            raise ValueError("Team approval preview control does not match identity")


def team_control_decision_sha256(identity: TeamControlApprovalIdentity, outcome: str) -> str:
    if type(identity) is not TeamControlApprovalIdentity:
        raise ValueError("Team approval identity is invalid")
    if outcome not in {"accepted", "rejected", "cancelled"}:
        raise ValueError("Team approval outcome is invalid")
    return _digest(
        b"coquo-team-control-decision-v1",
        {"team_control_identity_sha256": identity.digest, "outcome": outcome},
    )


def _request_mapping(request: TeamControlRequest) -> dict[str, object]:
    return {
        "body": request.body,
        "decision": request.decision,
        "dependency_ids": list(request.dependency_ids),
        "max_assignments": request.max_assignments,
        "max_parallel": request.max_parallel,
        "member_id": request.member_id,
        "message_id": request.message_id,
        "name": request.name,
        "name_value": request.name_value,
        "note": request.note,
        "objective": request.objective,
        "schedule_run_id": request.schedule_run_id,
        "team_id": request.team_id,
        "timeout_seconds": request.timeout_seconds,
        "title": request.title,
        "work_item_id": request.work_item_id,
    }
