"""Shared Host action boundary for declarative Skills and external plugins.

Skills and Plugins are extension data, not a second execution architecture.  This
module gives their runners one small adapter to the existing
``ActionCoordinator``.  The adapter creates a synthetic, exact ``ActionIdentity``
and delegates policy, approval, durable audit, revalidation, and execution to the
same Host-owned coordinator used by model ToolUse requests.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from coquo.core.action_coordinator import (
    ActionCoordinator,
    ActionCoordinatorResult,
    ActionExecutionResult,
    ApprovalResolution,
)
from coquo.core.actions import ActionIdentity, ActionLease, ActionPrecondition
from coquo.core.approval_preview import ApprovalPreview
from coquo.core.contracts import ToolArguments, ToolResult
from coquo.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from coquo.session_records import ActionExecutionOutcome, BindingSnapshot


ExtensionActionExecutor = Callable[[ActionIdentity], ActionExecutionResult]
ExtensionActionRevalidator = Callable[[ActionIdentity], ActionIdentity]
ExtensionActionUuidFactory = Callable[[], UUID | str]


@dataclass(frozen=True)
class ExtensionActionResult:
    """Stable result returned to an extension runner after Host coordination."""

    tool_result: ToolResult
    executed: bool
    result_code: str
    outcome: ActionExecutionOutcome | None = None
    approval_resolution: ApprovalResolution | None = None

    def __post_init__(self) -> None:
        if type(self.tool_result) is not ToolResult:
            raise ValueError("extension action tool result is invalid")
        if type(self.executed) is not bool:
            raise ValueError("extension action executed flag is invalid")
        if not isinstance(self.result_code, str) or not self.result_code:
            raise ValueError("extension action result code is invalid")
        if self.executed != (self.outcome is not None):
            raise ValueError("extension action outcome does not match execution flag")
        if self.outcome is not None and type(self.outcome) is not ActionExecutionOutcome:
            raise ValueError("extension action outcome is invalid")
        if (
            self.approval_resolution is not None
            and type(self.approval_resolution) is not ApprovalResolution
        ):
            raise ValueError("extension action approval resolution is invalid")


class CoordinatedExtensionActionInvoker:
    """Adapt one extension action to the existing Host ``ActionCoordinator``.

    The invoker owns no policy or execution.  Callers provide a prepared action
    executor, while this class supplies exact identity, permission mode,
    approval mode, revalidation, and coordinator routing.  A single invoker is
    normally scoped to one Host runtime/lease; callers must create a new one
    after a Session or runtime switch.
    """

    def __init__(
        self,
        *,
        coordinator: ActionCoordinator,
        binding: BindingSnapshot,
        permission_mode: PermissionMode,
        approval_mode: ApprovalMode,
        workspace_fingerprint: str,
        lease: ActionLease,
        revalidate: ExtensionActionRevalidator | None = None,
        uuid_factory: ExtensionActionUuidFactory = uuid4,
    ) -> None:
        if not isinstance(coordinator, ActionCoordinator):
            raise ValueError("extension action coordinator is invalid")
        if not isinstance(binding, BindingSnapshot):
            raise ValueError("extension action binding is invalid")
        if type(permission_mode) is not PermissionMode:
            raise ValueError("extension action permission mode is invalid")
        if type(approval_mode) is not ApprovalMode:
            raise ValueError("extension action approval mode is invalid")
        if not isinstance(workspace_fingerprint, str):
            raise ValueError("extension action workspace fingerprint is invalid")
        if not isinstance(lease, ActionLease):
            raise ValueError("extension action lease is invalid")
        if revalidate is not None and not callable(revalidate):
            raise ValueError("extension action revalidator is invalid")
        if not callable(uuid_factory):
            raise ValueError("extension action UUID factory is invalid")
        self.coordinator = coordinator
        self.binding = binding
        self.permission_mode = permission_mode
        self.approval_mode = approval_mode
        self.workspace_fingerprint = workspace_fingerprint
        self.lease = lease
        self.revalidate = revalidate or (lambda identity: identity)
        self.uuid_factory = uuid_factory

    def invoke(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        action: PermissionAction,
        execute: ExtensionActionExecutor,
        approval_required: bool = False,
        precondition: ActionPrecondition | None = None,
        approval_preview: ApprovalPreview | None = None,
    ) -> ExtensionActionResult:
        """Run one extension effect through policy, approval, audit, and execute."""
        if not isinstance(tool_name, str) or not tool_name or not tool_name.isascii():
            raise ValueError("extension action tool name is invalid")
        if not isinstance(arguments, Mapping):
            raise ValueError("extension action arguments are invalid")
        if type(action) is not PermissionAction:
            raise ValueError("extension action permission action is invalid")
        if not callable(execute):
            raise ValueError("extension action executor is invalid")
        if type(approval_required) is not bool:
            raise ValueError("extension action approval flag is invalid")
        if precondition is None:
            precondition = ActionPrecondition.none()
        if not isinstance(precondition, ActionPrecondition):
            raise ValueError("extension action precondition is invalid")

        request_id = _uuid4_text(self.uuid_factory(), "extension action request ID")
        tool_use_id = f"extension-{request_id}"
        identity = ActionIdentity(
            request_id=request_id,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            arguments=ToolArguments.from_mapping(dict(arguments)),
            action=action,
            workspace_fingerprint=self.workspace_fingerprint,
            lease=self.lease,
            precondition=precondition,
            execution_root_fingerprint=self.workspace_fingerprint,
            execution_scope="authority-workspace",
            version=2,
        )
        selected_approval = ApprovalMode.ASK if approval_required else self.approval_mode

        coordinated: ActionCoordinatorResult = self.coordinator.run(
            identity=identity,
            binding=self.binding,
            permission_mode=self.permission_mode,
            approval_mode=selected_approval,
            revalidate=self.revalidate,
            execute=execute,
            approval_preview=approval_preview,
        )
        return _project_result(coordinated)


def _project_result(result: ActionCoordinatorResult) -> ExtensionActionResult:
    if result.executed:
        assert result.execution_outcome is not None
        assert result.result_code is not None
        return ExtensionActionResult(
            result.tool_result,
            True,
            result.result_code,
            result.execution_outcome,
            result.approval_resolution,
        )
    if result.permission_result.decision.value == "deny":
        code = result.permission_result.reason.value
    elif result.approval_resolution is ApprovalResolution.REJECT:
        code = "approval_rejected"
    elif result.approval_resolution is ApprovalResolution.CANCEL:
        code = "approval_cancelled"
    else:
        code = "extension_action_not_executed"
    return ExtensionActionResult(
        result.tool_result,
        False,
        code,
        None,
        result.approval_resolution,
    )


def tool_result_execution(
    identity: ActionIdentity,
    result: object,
    *,
    success_code: str = "ok",
    failure_code: str = "tool_error",
    success_message: str = "extension tool action succeeded",
    failure_message: str = "extension tool action failed",
) -> ActionExecutionResult:
    """Normalize an existing ToolSet callback into coordinator metadata."""
    tool_result = result if isinstance(result, ToolResult) else None
    if tool_result is None:
        is_error = bool(getattr(result, "is_error", False))
        raw_code = getattr(result, "code", None)
        code = (
            str(raw_code)
            if isinstance(raw_code, str) and raw_code
            else (failure_code if is_error else success_code)
        )
        content = str(getattr(result, "content", code))
        tool_result = ToolResult(identity.tool_use_id, content, is_error=is_error)
    elif tool_result.tool_use_id != identity.tool_use_id:
        tool_result = ToolResult(
            identity.tool_use_id,
            tool_result.content,
            is_error=tool_result.is_error,
            truncated=tool_result.truncated,
        )
        code = failure_code if tool_result.is_error else success_code
    else:
        code = failure_code if tool_result.is_error else success_code
    failed = tool_result.is_error
    return ActionExecutionResult(
        tool_result=tool_result,
        outcome=ActionExecutionOutcome.FAILED if failed else ActionExecutionOutcome.SUCCEEDED,
        result_code=code,
        audit_message=failure_message if failed else success_message,
    )


def _uuid4_text(value: UUID | str, label: str) -> str:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{label} must be a canonical UUID4") from None
    if parsed.version != 4 or str(parsed) != str(value):
        raise ValueError(f"{label} must be a canonical UUID4")
    return str(parsed)


__all__ = [
    "CoordinatedExtensionActionInvoker",
    "ExtensionActionResult",
    "tool_result_execution",
]
