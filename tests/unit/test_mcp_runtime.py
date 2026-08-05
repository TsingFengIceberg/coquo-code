from __future__ import annotations

import sys
from pathlib import Path

import pytest

from leonervis_code.core.cancellation import TurnCancellation
from leonervis_code.mcp.catalog import McpCatalogService
from leonervis_code.mcp.client import McpStdioClient
from leonervis_code.mcp.config import McpServerConfiguration, McpServerStore
from leonervis_code.mcp.runtime import (
    McpCallPreparationError,
    McpProcessManager,
    McpRuntimeOutcome,
    prepare_mcp_call,
)
from leonervis_code.tools.command_sandbox import CommandSandboxLaunch


FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py"


class PassthroughSandbox:
    def prepare_launch(self, *, workspace, cwd, argv, environment) -> CommandSandboxLaunch:
        return CommandSandboxLaunch(argv=argv, cwd=cwd, environment=dict(environment))


def _runtime(tmp_path, mode="normal"):
    store = McpServerStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_server(
        McpServerConfiguration(
            name="fixture",
            command=sys.executable,
            args=(str(FIXTURE), mode),
            enabled=True,
        ),
        scope="project",
    )
    client = McpStdioClient(
        tmp_path,
        environment={},
        command_sandbox=PassthroughSandbox(),
    )
    catalog = McpCatalogService(store, client).snapshot(refresh=True)
    manager = McpProcessManager(store, client)
    candidate = next(item for item in catalog.accepted if item.remote_name == "read_widget")
    return manager, catalog, candidate


def test_runtime_reuses_one_generation_for_sequential_calls_and_closes(tmp_path) -> None:
    manager, catalog, candidate = _runtime(tmp_path)
    first = manager.execute(prepare_mcp_call(candidate, catalog.catalog_id, {"widget": "blue"}))
    second = manager.execute(prepare_mcp_call(candidate, catalog.catalog_id, {"widget": "green"}))

    assert first.outcome is McpRuntimeOutcome.SUCCEEDED
    assert first.process_reused is False
    assert "called read_widget #1" in first.content
    assert second.outcome is McpRuntimeOutcome.SUCCEEDED
    assert second.process_reused is True
    assert second.process_generation == first.process_generation
    assert '"calls":2' in second.content
    assert manager.statuses()[0].calls_completed == 2
    assert manager.close() is True
    assert manager.statuses() == ()


def test_prepare_validates_arguments_against_frozen_schema(tmp_path) -> None:
    manager, catalog, candidate = _runtime(tmp_path)

    with pytest.raises(McpCallPreparationError, match="wrong type"):
        prepare_mcp_call(candidate, catalog.catalog_id, {"widget": 3})

    assert manager.close() is True


@pytest.mark.parametrize(
    "mode, outcome, code",
    [
        ("call-error", McpRuntimeOutcome.FAILED, "mcp_tool_reported_error"),
        ("call-rpc-error", McpRuntimeOutcome.FAILED, "mcp_server_error"),
        ("call-invalid-result", McpRuntimeOutcome.PARTIAL, "mcp_result_invalid"),
        (
            "call-unsupported-content",
            McpRuntimeOutcome.PARTIAL,
            "mcp_result_content_unsupported",
        ),
        ("call-oversized", McpRuntimeOutcome.PARTIAL, "mcp_result_truncated"),
    ],
)
def test_runtime_classifies_server_and_normalization_outcomes(
    tmp_path, mode, outcome, code
) -> None:
    manager, catalog, candidate = _runtime(tmp_path, mode)

    result = manager.execute(prepare_mcp_call(candidate, catalog.catalog_id, {"widget": "blue"}))

    assert result.outcome is outcome
    assert result.result_code == code
    assert result.is_error is (outcome is not McpRuntimeOutcome.SUCCEEDED)
    assert "SECRET" not in result.content
    if mode in {"call-invalid-result", "call-unsupported-content", "call-oversized"}:
        assert manager.statuses() == ()
    assert manager.close() is True


