"""Bounded MCP Resources, subscriptions, and non-authoritative Prompts clients."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
from urllib.parse import urlsplit

from leonervis_code.mcp.client import McpClientError, McpNotificationSummary


MAX_MCP_CAPABILITY_PAGES = 16
MAX_MCP_RESOURCES = 256
MAX_MCP_RESOURCE_CONTENTS = 32
MAX_MCP_RESOURCE_TEXT_BYTES = 64 * 1024
MAX_MCP_RESOURCE_BLOB_BYTES = 16 * 1024 * 1024
MAX_MCP_PROMPTS = 256
MAX_MCP_PROMPT_ARGUMENTS = 64
MAX_MCP_PROMPT_MESSAGES = 64
MAX_MCP_PROMPT_OUTPUT_BYTES = 64 * 1024
MAX_MCP_URI_CHARACTERS = 4096
MAX_MCP_FIELD_CHARACTERS = 8192


@dataclass(frozen=True)
class McpResourceDescriptor:
    uri: str
    name: str
    title: str | None
    description: str | None
    mime_type: str | None
    size: int | None


@dataclass(frozen=True)
class McpResourceReadResult:
    uri: str
    content: str
    blocks: int
    truncated: bool
    notifications: McpNotificationSummary


@dataclass(frozen=True)
class McpPromptArgument:
    name: str
    description: str | None
    required: bool


@dataclass(frozen=True)
class McpPromptDescriptor:
    name: str
    title: str | None
    description: str | None
    arguments: tuple[McpPromptArgument, ...]


@dataclass(frozen=True)
class McpPromptResult:
    name: str
    description: str | None
    content: str
    messages: int
    truncated: bool
    notifications: McpNotificationSummary


class McpCapabilityClient:
    """Issue bounded capability requests through one initialized transport session."""

    def __init__(self, session) -> None:  # noqa: ANN001
        self._session = session

    def list_resources(self) -> tuple[McpResourceDescriptor, ...]:
        self._require("resources")
        resources: list[McpResourceDescriptor] = []
        seen_uris: set[str] = set()
        for result in self._pages("resources/list", "resources"):
            raw_resources = result["resources"]
            if not isinstance(raw_resources, list):
                raise McpClientError("mcp_resources_invalid", "MCP resources list is invalid")
            for value in raw_resources:
                descriptor = _parse_resource(value)
                if descriptor.uri in seen_uris:
                    raise McpClientError(
                        "mcp_resources_duplicate", "MCP resource URI is duplicated"
                    )
                seen_uris.add(descriptor.uri)
                resources.append(descriptor)
                if len(resources) > MAX_MCP_RESOURCES:
                    raise McpClientError("mcp_resources_limit", "MCP resource limit exceeded")
        return tuple(resources)

    def read_resource(self, uri: str) -> McpResourceReadResult:
        self._require("resources")
        canonical = _uri(uri)
        result, notifications = self._session.request(
            "resources/read",
            {"uri": canonical},
        )
        if not isinstance(result, dict) or set(result) - {"contents", "_meta"}:
            raise McpClientError("mcp_resource_invalid", "MCP resource result is invalid")
        contents = result.get("contents")
        if not isinstance(contents, list) or len(contents) > MAX_MCP_RESOURCE_CONTENTS:
            raise McpClientError("mcp_resource_invalid", "MCP resource contents are invalid")
        parts: list[str] = []
        for value in contents:
            parts.append(_normalize_resource_content(value))
        content, truncated = _bounded("\n".join(parts), MAX_MCP_RESOURCE_TEXT_BYTES)
        return McpResourceReadResult(canonical, content, len(contents), truncated, notifications)

    def subscribe_resource(self, uri: str) -> McpNotificationSummary:
        self._require("resources")
        _, notifications = self._session.request("resources/subscribe", {"uri": _uri(uri)})
        return notifications

    def unsubscribe_resource(self, uri: str) -> McpNotificationSummary:
        self._require("resources")
        _, notifications = self._session.request("resources/unsubscribe", {"uri": _uri(uri)})
        return notifications

    def list_prompts(self) -> tuple[McpPromptDescriptor, ...]:
        self._require("prompts")
        prompts: list[McpPromptDescriptor] = []
        seen: set[str] = set()
        for result in self._pages("prompts/list", "prompts"):
            raw_prompts = result["prompts"]
            if not isinstance(raw_prompts, list):
                raise McpClientError("mcp_prompts_invalid", "MCP prompts list is invalid")
            for value in raw_prompts:
                prompt = _parse_prompt(value)
                if prompt.name in seen:
                    raise McpClientError("mcp_prompts_duplicate", "MCP prompt name is duplicated")
                seen.add(prompt.name)
                prompts.append(prompt)
                if len(prompts) > MAX_MCP_PROMPTS:
                    raise McpClientError("mcp_prompts_limit", "MCP prompt limit exceeded")
        return tuple(prompts)

    def get_prompt(self, name: str, arguments: dict[str, str]) -> McpPromptResult:
        self._require("prompts")
        prompt_name = _inline_text(name, "MCP prompt name", 256)
        if not isinstance(arguments, dict) or len(arguments) > MAX_MCP_PROMPT_ARGUMENTS:
            raise McpClientError("mcp_prompt_arguments_invalid", "MCP prompt arguments are invalid")
        canonical_arguments: dict[str, str] = {}
        for key, value in sorted(arguments.items()):
            canonical_arguments[_inline_text(key, "MCP prompt argument name", 256)] = _text(
                value, "MCP prompt argument value"
            )
        result, notifications = self._session.request(
            "prompts/get",
            {"arguments": canonical_arguments, "name": prompt_name},
        )
        if not isinstance(result, dict) or set(result) - {"description", "messages", "_meta"}:
            raise McpClientError("mcp_prompt_invalid", "MCP prompt result is invalid")
        description = _optional_text(result.get("description"), "MCP prompt description")
        messages = result.get("messages")
        if not isinstance(messages, list) or len(messages) > MAX_MCP_PROMPT_MESSAGES:
            raise McpClientError("mcp_prompt_invalid", "MCP prompt messages are invalid")
        rendered = [_normalize_prompt_message(value) for value in messages]
        content, truncated = _bounded("\n\n".join(rendered), MAX_MCP_PROMPT_OUTPUT_BYTES)
        return McpPromptResult(
            prompt_name,
            description,
            content,
            len(messages),
            truncated,
            notifications,
        )

    def _pages(self, method: str, field: str):  # noqa: ANN202
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(MAX_MCP_CAPABILITY_PAGES):
            params: dict[str, object] = {} if cursor is None else {"cursor": cursor}
            result, _ = self._session.request(method, params)
            if not isinstance(result, dict) or field not in result:
                raise McpClientError("mcp_capability_invalid", "MCP paged result is invalid")
            yield result
            raw_cursor = result.get("nextCursor")
            if raw_cursor is None:
                return
            cursor = _text(raw_cursor, "MCP pagination cursor", 1024)
            if cursor in seen:
                raise McpClientError("mcp_cursor_repeated", "MCP pagination cursor repeated")
            seen.add(cursor)
        raise McpClientError("mcp_page_limit", "MCP capability page limit exceeded")

    def _require(self, capability: str) -> None:
        if capability not in self._session.capability_names:
            raise McpClientError(
                "mcp_capability_unavailable",
                f"MCP server does not advertise {capability}",
            )


def _parse_resource(value: object) -> McpResourceDescriptor:
    if not isinstance(value, dict) or set(value) - {
        "uri",
        "name",
        "title",
        "description",
        "mimeType",
        "size",
        "annotations",
        "icons",
        "_meta",
    }:
        raise McpClientError("mcp_resource_invalid", "MCP resource descriptor is invalid")
    size = value.get("size")
    if size is not None and (type(size) is not int or size < 0 or size > 2**63 - 1):
        raise McpClientError("mcp_resource_invalid", "MCP resource size is invalid")
    return McpResourceDescriptor(
        _uri(value.get("uri")),
        _inline_text(value.get("name"), "MCP resource name"),
        _optional_text(value.get("title"), "MCP resource title"),
        _optional_text(value.get("description"), "MCP resource description"),
        _optional_text(value.get("mimeType"), "MCP resource MIME type", 256),
        size,
    )


def _normalize_resource_content(value: object) -> str:
    if not isinstance(value, dict) or set(value) - {"uri", "mimeType", "text", "blob", "_meta"}:
        raise McpClientError("mcp_resource_invalid", "MCP resource content is invalid")
    uri = _uri(value.get("uri"))
    mime = _optional_text(value.get("mimeType"), "MCP resource MIME type", 256)
    has_text = "text" in value
    has_blob = "blob" in value
    if has_text == has_blob:
        raise McpClientError("mcp_resource_invalid", "MCP resource content type is invalid")
    header = json.dumps({"mime_type": mime, "uri": uri}, sort_keys=True, ensure_ascii=False)
    if has_text:
        return f"{header}\n{_text(value['text'], 'MCP resource text', MAX_MCP_RESOURCE_TEXT_BYTES)}"
    blob = value["blob"]
    if not isinstance(blob, str):
        raise McpClientError("mcp_resource_invalid", "MCP resource blob is invalid")
    try:
        decoded = base64.b64decode(blob, validate=True)
    except (ValueError, binascii.Error):
        raise McpClientError("mcp_resource_invalid", "MCP resource blob is invalid") from None
    if len(decoded) > MAX_MCP_RESOURCE_BLOB_BYTES:
        raise McpClientError("mcp_resource_limit", "MCP resource blob exceeds the limit")
    return f"{header}\n[binary resource: {len(decoded)} bytes]"


def _parse_prompt(value: object) -> McpPromptDescriptor:
    if not isinstance(value, dict) or set(value) - {
        "name",
        "title",
        "description",
        "arguments",
        "icons",
        "_meta",
    }:
        raise McpClientError("mcp_prompt_invalid", "MCP prompt descriptor is invalid")
    raw_arguments = value.get("arguments", [])
    if not isinstance(raw_arguments, list) or len(raw_arguments) > MAX_MCP_PROMPT_ARGUMENTS:
        raise McpClientError("mcp_prompt_invalid", "MCP prompt arguments are invalid")
    arguments: list[McpPromptArgument] = []
    seen: set[str] = set()
    for raw in raw_arguments:
        if not isinstance(raw, dict) or set(raw) - {"name", "description", "required"}:
            raise McpClientError("mcp_prompt_invalid", "MCP prompt argument is invalid")
        name = _text(raw.get("name"), "MCP prompt argument name", 256)
        required = raw.get("required", False)
        if type(required) is not bool or name in seen:
            raise McpClientError("mcp_prompt_invalid", "MCP prompt argument is invalid")
        seen.add(name)
        arguments.append(
            McpPromptArgument(
                name,
                _optional_text(raw.get("description"), "MCP prompt argument description"),
                required,
            )
        )
    return McpPromptDescriptor(
        _inline_text(value.get("name"), "MCP prompt name", 256),
        _optional_text(value.get("title"), "MCP prompt title"),
        _optional_text(value.get("description"), "MCP prompt description"),
        tuple(arguments),
    )


def _normalize_prompt_message(value: object) -> str:
    if not isinstance(value, dict) or set(value) - {"role", "content"}:
        raise McpClientError("mcp_prompt_invalid", "MCP prompt message is invalid")
    role = value.get("role")
    if role not in {"user", "assistant"}:
        raise McpClientError("mcp_prompt_invalid", "MCP prompt role is invalid")
    content = value.get("content")
    if (
        not isinstance(content, dict)
        or content.get("type") != "text"
        or set(content)
        - {
            "type",
            "text",
            "annotations",
            "_meta",
        }
    ):
        raise McpClientError(
            "mcp_prompt_content_unsupported",
            "MCP prompt content type is unsupported",
        )
    return f"[{role}] {_text(content.get('text'), 'MCP prompt text')}"


def _uri(value: object) -> str:
    text = _inline_text(value, "MCP resource URI", MAX_MCP_URI_CHARACTERS)
    parsed = urlsplit(text)
    if not parsed.scheme or parsed.fragment:
        raise McpClientError("mcp_resource_uri_invalid", "MCP resource URI is invalid")
    return text


def _inline_text(value: object, label: str, limit: int = MAX_MCP_FIELD_CHARACTERS) -> str:
    text = _text(value, label, limit)
    if any(ord(character) < 0x21 or ord(character) == 0x7F for character in text):
        raise McpClientError("mcp_field_invalid", f"{label} is invalid")
    return text


def _optional_text(value: object, label: str, limit: int = MAX_MCP_FIELD_CHARACTERS) -> str | None:
    return None if value is None else _text(value, label, limit)


def _text(value: object, label: str, limit: int = MAX_MCP_FIELD_CHARACTERS) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or len(value.encode("utf-8")) > max(limit * 4, MAX_MCP_FIELD_CHARACTERS)
        or any(ord(character) < 0x20 and character not in "\n\t" for character in value)
    ):
        raise McpClientError("mcp_field_invalid", f"{label} is invalid")
    return value


def _bounded(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    marker = b"\n[truncated]\n"
    selected = encoded[: limit - len(marker)]
    return selected.decode("utf-8", errors="ignore") + marker.decode("ascii"), True
