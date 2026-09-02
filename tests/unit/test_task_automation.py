from __future__ import annotations

import pytest

from coquo.task_automation import (
    TaskAutomationController,
    TaskAutomationError,
    TaskAutomationPolicy,
)


class FakeDrive:
    def __init__(self):
        self.calls = []

    def drive_until_review(self, workflow_id, session, *, policy):
        self.calls.append((workflow_id, session, policy))
        from coquo.workflow_orchestration import (
            WorkflowDriveResult,
            WorkflowDriveStopReason,
        )

        return WorkflowDriveResult(
            state=object(),
            stop_reason=WorkflowDriveStopReason.REVIEW_READY,
            stages_started=(),
            elapsed_seconds=0.0,
        )


def test_task_automation_requires_explicit_enablement(tmp_path):
    controller = TaskAutomationController(FakeDrive())
    with pytest.raises(TaskAutomationError, match="disabled"):
        controller.drive("workflow", object(), policy=TaskAutomationPolicy())
