from __future__ import annotations

import io
from pathlib import Path

from coquo.cli.main import main
from coquo.cli.slash import dispatch_slash
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore


class Session:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        writer = SessionStore(workspace).create(BindingSnapshot.fake())
        self.session_id = writer.session_id
        writer.release()
        self._session = None

    def create_child_run(self, objective):
        from coquo.child_run_store import ChildRunStore

        return ChildRunStore(self.workspace).create(objective, parent_session=self.session_id)

    def list_child_runs(self, *, status=None):
        from coquo.child_run_store import ChildRunStore

        return ChildRunStore(self.workspace).list(status=status)

    def inspect_child_run(self, child_run_id):
        from coquo.child_run_store import ChildRunStore

        return ChildRunStore(self.workspace).inspect(child_run_id)

    def cancel_child_run(self, child_run_id, reason):
        from coquo.child_run_store import ChildRunStore

        with ChildRunStore(self.workspace).open(child_run_id) as writer:
            writer.cancel(reason)
            return writer.info


def invoke(workspace: Path, arguments: list[str]):
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


def test_slash_child_commands_are_host_only(tmp_path: Path) -> None:
    session = Session(tmp_path)
    created = dispatch_slash("/child create Inspect files", session)
    assert created.handled and created.kind == "success"
    child_id = session.list_child_runs()[0].child_run_id
    shown = dispatch_slash(f"/child show {child_id}", session)
    assert "Status: queued" in shown.message
    cancelled = dispatch_slash(f"/child cancel {child_id} no longer needed", session)
    assert "Status: cancelled" in cancelled.message
    assert "No durable Child Runs" not in dispatch_slash("/child list", session).message


def test_standalone_child_commands_do_not_need_provider(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    before = writer.path.read_bytes()
    writer.release()
    status, output, errors = invoke(
        tmp_path,
        ["child", "create", "Queue this", "--parent-session", session_id],
    )
    assert status == 0 and errors == ""
    assert "Status: queued" in output
    assert SessionStore(tmp_path).inspect(session_id).path.read_bytes() == before
