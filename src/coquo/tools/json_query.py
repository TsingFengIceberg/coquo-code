"""Bounded RFC 6901 JSON Pointer reads from one workspace file."""

from __future__ import annotations

import json
from pathlib import Path

from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.tools._workspace_files import read_workspace_regular_file
from coquo.tools._workspace_paths import WorkspacePathFailure

JSON_QUERY_TOOL_NAME = "json_query"
MAX_JSON_QUERY_SOURCE_BYTES = 1024 * 1024
MAX_JSON_POINTER_SEGMENTS = 128
MAX_JSON_QUERY_OUTPUT_BYTES = 32 * 1024


def json_query_model_definition() -> dict[str, object]:
    return {
        "name": JSON_QUERY_TOOL_NAME,
        "description": (
            "Read one value from an existing workspace UTF-8 JSON file using an RFC 6901 JSON "
            "Pointer. Returns one bounded canonical JSON object and rejects duplicate object keys."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative JSON file."},
                "pointer": {
                    "type": "string",
                    "description": "RFC 6901 pointer; use an empty string for the document root.",
                },
            },
            "required": ["path", "pointer"],
            "additionalProperties": False,
        },
    }


def json_query_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(json_query_model_definition())


class JsonQueryTool:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if request.name != JSON_QUERY_TOOL_NAME or set(arguments) != {"path", "pointer"}:
                raise ValueError
            path = arguments["path"]
            pointer = arguments["pointer"]
            if not isinstance(path, str) or not isinstance(pointer, str):
                raise ValueError
            snapshot = read_workspace_regular_file(
                self._workspace,
                path,
                tool_name=JSON_QUERY_TOOL_NAME,
                max_bytes=MAX_JSON_QUERY_SOURCE_BYTES,
            )
            document = _load_json(snapshot.data)
            value = _resolve_pointer(document, pointer)
            content = (
                json.dumps(
                    {"pointer": pointer, "value": value},
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )
            if len(content.encode("utf-8")) > MAX_JSON_QUERY_OUTPUT_BYTES:
                raise WorkspacePathFailure("json_query selected value exceeds the output limit")
        except (AttributeError, ValueError):
            return ToolResult(request.tool_use_id, "json_query input is malformed", is_error=True)
        except WorkspacePathFailure as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        return ToolResult(request.tool_use_id, content)


def _load_json(data: bytes) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkspacePathFailure("json_query content is not valid UTF-8") from None

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise WorkspacePathFailure("json_query content contains duplicate object keys")
            value[key] = item
        return value

    def reject_constant(_value: str) -> object:
        raise WorkspacePathFailure("json_query content contains a non-finite number")

    try:
        return json.loads(text, object_pairs_hook=object_pairs, parse_constant=reject_constant)
    except WorkspacePathFailure:
        raise
    except (json.JSONDecodeError, RecursionError):
        raise WorkspacePathFailure("json_query content is not valid bounded JSON") from None


def _resolve_pointer(document: object, pointer: str) -> object:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise WorkspacePathFailure("json_query pointer must be empty or start with '/'")
    raw_segments = pointer[1:].split("/")
    if len(raw_segments) > MAX_JSON_POINTER_SEGMENTS:
        raise WorkspacePathFailure("json_query pointer has too many segments")
    current = document
    for raw in raw_segments:
        segment = _decode_pointer_segment(raw)
        if isinstance(current, dict):
            if segment not in current:
                raise WorkspacePathFailure("json_query pointer does not exist")
            current = current[segment]
        elif isinstance(current, list):
            if (
                not segment.isascii()
                or not segment.isdecimal()
                or (len(segment) > 1 and segment.startswith("0"))
            ):
                raise WorkspacePathFailure("json_query pointer has an invalid array index")
            index = int(segment)
            if index >= len(current):
                raise WorkspacePathFailure("json_query pointer does not exist")
            current = current[index]
        else:
            raise WorkspacePathFailure("json_query pointer traverses a scalar value")
    return current


def _decode_pointer_segment(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "~":
            output.append(value[index])
            index += 1
            continue
        if index + 1 >= len(value) or value[index + 1] not in {"0", "1"}:
            raise WorkspacePathFailure("json_query pointer contains an invalid escape")
        output.append("~" if value[index + 1] == "0" else "/")
        index += 2
    return "".join(output)
