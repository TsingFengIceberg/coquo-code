"""Generation-bound MCP process reuse, tools/call dispatch, and result normalization."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, replace
from enum import StrEnum
import json
import re
from threading import RLock

from leonervis_code.core.cancellation import TurnCancellation
from leonervis_code.mcp.catalog import McpCandidateDisposition, McpToolCandidate
from leonervis_code.mcp.client import (
    McpClientError,
    McpLiveProcessStatus,
    McpStdioClient,
    McpStdioSession,
    McpToolCallResult,
)
from leonervis_code.mcp.config import McpServerEntry, McpServerStore


MAX_MCP_ACTIVE_PROCESSES = 8
MAX_MCP_CALLS_PER_PROCESS = 128
MAX_MCP_RESULT_BLOCKS = 64
MAX_MCP_RESULT_OUTPUT_BYTES = 64 * 1024
MAX_MCP_RESULT_FIELD_CHARACTERS = 8192


class McpRuntimeOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class McpCallPreparationError(ValueError):
    """One model request does not satisfy its frozen MCP input contract."""


@dataclass(frozen=True)
class PreparedMcpCall:
    candidate: McpToolCandidate
    catalog_id: str
    arguments: dict[str, object]
    precondition_sha256: str


@dataclass(frozen=True)
class McpRuntimeExecution:
    content: str
    is_error: bool
    truncated: bool
    outcome: McpRuntimeOutcome
    result_code: str
    audit_message: str
    duration_ms: int | None = None
    process_generation: int | None = None
    process_reused: bool | None = None
    result_blocks: int | None = None
    cleanup_complete: bool = True

    def __post_init__(self) -> None:
        if (self.outcome is McpRuntimeOutcome.SUCCEEDED) == self.is_error:
            raise ValueError("MCP runtime outcome does not match error state")


@dataclass
class _ManagedProcess:
    entry: McpServerEntry
    catalog_id: str
    protocol_version: str
    generation: int
    session: McpStdioSession
    last_used: int

    @property
    def key(self) -> tuple[str, str, int, str, str]:
        return (
            self.entry.scope,
            self.entry.configuration.name,
            self.entry.configuration.revision,
            self.protocol_version,
            self.catalog_id,
        )


class McpProcessManager:
    """Own at most a bounded LRU set of sequential confined stdio processes."""

    def __init__(self, store: McpServerStore, client: McpStdioClient) -> None:
        self._store = store
        self._client = client
        self._processes: dict[tuple[str, str, int, str, str], _ManagedProcess] = {}
        self._next_generation = 1
        self._clock = 0
        self._closed = False
        self._lock = RLock()

    def statuses(self) -> tuple[McpLiveProcessStatus, ...]:
        with self._lock:
            self._retire_invalid_configurations()
            return tuple(
                McpLiveProcessStatus(
                    configured_name=managed.entry.configuration.name,
                    scope=managed.entry.scope,
                    configuration_revision=managed.entry.configuration.revision,
                    protocol_version=managed.protocol_version,
                    process_generation=managed.generation,
                    calls_completed=managed.session.calls_completed,
                    alive=managed.session.alive,
                    stderr_bytes=managed.session.stderr_bytes,
                    stderr_truncated=managed.session.stderr_truncated,
                )
                for managed in sorted(self._processes.values(), key=lambda item: item.key)
            )

    def execute(
        self,
        prepared: PreparedMcpCall,
        *,
        cancellation: TurnCancellation | None = None,
    ) -> McpRuntimeExecution:
        with self._lock:
            if self._closed:
                return _failed("mcp_runtime_closed", "MCP process manager is closed")
            try:
                entry = self._current_entry(prepared.candidate)
            except McpClientError as error:
                return _client_failure(error)
            key = (
                entry.scope,
                entry.configuration.name,
                entry.configuration.revision,
                prepared.candidate.protocol_version,
                prepared.catalog_id,
            )
            cleanup_complete = self._retire_stale(prepared.catalog_id, keep=key)
            if not cleanup_complete:
                return _partial(
                    "mcp_cleanup_incomplete",
                    "A stale MCP process could not be completely retired",
                    cleanup_complete=False,
                )
            managed = self._processes.get(key)
            reused = managed is not None
            if managed is not None and (
                not managed.session.alive
                or managed.session.calls_completed >= MAX_MCP_CALLS_PER_PROCESS
            ):
                if not self._retire(key):
                    return _partial(
                        "mcp_cleanup_incomplete",
                        "An expired MCP process could not be completely retired",
                        cleanup_complete=False,
                    )
                managed = None
                reused = False
            if managed is None:
                capacity = self._make_capacity()
                if not capacity:
                    return _partial(
                        "mcp_cleanup_incomplete",
                        "MCP process capacity could not be reclaimed",
                        cleanup_complete=False,
                    )
                try:
                    session = self._client.connect(entry)
                except McpClientError as error:
                    return _client_failure(error)
                managed = _ManagedProcess(
                    entry=entry,
                    catalog_id=prepared.catalog_id,
                    protocol_version=session.protocol_version,
                    generation=self._next_generation,
                    session=session,
                    last_used=0,
                )
                self._next_generation += 1
                if managed.key != key or not _session_matches_candidate(
                    session, prepared.candidate
                ):
                    cleanup = session.close()
                    if not cleanup:
                        return _partial(
                            "mcp_cleanup_incomplete",
                            "MCP process tools changed and cleanup is incomplete",
                            cleanup_complete=False,
                        )
                    return _failed(
                        "mcp_runtime_catalog_mismatch",
                        "MCP process tools changed after catalog preparation",
                    )
                self._processes[key] = managed
            self._clock += 1
            managed.last_used = self._clock
            try:
                call = managed.session.call_tool(
                    prepared.candidate.remote_name,
                    prepared.arguments,
                    process_generation=managed.generation,
                    process_reused=reused,
                    cancellation=cancellation,
                )
            except McpClientError as error:
                cleanup = True
                if error.code != "mcp_server_error":
                    cleanup = self._retire(key)
                return _client_failure(error, cleanup_complete=cleanup)
            try:
                normalized = _normalize_call_result(call)
            except McpClientError as error:
                cleanup = self._retire(key)
                return _partial(
                    error.code,
                    str(error),
                    cleanup_complete=cleanup,
                    duration_ms=call.duration_ms,
                    process_generation=call.process_generation,
                    process_reused=call.process_reused,
                )
            if normalized.outcome is McpRuntimeOutcome.PARTIAL:
                return replace(normalized, cleanup_complete=self._retire(key))
            return normalized

    def close(self) -> bool:
        with self._lock:
            if self._closed and not self._processes:
                return True
            self._closed = True
            complete = True
            for key in tuple(self._processes):
                complete = self._retire(key) and complete
            return complete and not self._processes

    def synchronize_catalog(self, catalog_id: str) -> bool:
        """Retire live generations that do not belong to the refreshed catalog."""
        with self._lock:
            complete = True
            for key, managed in tuple(self._processes.items()):
                if managed.catalog_id != catalog_id:
                    complete = self._retire(key) and complete
            return complete

    def _current_entry(self, candidate: McpToolCandidate) -> McpServerEntry:
        try:
            entry = self._store.get_server(candidate.configured_name)
        except Exception:
            raise McpClientError(
                "mcp_configuration_stale",
                "MCP server configuration is no longer available",
            ) from None
        if (
            entry.scope != candidate.scope
            or entry.configuration.revision != candidate.configuration_revision
            or not entry.configuration.enabled
        ):
            raise McpClientError(
                "mcp_configuration_stale",
                "MCP server configuration changed before execution",
            )
        return entry

    def _retire_stale(
        self,
        catalog_id: str,
        *,
        keep: tuple[str, str, int, str, str],
    ) -> bool:
        complete = True
        for key, managed in tuple(self._processes.items()):
            if key != keep and managed.catalog_id != catalog_id:
                complete = self._retire(key) and complete
        return complete

    def _make_capacity(self) -> bool:
        if len(self._processes) < MAX_MCP_ACTIVE_PROCESSES:
            return True
        oldest = min(self._processes.values(), key=lambda item: (item.last_used, item.key))
        return self._retire(oldest.key)

    def _retire(self, key: tuple[str, str, int, str, str]) -> bool:
        managed = self._processes.get(key)
        if managed is None:
            return True
        complete = managed.session.close()
        if complete:
            self._processes.pop(key, None)
        return complete

    def _retire_invalid_configurations(self) -> bool:
        active = {
            (entry.scope, entry.configuration.name, entry.configuration.revision)
            for entry in self._store.list_servers()
            if entry.configuration.enabled
        }
        complete = True
        for key in tuple(self._processes):
            if key[:3] not in active:
                complete = self._retire(key) and complete
        return complete


def prepare_mcp_call(
    candidate: McpToolCandidate,
    catalog_id: str,
    arguments: dict[str, object],
) -> PreparedMcpCall:
    if candidate.disposition is not McpCandidateDisposition.ACCEPTED or candidate.contract is None:
        raise McpCallPreparationError("MCP tool candidate is not executable")
    if not isinstance(arguments, dict):
        raise McpCallPreparationError("MCP tool arguments must be an object")
    schema = candidate.contract.definition.as_mapping()["input_schema"]
    try:
        _validate_instance(arguments, schema)
    except ValueError as error:
        raise McpCallPreparationError(str(error)) from None
    identity = {
        "candidate": candidate.identity_mapping(),
        "catalog_id": catalog_id,
    }
    import hashlib

    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return PreparedMcpCall(candidate, catalog_id, arguments, digest)


def _session_matches_candidate(session: McpStdioSession, candidate: McpToolCandidate) -> bool:
    if session.protocol_version != candidate.protocol_version:
        return False
    for tool in session.tools:
        if tool.name == candidate.remote_name:
            from leonervis_code.mcp.catalog import mcp_schema_fingerprint

            return mcp_schema_fingerprint(tool.input_schema_json) == candidate.schema_fingerprint
    return False


def _normalize_call_result(call: McpToolCallResult) -> McpRuntimeExecution:
    result = call.result
    if not isinstance(result, dict) or set(result) - {
        "_meta",
        "content",
        "isError",
        "structuredContent",
    }:
        raise McpClientError("mcp_result_invalid", "MCP tools/call result is invalid")
    raw_error = result.get("isError", False)
    if type(raw_error) is not bool:
        raise McpClientError("mcp_result_invalid", "MCP tools/call isError is invalid")
    raw_content = result.get("content", [])
    if not isinstance(raw_content, list):
        raise McpClientError("mcp_result_invalid", "MCP tools/call content is invalid")
    if len(raw_content) > MAX_MCP_RESULT_BLOCKS:
        raise McpClientError("mcp_result_limit", "MCP tools/call content exceeds its limit")
    pieces = [_normalize_content_block(block) for block in raw_content]
    structured = result.get("structuredContent")
    if structured is not None:
        if not isinstance(structured, dict):
            raise McpClientError(
                "mcp_result_invalid", "MCP tools/call structuredContent is invalid"
            )
        pieces.append("structuredContent: " + _canonical_json(structured))
    if not pieces:
        pieces.append("[MCP tool returned no content]")
    content, truncated = _bounded_output("\n".join(pieces), MAX_MCP_RESULT_OUTPUT_BYTES)
    if truncated:
        return McpRuntimeExecution(
            content=content,
            is_error=True,
            truncated=True,
            outcome=McpRuntimeOutcome.PARTIAL,
            result_code="mcp_result_truncated",
            audit_message="MCP tool returned an oversized result; bounded partial content retained",
            duration_ms=call.duration_ms,
            process_generation=call.process_generation,
            process_reused=call.process_reused,
            result_blocks=len(raw_content),
        )
    if raw_error:
        return McpRuntimeExecution(
            content=content,
            is_error=True,
            truncated=False,
            outcome=McpRuntimeOutcome.FAILED,
            result_code="mcp_tool_reported_error",
            audit_message="MCP tool completed with a server-reported error",
            duration_ms=call.duration_ms,
            process_generation=call.process_generation,
            process_reused=call.process_reused,
            result_blocks=len(raw_content),
        )
    return McpRuntimeExecution(
        content=content,
        is_error=False,
        truncated=False,
        outcome=McpRuntimeOutcome.SUCCEEDED,
        result_code="mcp_tool_succeeded",
        audit_message="MCP tool completed with a bounded normalized result",
        duration_ms=call.duration_ms,
        process_generation=call.process_generation,
        process_reused=call.process_reused,
        result_blocks=len(raw_content),
    )


def _normalize_content_block(value: object) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise McpClientError("mcp_result_invalid", "MCP content block is invalid")
    kind = value["type"]
    if kind == "text":
        if set(value) - {"_meta", "annotations", "text", "type"}:
            raise McpClientError("mcp_result_invalid", "MCP text block is invalid")
        return _result_text(value.get("text"), "MCP text block")
    if kind in {"image", "audio"}:
        if set(value) - {"_meta", "annotations", "data", "mimeType", "type"}:
            raise McpClientError("mcp_result_invalid", f"MCP {kind} block is invalid")
        data_bytes = _base64_size(value.get("data"), f"MCP {kind} data")
        mime = _result_text(value.get("mimeType"), f"MCP {kind} MIME type")
        return _canonical_json({"data_bytes": data_bytes, "mimeType": mime, "type": kind})
    if kind == "resource_link":
        allowed = {
            "_meta",
            "annotations",
            "description",
            "mimeType",
            "name",
            "size",
            "title",
            "type",
            "uri",
        }
        if set(value) - allowed:
            raise McpClientError("mcp_result_invalid", "MCP resource link is invalid")
        projected = {
            key: value[key] for key in sorted(set(value) & (allowed - {"_meta", "annotations"}))
        }
        if not isinstance(projected.get("uri"), str) or not isinstance(projected.get("name"), str):
            raise McpClientError("mcp_result_invalid", "MCP resource link is invalid")
        return _canonical_json(projected)
    if kind == "resource":
        if set(value) - {"_meta", "annotations", "resource", "type"}:
            raise McpClientError("mcp_result_invalid", "MCP embedded resource is invalid")
        resource = value.get("resource")
        if not isinstance(resource, dict) or set(resource) - {
            "_meta",
            "blob",
            "mimeType",
            "text",
            "uri",
        }:
            raise McpClientError("mcp_result_invalid", "MCP embedded resource is invalid")
        uri = _result_text(resource.get("uri"), "MCP resource URI")
        has_text = isinstance(resource.get("text"), str)
        has_blob = isinstance(resource.get("blob"), str)
        if has_text == has_blob:
            raise McpClientError("mcp_result_invalid", "MCP resource content is invalid")
        if has_text:
            return (
                _canonical_json({"type": "resource", "uri": uri})
                + "\n"
                + _result_text(resource["text"], "MCP resource text")
            )
        blob_bytes = _base64_size(resource["blob"], "MCP resource blob")
        return _canonical_json({"blob_bytes": blob_bytes, "type": "resource", "uri": uri})
    raise McpClientError("mcp_result_content_unsupported", "MCP content block type is unsupported")


def _validate_instance(value: object, schema: object, path: str = "$") -> None:
    if not isinstance(schema, dict):
        raise ValueError("MCP input schema is invalid")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"MCP tool argument {path} does not match const")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"MCP tool argument {path} is outside enum")
    raw_type = schema.get("type")
    types = raw_type if isinstance(raw_type, list) else ([raw_type] if raw_type is not None else [])
    if types and not any(_matches_type(value, item) for item in types):
        raise ValueError(f"MCP tool argument {path} has the wrong type")
    alternatives = schema.get("anyOf")
    if alternatives is not None and not any(
        _instance_valid(value, item, path) for item in alternatives
    ):
        raise ValueError(f"MCP tool argument {path} does not match anyOf")
    alternatives = schema.get("oneOf")
    if (
        alternatives is not None
        and sum(_instance_valid(value, item, path) for item in alternatives) != 1
    ):
        raise ValueError(f"MCP tool argument {path} does not match exactly one oneOf branch")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"MCP tool argument {path} is missing required field")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"MCP tool argument {path} contains an unknown field")
        for name, child in value.items():
            if name in properties:
                _validate_instance(child, properties[name], f"{path}.{name}")
    elif isinstance(value, list):
        if type(schema.get("minItems")) is int and len(value) < schema["minItems"]:
            raise ValueError(f"MCP tool argument {path} has too few items")
        if type(schema.get("maxItems")) is int and len(value) > schema["maxItems"]:
            raise ValueError(f"MCP tool argument {path} has too many items")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate_instance(item, schema["items"], f"{path}[{index}]")
    elif isinstance(value, str):
        if type(schema.get("minLength")) is int and len(value) < schema["minLength"]:
            raise ValueError(f"MCP tool argument {path} is too short")
        if type(schema.get("maxLength")) is int and len(value) > schema["maxLength"]:
            raise ValueError(f"MCP tool argument {path} is too long")
        if isinstance(schema.get("pattern"), str) and re.search(schema["pattern"], value) is None:
            raise ValueError(f"MCP tool argument {path} does not match pattern")
    elif type(value) in {int, float}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"MCP tool argument {path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"MCP tool argument {path} exceeds maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValueError(f"MCP tool argument {path} is below exclusive minimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise ValueError(f"MCP tool argument {path} exceeds exclusive maximum")


def _instance_valid(value: object, schema: object, path: str) -> bool:
    try:
        _validate_instance(value, schema, path)
    except ValueError:
        return False
    return True


def _matches_type(value: object, expected: object) -> bool:
    return {
        "array": isinstance(value, list),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "null": value is None,
        "number": type(value) in {int, float},
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
    }.get(expected, False)


def _client_failure(
    error: McpClientError,
    *,
    cleanup_complete: bool | None = None,
) -> McpRuntimeExecution:
    cleanup = error.cleanup_complete if cleanup_complete is None else cleanup_complete
    if error.outcome_uncertain or not cleanup:
        return _partial(error.code, str(error), cleanup_complete=cleanup)
    return _failed(error.code, str(error), cleanup_complete=cleanup)


def _failed(code: str, message: str, *, cleanup_complete: bool = True) -> McpRuntimeExecution:
    return McpRuntimeExecution(
        content=message,
        is_error=True,
        truncated=False,
        outcome=McpRuntimeOutcome.FAILED,
        result_code=code,
        audit_message=f"MCP execution failed: {code}",
        cleanup_complete=cleanup_complete,
    )


def _partial(
    code: str,
    message: str,
    *,
    cleanup_complete: bool,
    duration_ms: int | None = None,
    process_generation: int | None = None,
    process_reused: bool | None = None,
) -> McpRuntimeExecution:
    return McpRuntimeExecution(
        content=message,
        is_error=True,
        truncated=False,
        outcome=McpRuntimeOutcome.PARTIAL,
        result_code=code,
        audit_message=f"MCP execution outcome is uncertain: {code}",
        duration_ms=duration_ms,
        process_generation=process_generation,
        process_reused=process_reused,
        cleanup_complete=cleanup_complete,
    )


def _result_text(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_MCP_RESULT_FIELD_CHARACTERS
        or len(value.encode("utf-8")) > MAX_MCP_RESULT_OUTPUT_BYTES
    ):
        raise McpClientError("mcp_result_limit", f"{label} exceeds its bound")
    return value


def _base64_size(value: object, label: str) -> int:
    text = _result_text(value, label)
    try:
        return len(base64.b64decode(text, validate=True))
    except (binascii.Error, ValueError):
        raise McpClientError("mcp_result_invalid", f"{label} is not valid base64") from None


def _bounded_output(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    suffix = "\n[output truncated]"
    budget = limit - len(suffix.encode("ascii"))
    prefix = encoded[:budget].decode("utf-8", errors="ignore")
    return prefix + suffix, True


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError):
        raise McpClientError("mcp_json_invalid", "MCP JSON is not canonicalizable") from None
