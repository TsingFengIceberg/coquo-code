from __future__ import annotations

import json

import pytest

from coquo.skills.catalog import SkillCandidate, SkillManifest, SkillSourceKind
from coquo.skills.execution import (
    ExecutableSkillRunner,
    SkillExecutionError,
    SkillExecutionPlan,
    load_skill_execution_plan,
)


def candidate(*, allowed_tools=None) -> SkillCandidate:
    manifest = SkillManifest(
        name="inspect-config",
        description="Inspect a configuration file",
        allowed_tools=allowed_tools,
        instructions="Read the selected configuration and report facts.",
        fingerprint="skill-v1-" + "a" * 64,
    )
    return SkillCandidate(manifest, SkillSourceKind.WORKSPACE_LOCAL, "inspect-config/SKILL.md")


def test_declarative_skill_executes_only_allowlisted_steps() -> None:
    calls: list[tuple[str, dict]] = []

    def dispatch(name, arguments):
        calls.append((name, arguments.as_mapping()))
        return type("Result", (), {"code": "ok", "is_error": False})()

    plan = SkillExecutionPlan.from_mapping(
        {
            "schema_version": 1,
            "steps": [{"tool": "read_file", "arguments": {"path": "$input.path"}}],
        }
    )
    result = ExecutableSkillRunner(dispatch=dispatch).execute(
        candidate(allowed_tools=("read_file",)), plan, inputs={"path": "config.json"}
    )
    assert result.executed_steps == 1
    assert result.stopped_at is None
    assert calls == [("read_file", {"path": "config.json"})]


def test_skill_execution_rejects_forbidden_or_unallowlisted_tools() -> None:
    plan = SkillExecutionPlan.from_mapping(
        {"schema_version": 1, "steps": [{"tool": "run_command", "arguments": {}}]}
    )
    with pytest.raises(SkillExecutionError, match="dangerous execution"):
        ExecutableSkillRunner(dispatch=lambda *_: None).execute(candidate(), plan)

    unallowlisted = SkillExecutionPlan.from_mapping(
        {"schema_version": 1, "steps": [{"tool": "stat_path", "arguments": {}}]}
    )
    with pytest.raises(SkillExecutionError, match="allowlist"):
        ExecutableSkillRunner(dispatch=lambda *_: None).execute(
            candidate(allowed_tools=("read_file",)), unallowlisted
        )

    dangerous = SkillExecutionPlan.from_mapping(
        {
            "schema_version": 1,
            "allow_dangerous": True,
            "steps": [{"tool": "run_command", "arguments": {"argv": ["echo", "ok"]}}],
        }
    )
    denied = ExecutableSkillRunner(dispatch=lambda *_: None).execute(candidate(), dangerous)
    assert denied.denied is True


def test_skill_execution_requires_host_approval_for_explicit_dangerous_step() -> None:
    plan = SkillExecutionPlan.from_mapping(
        {
            "schema_version": 1,
            "allow_dangerous": True,
            "steps": [{"tool": "write_file", "arguments": {"path": "out.txt", "content": "ok"}}],
        }
    )
    seen: list[str] = []
    result = ExecutableSkillRunner(
        dispatch=lambda *_: None,
        approve=lambda name, _args: seen.append(name) or False,
    ).execute(candidate(allowed_tools=("write_file",)), plan)
    assert result.denied is True
    assert result.executed_steps == 0
    assert seen == ["write_file"]


def test_skill_execution_plan_sidecar_is_bounded_and_json_only(tmp_path) -> None:
    package = tmp_path / "inspect-config"
    package.mkdir()
    (package / "EXECUTION.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "steps": [{"tool": "stat_path", "arguments": {"path": "config.json"}}],
            }
        ),
        encoding="utf-8",
    )
    plan = load_skill_execution_plan(tmp_path, candidate())
    assert plan.steps[0].tool_name == "stat_path"
