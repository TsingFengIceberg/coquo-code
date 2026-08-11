from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from coquo.core.contracts import AssistantText, ConversationRequest, UserMessage
from coquo.providers.anthropic import (
    AnthropicProviderConfig,
    build_request as build_anthropic_request,
    parse_response as parse_anthropic_response,
)
from coquo.providers.native_search import (
    NativeSearchAdapterId,
    NativeSearchCitationFormat,
    NativeSearchConfigurationError,
    NativeSearchContextSize,
    NativeSearchManifest,
    NativeSearchMode,
    NativeSearchRuntimeOptions,
    canonical_native_search_domain,
    validate_native_search_runtime_options,
)
from coquo.providers.openai_compat import (
    build_request as build_openai_request,
)
from coquo.providers.openai_responses import (
    build_request as build_responses_request,
    parse_response as parse_responses_response,
)
from coquo.providers.profile import ProviderProfileSpec
from coquo.providers.profile_store import ProviderProfileStore
from coquo.providers.resolver import resolve_profile_route
from coquo.session import ProjectSession
from coquo.system_prompt import build_system_prompt
from coquo.providers.definitions import WireProtocol
from coquo.tools.web_search import TAVILY_SEARCH_API_KEY_ENV


def conversation() -> ConversationRequest:
    return ConversationRequest(build_system_prompt(), (UserMessage("latest Python news"),))


def test_runtime_options_canonicalize_domains_and_reject_unsupported_dialects() -> None:
    assert canonical_native_search_domain("OpenAI.COM.") == "openai.com"
    responses = resolve_profile_route(
        profile("deepseek", "deepseek-v4-flash"), environment={}
    ).native_search
    options = NativeSearchRuntimeOptions(
        NativeSearchMode.REQUIRED,
        ("openai.com",),
        NativeSearchContextSize.MEDIUM,
    )
    validate_native_search_runtime_options(responses, options)

    anthropic = resolve_profile_route(
        profile("anthropic", "claude-test"), environment={}
    ).native_search
    with pytest.raises(NativeSearchConfigurationError, match="required"):
        validate_native_search_runtime_options(anthropic, options)


def profile(provider: str, model: str, **overrides) -> ProviderProfileSpec:
    values = {
        "name": "work",
        "provider_id": provider,
        "protocol": (
            WireProtocol.ANTHROPIC_MESSAGES
            if provider == "anthropic"
            else (
                WireProtocol.OPENAI_RESPONSES
                if provider == "openai" or (provider == "deepseek" and model == "deepseek-v4-flash")
                else WireProtocol.OPENAI_CHAT_COMPLETIONS
            )
        ),
        "model": model,
    }
    values.update(overrides)
    return ProviderProfileSpec(**values)


def test_builtin_provider_catalog_resolves_native_search_without_protocol_guessing() -> None:
    anthropic = resolve_profile_route(profile("anthropic", "claude-sonnet-4"), environment={})
    openai_search = resolve_profile_route(profile("openai", "gpt-5"), environment={})
    openai_plain = resolve_profile_route(profile("openai", "gpt-5"), environment={})
    dashscope = resolve_profile_route(profile("dashscope", "qwen-plus"), environment={})
    deepseek = resolve_profile_route(profile("deepseek", "deepseek-chat"), environment={})
    deepseek_flash = resolve_profile_route(profile("deepseek", "deepseek-v4-flash"), environment={})

    assert anthropic.native_search.adapter_id is NativeSearchAdapterId.ANTHROPIC_WEB_SEARCH_20250305
    assert (
        openai_search.native_search.adapter_id
        is NativeSearchAdapterId.OPENAI_RESPONSES_WEB_SEARCH_V1
    )
    assert (
        openai_plain.native_search.adapter_id
        is NativeSearchAdapterId.OPENAI_RESPONSES_WEB_SEARCH_V1
    )
    assert dashscope.native_search.adapter_id is NativeSearchAdapterId.DASHSCOPE_ENABLE_SEARCH_V1
    assert deepseek.native_search.available is False
    assert (
        deepseek_flash.native_search.adapter_id
        is NativeSearchAdapterId.OPENAI_RESPONSES_WEB_SEARCH_V1
    )


def test_manifest_is_bounded_canonical_and_rejects_core_request_overrides() -> None:
    manifest = NativeSearchManifest.from_mapping(
        {
            "schema_version": 1,
            "id": "future-vendor-search-v1",
            "request": {
                "extra_body": {"enable_search": True, "search_options": {"count": 5}},
                "server_tool": None,
            },
            "response": {"citation_format": "openai-url-annotations"},
        }
    )

    assert manifest.citation_format is NativeSearchCitationFormat.OPENAI_URL_ANNOTATIONS
    assert len(manifest.fingerprint()) == 64
    assert NativeSearchManifest.from_mapping(manifest.to_dict()) == manifest

    exported = manifest.to_dict()
    exported["request"]["extra_body"]["enable_search"] = False  # type: ignore[index]
    assert manifest.extra_body["enable_search"] is True

    value = manifest.to_dict()
    value["request"]["extra_body"]["messages"] = []  # type: ignore[index]
    with pytest.raises(NativeSearchConfigurationError, match="protected field"):
        NativeSearchManifest.from_mapping(value)

    value = manifest.to_dict()
    value["request"]["extra_body"]["x-api-key"] = "must-not-persist"  # type: ignore[index]
    with pytest.raises(NativeSearchConfigurationError, match="credential field: x-api-key"):
        NativeSearchManifest.from_mapping(value)


