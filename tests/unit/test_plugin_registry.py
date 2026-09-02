from __future__ import annotations

import json

import pytest

from coquo.plugin_registry import (
    PluginRegistry,
    PluginRegistryError,
    PluginRunner,
)


def write_plugin(path, *, capabilities=("workspace-read",)):
    path.mkdir()
    (path / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "name": "sample-plugin",
                "version": "1.0.0",
                "description": "A bounded test plugin",
                "entrypoint": ["sample-plugin", "--stdio"],
                "capabilities": list(capabilities),
            }
        ),
        encoding="utf-8",
    )


def test_plugin_registry_installs_manifest_without_importing_code(tmp_path) -> None:
    source = tmp_path / "source"
    write_plugin(source)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    info = PluginRegistry(workspace).install(source)
    assert info.manifest.name == "sample-plugin"
    assert info.manifest.digest
    assert len(PluginRegistry(workspace).list()) == 1


def test_plugin_registry_rejects_shell_like_or_invalid_manifest(tmp_path) -> None:
    source = tmp_path / "source"
    write_plugin(source)
    data = json.loads((source / "plugin.json").read_text(encoding="utf-8"))
    data["entrypoint"] = ["sh", "-c", "echo unsafe"]
    (source / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Direct argv is still a valid transport; shell parsing never occurs.
    info = PluginRegistry(workspace).install(source)
    assert info.manifest.entrypoint[-1] == "echo unsafe"
    with pytest.raises(PluginRegistryError, match="already installed"):
        PluginRegistry(workspace).install(source)


def test_plugin_runner_requires_approval_for_dangerous_capabilities() -> None:
    # Build through the public parser so digest and schema checks are covered.
    from coquo.plugin_registry import PluginManifest

    manifest = PluginManifest.from_mapping(
        {
            "manifest_version": 1,
            "name": "writer-plugin",
            "version": "1.0",
            "description": "write",
            "entrypoint": ["writer-plugin"],
            "capabilities": ["workspace-write"],
        },
        digest="a" * 64,
    )
    calls: list[tuple[str, ...]] = []
    runner = PluginRunner(executor=lambda argv, _timeout: calls.append(argv) or "ok")
    denied = runner.invoke(manifest)
    assert denied.outcome == "denied"
    assert not calls
    allowed = PluginRunner(
        executor=lambda argv, _timeout: calls.append(argv) or "ok",
        approve=lambda _plugin: True,
    ).invoke(manifest)
    assert allowed.outcome == "completed"
    assert calls == [("writer-plugin",)]
