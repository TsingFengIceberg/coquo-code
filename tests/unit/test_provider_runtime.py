from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from coquo.core.compaction import (
    CompactSummaryRequest,
    CompactionUnavailableError,
    build_compact_prompt,
)
from coquo.core.contracts import (
    ToolArguments,
    AssistantText,
    ConversationRequest,
    ToolResult,
    ToolUse,
    UserMessage,
)
from coquo.core.session_title import build_session_title_request
from coquo.providers.definitions import ReasoningEffort, ReasoningProfile, WireProtocol
from coquo.providers.errors import ProviderAdapterError, adapter_error, output_limit_error
from coquo.core.orchestration import ProviderFailureKind
from coquo.providers.manager import (
    RuntimeProviderManager,
    RuntimeProviderStateError,
    RuntimeSwitchContextError,
)
from coquo.providers.model_context import (
    ModelContextCapability,
    ModelContextCapabilityResolver,
    ModelContextSource,
    ModelContextTarget,
)
from coquo.providers.profile import NamedProviderProfile
from coquo.providers.profile_store import ProviderProfileStore
from coquo.providers.request_context import (
    ContextFitDecision,
    ContextPreflightError,
    RequestTokenCount,
    RequestTokenCountMethod,
)
from coquo.providers.streaming import ProviderResponseOutcome, ProviderTextDelta
from coquo.providers.usage import ProviderInvocationKind, ProviderTokenUsage
from coquo.providers.reliability import ProviderReliabilityPolicy
from coquo.session import ProjectSession
from coquo.system_prompt import build_system_prompt
from coquo.tools.glob import GlobTool
from coquo.tools.grep import GrepTool
from coquo.tools.read_file import ReadFileTool


@dataclass
class RecordingProvider:
    label: str
    closed: bool = False

    def __post_init__(self) -> None:
        self.requests = []

    def respond(self, request):
        self.requests.append(request)
        return AssistantText(text=f"{self.label}: {request.history[-1].text}")

    def close(self) -> None:
        self.closed = True


def configured_store(tmp_path) -> ProviderProfileStore:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        NamedProviderProfile(
            name="one",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="model-one",
            base_url="http://127.0.0.1:11434/v1",
        )
    )
    store.add_profile(
        NamedProviderProfile(
            name="two",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="model-two",
            base_url="http://127.0.0.1:11435/v1",
        )
    )
    return store


def test_compaction_runtime_lease_is_real_pinned_and_blocks_switches(tmp_path) -> None:
    store = configured_store(tmp_path)
    profile = store.get_profile("one")
    store.replace_profile(
        profile.profile_id,
        replace(
            profile.to_spec(),
            context_window_tokens=1000,
            model_max_output_tokens=100,
            max_output_tokens=20,
        ),
        expected_revision=profile.revision,
    )

    class CompactProvider(RecordingProvider):
        def count_compact_summary_input_tokens(self, request):
            return RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED)

        def summarize_compact(self, request):
            return AssistantText("summary")

    provider = CompactProvider("one")
    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: provider,
    )
    request = CompactSummaryRequest(build_compact_prompt(), "source", 20)

    with manager.provider_for_compaction() as runtime:
        assert runtime.status.generation == manager.status().generation
        assert runtime.assess_summary_request(request).decision == ContextFitDecision.FITS
        assert runtime.summarize(request) == AssistantText("summary")
        with pytest.raises(RuntimeProviderStateError, match="active operation"):
            manager.use_profile("two")
    assert manager.use_profile("two").status.profile == "two"


def test_turn_runtime_combines_assessment_summary_and_response_under_one_lease(
    tmp_path,
) -> None:
    store = configured_store(tmp_path)
    profile = store.get_profile("one")
    store.replace_profile(
        profile.profile_id,
        replace(
            profile.to_spec(),
            context_window_tokens=1000,
            model_max_output_tokens=100,
            max_output_tokens=20,
        ),
        expected_revision=profile.revision,
    )

    class PromptProvider(RecordingProvider):
        def count_input_tokens(self, request):
            return RequestTokenCount(20, RequestTokenCountMethod.ESTIMATED)

        def count_compact_summary_input_tokens(self, request):
            return RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED)

        def summarize_compact(self, request):
            return AssistantText("summary")

    provider = PromptProvider("one")
    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: provider,
    )
    conversation = ConversationRequest(build_system_prompt(), (UserMessage("hello"),))
    summary = CompactSummaryRequest(build_compact_prompt(), "source", 20)

    with manager.provider_for_turn() as runtime:
        assessment = runtime.assess_context(conversation)
        assert assessment.fit_report.decision == ContextFitDecision.FITS
        assert runtime.assess_summary_request(summary).decision == ContextFitDecision.FITS
        assert runtime.summarize(summary) == AssistantText("summary")
        assert runtime.respond(conversation) == AssistantText("one: hello")
        with pytest.raises(RuntimeProviderStateError, match="active operation"):
            manager.use_profile("two")

    assert manager.use_profile("two").status.profile == "two"


