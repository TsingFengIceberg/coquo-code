from __future__ import annotations

import json

import pytest

from coquo.core.action_coordinator import ActionCoordinator, ApprovalResolution
from coquo.core.actions import ActionLease
from coquo.core.contracts import ToolResult
from coquo.core.extension_actions import CoordinatedExtensionActionInvoker
from coquo.core.permissions import ApprovalMode, PermissionMode
from coquo.session_records import BindingSnapshot
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


class _RecordingWriter:
    def __init__(self) -> None:
        self.records: list[str] = []

    def action_requested(self, **_values):
        self.records.append("requested")

    def permission_decided(self, **_values):
        self.records.append("permission")

    def approval_resolved(self, **_values):
        self.records.append("approval")

    def action_execution_started(self, **_values):
        self.records.append("started")

    def action_execution_finished(self, **_values):
        self.records.append("finished")


def _extension_invoker(
    writer: _RecordingWriter, *, mode: PermissionMode, approval: ApprovalResolution
):
    return CoordinatedExtensionActionInvoker(
        coordinator=ActionCoordinator(
            writer=writer,  # type: ignore[arg-type]
            approval_handler=lambda _request: approval,
        ),
        binding=BindingSnapshot.fake(),
        permission_mode=mode,
        approval_mode=ApprovalMode.ASK,
        workspace_fingerprint="v1-" + "1" * 64,
        lease=ActionLease(
            "12345678-1234-4234-9234-123456789abc",
            "22345678-1234-4234-9234-123456789abc",
            0,
            "ctx-v1-" + "2" * 64,
        ),
    )


def test_skill_runner_routes_steps_through_coordinator_when_invoker_is_supplied() -> None:
    writer = _RecordingWriter()
    calls: list[str] = []
    runner = ExecutableSkillRunner(
        dispatch=lambda name, _arguments: calls.append(name) or ToolResult("ignored", "ok"),
        action_invoker=_extension_invoker(
            writer,
            mode=PermissionMode.READ_ONLY,
            approval=ApprovalResolution.ACCEPT,
        ),
    )
    plan = SkillExecutionPlan.from_mapping(
        {"schema_version": 1, "steps": [{"tool": "read_file", "arguments": {}}]}
    )
    result = runner.execute(candidate(allowed_tools=("read_file",)), plan)
    assert result.result_codes == ("ok",)
    assert calls == ["read_file"]
    assert writer.records == ["requested", "permission", "started", "finished"]


def test_skill_dangerous_step_uses_coordinator_approval_instead_of_legacy_callback() -> None:
    writer = _RecordingWriter()
    runner = ExecutableSkillRunner(
        dispatch=lambda name, arguments: ToolResult("ignored", "executed"),
        action_invoker=_extension_invoker(
            writer,
            mode=PermissionMode.DANGER_FULL_ACCESS,
            approval=ApprovalResolution.ACCEPT,
        ),
    )
    plan = SkillExecutionPlan.from_mapping(
        {
            "schema_version": 1,
            "allow_dangerous": True,
            "steps": [{"tool": "write_file", "arguments": {"path": "x", "content": "y"}}],
        }
    )
    result = runner.execute(candidate(allowed_tools=("write_file",)), plan)
    assert result.denied is False
    assert result.executed_steps == 1
    assert writer.records == ["requested", "permission", "approval", "started", "finished"]
