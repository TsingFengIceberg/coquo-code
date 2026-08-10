from __future__ import annotations

import io
import json
from pathlib import Path

from coquo.cli.main import main
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.skill_authoring import (
    SKILL_PROPOSE_CREATE_TOOL_NAME,
    SkillCreationProposal,
)
from coquo.skill_candidates import SkillCandidateStore


def write_skill(root: Path, name: str, description: str = "Demo workflow") -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        f"---\nmanifest-version: 1\nname: {name}\ndescription: {description}\n---\nDo it.\n",
        encoding="utf-8",
    )
    return package


def test_skill_candidate_cli_lists_shows_and_installs_without_session(tmp_path: Path) -> None:
    environment = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    proposal = SkillCreationProposal.from_request(
        ToolUse(
            "proposal-cli",
            SKILL_PROPOSE_CREATE_TOOL_NAME,
            ToolArguments.from_mapping(
                {
                    "allowed_tools": None,
                    "description": "CLI candidate",
                    "instructions": "Run the explicit workflow and verify it.",
                    "name": "cli-candidate",
                    "scope": "project",
                }
            ),
        ),
        "ctx-v15-" + "b" * 64,
    )
    candidate = SkillCandidateStore(tmp_path, environment).create_generated(
        proposal, owner_session_id="session-cli", turn_sequence=1
    )

    output = io.StringIO()
    assert (
        main(["skills", "candidate", "list"], cwd=tmp_path, environment=environment, stdout=output)
        == 0
    )
    assert json.loads(output.getvalue())["candidate_id"] == candidate.candidate_id

    output = io.StringIO()
    assert (
        main(
            ["skills", "candidate", "show", candidate.candidate_id],
            cwd=tmp_path,
            environment=environment,
            stdout=output,
        )
        == 0
    )
    assert json.loads(output.getvalue())["instructions"].startswith("Run the explicit workflow")

    output = io.StringIO()
    assert (
        main(
            ["skills", "install", candidate.candidate_id],
            cwd=tmp_path,
            environment=environment,
            stdout=output,
        )
        == 0
    )
    installed = json.loads(output.getvalue())
    assert installed["status"] == "installed"
    assert installed["installed_scope"] == "project"
    assert not (tmp_path / ".coquo" / "sessions").exists()


def test_skills_cli_is_read_only_and_reports_active_package(tmp_path) -> None:
    package = tmp_path / ".agents" / "skills" / "demo"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nmanifest-version: 1\nname: demo\ndescription: Demo workflow\n---\nDo it.\n",
        encoding="utf-8",
    )
    (package / "guide.md").write_text("Guide.\n", encoding="utf-8")
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
    assert listed["resources"] == 1
    assert not (tmp_path / ".coquo" / "sessions").exists()

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

    output = io.StringIO()
    assert (
        main(
            ["skills", "show", "demo"],
            cwd=tmp_path,
            environment={"XDG_CONFIG_HOME": str(config)},
            stdout=output,
        )
        == 0
    )
    shown = json.loads(output.getvalue())
    assert shown["resources"][0]["path"] == "guide.md"
    assert shown["resources"][0]["text_readable"] is True


def test_skills_init_check_and_search_do_not_create_a_session(tmp_path) -> None:
    config = tmp_path / "config"
    environment = {"XDG_CONFIG_HOME": str(config)}
    initialized = io.StringIO()

    assert (
        main(
            ["skills", "init", "release-check", "--description", "Validate Python release"],
            cwd=tmp_path,
            environment=environment,
            stdout=initialized,
        )
        == 0
    )
    created = json.loads(initialized.getvalue())
    assert created["name"] == "release-check"
    assert created["source"] == "project-shared"
    assert created["template_version"] == 1

    checked = io.StringIO()
    assert (
        main(
            ["skills", "check", "release-check"],
            cwd=tmp_path,
            environment=environment,
            stdout=checked,
        )
        == 0
    )
    assert json.loads(checked.getvalue())["valid"] is True

    searched = io.StringIO()
    assert (
        main(
            ["skills", "search", "python release"],
            cwd=tmp_path,
            environment=environment,
            stdout=searched,
        )
        == 0
    )
    assert json.loads(searched.getvalue())["name"] == "release-check"
    assert not (tmp_path / ".coquo" / "sessions").exists()


