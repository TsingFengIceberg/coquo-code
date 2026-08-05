from __future__ import annotations

import json
import os

import pytest

from leonervis_code.mcp.config import (
    MCP_CONFIGURATION_SCHEMA_VERSION,
    MAX_MCP_ARGUMENTS,
    McpConfigurationError,
    McpServerConfiguration,
    McpServerStore,
    McpTransport,
    McpTrustMode,
    parse_environment_bindings,
)


def configuration(name: str = "local-tools", **changes) -> McpServerConfiguration:
    values = {
        "name": name,
        "command": "/usr/bin/python3",
        "args": ("server.py",),
        "cwd": ".",
        "environment": (("SERVICE_TOKEN", "HOST_SERVICE_TOKEN"),),
    }
    values.update(changes)
    return McpServerConfiguration(**values)


def store(tmp_path) -> McpServerStore:
    return McpServerStore(
        tmp_path / "user" / "mcp-servers.json",
        tmp_path / "workspace" / ".leonervis-code" / "mcp-servers.json",
    )


def test_configuration_round_trips_without_credential_values() -> None:
    original = configuration()
    mapping = original.as_mapping()

    assert mapping["enabled"] is False
    assert mapping["environment"] == {"SERVICE_TOKEN": "HOST_SERVICE_TOKEN"}
    assert "secret-value" not in json.dumps(mapping)
    assert McpServerConfiguration.from_mapping(mapping) == original
    assert parse_environment_bindings(
        ["SERVICE_TOKEN=HOST_SERVICE_TOKEN", "CACHE_TOKEN=HOST_CACHE_TOKEN"]
    ) == (
        ("CACHE_TOKEN", "HOST_CACHE_TOKEN"),
        ("SERVICE_TOKEN", "HOST_SERVICE_TOKEN"),
    )


def test_remote_configuration_is_https_credential_free_and_revisioned(tmp_path) -> None:
    remote = McpServerConfiguration(
        name="remote",
        endpoint="https://mcp.example.test/mcp",
        bearer_token_env="MCP_TOKEN",
        expose_workspace_root=True,
        resource_subscriptions=("file:///guide",),
        transport=McpTransport.STREAMABLE_HTTP,
        trust=McpTrustMode.REMOTE_HTTPS,
    )
    mapping = remote.as_mapping()
    assert mapping["endpoint"] == "https://mcp.example.test/mcp"
    assert mapping["bearer_token_env"] == "MCP_TOKEN"
    assert "token-value" not in json.dumps(mapping)
    assert McpServerConfiguration.from_mapping(mapping) == remote

    registry = store(tmp_path)
    created = registry.add_server(remote, scope="project")
    updated = registry.set_resource_subscriptions(
        "remote",
        scope="project",
        subscriptions=("file:///guide", "file:///other"),
        expected_revision=created.configuration.revision,
    )
    assert updated.configuration.revision == 2


def test_store_reads_legacy_v1_stdio_configuration_without_rewriting(tmp_path) -> None:
    registry = store(tmp_path)
    registry.project_path.parent.mkdir(parents=True)
    legacy = {
        "schema_version": 1,
        "servers": {
            "legacy": {
                "args": [],
                "command": "/bin/true",
                "cwd": ".",
                "enabled": False,
                "environment": {},
                "name": "legacy",
                "revision": 1,
                "transport": "stdio",
                "trust": "confined-stdio",
            }
        },
    }
    original = json.dumps(legacy)
    registry.project_path.write_text(original, encoding="utf-8")

    loaded = registry.get_server("legacy")

    assert loaded.configuration.transport is McpTransport.STDIO
    assert registry.project_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"name": "Bad Name"}, "server name"),
        ({"command": "python3"}, "absolute POSIX"),
        ({"args": ("x",) * (MAX_MCP_ARGUMENTS + 1)}, "at most"),
        ({"cwd": "../outside"}, "within the workspace"),
        ({"environment": (("BAD-NAME", "SOURCE"),)}, "environment name"),
        ({"environment": (("TOKEN", "ONE"), ("TOKEN", "TWO"))}, "duplicated"),
        ({"revision": 0}, "revision"),
    ],
)
def test_configuration_rejects_malformed_or_ambient_authority(changes, message) -> None:
    with pytest.raises(McpConfigurationError, match=message):
        configuration(**changes)


def test_store_add_enable_replace_and_remove_are_revisioned_and_atomic(tmp_path) -> None:
    registry = store(tmp_path)
    assert registry.list_servers() == ()
    assert not registry.user_path.exists()
    assert not registry.project_path.exists()

    created = registry.add_server(configuration(), scope="project")
    assert created.scope == "project"
    assert created.configuration.revision == 1
    assert created.configuration.enabled is False
    assert registry.project_path.stat().st_mode & 0o777 == 0o600

    enabled = registry.set_enabled(
        "local-tools",
        scope="project",
        enabled=True,
        expected_revision=1,
    )
    assert enabled.configuration.revision == 2
    assert enabled.configuration.enabled is True
    with pytest.raises(McpConfigurationError, match="revision conflict"):
        registry.set_enabled(
            "local-tools",
            scope="project",
            enabled=False,
            expected_revision=1,
        )

    replaced = registry.add_server(
        configuration(args=("new-server.py",), enabled=True),
        scope="project",
        replace_existing=True,
        expected_revision=2,
    )
    assert replaced.configuration.revision == 3
    assert replaced.configuration.args == ("new-server.py",)

    persisted = json.loads(registry.project_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == MCP_CONFIGURATION_SCHEMA_VERSION
    assert persisted["servers"]["local-tools"]["environment"] == {
        "SERVICE_TOKEN": "HOST_SERVICE_TOKEN"
    }
    registry.remove_server(
        "local-tools",
        scope="project",
        expected_revision=3,
    )
    assert registry.list_servers() == ()


def test_store_keeps_scopes_distinct_and_rejects_effective_name_collisions(tmp_path) -> None:
    registry = store(tmp_path)
    registry.add_server(configuration("user-server"), scope="user")
    registry.add_server(configuration("project-server"), scope="project")

    assert [(entry.configuration.name, entry.scope) for entry in registry.list_servers()] == [
        ("project-server", "project"),
        ("user-server", "user"),
    ]
    with pytest.raises(McpConfigurationError, match="other scope"):
        registry.add_server(configuration("user-server"), scope="project")

    project = json.loads(registry.project_path.read_text(encoding="utf-8"))
    project["servers"]["user-server"] = configuration("user-server").as_mapping()
    registry.project_path.write_text(json.dumps(project), encoding="utf-8")
    with pytest.raises(McpConfigurationError, match="both scopes"):
        registry.list_servers()


def test_store_rejects_unknown_fields_nonfinite_json_and_symlink_paths(tmp_path) -> None:
    registry = store(tmp_path)
    registry.user_path.parent.mkdir(parents=True)
    registry.user_path.write_text(
        '{"schema_version":1,"servers":{},"unexpected":true}',
        encoding="utf-8",
    )
    with pytest.raises(McpConfigurationError, match="unknown field"):
        registry.list_servers()

    registry.user_path.write_text(
        '{"schema_version":1,"servers":{"x":NaN}}',
        encoding="utf-8",
    )
    with pytest.raises(McpConfigurationError, match="invalid JSON constant"):
        registry.list_servers()

    registry.user_path.unlink()
    target = tmp_path / "outside.json"
    target.write_text('{"schema_version":1,"servers":{}}', encoding="utf-8")
    os.symlink(target, registry.user_path)
    with pytest.raises(McpConfigurationError, match="must not be a symlink"):
        registry.list_servers()
