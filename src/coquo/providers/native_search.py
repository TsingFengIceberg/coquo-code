"""Versioned provider-native web-search capability and manifest contracts."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re


NATIVE_SEARCH_MANIFEST_SCHEMA_VERSION = 1
MAX_NATIVE_SEARCH_MANIFEST_BYTES = 32 * 1024
MAX_NATIVE_SEARCH_MANIFEST_DEPTH = 8
MAX_NATIVE_SEARCH_MANIFEST_ENTRIES = 128
MAX_NATIVE_SEARCH_ID_CHARACTERS = 96
MAX_NATIVE_SEARCH_DOMAINS = 20
MAX_NATIVE_SEARCH_DOMAIN_CHARACTERS = 253
_MANIFEST_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}\Z")
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_PROTECTED_OPENAI_FIELDS = {
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "messages",
    "model",
    "n",
    "parallel_tool_calls",
    "presence_penalty",
    "response_format",
    "seed",
    "stop",
    "stream",
    "stream_options",
    "temperature",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "user",
}
_SENSITIVE_FIELD_FRAGMENTS = (
    "api_key",
    "api-key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "access_token",
    "access-token",
    "accesstoken",
    "bearer_token",
    "bearer-token",
    "bearertoken",
)


class NativeSearchAdapterId(StrEnum):
    """Implemented request/response dialects for provider-owned search."""

    ANTHROPIC_WEB_SEARCH_20250305 = "anthropic-web-search-20250305"
    OPENAI_CHAT_WEB_SEARCH_OPTIONS_V1 = "openai-chat-web-search-options-v1"
    OPENAI_RESPONSES_WEB_SEARCH_V1 = "openai-responses-web-search-v1"
    DASHSCOPE_ENABLE_SEARCH_V1 = "dashscope-enable-search-v1"
    OPENROUTER_WEB_SEARCH_V1 = "openrouter-web-search-v1"
    CUSTOM_MANIFEST_V1 = "custom-manifest-v1"


class NativeSearchCitationFormat(StrEnum):
    """Bounded citation layouts understood by compatible response parsers."""

    NONE = "none"
    OPENAI_URL_ANNOTATIONS = "openai-url-annotations"
    DASHSCOPE_SEARCH_INFO = "dashscope-search-info"


class NativeSearchConfigurationSource(StrEnum):
    BUILTIN = "built-in"
    PROFILE = "profile"
    CUSTOM_MANIFEST = "custom-manifest"
    UNAVAILABLE = "unavailable"


class NativeSearchMode(StrEnum):
    AUTO = "auto"
    REQUIRED = "required"


class NativeSearchContextSize(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NativeSearchConfigurationError(ValueError):
    """Reject an unknown, unsafe, or internally inconsistent search declaration."""


@dataclass(frozen=True)
class NativeSearchRuntimeOptions:
    """Bounded process-local controls for one Provider-native search request."""

    mode: NativeSearchMode = NativeSearchMode.AUTO
    allowed_domains: tuple[str, ...] = ()
    context_size: NativeSearchContextSize | None = None

    def __post_init__(self) -> None:
        if type(self.mode) is not NativeSearchMode:
            raise NativeSearchConfigurationError("native-search mode is invalid")
        if self.context_size is not None and type(self.context_size) is not NativeSearchContextSize:
            raise NativeSearchConfigurationError("native-search context size is invalid")
        if (
            not isinstance(self.allowed_domains, tuple)
            or len(self.allowed_domains) > MAX_NATIVE_SEARCH_DOMAINS
            or len(set(self.allowed_domains)) != len(self.allowed_domains)
        ):
            raise NativeSearchConfigurationError("native-search allowed domains are invalid")
        for domain in self.allowed_domains:
            if canonical_native_search_domain(domain) != domain:
                raise NativeSearchConfigurationError(
                    "native-search allowed domain is not canonical"
                )


def canonical_native_search_domain(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_NATIVE_SEARCH_DOMAIN_CHARACTERS:
        raise NativeSearchConfigurationError("native-search allowed domain is invalid")
    if value != value.strip() or "://" in value or "/" in value or "@" in value:
        raise NativeSearchConfigurationError("native-search allowed domain must be a hostname")
    try:
        canonical = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise NativeSearchConfigurationError("native-search allowed domain is invalid") from None
    if not canonical or len(canonical) > MAX_NATIVE_SEARCH_DOMAIN_CHARACTERS:
        raise NativeSearchConfigurationError("native-search allowed domain is invalid")
    labels = canonical.split(".")
    if len(labels) < 2 or any(_DOMAIN_LABEL.fullmatch(label) is None for label in labels):
        raise NativeSearchConfigurationError("native-search allowed domain is invalid")
    return canonical


def validate_native_search_runtime_options(
    configuration: NativeSearchConfiguration,
    options: NativeSearchRuntimeOptions,
) -> None:
    """Reject request controls that one selected native-search dialect cannot represent."""
    if not configuration.available:
        if options != NativeSearchRuntimeOptions():
            raise NativeSearchConfigurationError("provider native search is unavailable")
        return
    adapter = configuration.adapter_id
    if (
        options.mode is NativeSearchMode.REQUIRED
        and adapter is not NativeSearchAdapterId.OPENAI_RESPONSES_WEB_SEARCH_V1
    ):
        raise NativeSearchConfigurationError(
            "required native-search mode is supported only by OpenAI Responses"
        )
    if options.allowed_domains and adapter not in {
        NativeSearchAdapterId.OPENAI_RESPONSES_WEB_SEARCH_V1,
        NativeSearchAdapterId.ANTHROPIC_WEB_SEARCH_20250305,
    }:
        raise NativeSearchConfigurationError(
            "allowed domains are unsupported by the current native-search adapter"
        )
    if options.context_size is not None and adapter not in {
        NativeSearchAdapterId.OPENAI_RESPONSES_WEB_SEARCH_V1,
        NativeSearchAdapterId.OPENAI_CHAT_WEB_SEARCH_OPTIONS_V1,
    }:
        raise NativeSearchConfigurationError(
            "search context size is unsupported by the current native-search adapter"
        )


@dataclass(frozen=True)
class NativeSearchManifest:
    """A bounded declarative overlay for compatible provider search extensions."""

    manifest_id: str
    extra_body: dict[str, object]
    server_tool: dict[str, object] | None = None
    citation_format: NativeSearchCitationFormat = NativeSearchCitationFormat.NONE
    schema_version: int = NATIVE_SEARCH_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra_body", deepcopy(self.extra_body))
        object.__setattr__(self, "server_tool", deepcopy(self.server_tool))
        if self.schema_version != NATIVE_SEARCH_MANIFEST_SCHEMA_VERSION:
            raise NativeSearchConfigurationError(
                "unsupported native-search manifest schema version"
            )
        if (
            not isinstance(self.manifest_id, str)
            or len(self.manifest_id) > MAX_NATIVE_SEARCH_ID_CHARACTERS
            or _MANIFEST_ID.fullmatch(self.manifest_id) is None
        ):
            raise NativeSearchConfigurationError("native-search manifest ID is invalid")
        if not isinstance(self.extra_body, dict):
            raise NativeSearchConfigurationError(
                "native-search manifest extra_body must be an object"
            )
        protected = set(self.extra_body) & _PROTECTED_OPENAI_FIELDS
        if protected:
            raise NativeSearchConfigurationError(
                f"native-search manifest cannot override protected field: {sorted(protected)[0]}"
            )
        if self.server_tool is not None:
            if not isinstance(self.server_tool, dict) or not self.server_tool:
                raise NativeSearchConfigurationError(
                    "native-search manifest server_tool must be a non-empty object or null"
                )
            if self.server_tool.get("type") == "function" or "function" in self.server_tool:
                raise NativeSearchConfigurationError(
                    "native-search manifest cannot inject a client function tool"
                )
        if type(self.citation_format) is not NativeSearchCitationFormat:
            raise NativeSearchConfigurationError("native-search citation format is invalid")
        _validate_json_value(self.to_dict(), depth=0, counter=[0])
        if len(self.canonical_json().encode("utf-8")) > MAX_NATIVE_SEARCH_MANIFEST_BYTES:
            raise NativeSearchConfigurationError("native-search manifest exceeds its byte limit")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> NativeSearchManifest:
        if not isinstance(value, Mapping):
            raise NativeSearchConfigurationError("native-search manifest must be a JSON object")
        fields = set(value)
        allowed = {"schema_version", "id", "request", "response"}
        unknown = fields - allowed
        if unknown:
            raise NativeSearchConfigurationError(
                f"native-search manifest contains unknown field: {sorted(unknown)[0]}"
            )
        missing = allowed - fields
        if missing:
            raise NativeSearchConfigurationError(
                f"native-search manifest is missing required field: {sorted(missing)[0]}"
            )
        request = value["request"]
        response = value["response"]
        if not isinstance(request, Mapping):
            raise NativeSearchConfigurationError("native-search manifest request must be an object")
        if set(request) - {"extra_body", "server_tool"}:
            raise NativeSearchConfigurationError(
                "native-search manifest request contains an unknown field"
            )
        extra_body = request.get("extra_body")
        server_tool = request.get("server_tool")
        if not isinstance(extra_body, dict):
            raise NativeSearchConfigurationError(
                "native-search manifest request.extra_body must be an object"
            )
        if server_tool is not None and not isinstance(server_tool, dict):
            raise NativeSearchConfigurationError(
                "native-search manifest request.server_tool must be an object or null"
            )
        if not isinstance(response, Mapping) or set(response) != {"citation_format"}:
            raise NativeSearchConfigurationError(
                "native-search manifest response must contain only citation_format"
            )
        citation = response["citation_format"]
        if not isinstance(citation, str):
            raise NativeSearchConfigurationError("native-search citation format must be text")
        try:
            citation_format = NativeSearchCitationFormat(citation)
        except ValueError:
            raise NativeSearchConfigurationError(
                f"unsupported native-search citation format: {citation}"
            ) from None
        return cls(
            manifest_id=value["id"],  # type: ignore[arg-type]
            extra_body=extra_body,
            server_tool=server_tool,
            citation_format=citation_format,
            schema_version=value["schema_version"],  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.manifest_id,
            "request": {
                "extra_body": deepcopy(self.extra_body),
                "server_tool": deepcopy(self.server_tool),
            },
            "response": {"citation_format": self.citation_format.value},
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NativeSearchConfiguration:
    """One resolved non-secret native-search request contract."""

    adapter_id: NativeSearchAdapterId | None
    source: NativeSearchConfigurationSource
    manifest: NativeSearchManifest | None = None

    def __post_init__(self) -> None:
        if self.adapter_id is None:
            if self.source is not NativeSearchConfigurationSource.UNAVAILABLE or self.manifest:
                raise NativeSearchConfigurationError("unavailable native search is inconsistent")
            return
        if self.source is NativeSearchConfigurationSource.UNAVAILABLE:
            raise NativeSearchConfigurationError("available native search requires a source")
        if (self.adapter_id is NativeSearchAdapterId.CUSTOM_MANIFEST_V1) != (
            self.manifest is not None
        ):
            raise NativeSearchConfigurationError("custom native search requires one manifest")

    @property
    def available(self) -> bool:
        return self.adapter_id is not None

    @property
    def default_enabled(self) -> bool:
        return self.available

    @classmethod
    def unavailable(cls) -> NativeSearchConfiguration:
        return cls(None, NativeSearchConfigurationSource.UNAVAILABLE)

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "adapter_id": self.adapter_id.value if self.adapter_id is not None else None,
            "source": self.source.value,
            "manifest": self.manifest.to_dict() if self.manifest is not None else None,
        }


def adapter_option_values() -> tuple[str, ...]:
    return (
        "auto",
        "none",
        *(
            adapter.value
            for adapter in NativeSearchAdapterId
            if adapter is not NativeSearchAdapterId.CUSTOM_MANIFEST_V1
        ),
    )


def resolve_native_search_configuration(
    *,
    builtin_adapter: NativeSearchAdapterId | None,
    selected_adapter: str | None,
    manifest: NativeSearchManifest | None,
) -> NativeSearchConfiguration:
    """Resolve profile auto/none/known-adapter/custom-manifest selection."""
    if manifest is not None:
        if selected_adapter not in {None, "auto", NativeSearchAdapterId.CUSTOM_MANIFEST_V1.value}:
            raise NativeSearchConfigurationError(
                "native-search manifest cannot be combined with another adapter"
            )
        return NativeSearchConfiguration(
            NativeSearchAdapterId.CUSTOM_MANIFEST_V1,
            NativeSearchConfigurationSource.CUSTOM_MANIFEST,
            manifest,
        )
    if selected_adapter in {None, "auto"}:
        if builtin_adapter is None:
            return NativeSearchConfiguration.unavailable()
        return NativeSearchConfiguration(
            builtin_adapter,
            NativeSearchConfigurationSource.BUILTIN,
        )
    if selected_adapter == "none":
        return NativeSearchConfiguration.unavailable()
    try:
        adapter = NativeSearchAdapterId(selected_adapter)
    except ValueError:
        raise NativeSearchConfigurationError(
            f"unsupported native-search adapter: {selected_adapter}"
        ) from None
    if adapter is NativeSearchAdapterId.CUSTOM_MANIFEST_V1:
        raise NativeSearchConfigurationError("custom-manifest-v1 requires a manifest file")
    return NativeSearchConfiguration(adapter, NativeSearchConfigurationSource.PROFILE)


def _validate_json_value(value: object, *, depth: int, counter: list[int]) -> None:
    if depth > MAX_NATIVE_SEARCH_MANIFEST_DEPTH:
        raise NativeSearchConfigurationError("native-search manifest exceeds its depth limit")
    if value is None or type(value) in {bool, int, float, str}:
        if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
            raise NativeSearchConfigurationError(
                "native-search manifest contains a non-finite number"
            )
        if isinstance(value, str):
            try:
                encoded = value.encode("utf-8")
            except UnicodeEncodeError:
                raise NativeSearchConfigurationError(
                    "native-search manifest text must be valid UTF-8"
                ) from None
            if "\x00" in value or len(encoded) > MAX_NATIVE_SEARCH_MANIFEST_BYTES:
                raise NativeSearchConfigurationError("native-search manifest text is invalid")
        return
    if isinstance(value, list):
        counter[0] += len(value)
        if counter[0] > MAX_NATIVE_SEARCH_MANIFEST_ENTRIES:
            raise NativeSearchConfigurationError("native-search manifest has too many entries")
        for item in value:
            _validate_json_value(item, depth=depth + 1, counter=counter)
        return
    if isinstance(value, dict):
        counter[0] += len(value)
        if counter[0] > MAX_NATIVE_SEARCH_MANIFEST_ENTRIES:
            raise NativeSearchConfigurationError("native-search manifest has too many entries")
        for key, item in value.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                raise NativeSearchConfigurationError("native-search manifest object key is invalid")
            lowered = key.lower()
            if any(fragment in lowered for fragment in _SENSITIVE_FIELD_FRAGMENTS) or lowered in {
                "token",
                "access_token",
            }:
                raise NativeSearchConfigurationError(
                    f"native-search manifest cannot contain credential field: {key}"
                )
            _validate_json_value(item, depth=depth + 1, counter=counter)
        return
    raise NativeSearchConfigurationError("native-search manifest contains a non-JSON value")
