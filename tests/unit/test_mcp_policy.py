from __future__ import annotations

import os

import pytest

from coquo.core.permissions import PermissionAction
from coquo.mcp.policy import (
    McpPolicyDisposition,
    McpToolPolicyError,
    McpToolPolicyRule,
    McpToolPolicyStore,
)


QUALIFIED = "mcp_fixture_read_widget_0123456789"
FINGERPRINT = "mcp-schema-v1-" + "1" * 64


def _rule(**changes) -> McpToolPolicyRule:
    values = {
        "qualified_name": QUALIFIED,
        "configured_name": "fixture",
        "server_scope": "project",
        "configuration_revision": 3,
        "remote_name": "read_widget",
        "protocol_version": "2025-06-18",
        "schema_fingerprint": FINGERPRINT,
        "action": PermissionAction.WORKSPACE_READ,
    }
    values.update(changes)
    return McpToolPolicyRule(**values)


def _store(tmp_path) -> McpToolPolicyStore:
    return McpToolPolicyStore(tmp_path / "user.json", tmp_path / "project.json")


def test_policy_store_round_trips_mode_0600_and_exact_resolution(tmp_path) -> None:
    store = _store(tmp_path)
    saved = store.set_rule(_rule(), policy_scope="project")

    assert saved.revision == 1
    assert stat_mode(store.project_path) == 0o600
    scope, loaded = store.get_rule(QUALIFIED)
    assert scope == "project"
    assert loaded == saved
    assert store.resolve(
        qualified_name=QUALIFIED,
        configured_name="fixture",
        server_scope="project",
        configuration_revision=3,
        remote_name="read_widget",
        protocol_version="2025-06-18",
        schema_fingerprint=FINGERPRINT,
    ) == (McpPolicyDisposition.APPLIED, PermissionAction.WORKSPACE_READ, 1)


def test_policy_stale_falls_back_to_dangerous_and_cas_is_enforced(tmp_path) -> None:
    store = _store(tmp_path)
    store.set_rule(_rule(), policy_scope="project")

    assert store.resolve(
        qualified_name=QUALIFIED,
        configured_name="fixture",
        server_scope="project",
        configuration_revision=4,
        remote_name="read_widget",
        protocol_version="2025-06-18",
        schema_fingerprint=FINGERPRINT,
    ) == (McpPolicyDisposition.STALE, PermissionAction.DANGEROUS, 1)
    with pytest.raises(McpToolPolicyError, match="revision conflict"):
        store.set_rule(
            _rule(action=PermissionAction.DANGEROUS),
            policy_scope="project",
            replace_existing=True,
            expected_revision=9,
        )
    updated = store.set_rule(
        _rule(action=PermissionAction.DANGEROUS),
        policy_scope="project",
        replace_existing=True,
        expected_revision=1,
    )
    assert updated.revision == 2


def test_policy_rejects_symlinks_and_cross_scope_collisions(tmp_path) -> None:
    target = tmp_path / "target.json"
    target.write_text('{"schema_version":1,"policies":{}}\n', encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)
    store = McpToolPolicyStore(tmp_path / "user.json", linked)

    with pytest.raises(McpToolPolicyError, match="symlink"):
        store.list_rules()

    store = _store(tmp_path / "collision")
    store.set_rule(_rule(), policy_scope="user")
    with pytest.raises(McpToolPolicyError, match="other scope"):
        store.set_rule(_rule(), policy_scope="project")


def stat_mode(path) -> int:
    return os.stat(path).st_mode & 0o777
