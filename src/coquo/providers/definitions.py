"""Built-in real-provider definitions for the local Foundation 3B runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json

from coquo.providers.native_search import (
    NativeSearchAdapterId,
    NativeSearchConfiguration,
)


ADAPTER_CONTRACT_VERSION = 49


class WireProtocol(StrEnum):
    """The wire-protocol families implemented by Coquo."""

    ANTHROPIC_MESSAGES = "anthropic_messages"
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    OPENAI_RESPONSES = "openai_responses"


class ReasoningEffort(StrEnum):
    """Provider-neutral reasoning effort levels."""

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ReasoningNativeKind(StrEnum):
    """Supported string-based Provider reasoning contracts."""

    EFFORT = "effort"
    ANTHROPIC_ADAPTIVE_EFFORT = "anthropic_adaptive_effort"


@dataclass(frozen=True)
class ReasoningProfile:
    """Profile-declared native effort levels and Host-to-native mappings."""

    native_kind: ReasoningNativeKind
    native_levels: tuple[str, ...]
    mapping: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.native_kind, ReasoningNativeKind):
            raise ValueError("reasoning native kind is invalid")
        if not self.native_levels:
            raise ValueError("reasoning native levels must not be empty")
        if any(
            not isinstance(level, str) or not level or len(level) > 64 or not level.isascii()
            for level in self.native_levels
        ):
            raise ValueError("reasoning native levels must be non-empty ASCII strings")
        if len(set(self.native_levels)) != len(self.native_levels):
            raise ValueError("reasoning native levels must be unique")
        native_levels = set(self.native_levels)
        seen_hosts: set[str] = set()
        for host_level, native_level in self.mapping:
            if host_level in seen_hosts:
                raise ValueError("reasoning mapping contains duplicate Host levels")
            seen_hosts.add(host_level)
            if host_level not in {level.value for level in ReasoningEffort}:
                raise ValueError(f"reasoning mapping Host level is invalid: {host_level}")
            if native_level not in native_levels:
                raise ValueError("reasoning mapping refers to an undeclared native level")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ReasoningProfile:
        if not isinstance(value, Mapping):
            raise ValueError("profile reasoning must be an object")
        unknown = set(value) - {"native_kind", "native_levels", "mapping"}
        if unknown:
            raise ValueError(f"profile reasoning contains unknown field: {sorted(unknown)[0]}")
        kind_value = value.get("native_kind")
        try:
            native_kind = ReasoningNativeKind(kind_value)
        except (TypeError, ValueError):
            raise ValueError(f"unsupported reasoning native kind: {kind_value}") from None
        raw_levels = value.get("native_levels")
        if not isinstance(raw_levels, list) or not raw_levels:
            raise ValueError("profile reasoning native_levels must be a non-empty array")
        if any(not isinstance(level, str) for level in raw_levels):
            raise ValueError("profile reasoning native_levels must contain strings")
        raw_mapping = value.get("mapping")
        if not isinstance(raw_mapping, Mapping):
            raise ValueError("profile reasoning mapping must be an object")
        mapping: list[tuple[str, str]] = []
        for host_level, native_level in raw_mapping.items():
            if not isinstance(host_level, str) or not isinstance(native_level, str):
                raise ValueError("profile reasoning mapping must contain string pairs")
            mapping.append((host_level, native_level))
        return cls(native_kind, tuple(raw_levels), tuple(sorted(mapping)))

    def to_dict(self) -> dict[str, object]:
        return {
            "native_kind": self.native_kind.value,
            "native_levels": list(self.native_levels),
            "mapping": dict(self.mapping),
        }

    def map_effort(self, value: ReasoningEffort) -> str | None:
        return dict(self.mapping).get(value.value)


def wire_reasoning_effort(
    value: ReasoningEffort,
    reasoning_profile: ReasoningProfile | None = None,
) -> str:
    """Map a Host effort through an optional profile-declared native mapping."""
    if reasoning_profile is None:
        return value.value
    mapped = reasoning_profile.map_effort(value)
    if mapped is None:
        raise ValueError(f"reasoning effort is not mapped by the active profile: {value.value}")
    return mapped


@dataclass(frozen=True)
class ProviderDefinition:
    """Non-secret transport and compatibility metadata for one provider route."""

    provider_id: str
    protocol: WireProtocol
    credential_env: str | None
    credential_required: bool
    default_base_url: str
    base_url_env: str | None = None
    request_body_limit: int = 100 * 1024 * 1024
    native_search_adapter: NativeSearchAdapterId | None = None


@dataclass(frozen=True)
class RuntimeProviderRoute:
    """A resolved provider invocation plan that never contains a secret value."""

    definition: ProviderDefinition
    selected_model: str
    wire_model: str
    base_url: str
    base_url_source: str
    max_output_tokens: int = 1024
    temperature: float | None = None
    reasoning_effort: ReasoningEffort | None = None
    reasoning_profile: ReasoningProfile | None = None
    native_search: NativeSearchConfiguration = NativeSearchConfiguration.unavailable()

    def fingerprint(self) -> str:
        """Return a canonical route hash excluding credential value and presence."""
        return route_fingerprint(self)


def route_fingerprint(route: RuntimeProviderRoute) -> str:
    """Return a canonical SHA-256 for one resolved adapter invocation contract."""
    payload = {
        "adapter_contract_version": ADAPTER_CONTRACT_VERSION,
        "provider_id": route.definition.provider_id,
        "protocol": route.definition.protocol.value,
        "credential_env": route.definition.credential_env,
        "credential_required": route.definition.credential_required,
        "request_body_limit": route.definition.request_body_limit,
        "selected_model": route.selected_model,
        "wire_model": route.wire_model,
        "base_url": route.base_url,
        "base_url_source": route.base_url_source,
        "max_output_tokens": route.max_output_tokens,
        "temperature": route.temperature,
        "reasoning_effort": (
            route.reasoning_effort.value if route.reasoning_effort is not None else None
        ),
        "reasoning_profile": (
            route.reasoning_profile.to_dict() if route.reasoning_profile is not None else None
        ),
        "native_search": route.native_search.fingerprint_payload(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


ANTHROPIC = ProviderDefinition(
    provider_id="anthropic",
    protocol=WireProtocol.ANTHROPIC_MESSAGES,
    credential_env="ANTHROPIC_API_KEY",
    credential_required=True,
    default_base_url="https://api.anthropic.com",
    native_search_adapter=NativeSearchAdapterId.ANTHROPIC_WEB_SEARCH_20250305,
)
OPENAI = ProviderDefinition(
    provider_id="openai",
    protocol=WireProtocol.OPENAI_RESPONSES,
    credential_env="OPENAI_API_KEY",
    credential_required=True,
    default_base_url="https://api.openai.com/v1",
    base_url_env="OPENAI_BASE_URL",
    native_search_adapter=NativeSearchAdapterId.OPENAI_RESPONSES_WEB_SEARCH_V1,
)
XAI = ProviderDefinition(
    provider_id="xai",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env="XAI_API_KEY",
    credential_required=True,
    default_base_url="https://api.x.ai/v1",
    base_url_env="XAI_BASE_URL",
    request_body_limit=50 * 1024 * 1024,
)
DASHSCOPE = ProviderDefinition(
    provider_id="dashscope",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env="DASHSCOPE_API_KEY",
    credential_required=True,
    default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    base_url_env="DASHSCOPE_BASE_URL",
    request_body_limit=6 * 1024 * 1024,
    native_search_adapter=NativeSearchAdapterId.DASHSCOPE_ENABLE_SEARCH_V1,
)
OLLAMA = ProviderDefinition(
    provider_id="ollama",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env=None,
    credential_required=False,
    default_base_url="http://127.0.0.1:11434/v1",
    base_url_env="OLLAMA_HOST",
)
LOCAL = ProviderDefinition(
    provider_id="local",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env=None,
    credential_required=False,
    default_base_url="http://127.0.0.1:11434/v1",
    base_url_env="OPENAI_BASE_URL",
)
OPENROUTER = ProviderDefinition(
    provider_id="openrouter",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env="OPENROUTER_API_KEY",
    credential_required=True,
    default_base_url="https://openrouter.ai/api/v1",
    base_url_env="OPENROUTER_BASE_URL",
    native_search_adapter=NativeSearchAdapterId.OPENROUTER_WEB_SEARCH_V1,
)
DEEPSEEK = ProviderDefinition(
    provider_id="deepseek",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env="DEEPSEEK_API_KEY",
    credential_required=True,
    default_base_url="https://api.deepseek.com/v1",
    base_url_env="DEEPSEEK_BASE_URL",
    native_search_adapter=NativeSearchAdapterId.OPENAI_RESPONSES_WEB_SEARCH_V1,
)
ZHIPU = ProviderDefinition(
    provider_id="zhipu",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env="ZHIPU_API_KEY",
    credential_required=True,
    default_base_url="https://open.bigmodel.cn/api/paas/v4",
    base_url_env="ZHIPU_BASE_URL",
)
MOONSHOT = ProviderDefinition(
    provider_id="moonshot",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env="MOONSHOT_API_KEY",
    credential_required=True,
    default_base_url="https://api.moonshot.cn/v1",
    base_url_env="MOONSHOT_BASE_URL",
)
ARK = ProviderDefinition(
    provider_id="ark",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env="ARK_API_KEY",
    credential_required=True,
    default_base_url="https://ark.cn-beijing.volces.com/api/v3",
    base_url_env="ARK_BASE_URL",
)
HUNYUAN = ProviderDefinition(
    provider_id="hunyuan",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env="HUNYUAN_API_KEY",
    credential_required=True,
    default_base_url="https://api.hunyuan.cloud.tencent.com/v1",
    base_url_env="HUNYUAN_BASE_URL",
)
QIANFAN = ProviderDefinition(
    provider_id="qianfan",
    protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
    credential_env="QIANFAN_API_KEY",
    credential_required=True,
    default_base_url="https://qianfan.baidubce.com/v2",
    base_url_env="QIANFAN_BASE_URL",
)

BUILTIN_PROVIDERS: dict[str, ProviderDefinition] = {
    definition.provider_id: definition
    for definition in (
        ANTHROPIC,
        OPENAI,
        XAI,
        DASHSCOPE,
        OLLAMA,
        LOCAL,
        OPENROUTER,
        DEEPSEEK,
        ZHIPU,
        MOONSHOT,
        ARK,
        HUNYUAN,
        QIANFAN,
    )
}
