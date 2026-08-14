from __future__ import annotations

import io
from pathlib import Path

from coquo.cli.main import main
from coquo.cli.slash import dispatch_slash
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.team_store import TeamStore


class ReplTeamSession:
    def __init__(self, workspace: Path) -> None:
        writer = SessionStore(workspace).create(BindingSnapshot.fake())
        self.workspace = workspace
        self.session_id = writer.session_id
        self.transcript_before = writer.path.read_bytes()
        writer.release()
        self.store = TeamStore(workspace)

    def create_team(self, name: str):
        return self.store.create(name, owner_session=self.session_id)

    def list_teams(self, *, status=None):
        return self.store.list(status=status)

    def inspect_team(self, team_id: str):
        return self.store.inspect(team_id)

    def close_team(self, team_id: str):
        return self.store.close(team_id)

    def add_team_member(self, team_id: str, name: str):
        return self.store.add_member(team_id, name)

    def list_team_members(self, team_id: str):
        return self.store.inspect(team_id).members

    def inspect_team_member(self, team_id: str, member_id: str):
        return self.store.member(team_id, member_id)

    def disable_team_member(self, team_id: str, member_id: str, reason: str):
        return self.store.disable_member(team_id, member_id, reason)

    def enable_team_member(self, team_id: str, member_id: str):
        return self.store.enable_member(team_id, member_id)

    def leave_team_member(self, team_id: str, member_id: str, reason: str):
        return self.store.leave_member(team_id, member_id, reason)

    def create_team_assignment(self, team_id: str, member_id: str, objective: str):
        from coquo.team_service import TeamAssignmentService

        return TeamAssignmentService(self.workspace).create(team_id, member_id, objective)

    def list_team_assignments(self, team_id: str, *, limit: int = 100):
        from coquo.team_service import TeamAssignmentService

        return TeamAssignmentService(self.workspace).list(team_id, limit=limit)

    def inspect_team_assignment(self, team_id: str, assignment_id: str):
        from coquo.team_service import TeamAssignmentService

        return TeamAssignmentService(self.workspace).inspect(team_id, assignment_id)

    def recover_team_assignments(self, team_id: str, assignment_id=None, *, limit: int = 100):
        from coquo.team_service import TeamAssignmentService

        return TeamAssignmentService(self.workspace).recover(team_id, assignment_id, limit=limit)

    def send_team_message(self, team_id: str, member_id: str, body: str):
        from coquo.team_messaging import TeamMessagingService

        return TeamMessagingService(self.workspace).send_owner(team_id, member_id, body)

    def list_team_messages(self, team_id: str, *, limit: int = 100, member_id=None, status=None):
        from coquo.team_messaging import TeamMessagingService

        return TeamMessagingService(self.workspace).list(
            team_id, limit=limit, member_id=member_id, status=status
        )

    def cancel_team_message(self, team_id: str, message_id: str, reason: str):
        from coquo.team_messaging import TeamMessagingService

        return TeamMessagingService(self.workspace).cancel(team_id, message_id, reason)

    def create_team_work(self, team_id: str, title: str, objective: str, dependency_ids=()):
        from coquo.team_work import TeamWorkService

        return TeamWorkService(self.workspace).create(team_id, title, objective, dependency_ids)

    def list_team_work(self, team_id: str, *, limit: int = 100, status=None):
        from coquo.team_work import TeamWorkService

        return TeamWorkService(self.workspace).list(team_id, limit=limit, status=status)

    def inspect_team_work(self, team_id: str, work_item_id: str):
        from coquo.team_work import TeamWorkService

        return TeamWorkService(self.workspace).show(team_id, work_item_id)