def test_turn_runtime_preflights_and_holds_lease_while_consuming_stream(tmp_path) -> None:
    store = configured_store(tmp_path)
    profile = store.get_profile("one")
    store.replace_profile(
        profile.profile_id,
        replace(
            profile.to_spec(),
            context_window_tokens=1000,
            model_max_output_tokens=100,
            max_output_tokens=20,
        ),
        expected_revision=profile.revision,
    )

    class StreamingProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.counts = 0
            self.stream_calls = 0

        def count_input_tokens(self, request):
            self.counts += 1
            return RequestTokenCount(20, RequestTokenCountMethod.ESTIMATED)

        def respond_stream(self, request, *, event_sink):
            self.stream_calls += 1
            self.requests.append(request)
            with pytest.raises(RuntimeProviderStateError, match="active operation"):
                manager.use_profile("two")
            event_sink(ProviderTextDelta("streamed"))
            return AssistantText("streamed")

    provider = StreamingProvider("one")
    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: provider,
    )
    conversation = ConversationRequest(build_system_prompt(), (UserMessage("hello"),))
    events = []

    with manager.provider_for_turn() as runtime:
        assert runtime.streaming_supported is True
        assert runtime.respond_stream(conversation, event_sink=events.append) == AssistantText(
            "streamed"
        )

    assert provider.counts == 1
    assert provider.stream_calls == 1
    assert events == [ProviderTextDelta("streamed")]
    assert manager.use_profile("two").status.profile == "two"


def test_runtime_accounts_actual_and_unknown_usage_and_resets_after_switch(tmp_path) -> None:
    class UsageProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.outcomes = [ProviderTokenUsage(20, 5), None]

        def respond_outcome(self, request):
            self.requests.append(request)
            usage = self.outcomes.pop(0)
            return ProviderResponseOutcome(AssistantText("done"), False, usage)

    providers = []

    def factory(route, *, environment):
        provider = UsageProvider(route.wire_model)
        providers.append(provider)
        return provider

    manager = RuntimeProviderManager(
        configured_store(tmp_path),
        environment={},
        profile="one",
        provider_factory=factory,
    )
    conversation = ConversationRequest(build_system_prompt(), (UserMessage("hello"),))
    cursor = manager.begin_turn_usage()
    with manager.provider_for_turn() as runtime:
        runtime.respond(conversation)
        runtime.respond(conversation)
    usage = manager.finish_turn_usage(cursor)

    assert usage.turn_totals.input_tokens == 20
    assert usage.turn_totals.output_tokens == 5
    assert usage.turn_totals.known_invocations == 1
    assert usage.turn_totals.unknown_invocations == 1

    manager.use_profile("two")
    assert manager.usage_snapshot().latest_invocation is None


def test_turn_runtime_preflights_and_accounts_session_title_as_turn_usage(tmp_path) -> None:
    class TitleProvider(RecordingProvider):
        def count_session_title_input_tokens(self, request):
            assert request.conversation_request.allow_tools is False
            return RequestTokenCount(12, RequestTokenCountMethod.ESTIMATED)

        def generate_session_title_outcome(self, request):
            return ProviderResponseOutcome(
                AssistantText("Provider runtime review"),
                False,
                ProviderTokenUsage(12, 4),
            )

    provider = TitleProvider("one")
    manager = RuntimeProviderManager(
        configured_store(tmp_path),
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: provider,
    )
    cursor = manager.begin_turn_usage()

    with manager.provider_for_turn() as runtime:
        response = runtime.generate_session_title(build_session_title_request("Review runtime"))
    usage = manager.finish_turn_usage(cursor)

    assert response == AssistantText("Provider runtime review")
    assert len(usage.latest_turn) == 1
    assert usage.latest_turn[0].kind == ProviderInvocationKind.TURN
    assert usage.latest_turn[0].usage == ProviderTokenUsage(12, 4)


