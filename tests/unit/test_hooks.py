from __future__ import annotations

import json
import os

import pytest

from leonervis_code.core.contracts import ToolArguments
from leonervis_code.core.extensions import ExtensionSourceKind
from leonervis_code.core.permissions import PermissionAction
from leonervis_code.hooks import (
    HOOK_CONFIGURATION_SCHEMA_VERSION,
    HookConfigurationError,
    HookEffect,
    HookRule,
    HookSource,
    HookStore,
    evaluate_before_action_authorization,
)


def rule(hook_id: str = "protect-config", **changes) -> HookRule:
    values = {
        "hook_id": hook_id,
        "effect": HookEffect.DENY,
        "message": "Configuration changes require review.",
        "tool_names": ("edit_file", "write_file"),
        "permission_actions": (PermissionAction.WORKSPACE_OVERWRITE,),
        "path_prefixes": ("config",),
        "sources": (HookSource.BUILTIN,),
    }
    values.update(changes)
    return HookRule(**values)


def store(tmp_path) -> HookStore:
    return HookStore(
        tmp_path / "user" / "hooks.json",
        tmp_path / "workspace" / ".leonervis-code" / "hooks.json",
    )


def test_hook_rule_round_trips_as_closed_declarative_data() -> None:
    original = rule()
    mapping = original.as_mapping()

    assert mapping["enabled"] is False
    assert mapping["event"] == "before_action_authorization"
    assert "command" not in mapping
    assert HookRule.from_mapping(mapping) == original
    with pytest.raises(HookConfigurationError, match="unknown field"):
        HookRule.from_mapping({**mapping, "shell": "echo unsafe"})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"hook_id": "Bad Hook"}, "Hook ID"),
        ({"message": "bad\nmessage"}, "control-free"),
        ({"tool_names": ("write_file", "edit_file")}, "canonical"),
        ({"path_prefixes": ("../outside",)}, "workspace-relative"),
        ({"path_prefixes": ("config//nested",)}, "canonical"),
        ({"revision": 0}, "revision"),
        ({"effect": HookEffect.ADVISORY, "message": ""}, "requires a message"),
        ({"permission_actions": (PermissionAction.UNKNOWN,)}, "unknown"),
    ],
)
def test_hook_rule_rejects_malformed_or_authority_expanding_values(changes, message) -> None:
    with pytest.raises(HookConfigurationError, match=message):
        rule(**changes)


def test_store_is_revisioned_private_atomic_and_cross_scope_unique(tmp_path) -> None:
    registry = store(tmp_path)
    created = registry.add_hook(rule(), scope="project")

    assert created.rule.enabled is False
    assert created.rule.revision == 1
    assert registry.project_path.stat().st_mode & 0o777 == 0o600
    enabled = registry.set_enabled(
        created.rule.hook_id, scope="project", enabled=True, expected_revision=1
    )
    assert enabled.rule.enabled is True
    assert enabled.rule.revision == 2
    with pytest.raises(HookConfigurationError, match="revision conflict"):
        registry.set_enabled(
            created.rule.hook_id, scope="project", enabled=False, expected_revision=1
        )
    with pytest.raises(HookConfigurationError, match="other scope"):
        registry.add_hook(rule(), scope="user")

    persisted = json.loads(registry.project_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == HOOK_CONFIGURATION_SCHEMA_VERSION
    assert persisted["hooks"][created.rule.hook_id]["enabled"] is True
    registry.remove_hook(created.rule.hook_id, scope="project", expected_revision=2)
    assert registry.list_hooks() == ()


def test_store_rejects_unknown_nonfinite_and_symlinked_configuration(tmp_path) -> None:
    registry = store(tmp_path)
    registry.user_path.parent.mkdir(parents=True)
    registry.user_path.write_text(
        '{"schema_version":1,"hooks":{},"unexpected":true}', encoding="utf-8"
    )
    with pytest.raises(HookConfigurationError, match="unknown field"):
        registry.snapshot()
    registry.user_path.write_text('{"schema_version":NaN,"hooks":{}}', encoding="utf-8")
    with pytest.raises(HookConfigurationError, match="invalid JSON constant"):
        registry.snapshot()

    registry.user_path.unlink()
    target = tmp_path / "outside.json"
    target.write_text('{"schema_version":1,"hooks":{}}', encoding="utf-8")
    os.symlink(target, registry.user_path)
    with pytest.raises(HookConfigurationError, match="must not be a symlink"):
        registry.snapshot()


def test_snapshot_identity_is_deterministic_and_covers_disabled_configuration(tmp_path) -> None:
    registry = store(tmp_path)
    empty = registry.snapshot()
    registry.add_hook(rule(), scope="project")
    disabled = registry.snapshot()
    enabled = registry.set_enabled("protect-config", scope="project", enabled=True)
    active = registry.snapshot()

    assert empty.snapshot_id != disabled.snapshot_id != active.snapshot_id
    assert disabled.active_entries == ()
    assert active.active_entries == (enabled,)
    assert registry.snapshot().snapshot_id == active.snapshot_id


def test_preauthorization_matches_exact_tool_action_source_and_path_prefix(tmp_path) -> None:
    registry = store(tmp_path)
    registry.add_hook(rule(enabled=True), scope="project")
    snapshot = registry.snapshot()

    matched = evaluate_before_action_authorization(
        snapshot,
        tool_name="write_file",
        action=PermissionAction.WORKSPACE_OVERWRITE,
        arguments=ToolArguments.from_mapping({"path": "config/app.json", "content": "{}"}),
        source_kind=ExtensionSourceKind.BUILTIN,
    )
    outside = evaluate_before_action_authorization(
        snapshot,
        tool_name="write_file",
        action=PermissionAction.WORKSPACE_OVERWRITE,
        arguments=ToolArguments.from_mapping({"path": "docs/app.json", "content": "{}"}),
        source_kind=ExtensionSourceKind.BUILTIN,
    )

    assert matched.denied_by == "protect-config"
    assert outside.denied_by is None


def test_deny_precedes_require_ask_and_advisories_are_bounded(tmp_path) -> None:
    registry = store(tmp_path)
    registry.add_hook(rule("a-advice", effect=HookEffect.ADVISORY, enabled=True), scope="project")
    registry.add_hook(rule("b-ask", effect=HookEffect.REQUIRE_ASK, enabled=True), scope="project")
    registry.add_hook(rule("c-deny", enabled=True), scope="project")

    result = evaluate_before_action_authorization(
        registry.snapshot(),
        tool_name="write_file",
        action=PermissionAction.WORKSPACE_OVERWRITE,
        arguments=ToolArguments.from_mapping({"path": "config/app.json"}),
        source_kind=ExtensionSourceKind.BUILTIN,
    )

    assert result.denied_by == "c-deny"
    assert not result.requires_ask
    assert result.advisory_text is None