def invoke(workspace: Path, arguments: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = main(
        arguments,
        cwd=workspace,
        stdout=stdout,
        stderr=stderr,
        environment={},
        user_profile_path=workspace / "user.json",
        project_profile_path=workspace / "project.json",
    )
    return status, stdout.getvalue(), stderr.getvalue()


def _team_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("Team ID:"):
            return line.split(":", 1)[1].strip()
        if " (" in line and ")" in line:
            return line.split("(", 1)[1].split(")", 1)[0]
    raise AssertionError(f"missing Team ID in {output!r}")


def _member_id(output: str) -> str:
    for line in output.splitlines():
        if " (" in line and ")" in line:
            return line.split("(", 1)[1].split(")", 1)[0]
    raise AssertionError(f"missing member ID in {output!r}")


def _assignment_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("Assignment ID:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"missing assignment ID in {output!r}")


def _work_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("Work Item ID:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"missing work item ID in {output!r}")


def test_standalone_team_member_lifecycle_is_host_only(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    before = writer.path.read_bytes()
    writer.release()

    status, output, errors = invoke(tmp_path, ["team", "create", "Alpha"])
    assert status == 0 and errors == ""
    team_id = _team_id(output)

    status, output, errors = invoke(tmp_path, ["team", "member", "add", team_id, "Worker"])
    assert status == 0 and errors == ""
    member_id = _member_id(output)
    assert "active" in output

    status, output, errors = invoke(
        tmp_path, ["team", "member", "disable", team_id, member_id, "pause"]
    )
    assert status == 0 and errors == "" and "disabled" in output
    status, output, errors = invoke(tmp_path, ["team", "member", "enable", team_id, member_id])
    assert status == 0 and errors == "" and "active" in output
    status, output, errors = invoke(
        tmp_path, ["team", "member", "leave", team_id, member_id, "done"]
    )
    assert status == 0 and errors == "" and "left" in output

    status, output, errors = invoke(tmp_path, ["team", "show", team_id])
    assert status == 0 and errors == "" and "left" in output
    assert SessionStore(tmp_path).inspect(session_id).path.read_bytes() == before


def test_slash_team_commands_are_host_only_and_replayable(tmp_path: Path) -> None:
    session = ReplTeamSession(tmp_path)
    created = dispatch_slash("/team create Alpha", session)
    assert created.handled and created.kind == "success"
    team_id = session.list_teams()[0].team_id
    assert "Members: 0" in dispatch_slash(f"/team show {team_id}", session).message

    added = dispatch_slash(f"/team member add {team_id} Worker", session)
    assert added.handled and added.kind == "success"
    member_id = session.list_team_members(team_id)[0].member_id
    assert "active" in dispatch_slash(f"/team member list {team_id}", session).message
    assert (
        "disabled"
        in dispatch_slash(f"/team member disable {team_id} {member_id} pause", session).message
    )
    assert "active" in dispatch_slash(f"/team member enable {team_id} {member_id}", session).message
    assert (
        "left" in dispatch_slash(f"/team member leave {team_id} {member_id} done", session).message
    )
    assert dispatch_slash("/team list status=closed", session).message == "No durable Teams found."
    assert "Team commands:" in dispatch_slash("/help team", session).message
    assert (
        SessionStore(tmp_path).inspect(session.session_id).path.read_bytes()
        == session.transcript_before
    )


def test_slash_team_rejects_invalid_usage_without_side_effect(tmp_path: Path) -> None:
    session = ReplTeamSession(tmp_path)
    result = dispatch_slash("/team member add", session)
    assert result.handled and result.kind == "warning"
    assert result.message == "Usage: /team member add <team-id> <name>"
    assert session.list_teams() == ()
    assert dispatch_slash("/team list status=unknown", session).message == (
        "Usage: /team list [1-100] [status=open|closed]"
    )


def test_standalone_and_slash_assignment_metadata_are_provider_free(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    before = writer.path.read_bytes()
    writer.release()
    status, output, errors = invoke(tmp_path, ["team", "create", "Alpha"])
    assert status == 0 and errors == ""
    team_id = _team_id(output)
    status, output, errors = invoke(tmp_path, ["team", "member", "add", team_id, "Worker"])
    assert status == 0 and errors == ""
    member_id = _member_id(output)
    status, output, errors = invoke(
        tmp_path, ["team", "assignment", "create", team_id, member_id, "Inspect files"]
    )
    assert status == 0 and errors == "" and "Phase: child_bound" in output
    assignment_id = _assignment_id(output)
    assert invoke(tmp_path, ["team", "assignment", "show", team_id, assignment_id])[0] == 0
    status, output, errors = invoke(tmp_path, ["team", "assignment", "list", team_id])
    assert status == 0 and errors == "" and assignment_id in output
    status, output, errors = invoke(tmp_path, ["team", "assignment", "recover", team_id])
    assert status == 0 and errors == "" and "child_bound" in output
    assert SessionStore(tmp_path).inspect(session_id).path.read_bytes() == before

    session = ReplTeamSession(tmp_path)
    slash = dispatch_slash(f"/team assignment show {team_id} {assignment_id}", session)
    assert slash.handled and "Phase: child_bound" in slash.message


def test_standalone_team_message_and_work_commands_are_durable(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    writer.release()
    status, output, errors = invoke(tmp_path, ["team", "create", "Board"])
    assert status == 0 and errors == ""
    team_id = _team_id(output)
    status, output, errors = invoke(tmp_path, ["team", "member", "add", team_id, "Worker"])
    assert status == 0 and errors == ""
    member_id = _member_id(output)

    status, output, errors = invoke(
        tmp_path, ["team", "message", "send", team_id, member_id, "Inspect config"]
    )
    assert status == 0 and errors == "" and "pending" in output
    message_id = output.split("Message ID:", 1)[1].splitlines()[0].strip()
    status, output, errors = invoke(
        tmp_path, ["team", "message", "cancel", team_id, message_id, "superseded"]
    )
    assert status == 0 and errors == "" and "cancelled" in output

    status, output, errors = invoke(
        tmp_path, ["team", "work", "create", team_id, "Inspect", "Inspect config"]
    )
    assert status == 0 and errors == ""
    work_id = _work_id(output)
    status, output, errors = invoke(tmp_path, ["team", "work", "list", team_id])
    assert status == 0 and errors == "" and work_id in output
    status, output, errors = invoke(
        tmp_path, ["team", "work", "assign", team_id, work_id, member_id]
    )
    assert status == 0 and errors == "" and "Assignment ID:" in output


def test_slash_team_message_and_work_commands_are_host_only(tmp_path: Path) -> None:
    session = ReplTeamSession(tmp_path)
    created = dispatch_slash("/team create Board", session)
    assert created.handled
    team_id = session.list_teams()[0].team_id
    added = dispatch_slash(f"/team member add {team_id} Worker", session)
    assert added.handled
    member_id = session.list_team_members(team_id)[0].member_id
    sent = dispatch_slash(f"/team message send {team_id} {member_id} Inspect", session)
    assert sent.handled and "pending" in sent.message
    assert "pending" in dispatch_slash(f"/team message list {team_id}", session).message
    message_id = session.list_team_messages(team_id).messages[0].message_id
    cancelled = dispatch_slash(f"/team message cancel {team_id} {message_id} superseded", session)
    assert cancelled.handled and "cancelled" in cancelled.message
    work = dispatch_slash(f"/team work create {team_id} Inspect Config", session)
    assert work.handled and "ready" in work.message
    work_id = session.list_team_work(team_id).items[0].work_item_id
    assert "ready" in dispatch_slash(f"/team work list {team_id}", session).message
    assert work_id in dispatch_slash(f"/team work show {team_id} {work_id}", session).message
    assert (
        session.transcript_before
        == SessionStore(tmp_path).inspect(session.session_id).path.read_bytes()
    )