def test_non_turn_provider_operations_share_bounded_reliability_policy(tmp_path) -> None:
    class ReliableOperationsProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.title_calls = 0
            self.review_calls = 0
            self.compact_calls = 0

        def count_session_title_input_tokens(self, request):
            return RequestTokenCount(12, RequestTokenCountMethod.ESTIMATED)

        def count_input_tokens(self, request):
            return RequestTokenCount(12, RequestTokenCountMethod.ESTIMATED)

        def count_compact_summary_input_tokens(self, request):
            return RequestTokenCount(12, RequestTokenCountMethod.ESTIMATED)

        def generate_session_title_outcome(self, request):
            self.title_calls += 1
            if self.title_calls == 1:
                raise adapter_error(
                    provider_id="custom",
                    model_id=self.label,
                    kind=ProviderFailureKind.TRANSPORT,
                    code="temporary_transport",
                    message="temporary title transport failure",
                    retryable=True,
                )
            return ProviderResponseOutcome(
                AssistantText("Recovered title"),
                False,
                ProviderTokenUsage(12, 2),
            )

        def respond(self, request):
            self.review_calls += 1
            if self.review_calls == 1:
                raise adapter_error(
                    provider_id="custom",
                    model_id=self.label,
                    kind=ProviderFailureKind.TIMEOUT,
                    code="temporary_timeout",
                    message="temporary review timeout",
                    retryable=True,
                )
            return AssistantText("review")

        def summarize_compact(self, request):
            self.compact_calls += 1
            if self.compact_calls == 1:
                raise adapter_error(
                    provider_id="custom",
                    model_id=self.label,
                    kind=ProviderFailureKind.PROVIDER_UNAVAILABLE,
                    code="temporary_unavailable",
                    message="temporary compact outage",
                    retryable=True,
                )
            return AssistantText("summary")

    provider = ReliableOperationsProvider("one")
    manager = RuntimeProviderManager(
        configured_store(tmp_path),
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: provider,
        reliability_policy=ProviderReliabilityPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
        ),
    )
    with manager.provider_for_turn() as runtime:
        assert runtime.generate_session_title(
            build_session_title_request("retry title")
        ) == AssistantText("Recovered title")
        review_request = ConversationRequest(
            build_system_prompt(), (UserMessage("review"),), allow_tools=False
        )
        assert runtime.review(review_request) == AssistantText("review")
        summary = CompactSummaryRequest(build_compact_prompt(), "source", 20)
        assert runtime.summarize(summary) == AssistantText("summary")

    assert provider.title_calls == 2
    assert provider.review_calls == 2
    assert provider.compact_calls == 2
    usage = manager.usage_snapshot()
    assert usage.latest_compaction is not None
    assert usage.profile_compaction_totals.unknown_invocations == 2
    assert usage.profile_review_totals.unknown_invocations == 2
    assert usage.profile_turn_totals.known_invocations == 1
    assert usage.profile_turn_totals.unknown_invocations == 1


def test_runtime_usage_retains_known_usage_from_output_limit_failure(tmp_path) -> None:
    class LimitedProvider(RecordingProvider):
        def respond_outcome(self, request):
            self.requests.append(request)
            raise output_limit_error(
                provider_id="custom",
                model_id=self.label,
                message="provider response reached the configured output-token limit",
                requested_output_tokens=1024,
                usage=ProviderTokenUsage(30, 1024),
                partial_response_observed=True,
            )

    manager = RuntimeProviderManager(
        configured_store(tmp_path),
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: LimitedProvider(route.wire_model),
    )
    conversation = ConversationRequest(build_system_prompt(), (UserMessage("hello"),))
    cursor = manager.begin_turn_usage()

    with manager.provider_for_turn() as runtime:
        with pytest.raises(ProviderAdapterError):
            runtime.respond(conversation)
    usage = manager.finish_turn_usage(cursor)

    assert usage.turn_totals.input_tokens == 30
    assert usage.turn_totals.output_tokens == 1024
    assert usage.turn_totals.known_invocations == 1
    assert usage.turn_totals.unknown_invocations == 0


def test_compaction_usage_retains_known_usage_from_output_limit_failure(tmp_path) -> None:
    class LimitedCompactProvider(RecordingProvider):
        def count_compact_summary_input_tokens(self, request):
            return RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED)

        def summarize_compact_outcome(self, request):
            raise output_limit_error(
                provider_id="custom",
                model_id=self.label,
                message="compact summary reached the configured output-token limit",
                requested_output_tokens=request.max_output_tokens,
                usage=ProviderTokenUsage(40, 20),
                partial_response_observed=True,
            )

    manager = RuntimeProviderManager(
        configured_store(tmp_path),
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: LimitedCompactProvider(route.wire_model),
    )
    request = CompactSummaryRequest(build_compact_prompt(), "source", 20)

    with manager.provider_for_compaction() as runtime:
        with pytest.raises(ProviderAdapterError):
            runtime.summarize(request)
    usage = manager.usage_snapshot()

    assert usage.latest_compaction is not None
    assert usage.latest_compaction.usage == ProviderTokenUsage(40, 20)
    assert usage.profile_compaction_totals.known_invocations == 1