def test_runtime_timeout_is_partial_and_retires_process(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("leonervis_code.mcp.client.MCP_CALL_TOOL_TIMEOUT_SECONDS", 0.05)
    manager, catalog, candidate = _runtime(tmp_path, "call-timeout")

    result = manager.execute(prepare_mcp_call(candidate, catalog.catalog_id, {"widget": "blue"}))

    assert result.outcome is McpRuntimeOutcome.PARTIAL
    assert result.result_code == "mcp_timeout"
    assert result.cleanup_complete is True
    assert manager.statuses() == ()
    assert manager.close() is True


def test_runtime_cancel_is_partial_and_retires_process(tmp_path) -> None:
    manager, catalog, candidate = _runtime(tmp_path, "call-timeout")
    cancellation = TurnCancellation()
    cancellation.request()

    result = manager.execute(
        prepare_mcp_call(candidate, catalog.catalog_id, {"widget": "blue"}),
        cancellation=cancellation,
    )

    assert result.outcome is McpRuntimeOutcome.PARTIAL
    assert result.result_code == "mcp_cancelled"
    assert manager.statuses() == ()
    assert manager.close() is True


def test_runtime_retires_old_generation_when_catalog_configuration_changes(tmp_path) -> None:
    manager, catalog, candidate = _runtime(tmp_path)
    first = manager.execute(prepare_mcp_call(candidate, catalog.catalog_id, {"widget": "blue"}))
    store = manager._store
    store.add_server(
        McpServerConfiguration(
            name="fixture",
            command=sys.executable,
            args=(str(FIXTURE), "normal", "generation-2"),
            enabled=True,
        ),
        scope="project",
        replace_existing=True,
        expected_revision=1,
    )
    refreshed_catalog = McpCatalogService(store, manager._client).snapshot(refresh=True)
    refreshed_candidate = next(
        item for item in refreshed_catalog.accepted if item.remote_name == "read_widget"
    )

    second = manager.execute(
        prepare_mcp_call(
            refreshed_candidate,
            refreshed_catalog.catalog_id,
            {"widget": "green"},
        )
    )

    assert second.outcome is McpRuntimeOutcome.SUCCEEDED
    assert second.process_reused is False
    assert second.process_generation != first.process_generation
    assert len(manager.statuses()) == 1
    assert manager.statuses()[0].configuration_revision == 2
    assert manager.close() is True


def test_runtime_status_retires_process_after_configuration_is_disabled(tmp_path) -> None:
    manager, catalog, candidate = _runtime(tmp_path)
    result = manager.execute(prepare_mcp_call(candidate, catalog.catalog_id, {"widget": "blue"}))
    assert result.outcome is McpRuntimeOutcome.SUCCEEDED

    manager._store.set_enabled(
        "fixture",
        scope="project",
        enabled=False,
        expected_revision=1,
    )

    assert manager.statuses() == ()
    assert manager.close() is True


def test_runtime_retains_failed_cleanup_for_a_later_close_attempt(tmp_path, monkeypatch) -> None:
    manager, catalog, candidate = _runtime(tmp_path)
    result = manager.execute(prepare_mcp_call(candidate, catalog.catalog_id, {"widget": "blue"}))
    assert result.outcome is McpRuntimeOutcome.SUCCEEDED
    managed = next(iter(manager._processes.values()))
    real_close = managed.session.close
    monkeypatch.setattr(managed.session, "close", lambda: False)

    assert manager.close() is False
    assert len(manager._processes) == 1

    monkeypatch.setattr(managed.session, "close", real_close)
    assert manager.close() is True
    assert manager.statuses() == ()


def test_json_rpc_tool_error_counts_as_completed_call(tmp_path) -> None:
    manager, catalog, candidate = _runtime(tmp_path, "call-rpc-error")

    result = manager.execute(prepare_mcp_call(candidate, catalog.catalog_id, {"widget": "blue"}))

    assert result.outcome is McpRuntimeOutcome.FAILED
    assert manager.statuses()[0].calls_completed == 1
    assert manager.close() is True
