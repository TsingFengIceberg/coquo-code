from __future__ import annotations

import json

from coquo.core.action_coordinator import ApprovalResolution
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.permissions import ApprovalMode
from coquo.session import ProjectSession
from coquo.session_store import SessionStore
from coquo.tools.catalog import TOOL_REGISTRY_SNAPSHOT
from coquo.tools.team_control import TEAM_CONTROL_TOOL_NAMES


def control(name: str, values: dict[str, object], number: int) -> ToolUse:
    return ToolUse(f"team-control-{number}", name, ToolArguments.from_mapping(values))


def test_parent_installs_team_dispatcher_without_registry_exposure(tmp_path) -> None:
    with ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO) as session:
        assert set(TEAM_CONTROL_TOOL_NAMES).issubset(session._runtime.loop._team_control_names)
        assert set(TEAM_CONTROL_TOOL_NAMES).issubset(TOOL_REGISTRY_SNAPSHOT.names)


def test_rejected_team_create_is_audited_without_creating_team(tmp_path) -> None:
    with ProjectSession.open(
        tmp_path,
        environment={},
        approval_mode=ApprovalMode.ASK,
        approval_handler=lambda _request: ApprovalResolution.REJECT,
    ) as session:
        result = session._dispatch_team_control(
            control("team_create", {"name": "Rejected"}, 1),
            "ctx-v25-" + "a" * 64,
        )
        assert result.dispatch.tool_result.is_error
        assert session.list_teams() == ()
        decisions = SessionStore(tmp_path).team_control_decisions(session._writer.session_id)
        assert len(decisions) == 1
        assert decisions[0].outcome.value == "rejected"


def test_accepted_team_controls_are_durable_and_owner_bound(tmp_path) -> None:
    session = ProjectSession.open(tmp_path, environment={}, approval_mode=ApprovalMode.AUTO)
    try:
        context_id = "ctx-v25-" + "b" * 64
        created = session._dispatch_team_control(
            control("team_create", {"name": "Owned"}, 1), context_id
        )
        team_id = json.loads(created.dispatch.tool_result.content)["team_id"]
        member = session._dispatch_team_control(
            control("team_add_member", {"team_id": team_id, "name": "Reader"}, 2), context_id
        )
        member_id = json.loads(member.dispatch.tool_result.content)["member_id"]
        status = session._dispatch_team_control(
            control("team_status", {"team_id": team_id}, 3), context_id
        )
        payload = json.loads(status.dispatch.tool_result.content)
        assert payload["team_id"] == team_id
        assert payload["members"][0]["member_id"] == member_id
        assert len(SessionStore(tmp_path).team_control_decisions(session._writer.session_id)) == 2
    finally:
        session.close()