def test_context_transition_lease_is_pinned_read_only_and_releases_after_base_exception(
    tmp_path,
) -> None:
    store = configured_store(tmp_path)
    profile = store.get_profile("one")
    store.replace_profile(
        profile.profile_id,
        replace(
            profile.to_spec(),
            context_window_tokens=100,
            model_max_output_tokens=80,
            max_output_tokens=20,
        ),
        expected_revision=profile.revision,
    )

    class CountingProvider(RecordingProvider):
        def count_input_tokens(self, request):
            return RequestTokenCount(70, RequestTokenCountMethod.ESTIMATED)

    provider = CountingProvider("one")
    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: provider,
    )
    request = ConversationRequest(
        build_system_prompt(),
        (UserMessage("hello"), AssistantText("reply")),
    )

    with pytest.raises(KeyboardInterrupt):
        with manager.provider_for_context_transition() as runtime:
            assessment = runtime.assess_context(request)
            assert assessment.fit_report is not None
            assert assessment.fit_report.decision == ContextFitDecision.FITS
            assert provider.requests == []
            with pytest.raises(RuntimeProviderStateError, match="active operation"):
                manager.use_profile("two")
            with pytest.raises(RuntimeProviderStateError, match="already active"):
                with manager.provider_for_turn():
                    pass
            with pytest.raises(RuntimeProviderStateError, match="already active"):
                with manager.provider_for_compaction():
                    pass
            with pytest.raises(RuntimeProviderStateError, match="during a conversation turn"):
                manager.close()
            raise KeyboardInterrupt

    assert manager.use_profile("two").status.profile == "two"


def test_fake_runtime_rejects_controlled_compaction(tmp_path) -> None:
    manager = RuntimeProviderManager(configured_store(tmp_path), environment={})

    with pytest.raises(CompactionUnavailableError, match="real provider"):
        with manager.provider_for_compaction():
            raise AssertionError


def test_manager_reuses_client_and_atomically_switches_profiles(tmp_path) -> None:
    store = configured_store(tmp_path)
    constructed = []

    def factory(route, *, environment):
        provider = RecordingProvider(route.wire_model)
        constructed.append(provider)
        return provider

    manager = RuntimeProviderManager(store, environment={}, profile="one", provider_factory=factory)
    with manager.provider_for_turn() as first:
        assert first.provider is constructed[0]
        with pytest.raises(RuntimeProviderStateError, match="active operation"):
            manager.use_profile("two")
    result = manager.use_profile("two")
    status = result.status

    assert result.fit_report is None
    assert status.profile == "two"
    assert status.profile_name == "two"
    assert status.profile_id == store.get_profile("two").profile_id
    assert status.profile_revision == 1
    assert status.profile_fingerprint == store.get_profile("two").fingerprint()
    assert status.route_fingerprint is not None
    assert len(status.route_fingerprint) == 64
    assert status.model_override is None
    assert status.selected_model == "model-two"
    assert store.active_name("project") == "two"
    assert constructed[0].closed is True
    with manager.provider_for_turn() as current:
        assert current.provider is constructed[1]


def test_manager_failed_switch_preserves_client_and_persistence(tmp_path) -> None:
    store = configured_store(tmp_path)
    first = RecordingProvider("one")

    def factory(route, *, environment):
        if route.wire_model == "model-two":
            raise RuntimeError("construction failed")
        return first

    manager = RuntimeProviderManager(store, environment={}, profile="one", provider_factory=factory)
    with pytest.raises(RuntimeError, match="construction failed"):
        manager.use_profile("two")

    assert manager.status().profile == "one"
    assert store.active_name("project") is None
    assert first.closed is False


