from __future__ import annotations

import pytest

from coquo.tenant import TenantError, TenantPolicy, TenantRegistry, workspace_fingerprint


def test_tenant_registry_persists_workspace_ownership_and_quota(tmp_path):
    registry = TenantRegistry(tmp_path)
    policy = TenantPolicy("tenant-a", workspace_fingerprint(tmp_path), 3, 7)
    registry.configure(policy)
    restarted = TenantRegistry(tmp_path)
    assert restarted.require_workspace("tenant-a", policy.workspace_fingerprint) == policy
    assert restarted.list() == (policy,)


def test_tenant_registry_rejects_wrong_workspace_and_invalid_scope(tmp_path):
    registry = TenantRegistry(tmp_path)
    policy = TenantPolicy("tenant-a", workspace_fingerprint(tmp_path))
    registry.configure(policy)
    with pytest.raises(TenantError, match="does not own"):
        registry.require_workspace("tenant-a", "workspace-v1-other")
    with pytest.raises(TenantError, match="tenant ID"):
        registry.resolve("../tenant")
