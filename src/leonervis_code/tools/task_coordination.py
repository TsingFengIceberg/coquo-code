"""Model-visible schemas for bounded durable Task coordination proposals."""

from __future__ import annotations

from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.core.task_admission import TASK_PROPOSE_START_TOOL_NAME

TASK_PROPOSE_PLAN_TOOL_NAME = "task_propose_plan"
TASK_REPORT_REFLECTION_TOOL_NAME = "task_report_reflection"
TASK_REPORT_BLOCKER_TOOL_NAME = "task_report_blocker"
TASK_PROPOSE_COMPLETION_TOOL_NAME = "task_propose_completion"
TASK_ACCEPT_ADMISSION_TOOL_NAME = "task_accept_admission"
TASK_ACCEPT_PLAN_TOOL_NAME = "task_accept_plan"
TASK_CONFIRM_COMPLETION_TOOL_NAME = "task_confirm_completion"

TASK_CONTROL_TOOL_NAMES = (
    TASK_PROPOSE_PLAN_TOOL_NAME,
    TASK_REPORT_REFLECTION_TOOL_NAME,
    TASK_REPORT_BLOCKER_TOOL_NAME,
    TASK_PROPOSE_COMPLETION_TOOL_NAME,
    TASK_PROPOSE_START_TOOL_NAME,
    TASK_ACCEPT_ADMISSION_TOOL_NAME,
    TASK_ACCEPT_PLAN_TOOL_NAME,
    TASK_CONFIRM_COMPLETION_TOOL_NAME,
)

TASK_STAGE_CONTROL_TOOL_NAMES = TASK_CONTROL_TOOL_NAMES[:4]
TASK_LIFECYCLE_TOOL_NAMES = TASK_CONTROL_TOOL_NAMES[5:]


def task_propose_plan_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TASK_PROPOSE_PLAN_TOOL_NAME,
            "description": (
                "Propose a bounded ordered execution plan for the current durable Task planning "
                "Stage. This does not execute or accept the plan."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 4096},
                        "minItems": 1,
                        "maxItems": 32,
                    }
                },
                "required": ["steps"],
                "additionalProperties": False,
            },
        }
    )


def task_report_reflection_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TASK_REPORT_REFLECTION_TOOL_NAME,
            "description": (
                "Submit one bounded recommendation from the current durable Task reflection "
                "Stage. This is advice only and cannot execute, approve, verify, or complete."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "recommendation": {
                        "type": "string",
                        "enum": [
                            "continue",
                            "correction",
                            "revise-plan",
                            "needs-human",
                            "fail",
                        ],
                    },
                    "summary": {"type": "string", "minLength": 1, "maxLength": 1024},
                    "next_objective": {
                        "anyOf": [
                            {"type": "string", "minLength": 1, "maxLength": 4096},
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["recommendation", "summary", "next_objective"],
                "additionalProperties": False,
            },
        }
    )


def task_report_blocker_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TASK_REPORT_BLOCKER_TOOL_NAME,
            "description": (
                "Report that the current durable Task Stage cannot safely continue because it "
                "needs information, permission, human evidence, or an external condition. This "
                "does not grant permission or terminate the Task."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "information",
                            "permission",
                            "human-evidence",
                            "external-condition",
                            "other",
                        ],
                    },
                    "summary": {"type": "string", "minLength": 1, "maxLength": 1024},
                },
                "required": ["category", "summary"],
                "additionalProperties": False,
            },
        }
    )


def task_propose_completion_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TASK_PROPOSE_COMPLETION_TOOL_NAME,
            "description": (
                "Propose that the current durable Task appears complete after the current "
                "execution or correction Stage. The Host still requires its acceptance policy "
                "and evidence before completing the Task."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }
    )


def task_propose_start_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TASK_PROPOSE_START_TOOL_NAME,
            "description": (
                "Propose starting a durable Task when the current ordinary user request needs "
                "multiple bounded Stages. This only creates a pending admission proposal; it "
                "does not create a Task, grant permission, approve actions, or start execution."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 1024},
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 1024},
                        "minItems": 1,
                        "maxItems": 16,
                    },
                },
                "required": ["objective", "reason", "acceptance_criteria"],
                "additionalProperties": False,
            },
        }
    )


def task_accept_admission_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TASK_ACCEPT_ADMISSION_TOOL_NAME,
            "description": (
                "Accept the exact pending durable Task admission identified by admission_id only "
                "when the current user explicitly approves it. The Host revalidates current-Session "
                "pending state, applies the default reviewed configuration after this Turn commits, "
                "and hands the accepted Task to the foreground Driver."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "admission_id": {"type": "string", "minLength": 71, "maxLength": 71}
                },
                "required": ["admission_id"],
                "additionalProperties": False,
            },
        }
    )


def task_accept_plan_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TASK_ACCEPT_PLAN_TOOL_NAME,
            "description": (
                "Accept the latest unaccepted plan for task_id only when the current user "
                "explicitly approves that plan. The Host revalidates exact current Task state "
                "after this Turn commits and then resumes the bounded foreground Driver."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"task_id": {"type": "string", "minLength": 36, "maxLength": 36}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
        }
    )


def task_confirm_completion_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TASK_CONFIRM_COMPLETION_TOOL_NAME,
            "description": (
                "Record direct user confirmation for all unresolved human criteria of task_id "
                "only when the current user explicitly confirms completion. The Host requires a "
                "current completion proposal and all non-human criteria already verified."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"task_id": {"type": "string", "minLength": 36, "maxLength": 36}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
        }
    )


def task_control_tool_snapshots() -> tuple[CanonicalToolDefinition, ...]:
    return (
        task_propose_plan_tool_snapshot(),
        task_report_reflection_tool_snapshot(),
        task_report_blocker_tool_snapshot(),
        task_propose_completion_tool_snapshot(),
        task_propose_start_tool_snapshot(),
        task_accept_admission_tool_snapshot(),
        task_accept_plan_tool_snapshot(),
        task_confirm_completion_tool_snapshot(),
    )