def test_skills_check_reports_invalid_named_package_and_init_rejects_existing(tmp_path) -> None:
    environment = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    invalid = tmp_path / ".agents" / "skills" / "broken"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("---\nname: [\n---\nbody\n", encoding="utf-8")
    output = io.StringIO()

    assert (
        main(
            ["skills", "check", "broken"],
            cwd=tmp_path,
            environment=environment,
            stdout=output,
        )
        == 1
    )
    result = json.loads(output.getvalue())
    assert result["valid"] is False
    assert result["issue_codes"] == ["invalid-yaml"]

    errors = io.StringIO()
    assert (
        main(
            ["skills", "init", "broken", "--description", "Duplicate"],
            cwd=tmp_path,
            environment=environment,
            stderr=errors,
        )
        == 2
    )
    assert "package already exists" in errors.getvalue()


def test_skills_search_all_and_conflicts_explain_shadowing(tmp_path) -> None:
    config = tmp_path / "config"
    write_skill(config / "coquo" / "skills", "review", "Review user package")
    write_skill(tmp_path / ".agents" / "skills", "review", "Review project package")
    environment = {"XDG_CONFIG_HOME": str(config)}

    active = io.StringIO()
    all_matches = io.StringIO()
    conflicts = io.StringIO()
    assert (
        main(
            ["skills", "search", "review"],
            cwd=tmp_path,
            environment=environment,
            stdout=active,
        )
        == 0
    )
    assert len(active.getvalue().splitlines()) == 1
    assert (
        main(
            ["skills", "search", "review", "--all"],
            cwd=tmp_path,
            environment=environment,
            stdout=all_matches,
        )
        == 0
    )
    matches = [json.loads(line) for line in all_matches.getvalue().splitlines()]
    assert [item["source"] for item in matches] == ["project-shared", "user"]
    assert matches[1]["shadowed_by"] == "project-shared"
    assert (
        main(
            ["skills", "conflicts"],
            cwd=tmp_path,
            environment=environment,
            stdout=conflicts,
        )
        == 0
    )
    assert json.loads(conflicts.getvalue())["source"] == "user"


def test_skills_import_copies_local_package_and_detects_lock_drift(tmp_path) -> None:
    environment = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    source = write_skill(tmp_path / "sources", "imported", "Imported helper")
    (source / "scripts").mkdir()
    (source / "scripts" / "check.py").write_text("print('ok')\n", encoding="utf-8")
    imported = io.StringIO()

    assert (
        main(
            ["skills", "import", "sources/imported", "--scope", "project"],
            cwd=tmp_path,
            environment=environment,
            stdout=imported,
        )
        == 0
    )
    result = json.loads(imported.getvalue())
    assert result["name"] == "imported"
    assert result["resources"] == 1
    target = tmp_path / ".agents" / "skills" / "imported"
    assert (target / "scripts" / "check.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert (target / "scripts" / "check.py").stat().st_mode & 0o111 == 0
    lock_path = tmp_path / ".agents" / "skill-locks" / "imported.json"
    assert str(source) not in lock_path.read_text(encoding="utf-8")

    verified = io.StringIO()
    assert (
        main(
            ["skills", "lock", "verify", "imported"],
            cwd=tmp_path,
            environment=environment,
            stdout=verified,
        )
        == 0
    )
    assert json.loads(verified.getvalue())["valid"] is True

    (target / "scripts" / "check.py").write_text("print('changed')\n", encoding="utf-8")
    drifted = io.StringIO()
    assert (
        main(
            ["skills", "lock", "verify", "imported"],
            cwd=tmp_path,
            environment=environment,
            stdout=drifted,
        )
        == 1
    )
    assert json.loads(drifted.getvalue())["reason"] == "fingerprint-mismatch"
