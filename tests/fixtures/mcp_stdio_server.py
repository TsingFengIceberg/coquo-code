from __future__ import annotations

import json
import os
import sys
import time


MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
CALLS = 0
TOOL_MODES = {
    "normal",
    "call-error",
    "call-rpc-error",
    "call-timeout",
    "call-invalid-result",
    "call-oversized",
    "call-unsupported-content",
    "call-notifications",
    "call-malformed-notification",
    "call-notification-flood",
    "call-list-changed-rpc-error",
}


def send(value: object) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(request_id: object, value: object) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": value})


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        if MODE == "timeout":
            time.sleep(30)
            continue
        if MODE == "malformed-json":
            sys.stdout.write('{"jsonrpc":"2.0","id":1,"result":NaN}\n')
            sys.stdout.flush()
            continue
        if MODE == "duplicate-key":
            sys.stdout.write('{"jsonrpc":"2.0","id":1,"id":1,"result":{}}\n')
            sys.stdout.flush()
            continue
        if MODE == "wrong-id":
            result(999, {})
            continue
        if MODE == "server-request":
            send({"jsonrpc": "2.0", "id": "server-1", "method": "roots/list"})
            continue
        if MODE == "rpc-error":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32000, "message": "SECRET_SERVER_ERROR"},
                }
            )
            continue
        capabilities = {} if MODE == "no-tools" else {"tools": {}}
        protocol = "1900-01-01" if MODE == "unsupported-version" else "2025-06-18"
        result(
            request["id"],
            {
                "protocolVersion": protocol,
                "capabilities": capabilities,
                "serverInfo": {"name": "fixture-server", "version": "1.2.3"},
                "instructions": "UNTRUSTED_INSTRUCTIONS",
            },
        )
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        if MODE == "stderr":
            os.write(2, b"SECRET_STDERR\n" * 4096)
        cursor = request.get("params", {}).get("cursor")
        if cursor is None:
            next_cursor = (
                "page-2" if MODE in TOOL_MODES | {"repeated-cursor", "duplicate-tool"} else None
            )
            tools = [
                {
                    "name": "read_widget",
                    "description": "UNTRUSTED_TOOL_DESCRIPTION",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"widget": {"type": "string"}},
                    },
                    "annotations": {"readOnlyHint": True},
                }
            ]
        else:
            next_cursor = "page-2" if MODE == "repeated-cursor" else None
            name = "read_widget" if MODE == "duplicate-tool" else "list_widgets"
            tools = [{"name": name, "inputSchema": {"type": "object"}}]
        payload = {"tools": tools}
        if next_cursor is not None:
            payload["nextCursor"] = next_cursor
        result(request["id"], payload)
    elif method == "tools/call":
        CALLS += 1
        if MODE == "call-notifications":
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": "token-1",
                        "progress": 1,
                        "total": 2,
                        "message": "SECRET_PROGRESS",
                    },
                }
            )
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/message",
                    "params": {"level": "info", "data": "SECRET_LOG_DATA"},
                }
            )
            send({"jsonrpc": "2.0", "method": "notifications/unknown", "params": {}})
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        if MODE == "call-malformed-notification":
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progress": "SECRET_INVALID"},
                }
            )
            continue
        if MODE == "call-notification-flood":
            for _ in range(257):
                send({"jsonrpc": "2.0", "method": "notifications/unknown", "params": {}})
            continue
        if MODE == "call-list-changed-rpc-error":
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32000, "message": "SECRET_CALL_ERROR"},
                }
            )
            continue
        if MODE == "call-timeout":
            time.sleep(30)
            continue
        if MODE == "call-rpc-error":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32000, "message": "SECRET_CALL_ERROR"},
                }
            )
            continue
        if MODE == "call-invalid-result":
            result(request["id"], {"content": "not-an-array"})
            continue
        if MODE == "call-unsupported-content":
            result(request["id"], {"content": [{"type": "video", "data": "SECRET"}]})
            continue
        if MODE == "call-oversized":
            result(
                request["id"],
                {"content": [{"type": "text", "text": "x" * 8192} for _ in range(9)]},
            )
            continue
        arguments = request.get("params", {}).get("arguments", {})
        tool_name = request.get("params", {}).get("name")
        if MODE == "call-error":
            result(
                request["id"],
                {"content": [{"type": "text", "text": "fixture error"}], "isError": True},
            )
            continue
        result(
            request["id"],
            {
                "content": [
                    {
                        "type": "text",
                        "text": f"called {tool_name} #{CALLS}",
                    }
                ],
                "structuredContent": {"arguments": arguments, "calls": CALLS},
            },
        )
