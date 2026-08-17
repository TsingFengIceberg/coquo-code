from uuid import uuid4

import pytest

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.catalog import (
    tool_input_from_use,
    tool_use_from_input,
    tool_use_from_provider_input,
)
from coquo.tools.team_worktree_integrate import (
    TEAM_WORKTREE_INTEGRATE_TOOL_NAME,
    parse_team_worktree_integrate,
    team_worktree_integrate_tool_snapshot,
)


def _request(values):
    return ToolUse(
        "tool-1",
        TEAM_WORKTREE_INTEGRATE_TOOL_NAME,
        ToolArguments.from_mapping(values),
    )


def test_team_worktree_integrate_schema_is_closed_and_parent_contract() -> None:
    definition = team_worktree_integrate_tool_snapshot().as_mapping()
    assert definition["input_schema"]["additionalProperties"] is False
    assert definition["input_schema"]["required"] == [
        "team_id",
        "assignment_id",
        "expected_patch_sha256",
    ]


def test_team_worktree_integrate_parser_accepts_exact_arguments() -> None:
    team_id = str(uuid4())
    assignment_id = str(uuid4())
    digest = "a" * 64
    request = parse_team_worktree_integrate(
        _request(
            {
                "team_id": team_id,
                "assignment_id": assignment_id,
                "expected_patch_sha256": digest,
            }
        )
    )
    assert request.team_id == team_id
    assert request.assignment_id == assignment_id
    assert request.expected_patch_sha256 == digest
    assert (
        tool_use_from_input(
            "tool-1",
            TEAM_WORKTREE_INTEGRATE_TOOL_NAME,
            {
                "team_id": team_id,
                "assignment_id": assignment_id,
                "expected_patch_sha256": digest,
            },
        ).name
        == TEAM_WORKTREE_INTEGRATE_TOOL_NAME
    )


@pytest.mark.parametrize(
    "values",
    [
        {
            "team_id": str(uuid4()),
            "assignment_id": str(uuid4()),
            "expected_patch_sha256": "a" * 64,
            "extra": 1,
        },
        {"team_id": "not-a-uuid", "assignment_id": str(uuid4()), "expected_patch_sha256": "a" * 64},
        {"team_id": str(uuid4()), "assignment_id": str(uuid4()), "expected_patch_sha256": "A" * 64},
    ],
)
def test_team_worktree_integrate_rejects_unknown_or_invalid_arguments(values) -> None:
    with pytest.raises(ValueError):
        parse_team_worktree_integrate(_request(values))
    # Provider decoding freezes ordinary-tool arguments first; strict Host-side
    # validation is intentionally deferred until the request is prepared.
    frozen = tool_use_from_provider_input("tool-1", TEAM_WORKTREE_INTEGRATE_TOOL_NAME, values)
    with pytest.raises(ValueError):
        tool_input_from_use(frozen)
