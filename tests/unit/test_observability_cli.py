from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from coquo.child_run_store import ChildRunStore
from coquo.cli.main import build_parser, main
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.task_store import TaskStore
from coquo.team_store import TeamStore


def _invoke(workspace: Path, arguments: list[str]) -> tuple[int, str, str]:
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


def test_observe_all_projects_four_stores_without_mutating_them(tmp_path: Path) -> None:
    session_writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = session_writer.session_id
    session_writer.release()
    task = TaskStore(tmp_path).create("PRIVATE TASK", owner_session=session_id)
    child = ChildRunStore(tmp_path).create("PRIVATE CHILD", parent_session=session_id)
    team = TeamStore(tmp_path).create("PRIVATE TEAM", owner_session=session_id)
    paths = (
        SessionStore(tmp_path).inspect(session_id).path,
        task.path,
        child.path,
        team.path,
    )
    before = {path: path.read_bytes() for path in paths}

    status, output, errors = _invoke(tmp_path, ["observe", "timeline", "all", "--format", "jsonl"])

    assert status == 0
    assert errors == ""
    events = tuple(json.loads(line) for line in output.splitlines())
    assert {event["source"] for event in events} == {"session", "task", "child", "team"}
    assert {event["trace_id"] for event in events} == {session_id}
    assert all(event["record_type"].endswith("_header") for event in events)
    assert all(event["phase"] == "created" for event in events)
    assert "PRIVATE" not in output
    assert {path: path.read_bytes() for path in paths} == before


def test_observe_requires_ids_and_rejects_an_id_for_all(tmp_path: Path) -> None:
    status, output, errors = _invoke(tmp_path, ["observe", "timeline", "task"])
    assert status == 2
    assert output == ""
    assert errors == "observation error: a Task timeline requires a source ID\n"

    status, output, errors = _invoke(tmp_path, ["observe", "timeline", "all", "unexpected"])
    assert status == 2
    assert output == ""
    assert errors == "observation error: the all timeline does not accept a source ID\n"


def test_observe_limit_is_rejected_by_argparse(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["observe", "timeline", "all", "--limit", "1001"])
    assert caught.value.code == 2
    assert "observation timeline limit must be between 1 and 1000" in capsys.readouterr().err


def test_observe_filters_and_diagnose_are_read_only(tmp_path: Path) -> None:
    writer = SessionStore(tmp_path).create(BindingSnapshot.fake())
    session_id = writer.session_id
    writer.release()
    path = SessionStore(tmp_path).inspect(session_id).path
    before = path.read_bytes()

    status, output, errors = _invoke(
        tmp_path,
        [
            "observe",
            "timeline",
            "session",
            session_id,
            "--format",
            "jsonl",
            "--evidence",
            "host-verified",
            "--record-type",
            "session_header",
        ],
    )
    assert status == 0
    assert errors == ""
    assert len(output.splitlines()) == 1
    assert json.loads(output)["record_type"] == "session_header"

    status, output, errors = _invoke(
        tmp_path, ["observe", "diagnose", "session", session_id, "--format", "json"]
    )
    assert status == 0
    assert errors == ""
    assert json.loads(output) == []
    assert path.read_bytes() == before
