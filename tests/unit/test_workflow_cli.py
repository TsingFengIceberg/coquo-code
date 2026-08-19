from __future__ import annotations

import io
from pathlib import Path

from coquo.cli.main import main
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore


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


def _workflow_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("Workflow ID:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"missing Workflow ID in {output!r}")


def test_workflow_cli_exposes_host_phase_and_child_identity_without_provider(
    tmp_path: Path,
) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    owner_session = writer.session_id
    writer.release()

    status, started, errors = invoke(
        tmp_path,
        [
            "workflow",
            "start",
            "Inspect a bounded fixture",
            "--session",
            owner_session,
            "--accept",
            "Child handoff is observed",
        ],
    )
    assert status == 0 and errors == ""
    workflow_id = _workflow_id(started)
    assert "Phase: architecture" in started
    assert "Evidence records: 0 (all untrusted)" in started

    status, advanced, errors = invoke(tmp_path, ["workflow", "advance", workflow_id])
    assert status == 0 and errors == ""
    assert "Phase: exploration" in advanced

    status, admitted, errors = invoke(tmp_path, ["workflow", "explore-start", workflow_id])
    assert status == 0 and errors == ""
    assert "Bridge outcome: pending" in admitted
    assert "Child Run ID:" in admitted
    assert "Stage Task:" in admitted
    assert "handoff received: false" in admitted
    assert "evidence: untrusted" in admitted

    status, shown, errors = invoke(tmp_path, ["workflow", "show", workflow_id])
    assert status == 0 and errors == ""
    assert "Phase: exploration" in shown
    assert "Child Run:" in shown
    assert "Stage Task:" in shown


def test_workflow_cli_rejects_provider_selection_and_requires_explicit_team_identity(
    tmp_path: Path,
) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    owner_session = writer.session_id
    writer.release()

    status, output, errors = invoke(
        tmp_path,
        [
            "--profile",
            "offline-test",
            "workflow",
            "start",
            "Do not invoke a provider",
            "--session",
            owner_session,
            "--accept",
            "Rejected before provider selection",
        ],
    )
    assert status == 2
    assert output == ""
    assert "provider selection options cannot be combined with workflow management" in errors

    status, started, errors = invoke(
        tmp_path,
        [
            "workflow",
            "start",
            "Prepare Team execution",
            "--session",
            owner_session,
            "--accept",
            "Team identity is explicit",
        ],
    )
    assert status == 0 and errors == ""
    workflow_id = _workflow_id(started)
    status, _, errors = invoke(tmp_path, ["workflow", "advance", workflow_id])
    assert status == 0 and errors == ""
    status, _, errors = invoke(tmp_path, ["workflow", "explore-start", workflow_id])
    assert status == 0 and errors == ""
    status, _, errors = invoke(tmp_path, ["workflow", "execute-start", workflow_id])
    assert status == 2
    assert "workflow requires phase execution" in errors or "workflow phase" in errors