def test_output_budget_update_is_process_local_atomic_and_preserves_usage(tmp_path) -> None:
    store = configured_store(tmp_path)
    original = store.get_profile("one")
    store.replace_profile(
        original.profile_id,
        replace(
            original.to_spec(),
            context_window_tokens=1000,
            model_max_output_tokens=100,
            max_output_tokens=20,
        ),
        expected_revision=original.revision,
    )
    providers = []
    routes = []

    class CountingProvider(RecordingProvider):
        def count_input_tokens(self, request):
            return RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED)

    def factory(route, *, environment):
        routes.append(route)
        provider = CountingProvider(route.wire_model)
        providers.append(provider)
        return provider

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=factory,
    )
    request = ConversationRequest(build_system_prompt(), (UserMessage("hello"),))
    cursor = manager.begin_turn_usage()
    with manager.provider_for_turn() as runtime:
        runtime.respond(request)
        with pytest.raises(RuntimeProviderStateError, match="active operation"):
            manager.set_output_budget(40)
    before_usage = manager.finish_turn_usage(cursor)

    result = manager.set_output_budget(40, committed_context=request)

    assert result.changed is True
    assert result.previous_output_tokens == 20
    assert result.fit_report is not None
    assert result.fit_report.decision == ContextFitDecision.FITS
    assert result.status.max_output_tokens == 40
    assert result.status.default_max_output_tokens == 20
    assert result.status.max_output_tokens_source == "runtime"
    assert result.status.generation == 1
    assert routes[-1].max_output_tokens == 40
    assert providers[0].closed is True
    assert store.get_profile("one").max_output_tokens == 20
    after_usage = manager.usage_snapshot()
    assert after_usage.runtime_generation == 1
    assert after_usage.profile_turn_totals == before_usage.profile_turn_totals
    assert after_usage.latest_context is None

    reset = manager.set_output_budget(None, committed_context=request)

    assert reset.status.max_output_tokens == 20
    assert reset.status.default_max_output_tokens == 20
    assert reset.status.max_output_tokens_source == "profile"
    assert reset.status.generation == 2
    assert manager.usage_snapshot().profile_turn_totals == before_usage.profile_turn_totals


def test_output_budget_rejects_known_model_limit_without_changing_runtime(tmp_path) -> None:
    store = configured_store(tmp_path)
    original = store.get_profile("one")
    store.replace_profile(
        original.profile_id,
        replace(
            original.to_spec(),
            context_window_tokens=1000,
            model_max_output_tokens=30,
            max_output_tokens=20,
        ),
        expected_revision=original.revision,
    )
    providers = []

    def factory(route, *, environment):
        provider = RecordingProvider(route.wire_model)
        providers.append(provider)
        return provider

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=factory,
    )
    before = manager.status()

    with pytest.raises(RuntimeSwitchContextError) as caught:
        manager.set_output_budget(
            40,
            committed_context=ConversationRequest(build_system_prompt(), ()),
        )

    assert caught.value.report.decision == ContextFitDecision.MODEL_OUTPUT_EXCEEDED
    assert manager.status() == before
    assert providers[0].closed is False
    assert providers[1].closed is True


def test_model_switch_preserves_output_override_and_profile_switch_clears_it(tmp_path) -> None:
    store = configured_store(tmp_path)
    routes = []

    def factory(route, *, environment):
        routes.append(route)
        return RecordingProvider(route.wire_model)

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        max_output_tokens=2048,
        provider_factory=factory,
    )

    assert manager.status().max_output_tokens_source == "cli"
    assert manager.set_model("model-override").status.max_output_tokens == 2048
    assert routes[-1].max_output_tokens == 2048

    switched = manager.use_profile("two").status

    assert switched.max_output_tokens == 1024
    assert switched.default_max_output_tokens == 1024
    assert switched.max_output_tokens_source == "profile"


def test_fake_runtime_rejects_output_budget_override(tmp_path) -> None:
    store = configured_store(tmp_path)
    with pytest.raises(RuntimeProviderStateError, match="real provider runtime"):
        RuntimeProviderManager(store, environment={}, max_output_tokens=20)

    manager = RuntimeProviderManager(store, environment={})
    with pytest.raises(RuntimeProviderStateError, match="real provider runtime"):
        manager.set_output_budget(20)


def test_reset_clears_equal_cli_override_source(tmp_path) -> None:
    manager = RuntimeProviderManager(
        configured_store(tmp_path),
        environment={},
        profile="one",
        max_output_tokens=1024,
        provider_factory=lambda route, *, environment: RecordingProvider(route.wire_model),
    )
    assert manager.status().max_output_tokens_source == "cli"

    result = manager.set_output_budget(None)

    assert result.changed is True
    assert result.previous_output_tokens == result.status.max_output_tokens == 1024
    assert result.status.max_output_tokens_source == "profile"


def test_project_session_preserves_neutral_history_across_provider_switch(tmp_path) -> None:
    store = configured_store(tmp_path)
    providers = {}

    def factory(route, *, environment):
        provider = RecordingProvider(route.wire_model)
        providers[route.wire_model] = provider
        return provider

    session = ProjectSession.open(
        tmp_path,
        profile="one",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=factory,
    )
    assert session.prompt("first") == "model-one: first"
    session.use_profile("two")
    assert session.prompt("second") == "model-two: second"

    assert session.history == (
        UserMessage("first"),
        AssistantText("model-one: first"),
        UserMessage("second"),
        AssistantText("model-two: second"),
    )
    assert providers["model-two"].requests[0].history[:2] == session.history[:2]
    assert (
        providers["model-one"].requests[0].system_prompt
        == providers["model-two"].requests[0].system_prompt
    )
    assert all(
        request.system_prompt.text not in repr(item)
        for provider in providers.values()
        for request in provider.requests
        for item in request.history
    )


