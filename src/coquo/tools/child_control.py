"""Closed schemas and argument parsing for bounded parent Child controls."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from uuid import UUID

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition

CHILD_SPAWN_TOOL_NAME = "child_spawn"
CHILD_STATUS_TOOL_NAME = "child_status"
CHILD_WAIT_TOOL_NAME = "child_wait"
CHILD_CANCEL_TOOL_NAME = "child_cancel"
CHILD_CONTROL_TOOL_NAMES = (
    CHILD_SPAWN_TOOL_NAME,
    CHILD_STATUS_TOOL_NAME,
    CHILD_WAIT_TOOL_NAME,
    CHILD_CANCEL_TOOL_NAME,
)
MAX_CHILD_SPAWNS_PER_TURN = 4
MAX_CHILD_WAIT_SECONDS_PER_REQUEST = 30
MAX_CHILD_WAIT_SECONDS_PER_TURN = 60


@dataclass(frozen=True)
class ChildControlRequest:
    name: str
    child_run_id: str | None = None
    objective: str | None = None
    timeout_seconds: int | None = None
    reason: str | None = None


def child_control_tool_snapshots() -> tuple[CanonicalToolDefinition, ...]:
    return (
        _definition(
            CHILD_SPAWN_TOOL_NAME,
            "Start one bounded read-only one-Turn Child for an independent objective.",
            {"objective": {"type": "string", "minLength": 1, "maxLength": 4096}},
            ("objective",),
        ),
        _definition(
            CHILD_STATUS_TOOL_NAME,
            "Inspect the durable state of one Child owned by the current parent Session.",
            {"child_run_id": {"type": "string", "minLength": 36, "maxLength": 36}},
            ("child_run_id",),
        ),
        _definition(
            CHILD_WAIT_TOOL_NAME,
            "Wait for one owned Child for a bounded number of seconds and return observed state.",
            {
                "child_run_id": {"type": "string", "minLength": 36, "maxLength": 36},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_CHILD_WAIT_SECONDS_PER_REQUEST,
                },
            },
            ("child_run_id", "timeout_seconds"),
        ),
        _definition(
            CHILD_CANCEL_TOOL_NAME,
            "Durably request cooperative cancellation of one owned Child.",
            {
                "child_run_id": {"type": "string", "minLength": 36, "maxLength": 36},
                "reason": {"type": "string", "minLength": 1, "maxLength": 4096},
            },
            ("child_run_id", "reason"),
        ),
    )


def parse_child_control(request: ToolUse) -> ChildControlRequest:
    if not isinstance(request, ToolUse) or request.name not in CHILD_CONTROL_TOOL_NAMES:
        raise ValueError("Child control request is invalid")
    if not isinstance(request.arguments, ToolArguments):
        raise ValueError("Child control arguments are invalid")
    values = request.arguments.as_mapping()
    expected = {
        CHILD_SPAWN_TOOL_NAME: {"objective"},
        CHILD_STATUS_TOOL_NAME: {"child_run_id"},
        CHILD_WAIT_TOOL_NAME: {"child_run_id", "timeout_seconds"},
        CHILD_CANCEL_TOOL_NAME: {"child_run_id", "reason"},
    }[request.name]
    if set(values) != expected:
        raise ValueError(f"{request.name} input is malformed")
    if request.name == CHILD_SPAWN_TOOL_NAME:
        objective = _bounded_text(values["objective"], "child_spawn objective")
        return ChildControlRequest(request.name, objective=objective)
    child_run_id = _canonical_uuid4(values["child_run_id"])
    if request.name == CHILD_WAIT_TOOL_NAME:
        timeout = values["timeout_seconds"]
        if type(timeout) is not int or not 0 <= timeout <= MAX_CHILD_WAIT_SECONDS_PER_REQUEST:
            raise ValueError("child_wait timeout_seconds must be an integer from 0 to 30")
        return ChildControlRequest(request.name, child_run_id, timeout_seconds=timeout)
    if request.name == CHILD_CANCEL_TOOL_NAME:
        reason = _bounded_text(values["reason"], "child_cancel reason")
        return ChildControlRequest(request.name, child_run_id, reason=reason)
    return ChildControlRequest(request.name, child_run_id)


def _canonical_uuid4(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Child Run ID must be text")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Child Run ID must be a canonical UUID4") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("Child Run ID must be a canonical UUID4")
    return value


def _bounded_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{label} must not contain control characters")
    if len(value) > 4096:
        raise ValueError(f"{label} exceeds 4096 characters")
    if len(value.encode("utf-8")) > 16 * 1024:
        raise ValueError(f"{label} exceeds 16384 UTF-8 bytes")
    return value


def _definition(
    name: str,
    description: str,
    properties: dict[str, object],
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
