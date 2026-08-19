"""Closed identity and informed approval contract for model Child delegation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from coquo.core.permissions import ApprovalMode

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CONTEXT_ID = re.compile(r"ctx-v[1-9][0-9]*-[0-9a-f]{64}\Z")
MAX_DELEGATION_DEPTH = 2
READ_ONLY_RECURSIVE_CAPABILITY = "read-only-explorer-v1"


def _digest(label: bytes, payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(label + b"\0" + encoded).hexdigest()


@dataclass(frozen=True)
class DelegationApprovalIdentity:
    parent_session_id: str
    context_id: str
    tool_use_id: str
    objective_sha256: str
    route_fingerprint: str
    child_tool_set_id: str
    max_provider_invocations: int
    max_tool_requests: int
    max_output_tokens: int
    deadline_seconds: int
    depth: int
    approval_mode: ApprovalMode
    parent_child_run_id: str | None = None
    root_child_run_id: str | None = None
    capability: str = READ_ONLY_RECURSIVE_CAPABILITY

    def __post_init__(self) -> None:
        from coquo.session_records import canonical_session_id

        canonical_session_id(self.parent_session_id)
        if _CONTEXT_ID.fullmatch(self.context_id) is None:
            raise ValueError("delegation context identity is invalid")
        if not isinstance(self.tool_use_id, str) or not self.tool_use_id:
            raise ValueError("delegation ToolUse ID is invalid")
        if _SHA256.fullmatch(self.objective_sha256) is None:
            raise ValueError("delegation objective digest is invalid")
        if not isinstance(self.route_fingerprint, str) or not self.route_fingerprint:
            raise ValueError("delegation route fingerprint is invalid")
        if not isinstance(self.child_tool_set_id, str) or not self.child_tool_set_id:
            raise ValueError("delegation Child ToolSet ID is invalid")
        limits = (
            self.max_provider_invocations,
            self.max_tool_requests,
            self.max_output_tokens,
            self.deadline_seconds,
        )
        if any(type(value) is not int or value < 1 for value in limits):
            raise ValueError("delegation budgets are invalid")
        if type(self.depth) is not int or not 1 <= self.depth <= MAX_DELEGATION_DEPTH:
            raise ValueError("delegation depth must be between one and two")
        if self.depth == 1 and self.parent_child_run_id is not None:
            raise ValueError("root delegation cannot have a parent Child Run")
        if self.depth == 2 and self.parent_child_run_id is None:
            raise ValueError("grandchild delegation requires a parent Child Run")
        for value, label in (
            (self.parent_child_run_id, "parent Child Run ID"),
            (self.root_child_run_id, "root Child Run ID"),
        ):
            if value is not None:
                canonical_session_id(value)
        if self.depth == 1 and self.root_child_run_id is not None:
            raise ValueError("root delegation cannot carry a root Child Run ID")
        if self.depth == 2 and self.root_child_run_id is None:
            raise ValueError("grandchild delegation requires a root Child Run ID")
        if self.capability != READ_ONLY_RECURSIVE_CAPABILITY:
            raise ValueError("unsupported delegation capability")
        if type(self.approval_mode) is not ApprovalMode:
            raise ValueError("delegation approval mode is invalid")

    @property
    def digest(self) -> str:
        return _digest(
            b"coquo-delegation-approval-identity-v2",
            {
                "approval_mode": self.approval_mode.value,
                "child_tool_set_id": self.child_tool_set_id,
                "context_id": self.context_id,
                "deadline_seconds": self.deadline_seconds,
                "depth": self.depth,
                "max_output_tokens": self.max_output_tokens,
                "max_provider_invocations": self.max_provider_invocations,
                "max_tool_requests": self.max_tool_requests,
                "objective_sha256": self.objective_sha256,
                "parent_session_id": self.parent_session_id,
                "route_fingerprint": self.route_fingerprint,
                "tool_use_id": self.tool_use_id,
                "parent_child_run_id": self.parent_child_run_id,
                "root_child_run_id": self.root_child_run_id,
                "capability": self.capability,
            },
        )


@dataclass(frozen=True)
class DelegationApprovalPreview:
    objective: str
    provider_id: str
    profile_name: str | None
    model: str | None
    tool_names: tuple[str, ...]
    max_provider_invocations: int
    max_tool_requests: int
    max_output_tokens: int
    deadline_seconds: int
    spawn_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("delegation preview objective is invalid")
        if len(self.objective) > 4096 or len(self.objective.encode("utf-8")) > 16384:
            raise ValueError("delegation preview objective exceeds its bound")
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("delegation preview Provider is invalid")
        if self.profile_name is not None and not isinstance(self.profile_name, str):
            raise ValueError("delegation preview profile is invalid")
        if self.model is not None and not isinstance(self.model, str):
            raise ValueError("delegation preview model is invalid")
        if not self.tool_names or len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("delegation preview tools are invalid")
        if any(not isinstance(name, str) or not name for name in self.tool_names):
            raise ValueError("delegation preview tools are invalid")
        if type(self.spawn_number) is not int or self.spawn_number < 1:
            raise ValueError("delegation preview spawn number is invalid")


@dataclass(frozen=True)
class DelegationApprovalRequest:
    identity: DelegationApprovalIdentity
    preview: DelegationApprovalPreview

    def __post_init__(self) -> None:
        if type(self.identity) is not DelegationApprovalIdentity:
            raise ValueError("delegation approval identity is invalid")
        if type(self.preview) is not DelegationApprovalPreview:
            raise ValueError("delegation approval preview is invalid")
        if hashlib.sha256(self.preview.objective.encode("utf-8")).hexdigest() != (
            self.identity.objective_sha256
        ):
            raise ValueError("delegation approval objective does not match identity")


def delegation_decision_sha256(identity: DelegationApprovalIdentity, outcome: str) -> str:
    if outcome not in {"accepted", "rejected", "cancelled"}:
        raise ValueError("delegation approval outcome is invalid")
    return _digest(
        b"coquo-delegation-decision-v1",
        {"delegation_identity_sha256": identity.digest, "outcome": outcome},
    )