def test_user_scope_switch_respects_existing_project_precedence(tmp_path) -> None:
    store = configured_store(tmp_path)
    store.set_active("one", scope="project")
    providers = []

    def factory(route, *, environment):
        provider = RecordingProvider(route.wire_model)
        providers.append(provider)
        return provider

    manager = RuntimeProviderManager(store, environment={}, provider_factory=factory)
    result = manager.use_profile("two", scope="user")
    status = result.status

    assert store.active_name("user") == "two"
    assert store.active_selection().name == "one"
    assert status.profile == "one"
    assert status.selection_source == "project"
    assert status.selected_model == "model-one"


def test_direct_runtime_supports_process_local_model_switch(tmp_path) -> None:
    store = configured_store(tmp_path)
    constructed = []

    def factory(route, *, environment):
        constructed.append(route)
        return RecordingProvider(route.wire_model)

    manager = RuntimeProviderManager(
        store,
        environment={},
        model="local/model-one",
        provider_factory=factory,
    )
    result = manager.set_model("model-two")
    status = result.status

    assert status.profile is None
    assert status.profile_id is None
    assert status.profile_revision is None
    assert status.profile_fingerprint is None
    assert status.route_fingerprint is not None
    assert status.model_override == "model-two"
    assert status.selected_model == "model-two"
    assert constructed[-1].wire_model == "model-two"


def test_manager_sets_and_resets_process_local_reasoning_effort(tmp_path) -> None:
    store = configured_store(tmp_path)
    routes = []

    def factory(route, *, environment):
        routes.append(route)
        return RecordingProvider(route.wire_model)

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=factory,
    )

    changed = manager.set_reasoning_effort(ReasoningEffort.MAX)
    reset = manager.set_reasoning_effort(None)

    assert changed.changed is True
    assert changed.status.reasoning_effort == "max"
    assert reset.changed is True
    assert reset.status.reasoning_effort is None
    assert routes[-2].reasoning_effort is ReasoningEffort.MAX
    assert routes[-1].reasoning_effort is None


def test_manager_preserves_profile_reasoning_default_without_invocation_override(tmp_path) -> None:
    store = configured_store(tmp_path)
    profile = store.get_profile("one")
    store.replace_profile(
        profile.profile_id,
        replace(
            profile.to_spec(),
            default_reasoning_effort=ReasoningEffort.HIGH,
            reasoning=ReasoningProfile.from_mapping(
                {
                    "native_kind": "effort",
                    "native_levels": ["deep"],
                    "mapping": {"high": "deep"},
                }
            ),
        ),
        expected_revision=profile.revision,
    )
    routes = []

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: (
            routes.append(route) or RecordingProvider(route.wire_model)
        ),
    )

    assert routes[0].reasoning_effort is ReasoningEffort.HIGH
    assert manager.status().reasoning_effort == "high"


def test_manager_set_model_tracks_profile_by_id_across_rename(tmp_path) -> None:
    store = configured_store(tmp_path)
    original = store.get_profile("one")
    routes = []

    def factory(route, *, environment):
        routes.append(route)
        return RecordingProvider(route.wire_model)

    manager = RuntimeProviderManager(store, environment={}, profile="one", provider_factory=factory)
    renamed = store.rename_profile(original.profile_id, "renamed", expected_revision=1)

    result = manager.set_model("override-model")
    status = result.status

    assert status.profile == "renamed"
    assert status.profile_id == original.profile_id
    assert status.profile_revision == renamed.revision
    assert status.model_override == "override-model"
    assert routes[-1].wire_model == "override-model"


def test_runtime_resolves_profile_override_and_model_override_independently(tmp_path) -> None:
    store = configured_store(tmp_path)
    original = store.get_profile("one")
    store.replace_profile(
        original.profile_id,
        replace(original.to_spec(), context_window_tokens=65_536),
        expected_revision=original.revision,
    )

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: RecordingProvider(route.wire_model),
    )
    assert manager.status().context_window_tokens == 65_536
    assert manager.status().context_window_source == ModelContextSource.PROFILE_OVERRIDE

    switched = manager.set_model("other").status
    assert switched.context_window_tokens is None
    assert switched.context_window_source == ModelContextSource.UNKNOWN


