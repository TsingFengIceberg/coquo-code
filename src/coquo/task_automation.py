"""Explicitly enabled Task automation over the existing Workflow driver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coquo.workflow_orchestration import (
    WorkflowDrivePolicy,
    WorkflowDriveResult,
    WorkflowDriveStopReason,
    WorkflowOrchestrator,
)


class TaskAutomationError(RuntimeError):
    """Raised when an automation policy would cross a Host decision gate."""


@dataclass(frozen=True)
class TaskAutomationPolicy:
    enabled: bool = False
    max_stages: int = 2
    max_elapsed_seconds: float = 300.0
    auto_review_proposal: bool = False

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("task automation enabled flag is invalid")
        if type(self.max_stages) is not int or not 1 <= self.max_stages <= 4:
            raise ValueError("task automation stage limit is invalid")
        if not 0 < self.max_elapsed_seconds <= 3600:
            raise ValueError("task automation elapsed limit is invalid")
        if type(self.auto_review_proposal) is not bool:
            raise ValueError("task automation review flag is invalid")


@dataclass(frozen=True)
class TaskAutomationResult:
    workflow: WorkflowDriveResult
    gated: bool
    gate: str


class TaskAutomationController:
    """Drive only safe pre-review stages; all consequential decisions remain explicit."""

    def __init__(self, orchestrator: WorkflowOrchestrator) -> None:
        self.orchestrator = orchestrator

    def drive(
        self, workflow_id: str, session: Any, *, policy: TaskAutomationPolicy
    ) -> TaskAutomationResult:
        if not isinstance(policy, TaskAutomationPolicy):
            raise TaskAutomationError("task automation policy is invalid")
        if not policy.enabled:
            raise TaskAutomationError("task automation is disabled; explicit enablement required")
        result = self.orchestrator.drive_until_review(
            workflow_id,
            session,
            policy=WorkflowDrivePolicy(
                max_stages=policy.max_stages, max_elapsed_seconds=policy.max_elapsed_seconds
            ),
        )
        if result.stop_reason is WorkflowDriveStopReason.REVIEW_READY:
            return TaskAutomationResult(result, True, "review-required")
        if result.stop_reason in {
            WorkflowDriveStopReason.PENDING_STAGE,
            WorkflowDriveStopReason.RECOVERY_REQUIRED,
        }:
            return TaskAutomationResult(result, True, "external-stage-observation")
        return TaskAutomationResult(result, True, "host-intervention-required")


__all__ = [
    "TaskAutomationController",
    "TaskAutomationError",
    "TaskAutomationPolicy",
    "TaskAutomationResult",
]
