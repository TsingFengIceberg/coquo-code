from __future__ import annotations

import io
import json
from pathlib import Path

from coquo.cli.main import main


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


def test_memory_cli_switch_and_explicit_candidate_management(tmp_path: Path) -> None:
    status, output, errors = invoke(tmp_path, ["memory", "status"])
    assert status == 0 and errors == ""
    assert "enabled: no" in output
    assert "effective recall: off" in output
    assert "configured capture: explicit" in output

    status, output, errors = invoke(
        tmp_path,
        [
            "memory",
            "configure",
            "--enable",
            "--recall",
            "on",
            "--write",
            "propose",
            "--capture",
            "conservative",
            "--tools",
        ],
    )
    assert status == 0 and errors == ""
    assert "capture=conservative" in output
    status, output, errors = invoke(tmp_path, ["memory", "status"])
    assert status == 0 and errors == ""
    assert "configured capture: conservative" in output
    status, candidate_json, errors = invoke(
        tmp_path,
        ["memory", "add", "Prefer deterministic tests", "--category", "preference"],
    )
    assert status == 0 and errors == ""
    memory_id = json.loads(candidate_json)["memory_id"]

    status, confirmed, errors = invoke(tmp_path, ["memory", "confirm", memory_id])
    assert status == 0 and errors == ""
    assert '"status": "confirmed"' in confirmed
    status, searched, errors = invoke(tmp_path, ["memory", "search", "deterministic"])
    assert status == 0 and errors == ""
    assert memory_id in searched

    status, _, errors = invoke(tmp_path, ["memory", "disable"])
    assert status == 0 and errors == ""
    status, output, errors = invoke(tmp_path, ["memory", "status"])
    assert status == 0 and errors == ""
    assert "enabled: no" in output
    assert "effective recall: off" in output
