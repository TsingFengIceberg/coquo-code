"""Closed, parent-only request contracts for Team coordination controls.

The definitions are intentionally isolated from the active Tool Registry until the
atomic B8 contract migration.  Host code can still validate forged ToolUse values
against the same canonical schemas while the model-visible surface remains unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import unicodedata

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition

TEAM_MEMBER_ROLE_CONTRACT = "read-only-investigator-v1"
TEAM_MEMBER_ROLE_CONTRACTS = (
    TEAM_MEMBER_ROLE_CONTRACT,
    "isolated-workspace-writer-v1",
    "isolated-coder-v1",
)

# Keep the parser import-light: team_records itself depends on Session/provider
# replay types, while the active catalog is imported during that replay assembly.
MAX_TEAM_SCHEDULE_ASSIGNMENTS = 32
MAX_TEAM_SCHEDULE_PARALLEL = 4
MAX_TEAM_OWNER_MESSAGE_CHARACTERS = 4096
MAX_TEAM_OWNER_MESSAGE_BYTES = 8 * 1024
MAX_TEAM_WORK_DEPENDENCIES = 16

TEAM_CREATE_TOOL_NAME = "team_create"
TEAM_ADD_MEMBER_TOOL_NAME = "team_add_member"
TEAM_STATUS_TOOL_NAME = "team_status"
TEAM_MESSAGE_SEND_TOOL_NAME = "team_message_send"
TEAM_MESSAGE_SHOW_TOOL_NAME = "team_message_show"
TEAM_MESSAGE_READ_TOOL_NAME = "team_message_read"
TEAM_WORK_CREATE_TOOL_NAME = "team_work_create"
TEAM_SCHEDULE_START_TOOL_NAME = "team_schedule_start"
TEAM_SCHEDULE_WAIT_TOOL_NAME = "team_schedule_wait"
TEAM_WORK_REVIEW_TOOL_NAME = "team_work_review"
TEAM_CLOSE_TOOL_NAME = "team_close"

TEAM_CONTROL_TOOL_NAMES = (
    TEAM_CREATE_TOOL_NAME,
    TEAM_ADD_MEMBER_TOOL_NAME,
    TEAM_STATUS_TOOL_NAME,
    TEAM_MESSAGE_SEND_TOOL_NAME,
    TEAM_MESSAGE_SHOW_TOOL_NAME,
    TEAM_MESSAGE_READ_TOOL_NAME,
    TEAM_WORK_CREATE_TOOL_NAME,
    TEAM_SCHEDULE_START_TOOL_NAME,
    TEAM_SCHEDULE_WAIT_TOOL_NAME,
    TEAM_WORK_REVIEW_TOOL_NAME,
    TEAM_CLOSE_TOOL_NAME,
)

MAX_TEAM_MUTATIONS_PER_TURN = 8
MAX_TEAM_CREATES_PER_TURN = 1
MAX_TEAM_MEMBER_ADDS_PER_TURN = 4
MAX_TEAM_SCHEDULE_STARTS_PER_TURN = 1
MAX_TEAM_MESSAGE_SHOWS_PER_TURN = 4
MAX_TEAM_WAIT_SECONDS_PER_REQUEST = 30
MAX_TEAM_WAIT_SECONDS_PER_TURN = 60
MAX_TEAM_RESULT_BYTES = 40 * 1024


@dataclass(frozen=True)
class TeamControlRequest:
    """Canonical parsed arguments for one Team control ToolUse."""

    name: str
    team_id: str | None = None
    member_id: str | None = None
    message_id: str | None = None
    work_item_id: str | None = None
    schedule_run_id: str | None = None
    name_value: str | None = None
    role_contract: str = TEAM_MEMBER_ROLE_CONTRACT
    body: str | None = None
    title: str | None = None
    objective: str | None = None
    dependency_ids: tuple[str, ...] = ()
    max_assignments: int | None = None
    max_parallel: int | None = None
    timeout_seconds: int | None = None
    decision: str | None = None
    note: str | None = None

    @property
    def team_name(self) -> str | None:
        return self.name_value if self.name == TEAM_CREATE_TOOL_NAME else None


def team_control_tool_snapshots() -> tuple[CanonicalToolDefinition, ...]:
    """Return the fixed Team definition order without registering the tools."""

    uuid = {"type": "string", "minLength": 36, "maxLength": 36}
    return (
        _definition(
            TEAM_CREATE_TOOL_NAME,
            "Create one parent-owned bounded read-only Team.",
            {"name": {"type": "string", "minLength": 1, "maxLength": 80}},
            ("name",),
        ),
        _definition(
            TEAM_ADD_MEMBER_TOOL_NAME,
            "Add one fixed-role member to an owned Team; writable roles receive an isolated linked worktree.",
            {
                "team_id": uuid,
                "name": {"type": "string", "minLength": 1, "maxLength": 80},
                "role": {"type": "string", "enum": list(TEAM_MEMBER_ROLE_CONTRACTS)},
            },
            ("team_id", "name"),
        ),
        _definition(
            TEAM_STATUS_TOOL_NAME,
            "Inspect bounded durable state for one owned Team.",
            {"team_id": uuid},
            ("team_id",),
        ),
        _definition(
            TEAM_MESSAGE_SEND_TOOL_NAME,
            "Send one bounded owner message to a fixed read-only Team member.",
            {
                "team_id": uuid,
                "member_id": uuid,
                "body": {"type": "string", "minLength": 1, "maxLength": 4096},
            },
            ("team_id", "member_id", "body"),
        ),
        _definition(
            TEAM_MESSAGE_SHOW_TOOL_NAME,
            "Show one exact bounded Team reply body with delivery provenance.",
            {"team_id": uuid, "message_id": uuid},
            ("team_id", "message_id"),
        ),
        _definition(
            TEAM_MESSAGE_READ_TOOL_NAME,
            "Mark one previously delivered Team reply as read.",
            {"team_id": uuid, "message_id": uuid},
            ("team_id", "message_id"),
        ),
        _definition(
            TEAM_WORK_CREATE_TOOL_NAME,
            "Create one bounded Team work item with backward dependencies.",
            {
                "team_id": uuid,
                "title": {"type": "string", "minLength": 1, "maxLength": 80},
                "objective": {"type": "string", "minLength": 1, "maxLength": 4096},
                "dependency_ids": {
                    "type": "array",
                    "maxItems": MAX_TEAM_WORK_DEPENDENCIES,
                    "items": uuid,
                },
            },
            ("team_id", "title", "objective", "dependency_ids"),
        ),
        _definition(
            TEAM_SCHEDULE_START_TOOL_NAME,
            "Start one bounded process-local Team schedule wave.",
            {
                "team_id": uuid,
                "max_assignments": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TEAM_SCHEDULE_ASSIGNMENTS,
                },
                "max_parallel": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TEAM_SCHEDULE_PARALLEL,
                },
            },
            ("team_id", "max_assignments", "max_parallel"),
        ),
        _definition(
            TEAM_SCHEDULE_WAIT_TOOL_NAME,
            "Wait for one owned Team schedule for a bounded interval.",
            {
                "team_id": uuid,
                "schedule_run_id": uuid,
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_TEAM_WAIT_SECONDS_PER_REQUEST,
                },
            },
            ("team_id", "schedule_run_id", "timeout_seconds"),
        ),
        _definition(
            TEAM_WORK_REVIEW_TOOL_NAME,
            "Explicitly complete, release, or cancel one reviewed Team work item.",
            {
                "team_id": uuid,
                "work_item_id": uuid,
                "decision": {"type": "string", "enum": ["complete", "release", "cancel"]},
                "note": {"type": "string", "minLength": 1, "maxLength": 4096},
                "message_id": uuid,
            },
            ("team_id", "work_item_id", "decision", "note"),
        ),
        _definition(
            TEAM_CLOSE_TOOL_NAME,
            "Close an owned Team only after every durable gate is settled.",
            {"team_id": uuid},
            ("team_id",),
        ),
    )


def parse_team_control(request: ToolUse) -> TeamControlRequest:
    """Parse one exact Team ToolUse, including conditional review arguments."""

    if not isinstance(request, ToolUse) or request.name not in TEAM_CONTROL_TOOL_NAMES:
        raise ValueError("Team control request is invalid")
    if not isinstance(request.arguments, ToolArguments):
        raise ValueError("Team control arguments are invalid")
    values = request.arguments.as_mapping()
    expected = {
        TEAM_CREATE_TOOL_NAME: {"name"},
        TEAM_ADD_MEMBER_TOOL_NAME: {"team_id", "name"},
        TEAM_STATUS_TOOL_NAME: {"team_id"},
        TEAM_MESSAGE_SEND_TOOL_NAME: {"team_id", "member_id", "body"},
        TEAM_MESSAGE_SHOW_TOOL_NAME: {"team_id", "message_id"},
        TEAM_MESSAGE_READ_TOOL_NAME: {"team_id", "message_id"},
        TEAM_WORK_CREATE_TOOL_NAME: {"team_id", "title", "objective", "dependency_ids"},
        TEAM_SCHEDULE_START_TOOL_NAME: {"team_id", "max_assignments", "max_parallel"},
        TEAM_SCHEDULE_WAIT_TOOL_NAME: {"team_id", "schedule_run_id", "timeout_seconds"},
        TEAM_WORK_REVIEW_TOOL_NAME: {"team_id", "work_item_id", "decision", "note"},
        TEAM_CLOSE_TOOL_NAME: {"team_id"},
    }[request.name]
    if request.name == TEAM_ADD_MEMBER_TOOL_NAME and "role" in values:
        expected = expected | {"role"}
    if request.name == TEAM_WORK_REVIEW_TOOL_NAME:
        decision = values.get("decision")
        if decision == "complete":
            expected = expected | {"message_id"}
        elif decision in {"release", "cancel"} and "message_id" in values:
            raise ValueError("team_work_review message_id is only valid for complete")
    if set(values) != expected:
        raise ValueError(f"{request.name} input is malformed")

    if request.name == TEAM_CREATE_TOOL_NAME:
        return TeamControlRequest(request.name, name_value=_team_name(values["name"]))
    team_id = _id(values["team_id"], "Team ID")
    if request.name == TEAM_ADD_MEMBER_TOOL_NAME:
        role = values.get("role", TEAM_MEMBER_ROLE_CONTRACT)
        if not isinstance(role, str) or role not in TEAM_MEMBER_ROLE_CONTRACTS:
            raise ValueError("team_add_member role is invalid")
        return TeamControlRequest(
            request.name,
            team_id=team_id,
            name_value=_team_name(values["name"]),
            role_contract=role,
        )
    if request.name == TEAM_STATUS_TOOL_NAME or request.name == TEAM_CLOSE_TOOL_NAME:
        return TeamControlRequest(request.name, team_id=team_id)
    if request.name in {TEAM_MESSAGE_SHOW_TOOL_NAME, TEAM_MESSAGE_READ_TOOL_NAME}:
        return TeamControlRequest(
            request.name, team_id=team_id, message_id=_id(values["message_id"], "message ID")
        )
    if request.name == TEAM_MESSAGE_SEND_TOOL_NAME:
        return TeamControlRequest(
            request.name,
            team_id=team_id,
            member_id=_id(values["member_id"], "member ID"),
            body=_owner_body(values["body"]),
        )
    if request.name == TEAM_WORK_CREATE_TOOL_NAME:
        dependencies = values["dependency_ids"]
        if not isinstance(dependencies, list) or len(dependencies) > MAX_TEAM_WORK_DEPENDENCIES:
            raise ValueError("team_work_create dependency_ids are invalid")
        parsed_dependencies = tuple(_id(value, "dependency ID") for value in dependencies)
        if len(set(parsed_dependencies)) != len(parsed_dependencies):
            raise ValueError("team_work_create dependency_ids must be unique")
        return TeamControlRequest(
            request.name,
            team_id=team_id,
            title=_team_name(values["title"]),
            objective=_team_objective(values["objective"]),
            dependency_ids=parsed_dependencies,
        )
    if request.name == TEAM_SCHEDULE_START_TOOL_NAME:
        return TeamControlRequest(
            request.name,
            team_id=team_id,
            max_assignments=_bounded_int(
                values["max_assignments"], 1, MAX_TEAM_SCHEDULE_ASSIGNMENTS, "max_assignments"
            ),
            max_parallel=_bounded_int(
                values["max_parallel"], 1, MAX_TEAM_SCHEDULE_PARALLEL, "max_parallel"
            ),
        )
    if request.name == TEAM_SCHEDULE_WAIT_TOOL_NAME:
        return TeamControlRequest(
            request.name,
            team_id=team_id,
            schedule_run_id=_id(values["schedule_run_id"], "schedule run ID"),
            timeout_seconds=_bounded_int(
                values["timeout_seconds"], 0, MAX_TEAM_WAIT_SECONDS_PER_REQUEST, "timeout_seconds"
            ),
        )
    decision = values["decision"]
    if not isinstance(decision, str) or decision not in {"complete", "release", "cancel"}:
        raise ValueError("team_work_review decision is invalid")
    return TeamControlRequest(
        request.name,
        team_id=team_id,
        work_item_id=_id(values["work_item_id"], "work item ID"),
        decision=decision,
        note=_team_reason(values["note"]),
        message_id=_id(values["message_id"], "message ID") if "message_id" in values else None,
    )


def _id(value: object, label: str) -> str:
    from coquo.team_records import canonical_team_id

    try:
        return canonical_team_id(value)
    except Exception:
        raise ValueError(f"{label} must be a canonical UUID4") from None


def _bounded_int(value: object, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}")
    return value


def _team_name(value: object) -> str:
    from coquo.team_records import canonical_team_name

    try:
        return canonical_team_name(value)
    except Exception:
        raise ValueError("Team name is invalid") from None


def _team_objective(value: object) -> str:
    from coquo.team_records import canonical_team_assignment_objective

    try:
        return canonical_team_assignment_objective(value)
    except Exception:
        raise ValueError("Team objective is invalid") from None


def _team_reason(value: object) -> str:
    from coquo.team_records import canonical_team_reason

    try:
        return canonical_team_reason(value)
    except Exception:
        raise ValueError("Team note is invalid") from None


def _owner_body(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("team_message_send body must be nonblank text")
    if any(
        unicodedata.category(character).startswith("C") and character not in {"\n", "\t"}
        for character in value
    ):
        raise ValueError("team_message_send body must not contain control characters")
    if (
        len(value) > MAX_TEAM_OWNER_MESSAGE_CHARACTERS
        or len(value.encode("utf-8")) > MAX_TEAM_OWNER_MESSAGE_BYTES
    ):
        raise ValueError("team_message_send body exceeds its bound")
    return value


def _definition(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: tuple[str, ...],
) -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        }
    )
