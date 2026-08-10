"""Shared safe failures for real provider adapters."""

from __future__ import annotations

from coquo.core.orchestration import ProviderFailure, ProviderFailureKind
from coquo.providers.usage import ProviderTokenUsage


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


def safe_retry_after(headers: object) -> int | None:
    """Parse a bounded integer Retry-After value from header-like metadata."""
    if headers is None or not hasattr(headers, "get"):
        return None
    value = headers.get("retry-after")
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        return None
    seconds = int(value)
    return seconds if 0 <= seconds <= 86_400 else None
