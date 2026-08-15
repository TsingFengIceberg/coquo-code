from __future__ import annotations

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.extensions import ToolExecutionKind
from coquo.tools.catalog import (
    CHILD_CONTROL_TOOL_CATALOG,
    CHILD_CONTROL_TOOL_CONTRACTS,
    ORDINARY_PROMPT_TOOL_NAMES,
    TOOL_CATALOG,
    TOOL_REGISTRY_SNAPSHOT,
    model_tool_definitions,
)
from coquo.tools.team_control import TEAM_CONTROL_TOOL_NAMES
from coquo.tools.child_control import (
    CHILD_CANCEL_TOOL_NAME,
    CHILD_CONTROL_TOOL_NAMES,
    CHILD_SPAWN_TOOL_NAME,
    CHILD_STATUS_TOOL_NAME,
    CHILD_WAIT_TOOL_NAME,
    child_control_tool_snapshots,
    parse_child_control,
)


CHILD_ID = "42345678-1234-4234-9234-123456789abc"


def request(name: str, arguments: dict[str, object]) -> ToolUse:
    return ToolUse("tool-1", name, ToolArguments.from_mapping(arguments))


def test_child_control_definitions_are_closed_ordered_and_exposed_only_to_parent() -> None:
    assert tuple(item.name for item in CHILD_CONTROL_TOOL_CATALOG) == CHILD_CONTROL_TOOL_NAMES
    assert child_control_tool_snapshots() == CHILD_CONTROL_TOOL_CATALOG
    assert tuple(item.name for item in CHILD_CONTROL_TOOL_CONTRACTS) == CHILD_CONTROL_TOOL_NAMES
    assert all(
        item.execution_kind is ToolExecutionKind.CHILD_CONTROL and item.permission_actions == ()
        for item in CHILD_CONTROL_TOOL_CONTRACTS
    )
    assert ORDINARY_PROMPT_TOOL_NAMES[-15:-11] == CHILD_CONTROL_TOOL_NAMES
    assert tuple(item.name for item in TOOL_CATALOG[-15:-11]) == CHILD_CONTROL_TOOL_NAMES
    assert ORDINARY_PROMPT_TOOL_NAMES[-11:] == TEAM_CONTROL_TOOL_NAMES
    assert tuple(item.name for item in TOOL_CATALOG[-11:]) == TEAM_CONTROL_TOOL_NAMES
    assert TOOL_REGISTRY_SNAPSHOT.names[-15:-11] == CHILD_CONTROL_TOOL_NAMES
    assert (
        tuple(
            definition["name"]
            for definition in model_tool_definitions(ORDINARY_PROMPT_TOOL_NAMES)[-15:-11]
        )
        == CHILD_CONTROL_TOOL_NAMES
    )

    schemas = {item.name: item.as_mapping()["input_schema"] for item in CHILD_CONTROL_TOOL_CATALOG}
    assert tuple(schemas) == CHILD_CONTROL_TOOL_NAMES
    assert schemas[CHILD_SPAWN_TOOL_NAME]["required"] == ["objective"]
    assert schemas[CHILD_STATUS_TOOL_NAME]["required"] == ["child_run_id"]
    assert schemas[CHILD_WAIT_TOOL_NAME]["required"] == [
        "child_run_id",
        "timeout_seconds",
    ]
    assert schemas[CHILD_CANCEL_TOOL_NAME]["required"] == ["child_run_id", "reason"]
    assert all(schema["additionalProperties"] is False for schema in schemas.values())


def test_child_control_parser_accepts_exact_inputs() -> None:
    spawned = parse_child_control(request(CHILD_SPAWN_TOOL_NAME, {"objective": "inspect"}))
    assert spawned.objective == "inspect"
    status = parse_child_control(request(CHILD_STATUS_TOOL_NAME, {"child_run_id": CHILD_ID}))
    assert status.child_run_id == CHILD_ID
    waited = parse_child_control(
        request(CHILD_WAIT_TOOL_NAME, {"child_run_id": CHILD_ID, "timeout_seconds": 30})
    )
    assert waited.timeout_seconds == 30
    cancelled = parse_child_control(
        request(CHILD_CANCEL_TOOL_NAME, {"child_run_id": CHILD_ID, "reason": "stop"})
    )
    assert cancelled.reason == "stop"


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        (CHILD_SPAWN_TOOL_NAME, {}),
        (CHILD_SPAWN_TOOL_NAME, {"objective": "inspect", "extra": 1}),
        (CHILD_SPAWN_TOOL_NAME, {"objective": ""}),
        (CHILD_SPAWN_TOOL_NAME, {"objective": "\x00bad"}),
        (CHILD_SPAWN_TOOL_NAME, {"objective": "x" * 4097}),
        (CHILD_STATUS_TOOL_NAME, {"child_run_id": "not-a-uuid"}),
        (CHILD_WAIT_TOOL_NAME, {"child_run_id": CHILD_ID, "timeout_seconds": True}),
        (CHILD_WAIT_TOOL_NAME, {"child_run_id": CHILD_ID, "timeout_seconds": -1}),
        (CHILD_WAIT_TOOL_NAME, {"child_run_id": CHILD_ID, "timeout_seconds": 31}),
        (CHILD_CANCEL_TOOL_NAME, {"child_run_id": CHILD_ID, "reason": ""}),
        (CHILD_CANCEL_TOOL_NAME, {"child_run_id": CHILD_ID, "reason": "\x00bad"}),
        (CHILD_CANCEL_TOOL_NAME, {"child_run_id": CHILD_ID, "reason": "x" * 4097}),
    ],
)
def test_child_control_parser_rejects_malformed_inputs(
    name: str, arguments: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        parse_child_control(request(name, arguments))


def test_child_control_parser_rejects_unknown_tool() -> None:
    with pytest.raises(ValueError, match="request is invalid"):
        parse_child_control(request("read_file", {"path": "README.md"}))
