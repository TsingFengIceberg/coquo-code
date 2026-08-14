from __future__ import annotations

import io
import json
from pathlib import Path

from coquo.cli.main import main
from coquo.cli.slash import dispatch_slash
from coquo.child_run_store import ChildRunStore
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

    def start_child_run(self, child_run_id):
        from coquo.child_supervisor import ChildRunSupervisor

        if not hasattr(self, "supervisor"):
            self.supervisor = ChildRunSupervisor(self.workspace)
        return self.supervisor.submit(child_run_id)

    def publish_child_handoff(self, child_run_id):
        from coquo.child_handoff import publish_child_handoff

        return publish_child_handoff(self.workspace, child_run_id)

    def deliver_child_handoff(self, child_run_id):
        from coquo.child_handoff import deliver_child_handoff

        writer = SessionStore(self.workspace).open_for_audit(self.session_id)
        try:
            return deliver_child_handoff(
                self.workspace,
                child_run_id,
                parent_writer=writer,
            )
        finally:
            writer.release()


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


def test_standalone_and_slash_child_handoff(tmp_path: Path) -> None:
    session = Session(tmp_path)
    info = session.create_child_run("do not expose this objective")
    session.cancel_child_run(info.child_run_id, "stop")

    status, output, errors = invoke(tmp_path, ["child", "handoff", info.child_run_id])
    payload = json.loads(output)
    assert status == 0 and errors == ""
    assert payload["child_run_id"] == info.child_run_id
    assert payload["outcome"] == "cancelled"
    assert "do not expose" not in payload["body"]

    result = dispatch_slash(f"/child handoff {info.child_run_id}", session)
    assert result.handled and result.kind == "info"
    assert result.message == payload["body"]


def test_standalone_and_slash_child_delivery_commits_one_receipt(tmp_path: Path) -> None:
    session = Session(tmp_path)
    info = session.create_child_run("do not expose this objective")
    session.cancel_child_run(info.child_run_id, "stop")

    status, output, errors = invoke(tmp_path, ["child", "deliver", info.child_run_id])
    payload = json.loads(output)
    assert status == 0 and errors == ""
    assert payload["outcome"] == "cancelled"
    receipts = SessionStore(tmp_path).child_handoff_deliveries(session.session_id)
    assert len(receipts) == 1
    transcript = SessionStore(tmp_path).inspect(session.session_id).path.read_text(encoding="utf-8")
    assert transcript.count('"record_type":"child_handoff_delivered"') == 1
    assert '"record_type":"session_resumed"' not in transcript

    result = dispatch_slash(f"/child deliver {info.child_run_id}", session)
    assert result.handled and result.kind == "info"
    assert result.message == payload["body"]
    assert len(SessionStore(tmp_path).child_handoff_deliveries(session.session_id)) == 1


def test_standalone_child_delivery_failure_does_not_render_body(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    writer.release()
    info = ChildRunStore(tmp_path).create("still queued", parent_session=session_id)

    status, output, errors = invoke(tmp_path, ["child", "deliver", info.child_run_id])
    assert status == 2 and output == ""
    assert errors == "child error: Child Run is not terminal\n"


def test_standalone_child_handoff_failure_has_no_traceback(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    writer.release()
    info = ChildRunStore(tmp_path).create("still queued", parent_session=session_id)

    status, output, errors = invoke(tmp_path, ["child", "handoff", info.child_run_id])
    assert status == 2 and output == ""
    assert errors == "child error: Child Run is not terminal\n"
    assert "Traceback" not in errors


def test_standalone_child_prepare_binds_detached_session_without_provider(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    writer.release()
    status, output, errors = invoke(
        tmp_path,
        ["child", "create", "Prepare this", "--parent-session", session_id],
    )
    assert status == 0 and errors == ""
    child_id = next(
        line.splitlines()[0].split(":", 1)[1].strip()
        for line in output.split("\n\n")
        if "Child Run ID:" in line
    )
    status, output, errors = invoke(tmp_path, ["child", "prepare", child_id])
    assert status == 0 and errors == ""
    assert "Status: ready" in output
    assert "Child Session:" in output
    assert SessionStore(tmp_path).inspect("latest").session_id == session_id


def test_standalone_child_run_uses_fake_provider_and_is_detached(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    writer.release()
    status, output, errors = invoke(
        tmp_path,
        ["child", "create", "Inspect this", "--parent-session", session_id],
    )
    assert status == 0 and errors == ""
    child_id = next(
        line.splitlines()[0].split(":", 1)[1].strip()
        for line in output.split("\n\n")
        if "Child Run ID:" in line
    )
    assert invoke(tmp_path, ["child", "prepare", child_id])[0] == 0
    status, output, errors = invoke(tmp_path, ["child", "run", child_id])
    assert status == 0 and errors == ""
    assert "Status: completed" in output
    assert SessionStore(tmp_path).inspect("latest").session_id == session_id


def test_slash_child_start_queues_prepared_run(tmp_path: Path) -> None:
    session = Session(tmp_path)
    from coquo.child_runtime import build_child_runtime_spec_from_binding

    info = session.create_child_run("Inspect files")
    parent = SessionStore(tmp_path).inspect(session.session_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=session.session_id,
        child_session_id="22345678-1234-4234-9234-123456789abc",
        objective=info.objective,
        binding=parent.binding,
    )
    from coquo.child_run_store import ChildRunStore

    ChildRunStore(tmp_path).prepare(
        info.child_run_id,
        runtime_spec=spec,
        session_store=SessionStore(tmp_path),
        binding=parent.binding,
    )
    result = dispatch_slash(f"/child start {info.child_run_id}", session)
    assert result.handled and result.kind == "success"
    session.supervisor.close()
