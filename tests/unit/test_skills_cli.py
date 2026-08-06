from __future__ import annotations

import io
import json

from leonervis_code.cli.main import main


def test_skills_cli_is_read_only_and_reports_active_package(tmp_path) -> None:
    package = tmp_path / ".agents" / "skills" / "demo"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nmanifest-version: 1\nname: demo\ndescription: Demo workflow\n---\nDo it.\n",
        encoding="utf-8",
    )
    config = tmp_path / "config"
    output = io.StringIO()

    assert (
        main(
            ["skills", "list"],
            cwd=tmp_path,
            environment={"XDG_CONFIG_HOME": str(config)},
            stdout=output,
        )
        == 0
    )
    listed = json.loads(output.getvalue())
    assert listed["name"] == "demo"
    assert listed["active"] is True
    assert not (tmp_path / ".leonervis-code" / "sessions").exists()

    output = io.StringIO()
    assert (
        main(
            ["skills", "doctor"],
            cwd=tmp_path,
            environment={"XDG_CONFIG_HOME": str(config)},
            stdout=output,
        )
        == 0
    )
    assert json.loads(output.getvalue())["issues"] == 0
