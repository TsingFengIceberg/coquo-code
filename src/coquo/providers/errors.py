"""Shared safe failures for real provider adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from coquo.core.orchestration import ProviderFailure, ProviderFailureKind
from coquo.providers.usage import ProviderTokenUsage

MAX_UPSTREAM_ERROR_CODE_CHARS = 160
MAX_UPSTREAM_ERROR_TYPE_CHARS = 160
MAX_UPSTREAM_MESSAGE_CHARS = 1_024


@dataclass(frozen=True)
class UpstreamErrorMetadata:
    """Bounded, terminal-safe facts extracted from one SDK error."""

    http_status_code: int | None = None
    upstream_error_code: str | None = None
    upstream_error_type: str | None = None
    upstream_message: str | None = None
    request_id: str | None = None
    retry_after_seconds: int | None = None


class ProviderAdapterError(RuntimeError):
    """Expose only a normalized safe provider failure to callers."""

    def __init__(
        self,
        failure: ProviderFailure,
        *,
        requested_output_tokens: int | None = None,
        usage: ProviderTokenUsage | None = None,
        partial_response_observed: bool = False,
    ) -> None:
        super().__init__(failure.message)
        if requested_output_tokens is not None and (
            type(requested_output_tokens) is not int or requested_output_tokens < 1
        ):
            raise ValueError("requested output tokens must be positive when known")
        if usage is not None and type(usage) is not ProviderTokenUsage:
            raise ValueError("provider error usage must be a ProviderTokenUsage")
        if type(partial_response_observed) is not bool:
            raise ValueError("partial response observation must be boolean")
        if failure.kind == ProviderFailureKind.OUTPUT_LIMIT and requested_output_tokens is None:
            raise ValueError("output-limit failures require the requested output tokens")
        if failure.kind != ProviderFailureKind.OUTPUT_LIMIT and (
            requested_output_tokens is not None or usage is not None or partial_response_observed
        ):
            raise ValueError("provider failure observations are reserved for output limits")
        self.failure = failure
        self.requested_output_tokens = requested_output_tokens
        self.usage = usage
        self.partial_response_observed = partial_response_observed


def adapter_error(
    *,
    provider_id: str,
    model_id: str,
    kind: ProviderFailureKind,
    code: str,
    message: str,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
    request_id: str | None = None,
    http_status_code: int | None = None,
    upstream_error_code: str | None = None,
    upstream_error_type: str | None = None,
    upstream_message: str | None = None,
) -> ProviderAdapterError:
    """Build one redacted adapter error from provider-neutral metadata."""
    return ProviderAdapterError(
        ProviderFailure(
            provider_id=provider_id,
            model_id=model_id,
            kind=kind,
            diagnostic_code=code,
            message=message,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            request_id=request_id,
            http_status_code=safe_http_status(http_status_code),
            upstream_error_code=safe_upstream_text(
                upstream_error_code, maximum=MAX_UPSTREAM_ERROR_CODE_CHARS
            ),
            upstream_error_type=safe_upstream_text(
                upstream_error_type, maximum=MAX_UPSTREAM_ERROR_TYPE_CHARS
            ),
            upstream_message=safe_upstream_text(
                upstream_message, maximum=MAX_UPSTREAM_MESSAGE_CHARS
            ),
        )
    )


def output_limit_error(
    *,
    provider_id: str,
    model_id: str,
    message: str,
    requested_output_tokens: int,
    usage: ProviderTokenUsage | None = None,
    partial_response_observed: bool = False,
) -> ProviderAdapterError:
    """Build one normalized output-limit failure with bounded Host observations."""
    return ProviderAdapterError(
        ProviderFailure(
            provider_id=provider_id,
            model_id=model_id,
            kind=ProviderFailureKind.OUTPUT_LIMIT,
            diagnostic_code="output_token_limit",
            message=message,
            retryable=False,
        ),
        requested_output_tokens=requested_output_tokens,
        usage=usage,
        partial_response_observed=partial_response_observed,
    )


def safe_request_id(value: object) -> str | None:
    """Retain only short printable provider request identifiers."""
    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    return value if value.isprintable() else None


def safe_http_status(value: object) -> int | None:
    """Retain only a conventional three-digit HTTP status code."""
    if type(value) is not int or not 100 <= value <= 599:
        return None
    return value


def safe_upstream_text(value: object, *, maximum: int) -> str | None:
    """Retain short printable upstream text without terminal control bytes."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or not text.isprintable():
        return None
    return text[:maximum]


def safe_request_id_from_headers(headers: object) -> str | None:
    """Read common request-id headers without retaining the header map."""
    if headers is None or not hasattr(headers, "get"):
        return None
    for name in ("request-id", "x-request-id"):
        request_id = safe_request_id(headers.get(name))
        if request_id is not None:
            return request_id
    return None


def extract_upstream_error_metadata(error: object) -> UpstreamErrorMetadata:
    """Extract known SDK error facts, never the raw response payload.

    OpenAI-compatible and Anthropic SDKs expose a bounded parsed ``body`` for
    status errors.  We inspect only the standard ``error`` object fields and
    fall back to response status/headers.  Unknown or non-JSON bodies are not
    copied into the normalized failure.
    """
    response = getattr(error, "response", None)
    status = safe_http_status(getattr(error, "status_code", None))
    if status is None:
        status = safe_http_status(getattr(response, "status_code", None))
    headers = getattr(response, "headers", None)
    request_id = safe_request_id(getattr(error, "request_id", None))
    if request_id is None:
        request_id = safe_request_id_from_headers(headers)
    retry_after = safe_retry_after(headers)

    body = getattr(error, "body", None)
    mappings: list[Mapping[object, object]] = []
    if isinstance(body, Mapping):
        nested = body.get("error")
        if isinstance(nested, Mapping):
            mappings.append(nested)
        mappings.append(body)

    def first_text(name: str, *, maximum: int) -> str | None:
        for payload in mappings:
            candidate = safe_upstream_text(payload.get(name), maximum=maximum)
            if candidate is not None:
                return candidate
        return None

    return UpstreamErrorMetadata(
        http_status_code=status,
        upstream_error_code=first_text("code", maximum=MAX_UPSTREAM_ERROR_CODE_CHARS),
        upstream_error_type=first_text("type", maximum=MAX_UPSTREAM_ERROR_TYPE_CHARS),
        upstream_message=first_text("message", maximum=MAX_UPSTREAM_MESSAGE_CHARS),
        request_id=request_id,
        retry_after_seconds=retry_after,
    )


def safe_retry_after(headers: object) -> int | None:
    """Parse a bounded integer Retry-After value from header-like metadata."""
    if headers is None or not hasattr(headers, "get"):
        return None
    value = headers.get("retry-after")
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        return None
    seconds = int(value)
    return seconds if 0 <= seconds <= 86_400 else None
