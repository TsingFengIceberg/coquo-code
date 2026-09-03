from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from a2a.types.a2a_pb2 import Message, Part, SendMessageRequest, Task, TaskState, TaskStatus
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value

from coquo.a2a_provider import (
    A2ATaskStore,
    CoquoA2AHandler,
    build_agent_card,
    create_app,
    create_project_session_factory,
)
from coquo.a2a_server import main as a2a_main


class _Context:
    state: dict[str, str] = {}
    tenant = "tenant-a"


class _Session:
    def __init__(self, workspace: Path, *, wait_for_cancel: bool = False) -> None:
        self.workspace = workspace
        self.wait_for_cancel = wait_for_cancel
        self.prompts: list[str] = []
        self.closed = False

    def prompt(self, prompt: str, *, cancellation) -> str:
        self.prompts.append(prompt)
        if self.wait_for_cancel:
            cancellation.wait(1)
        cancellation.check()
        return f"# Result\n\nHandled: {prompt}"

    def close(self) -> None:
        self.closed = True


def _request(*parts: Part) -> SendMessageRequest:
    return SendMessageRequest(message=Message(message_id="message-1", parts=list(parts)))


def test_agent_card_advertises_standard_json_rpc_contract(tmp_path: Path) -> None:
    card = build_agent_card("http://127.0.0.1:18751")
    assert card.supported_interfaces[0].protocol_binding == "JSON_RPC"
    assert card.supported_interfaces[0].protocol_version == "1.0"
    assert card.capabilities.streaming is True
    assert card.capabilities.push_notifications is False
    assert card.skills[0].id == "coding-assistance"
    app = create_app(
        tmp_path, "http://127.0.0.1:18751", session_factory=lambda workspace: _Session(workspace)
    )
    assert any(route.path == "/.well-known/agent-card.json" for route in app.routes)


def test_fixture_session_factory_is_explicit_and_uses_the_offline_provider(tmp_path: Path) -> None:
    session = create_project_session_factory(fixture_provider=True)(tmp_path)
    try:
        assert session.prompt("inspect the workspace").startswith("Fake response:")
    finally:
        session.close()


def test_a2a_command_requires_an_explicit_provider_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_create_app(workspace: Path, public_url: str, **kwargs: object) -> object:
        captured.update(workspace=workspace, public_url=public_url, **kwargs)
        return object()

    monkeypatch.setattr("coquo.a2a_server.create_app", fake_create_app)
    monkeypatch.setattr("coquo.a2a_server.uvicorn.run", lambda *_args, **_kwargs: None)
    assert a2a_main(["--workspace", str(tmp_path), "--fixture-provider"]) == 0
    assert captured["fixture_provider"] is True
    with pytest.raises(SystemExit, match="2"):
        a2a_main(["--workspace", str(tmp_path)])


def test_handler_runs_task_returns_artifact_and_keeps_host_state_private(tmp_path: Path) -> None:
    created: list[_Session] = []

    def factory(workspace: Path) -> _Session:
        session = _Session(workspace)
        created.append(session)
        return session

    async def run() -> None:
        handler = CoquoA2AHandler(tmp_path, session_factory=factory)
        structured = Value()
        ParseDict({"repository": "demo"}, structured)
        task = await handler.on_message_send(
            _request(Part(text="inspect this repository"), Part(data=structured)), _Context()
        )
        binding = handler._tasks[("anonymous", "tenant-a", task.id)]
        assert binding.runner is not None
        await binding.runner
        completed = await handler.on_get_task(type("Request", (), {"id": task.id})(), _Context())
        assert completed.status.state == TaskState.TASK_STATE_COMPLETED
        assert len(completed.artifacts) == 1
        assert completed.artifacts[0].parts[0].text.startswith("# Result")
        assert completed.artifacts[0].parts[1].HasField("data")
        assert "session" not in str(completed).lower()
        assert "A2A structured input (untrusted data)" in created[0].prompts[0]
        assert created[0].closed is True

    asyncio.run(run())


def test_handler_cancellation_reaches_coquo_turn_token(tmp_path: Path) -> None:
    created: list[_Session] = []

    def factory(workspace: Path) -> _Session:
        session = _Session(workspace, wait_for_cancel=True)
        created.append(session)
        return session

    async def run() -> None:
        handler = CoquoA2AHandler(tmp_path, session_factory=factory)
        task = await handler.on_message_send(_request(Part(text="long task")), _Context())
        canceled = await handler.on_cancel_task(type("Request", (), {"id": task.id})(), _Context())
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED
        binding = handler._tasks[("anonymous", "tenant-a", task.id)]
        assert binding.runner is not None
        await binding.runner
        assert created[0].closed is True

    asyncio.run(run())


def test_handler_rejects_file_parts_before_host_prompt(tmp_path: Path) -> None:
    async def run() -> None:
        handler = CoquoA2AHandler(tmp_path, session_factory=lambda workspace: _Session(workspace))
        with pytest.raises(Exception, match="accepts only text and application/json"):
            await handler.on_message_send(_request(Part(raw=b"document")), _Context())

    asyncio.run(run())


def test_handler_replays_same_message_id_idempotently(tmp_path: Path) -> None:
    async def run() -> None:
        handler = CoquoA2AHandler(tmp_path, session_factory=lambda workspace: _Session(workspace))
        first = await handler.on_message_send(_request(Part(text="same request")), _Context())
        second = await handler.on_message_send(_request(Part(text="same request")), _Context())
        assert first.id == second.id
        await handler._tasks[("anonymous", "tenant-a", first.id)].runner

    asyncio.run(run())


def test_handler_rejects_same_message_id_with_different_content(tmp_path: Path) -> None:
    async def run() -> None:
        handler = CoquoA2AHandler(tmp_path, session_factory=lambda workspace: _Session(workspace))
        await handler.on_message_send(_request(Part(text="first request")), _Context())
        with pytest.raises(ValueError, match="different message content"):
            await handler.on_message_send(_request(Part(text="different request")), _Context())

    asyncio.run(run())


def test_handler_idempotency_survives_reusing_mutated_request_object(tmp_path: Path) -> None:
    async def run() -> None:
        handler = CoquoA2AHandler(tmp_path, session_factory=lambda workspace: _Session(workspace))
        request = _request(Part(text="same request"))
        first = await handler.on_message_send(request, _Context())
        second = await handler.on_message_send(request, _Context())
        assert first.id == second.id

    asyncio.run(run())


def test_a2a_task_store_replays_and_handler_marks_inflight_recovery(tmp_path: Path) -> None:
    async def run() -> None:
        store = A2ATaskStore(tmp_path)
        task = Task(
            id="task-restart",
            context_id="ctx-restart",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        await store.append("accepted", task, owner="operator", tenant="tenant-a")
        handler = CoquoA2AHandler(tmp_path, session_factory=lambda workspace: _Session(workspace))
        await handler.restore()
        restored = await handler.on_get_task(
            type("Request", (), {"id": "task-restart"})(),
            type("Context", (), {"state": {"a2a_owner": "operator"}, "tenant": "tenant-a"})(),
        )
        assert restored.status.state == TaskState.TASK_STATE_FAILED
        assert "recovery required" in restored.status.message.parts[0].text

    asyncio.run(run())
