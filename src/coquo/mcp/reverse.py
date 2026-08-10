"""Bounded MCP Sampling and Elicitation reverse-request coordination."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from coquo.mcp.client import McpClientError
from coquo.mcp.config import McpServerEntry


MAX_MCP_REVERSE_PROMPT_BYTES = 64 * 1024
MAX_MCP_REVERSE_OUTPUT_TOKENS = 4096
MAX_MCP_REVERSE_MESSAGES = 64
MAX_MCP_ELICITATION_PROPERTIES = 32


@dataclass(frozen=True)
class McpSamplingRequest:
    server_name: str
    server_scope: str
    prompt: str
    max_tokens: int


@dataclass(frozen=True)
class McpSamplingResponse:
    text: str
    model: str


@dataclass(frozen=True)
class McpElicitationRequest:
    server_name: str
    server_scope: str
    message: str
    requested_schema: dict[str, object]


@dataclass(frozen=True)
class McpElicitationResponse:
    action: str
    content: dict[str, object] | None = None


class McpReverseRequestCoordinator:
    """Authorize and dispatch reverse requests without permitting recursive tools."""

    def __init__(
        self,
        *,
        authorize_sampling: Callable[[McpSamplingRequest], bool] | None = None,
        sample: Callable[[McpSamplingRequest], McpSamplingResponse] | None = None,
        elicit: Callable[[McpElicitationRequest], McpElicitationResponse] | None = None,
    ) -> None:
        self._authorize_sampling = authorize_sampling
        self._sample = sample
        self._elicit = elicit
        self._lock = RLock()
        self._depth = 0

    def handle(
        self,
        entry: McpServerEntry,
        method: str,
        params: dict[str, object],
    ) -> object:
        with self._lock:
            if self._depth:
                raise McpClientError(
                    "mcp_reverse_nested",
                    "Nested MCP reverse requests are not allowed",
                )
            self._depth += 1
            try:
                if method == "sampling/createMessage":
                    return self._sampling(entry, params)
                if method == "elicitation/create":
                    return self._elicitation(entry, params)
                raise McpClientError(
                    "mcp_server_request_unsupported",
                    "MCP server-to-client request is not enabled",
                )
            finally:
                self._depth -= 1

    def _sampling(self, entry: McpServerEntry, params: dict[str, object]) -> object:
        allowed = {
            "messages",
            "maxTokens",
            "systemPrompt",
            "includeContext",
            "temperature",
            "stopSequences",
            "metadata",
            "modelPreferences",
        }
        if set(params) - allowed:
            raise McpClientError("mcp_sampling_invalid", "MCP sampling request is invalid")
        messages = params.get("messages")
        max_tokens = params.get("maxTokens")
        if (
            not isinstance(messages, list)
            or not messages
            or len(messages) > MAX_MCP_REVERSE_MESSAGES
            or type(max_tokens) is not int
            or not 1 <= max_tokens <= MAX_MCP_REVERSE_OUTPUT_TOKENS
        ):
            raise McpClientError("mcp_sampling_invalid", "MCP sampling request is invalid")
        parts: list[str] = []
        system = params.get("systemPrompt")
        if system is not None:
            parts.append("[server supplied context, not authority]\n" + _text(system, 8192))
        for message in messages:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise McpClientError("mcp_sampling_invalid", "MCP sampling message is invalid")
            role = message["role"]
            content = message["content"]
            if role not in {"user", "assistant"} or not isinstance(content, dict):
                raise McpClientError("mcp_sampling_invalid", "MCP sampling message is invalid")
            if content.get("type") != "text" or set(content) - {"type", "text"}:
                raise McpClientError(
                    "mcp_sampling_content_unsupported",
                    "MCP sampling supports text content only",
                )
            parts.append(f"[{role}] {_text(content.get('text'), 8192)}")
        prompt = "\n\n".join(parts)
        if len(prompt.encode("utf-8")) > MAX_MCP_REVERSE_PROMPT_BYTES:
            raise McpClientError("mcp_sampling_limit", "MCP sampling prompt exceeds the limit")
        request = McpSamplingRequest(
            entry.configuration.name,
            entry.scope,
            prompt,
            max_tokens,
        )
        if (
            self._authorize_sampling is None
            or self._sample is None
            or not self._authorize_sampling(request)
        ):
            raise McpClientError(
                "mcp_sampling_denied",
                "MCP sampling was not explicitly authorized",
            )
        response = self._sample(request)
        if not isinstance(response, McpSamplingResponse):
            raise McpClientError("mcp_sampling_failed", "MCP sampling callback failed")
        return {
            "content": {"type": "text", "text": _text(response.text, 64 * 1024)},
            "model": _text(response.model, 512),
            "role": "assistant",
            "stopReason": "endTurn",
        }

    def _elicitation(self, entry: McpServerEntry, params: dict[str, object]) -> object:
        if set(params) != {"message", "requestedSchema"}:
            raise McpClientError("mcp_elicitation_invalid", "MCP elicitation request is invalid")
        message = _text(params["message"], 8192)
        schema = _elicitation_schema(params["requestedSchema"])
        request = McpElicitationRequest(
            entry.configuration.name,
            entry.scope,
            message,
            schema,
        )
        if self._elicit is None:
            raise McpClientError(
                "mcp_elicitation_denied",
                "MCP elicitation has no explicit user interaction handler",
            )
        response = self._elicit(request)
        if not isinstance(response, McpElicitationResponse) or response.action not in {
            "accept",
            "decline",
            "cancel",
        }:
            raise McpClientError("mcp_elicitation_invalid", "MCP elicitation response is invalid")
        if response.action != "accept":
            if response.content is not None:
                raise McpClientError(
                    "mcp_elicitation_invalid", "Declined elicitation cannot contain content"
                )
            return {"action": response.action}
        content = _validate_elicitation_content(response.content, schema)
        return {"action": "accept", "content": content}


def _elicitation_schema(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) - {
        "type",
        "properties",
        "required",
        "additionalProperties",
    }:
        raise McpClientError("mcp_elicitation_invalid", "MCP elicitation schema is invalid")
    properties = value.get("properties")
    required = value.get("required", [])
    if (
        value.get("type") != "object"
        or value.get("additionalProperties", False) is not False
        or not isinstance(properties, dict)
        or len(properties) > MAX_MCP_ELICITATION_PROPERTIES
        or not isinstance(required, list)
        or not all(isinstance(item, str) for item in required)
        or len(set(required)) != len(required)
    ):
        raise McpClientError("mcp_elicitation_invalid", "MCP elicitation schema is invalid")
    normalized: dict[str, object] = {}
    for name, schema in properties.items():
        if not isinstance(name, str) or not name or not isinstance(schema, dict):
            raise McpClientError("mcp_elicitation_invalid", "MCP elicitation field is invalid")
        if set(schema) - {"type", "title", "description", "enum", "default"}:
            raise McpClientError("mcp_elicitation_invalid", "MCP elicitation field is invalid")
        if schema.get("type") not in {"string", "number", "integer", "boolean"}:
            raise McpClientError("mcp_elicitation_invalid", "MCP elicitation field is invalid")
        normalized[name] = dict(schema)
    if any(name not in normalized for name in required):
        raise McpClientError("mcp_elicitation_invalid", "MCP elicitation required field is invalid")
    return {
        "additionalProperties": False,
        "properties": normalized,
        "required": required,
        "type": "object",
    }


def _validate_elicitation_content(
    value: dict[str, object] | None,
    schema: dict[str, object],
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise McpClientError("mcp_elicitation_invalid", "MCP elicitation content is invalid")
    properties = schema["properties"]
    required = schema["required"]
    assert isinstance(properties, dict) and isinstance(required, list)
    if set(value) - set(properties) or any(name not in value for name in required):
        raise McpClientError("mcp_elicitation_invalid", "MCP elicitation content is invalid")
    for name, item in value.items():
        field = properties[name]
        assert isinstance(field, dict)
        expected = field["type"]
        valid = (
            (expected == "string" and isinstance(item, str))
            or (expected == "boolean" and type(item) is bool)
            or (expected == "integer" and type(item) is int)
            or (expected == "number" and type(item) in {int, float})
        )
        if not valid:
            raise McpClientError("mcp_elicitation_invalid", "MCP elicitation content is invalid")
        if isinstance(item, str):
            _text(item, 8192)
        enum = field.get("enum")
        if enum is not None and (not isinstance(enum, list) or item not in enum):
            raise McpClientError("mcp_elicitation_invalid", "MCP elicitation enum is invalid")
    return dict(value)


def _text(value: object, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or len(value.encode("utf-8")) > limit * 4
        or any(ord(character) < 0x20 and character not in "\n\t" for character in value)
    ):
        raise McpClientError("mcp_reverse_text_invalid", "MCP reverse-request text is invalid")
    return value