def test_runtime_discovery_failure_is_nonfatal_and_redacted(tmp_path) -> None:
    store = configured_store(tmp_path)

    class DiscoveringProvider(RecordingProvider):
        def discover_model_context(self):
            raise RuntimeError("secret provider response")

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: DiscoveringProvider(route.wire_model),
        context_resolver=ModelContextCapabilityResolver(),
    )

    status = manager.status()
    assert status.context_window_tokens is None
    assert status.context_window_source == ModelContextSource.UNKNOWN
    assert "secret" not in (status.context_window_diagnostic or "")


def test_preflight_rejects_known_overflow_before_provider_send(tmp_path) -> None:
    store = configured_store(tmp_path)
    original = store.get_profile("one")
    store.replace_profile(
        original.profile_id,
        replace(
            original.to_spec(),
            context_window_tokens=100,
            model_max_output_tokens=80,
            max_output_tokens=20,
        ),
        expected_revision=original.revision,
    )

    class CountingProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.stream_calls = 0

        def count_input_tokens(self, request):
            return RequestTokenCount(81, RequestTokenCountMethod.ESTIMATED)

        def respond_stream(self, request, *, event_sink):
            self.stream_calls += 1
            return AssistantText("must not run")

    provider = CountingProvider("model-one")
    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: provider,
    )
    with manager.provider_for_turn() as runtime:
        request = ConversationRequest(build_system_prompt(), (UserMessage("too large"),))
        with pytest.raises(ContextPreflightError, match="input=81"):
            runtime.respond(request)
        with pytest.raises(ContextPreflightError, match="input=81"):
            runtime.respond_stream(request, event_sink=lambda _event: None)
    assert provider.requests == []
    assert provider.stream_calls == 0
    assert manager.usage_snapshot().latest_invocation is None


def test_switch_rejects_known_committed_context_overflow_without_changing_state(
    tmp_path,
) -> None:
    store = configured_store(tmp_path)
    target = store.get_profile("two")
    store.replace_profile(
        target.profile_id,
        replace(
            target.to_spec(),
            context_window_tokens=100,
            model_max_output_tokens=80,
            max_output_tokens=20,
        ),
        expected_revision=target.revision,
    )
    providers = []

    class CountingProvider(RecordingProvider):
        def count_input_tokens(self, request):
            return RequestTokenCount(81, RequestTokenCountMethod.ESTIMATED)

    def factory(route, *, environment):
        provider = CountingProvider(route.wire_model)
        providers.append(provider)
        return provider

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=factory,
    )
    before = manager.status()
    request = ConversationRequest(
        build_system_prompt(),
        (UserMessage("hello"), AssistantText("reply")),
    )

    with pytest.raises(RuntimeSwitchContextError) as caught:
        manager.use_profile("two", committed_context=request)

    assert caught.value.report.decision == ContextFitDecision.CONTEXT_EXCEEDED
    assert manager.status() == before
    assert store.active_name("project") is None
    assert providers[0].closed is False
    assert providers[1].closed is True


def test_switch_allows_unknown_count_with_explicit_report(tmp_path) -> None:
    store = configured_store(tmp_path)
    target = store.get_profile("two")
    store.replace_profile(
        target.profile_id,
        replace(target.to_spec(), context_window_tokens=100),
        expected_revision=target.revision,
    )

    class FailingCounter(RecordingProvider):
        def count_input_tokens(self, request):
            raise RuntimeError("raw provider secret")

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: FailingCounter(route.wire_model),
    )
    result = manager.use_profile(
        "two",
        committed_context=ConversationRequest(
            build_system_prompt(),
            (UserMessage("hello"), AssistantText("reply")),
        ),
    )

    assert result.status.profile == "two"
    assert result.fit_report is not None
    assert result.fit_report.decision == ContextFitDecision.UNKNOWN
    assert "secret" not in (result.fit_report.input_count.diagnostic or "")


def test_switch_model_output_limit_precedes_counting(tmp_path) -> None:
    store = configured_store(tmp_path)
    target = store.get_profile("two")
    store.replace_profile(
        target.profile_id,
        replace(
            target.to_spec(),
            context_window_tokens=100,
            max_output_tokens=20,
        ),
        expected_revision=target.revision,
    )
    count_calls = []

    class OutputLimitedResolver:
        def resolve(self, route, **kwargs):
            return ModelContextCapability(
                target=ModelContextTarget.from_route(route),
                context_window_tokens=100,
                source=ModelContextSource.PROFILE_OVERRIDE,
                model_max_output_tokens=10,
                model_max_output_source=ModelContextSource.LIVE_DISCOVERY,
            )

    class CountingProvider(RecordingProvider):
        def count_input_tokens(self, request):
            count_calls.append(request)
            return RequestTokenCount(1, RequestTokenCountMethod.EXACT)

    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: CountingProvider(route.wire_model),
        context_resolver=OutputLimitedResolver(),
    )

    with pytest.raises(RuntimeSwitchContextError) as caught:
        manager.use_profile(
            "two",
            committed_context=ConversationRequest(build_system_prompt(), ()),
        )

    assert caught.value.report.decision == ContextFitDecision.MODEL_OUTPUT_EXCEEDED
    assert count_calls == []


