from __future__ import annotations

import json
import os
import sys
import time


MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"


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
                "page-2" if MODE in {"normal", "repeated-cursor", "duplicate-tool"} else None
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
