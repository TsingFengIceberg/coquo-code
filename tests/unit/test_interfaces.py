from __future__ import annotations

from io import StringIO
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from coquo.interfaces import (
    IDEJsonRpcBridge,
    InterfaceEvent,
    InterfaceResponse,
    LocalWebBridge,
    ProjectSessionManager,
)
from coquo.observability import ObservationContext, ObservationStream


def response(request_id: str, prompt: str) -> InterfaceResponse:
    return InterfaceResponse(request_id, "completed", text=f"echo:{prompt}")


def test_ide_json_rpc_bridge_handles_prompt_and_events() -> None:
    events = (InterfaceEvent("turn_started", {"turn": 1}, 0),)
    bridge = IDEJsonRpcBridge(response, event_source=lambda: events)
    prompt = json.loads(
        bridge.handle_line('{"id":"r1","method":"prompt","params":{"prompt":"hi"}}')
    )
    assert prompt["text"] == "echo:hi"
    listed = json.loads(bridge.handle_line('{"id":"r2","method":"events","params":{}}'))
    assert listed["events"][0]["kind"] == "turn_started"
    invalid = json.loads(bridge.handle_line('{"id":"r3","method":"unknown","params":{}}'))
    assert invalid["outcome"] == "invalid-request"


def test_local_web_bridge_is_loopback_only_and_bearer_protected() -> None:
    bridge = LocalWebBridge(response, bearer_token="t" * 16)
    try:
        host, port = bridge.start()
    except PermissionError:
        pytest.skip("socket creation is unavailable in the test sandbox")
    try:
        request = Request(
            f"http://{host}:{port}/v1/prompt",
            data=json.dumps({"id": "r1", "prompt": "hi"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 401
        request.add_header("Authorization", "Bearer " + "t" * 16)
        with urlopen(request) as result:
            payload = json.loads(result.read())
        assert payload["text"] == "echo:hi"
    finally:
        bridge.close()


def test_ide_bridge_serves_line_protocol_with_flush() -> None:
    bridge = IDEJsonRpcBridge(response)
    input_stream = StringIO('{"id":"r1","method":"prompt","params":{"prompt":"ok"}}\n')
    output_stream = StringIO()
    bridge.serve(input_stream, output_stream)
    assert json.loads(output_stream.getvalue())["outcome"] == "completed"


class _ManagedFakeSession:
    def __init__(self, workspace) -> None:
        self.workspace = workspace
        self.session_id = str(uuid4())
        self.observation_stream = ObservationStream(
            source_id=self.session_id,
            context=ObservationContext.new(session_id=self.session_id),
        )
        self.closed = False

    def prompt(self, prompt, *, cancellation):
        cancellation.check()
        self.observation_stream.publish(
            record_type="turn_started",
            status="started",
            summary="turn started",
        )
        cancellation.check()
        self.observation_stream.publish(
            record_type="turn_finished",
            status="completed",
            summary="turn finished",
        )
        return f"handled:{prompt}"

    def close(self):
        self.closed = True


def test_project_session_manager_runs_real_session_and_exposes_live_cursor(tmp_path) -> None:
    created: list[_ManagedFakeSession] = []

    def factory(workspace):
        session = _ManagedFakeSession(workspace)
        created.append(session)
        return session

    manager = ProjectSessionManager(tmp_path, session_factory=factory)
    created_response = manager.create()
    assert created_response.session_id == created[0].session_id
    started = manager.start_prompt("r1", "hello", session_id=created[0].session_id)
    assert started.outcome == "started"
    assert started.turn_id is not None
    finished = manager.wait("r2", started.turn_id, timeout=2)
    assert finished.outcome == "completed"
    assert finished.text == "handled:hello"
    events = manager.events(session_id=created[0].session_id, after=-1)
    assert [event.sequence for event in events] == [0, 1]
    assert all("prompt" not in event.payload for event in events)
    bridge = IDEJsonRpcBridge(session_manager=manager)
    listed = json.loads(bridge.handle_line('{"id":"r3","method":"session_list"}'))
    assert created[0].session_id in json.loads(listed["text"])["sessions"]
    polled = json.loads(
        bridge.handle_line(
            json.dumps(
                {
                    "id": "r4",
                    "method": "events",
                    "params": {"session_id": created[0].session_id, "after": 0},
                }
            )
        )
    )
    assert [event["sequence"] for event in polled["events"]] == [1]
    manager.close(created[0].session_id)
    assert created[0].closed is True