def test_current_context_assessment_is_read_only_and_never_generates(tmp_path) -> None:
    store = configured_store(tmp_path)
    original = store.get_profile("one")
    store.replace_profile(
        original.profile_id,
        replace(
            original.to_spec(),
            context_window_tokens=100,
            model_max_output_tokens=80,
            max_output_tokens=20,
        ),
        expected_revision=original.revision,
    )

    class CountingProvider(RecordingProvider):
        def count_input_tokens(self, request):
            return RequestTokenCount(70, RequestTokenCountMethod.EXACT)

    provider = CountingProvider("model-one")
    manager = RuntimeProviderManager(
        store,
        environment={},
        profile="one",
        provider_factory=lambda route, *, environment: provider,
    )
    before = manager.status()

    assessment = manager.assess_current_context(
        ConversationRequest(
            build_system_prompt(),
            (UserMessage("hello"), AssistantText("reply")),
        )
    )

    assert assessment.status == before
    assert assessment.fit_report is not None
    assert assessment.fit_report.decision == ContextFitDecision.FITS
    assert assessment.fit_report.input_count.input_tokens == 70
    assert provider.requests == []
    assert manager.status() == before
    with manager.provider_for_turn():
        pass


def test_fake_current_context_assessment_is_explicitly_unavailable(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    manager = RuntimeProviderManager(store, environment={})

    assessment = manager.assess_current_context(ConversationRequest(build_system_prompt(), ()))

    assert assessment.status.mode == "fake"
    assert assessment.fit_report is None
    assert "unavailable" in assessment.unavailable_diagnostic


def test_fake_runtime_has_explicit_empty_provenance(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    manager = RuntimeProviderManager(store, environment={})

    status = manager.status()

    assert status.mode == "fake"
    assert status.profile_id is None
    assert status.profile_revision is None
    assert status.profile_fingerprint is None
    assert status.route_fingerprint is None
    assert status.model_override is None


def test_project_session_constructs_all_tools_from_the_resolved_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    seen: list[tuple[str, object]] = []

    def read_factory(path):
        seen.append(("read_file", path))
        return ReadFileTool(path)

    def glob_factory(path):
        seen.append(("glob", path))
        return GlobTool(path)

    def grep_factory(path):
        seen.append(("grep", path))
        return GrepTool(path)

    session = ProjectSession.open(
        workspace / ".",
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        read_file_factory=read_factory,
        glob_factory=glob_factory,
        grep_factory=grep_factory,
    )
    session.close()

    assert seen == [
        ("read_file", workspace.resolve()),
        ("glob", workspace.resolve()),
        ("grep", workspace.resolve()),
    ]


def test_session_closes_provider_when_tool_construction_fails(tmp_path) -> None:
    store = configured_store(tmp_path)
    provider = RecordingProvider("model-one")

    with pytest.raises(RuntimeError, match="tool failed"):
        ProjectSession.open(
            tmp_path,
            profile="one",
            environment={},
            user_profile_path=store.user_path,
            project_profile_path=store.project_path,
            provider_factory=lambda route, *, environment: provider,
            read_file_factory=lambda path: (_ for _ in ()).throw(RuntimeError("tool failed")),
        )

    assert provider.closed is True


def test_project_session_pins_provider_for_tool_continuation(tmp_path) -> None:
    (tmp_path / "README.md").write_text("notes\n", encoding="utf-8")
    store = configured_store(tmp_path)

    class ToolProvider:
        def __init__(self):
            self.calls = 0
            self.requests = []

        def respond(self, request):
            self.calls += 1
            self.requests.append(request)
            if self.calls == 1:
                return ToolUse(
                    "call-1", "read_file", ToolArguments.from_mapping({"path": "README.md"})
                )
            assert request.history[-1] == ToolResult("call-1", "notes\n")
            return AssistantText("done")

    provider = ToolProvider()
    session = ProjectSession.open(
        tmp_path,
        profile="one",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: provider,
    )

    assert session.prompt("read it") == "done"
    assert provider.calls == 2
    assert provider.requests[0].system_prompt is provider.requests[1].system_prompt
    assert session.status().credential_present is False
