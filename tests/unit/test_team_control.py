from __future__ import annotations

import pytest

from coquo.agent.team_control import TeamControlState
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.team_control import (
    TEAM_CONTROL_TOOL_NAMES,
    parse_team_control,
    team_control_tool_snapshots,
)


TEAM_ID = "12345678-1234-4234-9234-123456789abc"
MEMBER_ID = "22345678-1234-4234-9234-123456789abc"
MESSAGE_ID = "32345678-1234-4234-9234-123456789abc"
WORK_ID = "42345678-1234-4234-9234-123456789abc"
RUN_ID = "52345678-1234-4234-9234-123456789abc"


def request(name: str, arguments: dict[str, object]) -> ToolUse:
    return ToolUse(f"{name}-tool", name, ToolArguments.from_mapping(arguments))


def test_team_definitions_are_closed_and_stable() -> None:
    definitions = team_control_tool_snapshots()
    assert tuple(item.name for item in definitions) == TEAM_CONTROL_TOOL_NAMES
    assert len(definitions) == 11
    for definition in definitions:
        schema = definition.as_mapping()["input_schema"]
        assert schema["additionalProperties"] is False


def test_team_control_parser_canonicalizes_all_control_shapes() -> None:
    assert parse_team_control(request("team_create", {"name": " Team A "})).team_name == " Team A "
    assert (
        parse_team_control(
            request("team_add_member", {"team_id": TEAM_ID, "name": "Member"})
        ).role_contract
        == "read-only-investigator-v1"
    )
    assert (
        parse_team_control(
            request(
                "team_add_member",
                {
                    "team_id": TEAM_ID,
                    "name": "Coder",
                    "role": "isolated-coder-v1",
                },
            )
        ).role_contract
        == "isolated-coder-v1"
    )
    assert (
        parse_team_control(
            request(
                "team_message_send",
                {"team_id": TEAM_ID, "member_id": MEMBER_ID, "body": "hello\nworld"},
            )
        ).body
        == "hello\nworld"
    )
    assert parse_team_control(
        request(
            "team_work_create",
            {
                "team_id": TEAM_ID,
                "title": "Task",
                "objective": "Inspect",
                "dependency_ids": [WORK_ID],
            },
        )
    ).dependency_ids == (WORK_ID,)
    assert (
        parse_team_control(
            request(
                "team_work_review",
                {
                    "team_id": TEAM_ID,
                    "work_item_id": WORK_ID,
                    "decision": "complete",
                    "note": "verified",
                    "message_id": MESSAGE_ID,
                },
            )
        ).message_id
        == MESSAGE_ID
    )


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("team_status", {"team_id": TEAM_ID, "extra": 1}),
        (
            "team_add_member",
            {"team_id": TEAM_ID, "name": "Coder", "role": "unknown-role"},
        ),
        ("team_message_show", {"team_id": "not-a-uuid", "message_id": MESSAGE_ID}),
        (
            "team_work_create",
            {
                "team_id": TEAM_ID,
                "title": "x",
                "objective": "y",
                "dependency_ids": [WORK_ID, WORK_ID],
            },
        ),
        (
            "team_schedule_wait",
            {"team_id": TEAM_ID, "schedule_run_id": RUN_ID, "timeout_seconds": 31},
        ),
        (
            "team_work_review",
            {
                "team_id": TEAM_ID,
                "work_item_id": WORK_ID,
                "decision": "release",
                "note": "done",
                "message_id": MESSAGE_ID,
            },
        ),
    ],
)
def test_team_control_parser_rejects_malformed_arguments(
    name: str, arguments: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        parse_team_control(request(name, arguments))


def test_team_control_budgets_are_reserved_and_reset() -> None:
    state = TeamControlState()
    for _ in range(4):
        state.reserve_member_add()
    state.reserve_create()
    state.reserve_schedule_start()
    state.reserve_mutation()
    state.reserve_mutation()
    with pytest.raises(ValueError, match="8"):
        state.reserve_mutation()
    state.reserve_wait(30)
    state.reserve_wait(30)
    with pytest.raises(ValueError, match="60"):
        state.reserve_wait(1)
    state.clear()
    assert state.mutation_count == 0
    assert state.requested_wait_seconds == 0
    assert state.pending_approval_identity is None