def test_custom_profile_embeds_manifest_and_projects_bounded_extra_body() -> None:
    manifest = NativeSearchManifest.from_mapping(
        {
            "schema_version": 1,
            "id": "gateway-search-v1",
            "request": {
                "extra_body": {"enable_search": True},
                "server_tool": {"type": "vendor:web_search"},
            },
            "response": {"citation_format": "openai-url-annotations"},
        }
    )
    configured = profile(
        "custom",
        "vendor/model",
        base_url="https://gateway.example/v1",
        native_search_manifest=manifest,
    )
    route = resolve_profile_route(configured, environment={})

    request = build_openai_request(route, conversation())

    assert route.native_search.adapter_id is NativeSearchAdapterId.CUSTOM_MANIFEST_V1
    assert request["extra_body"] == {"enable_search": True}
    assert request["tools"][-1] == {"type": "vendor:web_search"}  # type: ignore[index]
    assert route.fingerprint() == resolve_profile_route(configured, environment={}).fingerprint()


def test_openai_compatible_native_search_projection_is_adapter_specific() -> None:
    openai_route = resolve_profile_route(profile("openai", "gpt-5"), environment={})
    dashscope_route = resolve_profile_route(profile("dashscope", "qwen-plus"), environment={})
    openrouter_route = resolve_profile_route(
        profile("openrouter", "anthropic/claude-sonnet-4"), environment={}
    )

    assert build_responses_request(openai_route, conversation())["tools"][-1] == {
        "type": "web_search"
    }
    assert build_openai_request(dashscope_route, conversation())["extra_body"] == {
        "enable_search": True
    }
    assert build_openai_request(openrouter_route, conversation())["tools"][-1] == {  # type: ignore[index]
        "type": "openrouter:web_search"
    }


def test_openai_url_annotations_are_normalized_into_durable_visible_text() -> None:
    route = resolve_profile_route(profile("openai", "gpt-5"), environment={})
    response = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Python 3.14 was released.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://python.org/downloads/",
                                "title": "Python downloads",
                            }
                        ],
                    }
                ],
            }
        ],
    }

    parsed = parse_responses_response(response, route=route)

    assert isinstance(parsed, AssistantText)
    assert "Sources:" in parsed.text
    assert "[Python downloads](https://python.org/downloads/)" in parsed.text


def test_anthropic_server_search_blocks_are_not_host_tool_calls_and_keep_citations() -> None:
    route = resolve_profile_route(profile("anthropic", "claude-sonnet-4"), environment={})
    config = AnthropicProviderConfig(
        model_id=route.wire_model,
        native_search=route.native_search,
    )
    request = build_anthropic_request(config, conversation())
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="server_tool_use", id="srv-1", name="web_search"),
            SimpleNamespace(type="web_search_tool_result", tool_use_id="srv-1"),
            SimpleNamespace(
                type="text",
                text="Python information.",
                citations=[SimpleNamespace(url="https://python.org/", title="Python")],
            ),
        ],
    )

    parsed = parse_anthropic_response(response, config=config)

    assert request["tools"][-1]["type"] == "web_search_20250305"  # type: ignore[index]
    assert isinstance(parsed, AssistantText)
    assert "[Python](https://python.org/)" in parsed.text


@dataclass
class _NativeSearchProvider:
    enabled: bool = False
    options: NativeSearchRuntimeOptions = NativeSearchRuntimeOptions()

    def set_native_search_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def set_native_search_options(self, options: NativeSearchRuntimeOptions) -> None:
        self.options = options

    def close(self) -> None:
        return None


def test_project_session_defaults_to_provider_search_and_leaves_tavily_inactive(tmp_path) -> None:
    user_path = tmp_path / "config" / "providers.json"
    project_path = tmp_path / "workspace-provider.json"
    store = ProviderProfileStore(user_path, project_path)
    store.add_profile(
        profile(
            "anthropic",
            "claude-sonnet-4",
            context_window_tokens=200_000,
            model_max_output_tokens=64_000,
        )
    )
    store.set_active("work", scope="user")
    provider = _NativeSearchProvider()
    session = ProjectSession.open(
        tmp_path,
        environment={TAVILY_SEARCH_API_KEY_ENV: "tavily-secret"},
        user_profile_path=user_path,
        project_profile_path=project_path,
        provider_factory=lambda _route, *, environment: provider,
    )
    try:
        initial = session.inspect_web_search_sources()
        assert initial.ordered_source_names == ("provider",)
        assert initial.provider_active is True
        assert initial.active_sources == ()
        assert provider.enabled is True

        selected = session.set_web_search_sources(("tavily",))
        assert selected.ordered_source_names == ("tavily",)
        assert provider.enabled is False

        fallback = session.set_web_search_sources(("provider", "tavily"))
        assert fallback.execution_mode == "primary-with-explicit-fallback"
        assert fallback.active_sources[0].value == "tavily"
        assert provider.enabled is True

        reset = session.reset_web_search_sources()
        assert reset.ordered_source_names == ("provider",)
        assert provider.enabled is True
    finally:
        session.close()
