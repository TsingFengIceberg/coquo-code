from __future__ import annotations

from io import StringIO
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from coquo.interfaces import (
    IDEJsonRpcBridge,
    InterfaceEvent,
    InterfaceResponse,
    LocalWebBridge,
)


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
