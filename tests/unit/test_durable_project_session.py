from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from leonervis_code.agent.tool_events import (
    AssistantToolTextReceived,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestStarted,
    ToolTurnSummaryCommitted,
)
from leonervis_code.core.contracts import (
    ToolArguments,
    AssistantText,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    UserMessage,
)
from leonervis_code.core.compaction import CompactionCandidateError
from leonervis_code.core.cancellation import TurnCancellation, TurnCancelled
from leonervis_code.providers.definitions import WireProtocol
from leonervis_code.providers.manager import RuntimeSwitchAuditError
from leonervis_code.providers.errors import ProviderAdapterError, output_limit_error
from leonervis_code.providers.profile import ProviderProfileSpec
from leonervis_code.providers.profile_store import ProviderProfileStore
from leonervis_code.providers.request_context import (
    ContextFitDecision,
    RequestTokenCount,
    RequestTokenCountMethod,
)
from leonervis_code.providers.streaming import ProviderResponseOutcome
from leonervis_code.providers.usage import ProviderInvocationKind, ProviderTokenUsage
from leonervis_code.session import (
    AutoCompactionCommitted,
    AutoCompactionNotApplied,
    AutoCompactionStarted,
    ProjectSession,
    ResumeEffect,
    SessionResumeContextError,
    TurnCommitStarted,
)
from leonervis_code.session_records import (
    BindingSnapshot,
    CompactionFailed,
    SessionNameSource,
    TurnCommitted,
)
from leonervis_code.session_store import SessionStore, SessionStoreError
from leonervis_code.system_prompt import build_system_prompt

SESSION_ONE = "12345678-1234-4234-9234-123456789abc"
SESSION_TWO = "22345678-1234-4234-9234-123456789abc"
NOW = "2026-07-17T12:00:00.000000Z"


@dataclass
class RecordingProvider:
    label: str
    requests: list = None

    def __post_init__(self) -> None:
        self.requests = []
        self.summary_requests = []

    def count_input_tokens(self, request):
        value = 100 if request.effective_summary is not None else 1000 + len(request.history)
        return RequestTokenCount(value, RequestTokenCountMethod.ESTIMATED)

    def count_compact_summary_input_tokens(self, request):
        return RequestTokenCount(len(request.source_text), RequestTokenCountMethod.ESTIMATED)

    def summarize_compact(self, request):
        self.summary_requests.append(request)
        return AssistantText("Earlier turns summarized compactly.")

    def respond(self, request):
        self.requests.append(request)
        return AssistantText(f"{self.label}: {request.history[-1].text}")


def session_store_factory(*ids: str):
    values = iter(ids)

    def factory(workspace: Path) -> SessionStore:
        return SessionStore(
            workspace,
            uuid_factory=lambda: UUID(next(values)),
            clock=lambda: NOW,
        )

    return factory


def test_project_session_persists_and_resumes_history_with_current_runtime(tmp_path: Path) -> None:
    first = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    assert first.prompt("hello") == "Fake response: hello"
    first_ledgers = first.tool_ledgers(5)
    assert first_ledgers.total_turns == 1
    assert first_ledgers.turns[0].ledger is not None
    assert first_ledgers.turns[0].ledger.requested == 0
    transcript = first.transcript_path
    first.close()

    second = ProjectSession.open(
        tmp_path,
        resume=SESSION_ONE,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        session_store_factory=session_store_factory(SESSION_TWO),
    )

    assert second.history == (UserMessage("hello"), AssistantText("Fake response: hello"))
    assert second.prompt("again") == "Fake response: again"
    resumed_ledgers = second.tool_ledgers(1)
    assert resumed_ledgers.total_turns == 2
    assert resumed_ledgers.turns[0].turn_number == 2
    assert second.transcript_path == transcript
    second.close()


def test_project_session_names_after_commit_and_renames_without_changing_context(
    tmp_path: Path,
) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    assert session.session_info().name == "New session 1"
    assert session.session_info().name_source == SessionNameSource.DEFAULT

    session.prompt("Review provider adapters")
    assert session.session_info().name == "Review provider adapters"
    assert session.session_info().name_source == SessionNameSource.MODEL
    history = session.history
    context_id = session.inspect_context().context_id

    renamed = session.rename_session("Release review")
    assert renamed.name == "Release review"
    assert renamed.name_source == SessionNameSource.MANUAL
    assert session.history == history
    assert session.inspect_context().context_id == context_id

    restored = session.rename_session()
    assert restored.name == "Review provider adapters"
    assert restored.name_source == SessionNameSource.MODEL
    assert session.history == history
    assert session.inspect_context().context_id == context_id
    session.close()


def test_project_session_retries_conflicting_model_title_then_commits_unique_title(
    tmp_path: Path,
) -> None:
    profile_store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    profile_store.add_profile(
        ProviderProfileSpec(
            name="titles",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="title-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )
    seed_store = SessionStore(
        tmp_path,
        uuid_factory=lambda: UUID(SESSION_ONE),
        clock=lambda: NOW,
    )
    seed = seed_store.create(BindingSnapshot.fake())
    seed.append_turn(
        (UserMessage("seed"), AssistantText("done")),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(),
        session_name="Adapter review",
        session_name_source=SessionNameSource.MODEL,
    )
    seed.release()

    class TitleProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.title_requests = []
            self.titles = ["Adapter review", "Unique adapter review"]

        def respond_outcome(self, request):
            self.requests.append(request)
            return ProviderResponseOutcome(
                AssistantText("done"), False, ProviderTokenUsage(100, 10)
            )

        def count_session_title_input_tokens(self, request):
            return RequestTokenCount(20, RequestTokenCountMethod.ESTIMATED)

        def generate_session_title_outcome(self, request):
            self.title_requests.append(request)
            return ProviderResponseOutcome(
                AssistantText(self.titles.pop(0)), False, ProviderTokenUsage(20, 4)
            )

    provider = TitleProvider("title-model")
    session = ProjectSession.open(
        tmp_path,
        profile="titles",
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_TWO),
    )

    session.prompt("Review provider adapters")

    assert session.session_info().name == "Unique adapter review"
    assert session.session_info().name_source == SessionNameSource.MODEL
    assert len(provider.title_requests) == 2
    assert provider.title_requests[0].rejected_titles == ()
    assert provider.title_requests[1].rejected_titles == ("Adapter review",)
    committed = next(
        record for record in session._writer.state.records if isinstance(record, TurnCommitted)
    )
    assert len(committed.provider_usage) == 3
    assert all(item.kind == ProviderInvocationKind.TURN for item in committed.provider_usage)
    session.close()


def test_project_session_uses_numbered_fallback_after_three_title_collisions(
    tmp_path: Path,
) -> None:
    profile_store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    profile_store.add_profile(
        ProviderProfileSpec(
            name="titles",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="title-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )
    seed_store = SessionStore(
        tmp_path,
        uuid_factory=lambda: UUID(SESSION_ONE),
        clock=lambda: NOW,
    )
    seed = seed_store.create(BindingSnapshot.fake())
    seed.append_turn(
        (UserMessage("seed"), AssistantText("done")),
        binding=BindingSnapshot.fake(),
        tool_ledger=ToolTurnLedger(),
        session_name="Repeated title",
        session_name_source=SessionNameSource.MODEL,
    )
    seed.release()

    class RepeatingTitleProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.title_requests = []

        def respond(self, request):
            self.requests.append(request)
            return AssistantText("done")

        def count_session_title_input_tokens(self, request):
            return RequestTokenCount(20, RequestTokenCountMethod.ESTIMATED)

        def generate_session_title_outcome(self, request):
            self.title_requests.append(request)
            return ProviderResponseOutcome(AssistantText("Repeated title"), False, None)

    provider = RepeatingTitleProvider("title-model")
    session = ProjectSession.open(
        tmp_path,
        profile="titles",
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_TWO),
    )

    session.prompt("Repeated title")

    assert len(provider.title_requests) == 3
    assert session.session_info().name == "Repeated title (2)"
    assert session.session_info().name_source == SessionNameSource.FALLBACK
    session.close()


def test_project_session_does_not_exceed_24_provider_invocations_for_title(
    tmp_path: Path,
) -> None:
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    profile_store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    profile_store.add_profile(
        ProviderProfileSpec(
            name="budget",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="budget-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )

    class BudgetProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.title_requests = []

        def respond(self, request):
            self.requests.append(request)
            index = len(self.requests)
            if index < 24:
                return ToolUse(
                    f"read-{index}",
                    "read_file",
                    ToolArguments.from_mapping({"path": "seed.txt"}),
                )
            return AssistantText("done")

        def generate_session_title_outcome(self, request):
            self.title_requests.append(request)
            raise AssertionError("title request exceeded the provider invocation budget")

    provider = BudgetProvider("budget-model")
    session = ProjectSession.open(
        tmp_path,
        profile="budget",
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )

    session.prompt("Read repeatedly")

    assert len(provider.requests) == 24
    assert provider.title_requests == []
    assert session.session_info().name == "Read repeatedly"
    assert session.session_info().name_source == SessionNameSource.FALLBACK
    committed = next(
        record for record in session._writer.state.records if isinstance(record, TurnCommitted)
    )
    assert len(committed.provider_usage) == 24
    session.close()


def test_project_session_falls_back_after_one_title_provider_failure(tmp_path: Path) -> None:
    profile_store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    profile_store.add_profile(
        ProviderProfileSpec(
            name="titles",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="title-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )

    class FailingTitleProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.title_calls = 0

        def generate_session_title_outcome(self, request):
            self.title_calls += 1
            raise RuntimeError("title endpoint unavailable")

    provider = FailingTitleProvider("title-model")
    session = ProjectSession.open(
        tmp_path,
        profile="titles",
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )

    session.prompt("Review title fallback")

    assert provider.title_calls == 1
    assert session.session_info().name == "Review title fallback"
    assert session.session_info().name_source == SessionNameSource.FALLBACK
    assert len(session.history) == 2
    session.close()


def test_project_session_cancellation_after_title_response_commits_neither_turn_nor_name(
    tmp_path: Path,
) -> None:
    profile_store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    profile_store.add_profile(
        ProviderProfileSpec(
            name="titles",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="title-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )
    cancellation = TurnCancellation()

    class CancellingTitleProvider(RecordingProvider):
        def generate_session_title_outcome(self, request):
            cancellation.request()
            return ProviderResponseOutcome(AssistantText("Should not commit"), False, None)

    session = ProjectSession.open(
        tmp_path,
        profile="titles",
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        provider_factory=lambda route, *, environment: CancellingTitleProvider("title-model"),
        session_store_factory=session_store_factory(SESSION_ONE),
    )

    with pytest.raises(TurnCancelled):
        session.prompt("Cancel during naming", cancellation=cancellation)

    assert session.history == ()
    assert session.session_info().turn_count == 0
    assert session.session_info().name == "New session 1"
    assert session.session_info().name_source == SessionNameSource.DEFAULT
    session.close()


def test_project_session_persists_known_turn_usage_across_resume(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="usage",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="usage-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )

    class UsageProvider(RecordingProvider):
        def respond_outcome(self, request):
            self.requests.append(request)
            return ProviderResponseOutcome(
                AssistantText("done"),
                False,
                ProviderTokenUsage(120, 30),
            )

    first = ProjectSession.open(
        tmp_path,
        profile="usage",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: UsageProvider("usage"),
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    assert first.prompt("hello") == "done"
    durable = first.session_usage()
    assert durable.totals.input_tokens == 120
    assert durable.totals.output_tokens == 30
    assert durable.unavailable_operations == 0
    assert first.turn_usage_history().operations[0].outcome == "committed"
    first.close()

    resumed = ProjectSession.open(
        tmp_path,
        resume=SESSION_ONE,
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: UsageProvider("usage"),
        session_store_factory=session_store_factory(SESSION_TWO),
    )
    assert resumed.session_usage().totals.input_tokens == 120
    assert resumed.session_usage().operations[0].model == "usage-model"
    resumed.close()


def test_project_session_persists_failed_provider_usage(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="usage-failure",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="usage-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )

    class FailingUsageProvider(RecordingProvider):
        def respond_outcome(self, request):
            raise output_limit_error(
                provider_id="custom",
                model_id="usage-model",
                message="output limit",
                requested_output_tokens=4096,
                usage=ProviderTokenUsage(200, 4096),
                partial_response_observed=True,
            )

    session = ProjectSession.open(
        tmp_path,
        profile="usage-failure",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: FailingUsageProvider("usage"),
        session_store_factory=session_store_factory(SESSION_ONE),
    )

    with pytest.raises(ProviderAdapterError, match="output limit"):
        session.prompt("long answer")

    durable = session.session_usage()
    assert durable.totals.input_tokens == 200
    assert durable.totals.output_tokens == 4096
    assert durable.operations[0].outcome == "failed"
    assert session.history == ()
    session.close()


def test_project_session_persists_exact_multiline_prompt_as_one_turn(tmp_path: Path) -> None:
    prompt = "  explain this:\n    value = 1\n"
    first = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        session_store_factory=session_store_factory(SESSION_ONE),
    )

    assert first.prompt(prompt) == f"Fake response: {prompt}"
    assert first.history == (UserMessage(prompt), AssistantText(f"Fake response: {prompt}"))
    first.close()

    resumed = ProjectSession.open(
        tmp_path,
        resume=SESSION_ONE,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        session_store_factory=session_store_factory(SESSION_TWO),
    )

    assert resumed.turns[0].user.text == prompt
    assert resumed.history[0] == UserMessage(prompt)
    resumed.close()


def test_project_session_persists_and_resumes_grep_causality(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("needle\n", encoding="utf-8")
    arguments = ToolArguments.from_mapping({"query": "needle", "include": "src/*.py"})
    result = '{"path":"src/app.py","line":1,"text":"needle"}\n'

    class GrepProvider:
        def __init__(self, *, continue_history=False):
            self.calls = 0
            self.continue_history = continue_history
            self.requests = []

        def count_input_tokens(self, request):
            return RequestTokenCount(100, RequestTokenCountMethod.ESTIMATED)

        def respond(self, request):
            self.calls += 1
            self.requests.append(request)
            if self.continue_history:
                assert ToolUse("grep-1", "grep", arguments) in request.history
                assert ToolResult("grep-1", result) in request.history
                return AssistantText("resumed")
            if self.calls == 1:
                return ToolUse("grep-1", "grep", arguments)
            return AssistantText("found")

    first_provider = GrepProvider()
    first = ProjectSession.open(
        tmp_path,
        model="custom/model",
        custom_protocol="openai-compatible",
        custom_base_url="http://127.0.0.1:11434/v1",
        environment={},
        provider_factory=lambda route, *, environment: first_provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    assert first.prompt("find") == "found"
    first.close()

    resumed_provider = GrepProvider(continue_history=True)
    second = ProjectSession.open(
        tmp_path,
        resume=SESSION_ONE,
        model="custom/model",
        custom_protocol="openai-compatible",
        custom_base_url="http://127.0.0.1:11434/v1",
        environment={},
        provider_factory=lambda route, *, environment: resumed_provider,
        session_store_factory=session_store_factory(SESSION_TWO),
    )
    assert second.prompt("continue") == "resumed"
    second.close()


def test_project_session_executes_displays_persists_and_resumes_mixed_tool_response(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("workspace notes\n", encoding="utf-8")
    arguments = ToolArguments.from_mapping({"path": "README.md"})
    call = ToolUse(
        "read-1",
        "read_file",
        arguments,
        assistant_text="I will inspect the file first.\n",
    )

    class MixedProvider:
        def __init__(self, responses) -> None:
            self.responses = iter(responses)
            self.requests = []

        def count_input_tokens(self, _request):
            return RequestTokenCount(100, RequestTokenCountMethod.ESTIMATED)

        def respond(self, request):
            self.requests.append(request)
            return next(self.responses)

    first_provider = MixedProvider([call, AssistantText("The file contains workspace notes.")])
    first = ProjectSession.open(
        tmp_path,
        model="custom/model",
        custom_protocol="openai-compatible",
        custom_base_url="http://127.0.0.1:11434/v1",
        environment={},
        provider_factory=lambda route, *, environment: first_provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    events = []
    assert first.prompt("inspect", event_sink=events.append) == "The file contains workspace notes."
    result = ToolResult("read-1", "workspace notes\n")
    tool_events = [
        event
        for event in events
        if isinstance(
            event,
            (AssistantToolTextReceived, ToolRequestStarted, ToolRequestFinished),
        )
    ]
    assert tool_events == [
        AssistantToolTextReceived("I will inspect the file first.\n"),
        ToolRequestStarted("read_file", 1, 32, "path='README.md'"),
        ToolRequestFinished("read_file", 1, 32, ToolEventStatus.SUCCEEDED, "ok"),
    ]
    ledger_event = next(event for event in events if isinstance(event, ToolTurnSummaryCommitted))
    commit_event = next(event for event in events if isinstance(event, TurnCommitStarted))
    assert events.index(commit_event) < events.index(ledger_event)
    assert ledger_event.ledger.requested == 1
    assert first_provider.requests[1].history[-2:] == (call, result)
    assert first.history == (
        UserMessage("inspect"),
        call,
        result,
        AssistantText("The file contains workspace notes."),
    )
    first.close()

    resumed_provider = MixedProvider([AssistantText("resumed")])
    resumed = ProjectSession.open(
        tmp_path,
        resume=SESSION_ONE,
        model="custom/model",
        custom_protocol="openai-compatible",
        custom_base_url="http://127.0.0.1:11434/v1",
        environment={},
        provider_factory=lambda route, *, environment: resumed_provider,
        session_store_factory=session_store_factory(SESSION_TWO),
    )
    assert resumed.history[:4] == first.history
    assert resumed.prompt("continue") == "resumed"
    assert resumed_provider.requests[0].history[:4] == first.history
    resumed.close()


def test_target_aware_resume_rejects_known_overflow_without_mutation(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="tiny",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="tiny-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100,
            model_max_output_tokens=4096,
        )
    )
    first = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    first.prompt("hello")
    target = first.transcript_path
    first.close()
    target_before = target.read_bytes()
    latest = target.parent / "latest.json"
    latest_before = latest.read_bytes()

    with pytest.raises(SessionResumeContextError):
        ProjectSession.open(
            tmp_path,
            resume=SESSION_ONE,
            profile="tiny",
            environment={},
            user_profile_path=store.user_path,
            project_profile_path=store.project_path,
            provider_factory=lambda route, *, environment: RecordingProvider("tiny"),
            session_store_factory=session_store_factory(SESSION_TWO),
        )

    assert target.read_bytes() == target_before
    assert latest.read_bytes() == latest_before


def test_same_current_resume_is_a_mutation_free_noop(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    before = session.transcript_path.read_bytes()
    before_records = session.session_info().record_count

    result = session.switch_session(session.session_id)

    assert result.effect == ResumeEffect.ALREADY_CURRENT
    assert session.transcript_path.read_bytes() == before
    assert session.session_info().record_count == before_records
    session.close()


def test_project_session_resume_does_not_restore_historical_provider_binding(
    tmp_path: Path,
) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    stored = store.add_profile(
        ProviderProfileSpec(
            name="one",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="model-one",
            base_url="http://127.0.0.1:11434/v1",
        )
    )
    providers = []

    def factory(route, *, environment):
        provider = RecordingProvider(route.wire_model)
        providers.append(provider)
        return provider

    first = ProjectSession.open(
        tmp_path,
        profile="one",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=factory,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    assert first.prompt("first") == "model-one: first"
    first_prompt = providers[0].requests[0].system_prompt
    first.close()

    store.remove_profile_by_id(stored.profile_id)
    resumed = ProjectSession.open(
        tmp_path,
        resume=SESSION_ONE,
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        session_store_factory=session_store_factory(SESSION_TWO),
    )

    assert resumed.status().mode == "fake"
    assert resumed.history[-1] == AssistantText("model-one: first")
    assert first_prompt == build_system_prompt()
    assert all(first_prompt.text not in repr(item) for item in resumed.history)
    assert resumed.prompt("second") == "Fake response: second"
    resumed.close()


def test_project_session_switches_durable_history_without_changing_runtime(tmp_path: Path) -> None:
    provider = RecordingProvider("runtime")
    factory = session_store_factory(SESSION_ONE, SESSION_TWO)
    session = ProjectSession.open(
        tmp_path,
        model="local/model",
        environment={},
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=factory,
    )
    session.prompt("one")
    first_id = session.session_id
    assert session.latest_session_info().session_id == first_id
    second_id = session.new_session().session_id
    assert session.latest_session_info().session_id == second_id
    session.prompt("two")
    assert session.history == (UserMessage("two"), AssistantText("runtime: two"))
    session.switch_session(first_id)
    assert session.latest_session_info().session_id == first_id

    info = session.switch_session(second_id)

    assert info.session_id == second_id
    assert session.latest_session_info().session_id == second_id
    assert session.status().selected_model == "local/model"
    assert session.history == (UserMessage("two"), AssistantText("runtime: two"))
    session.close()


def test_context_inspection_does_not_mutate_session_or_transcript(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    session.prompt("hello")
    before_bytes = session.transcript_path.read_bytes()
    before_info = session.session_info()
    before_history = session.history
    before_status = session.status()

    first = session.inspect_context()
    second = session.inspect_context()

    assert first.context_id == second.context_id
    assert first.full_turn_count == first.effective_turn_count == 1
    assert first.full_item_count == first.effective_item_count == 2
    assert first.fit_decision.value == "unknown"
    assert session.history == before_history
    assert session.effective_history == before_history
    assert session.status() == before_status
    assert session.session_info() == before_info
    assert session.transcript_path.read_bytes() == before_bytes
    session.close()


def test_runtime_switch_records_real_generation_and_reports_audit_failure(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="one",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="model-one",
            base_url="http://127.0.0.1:11434/v1",
        )
    )
    store.add_profile(
        ProviderProfileSpec(
            name="two",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="model-two",
            base_url="http://127.0.0.1:11435/v1",
        )
    )
    session = ProjectSession.open(
        tmp_path,
        profile="one",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: RecordingProvider(route.wire_model),
        session_store_factory=session_store_factory(SESSION_ONE),
    )

    result = session.use_profile("two")

    assert result.status.generation == 1
    assert session._writer.state.records[-1].binding.generation == 1

    session._writer.release()
    with pytest.raises(RuntimeSwitchAuditError) as caught:
        session.set_model("model-three")
    assert caught.value.result.status.selected_model == "model-three"
    assert caught.value.result.status.generation == 2
    assert session.status().selected_model == "model-three"
    session._closed = True
    session._manager.close()


def test_output_budget_update_does_not_append_runtime_audit_and_turn_records_effective_route(
    tmp_path: Path,
) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="one",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="model-one",
            base_url="http://127.0.0.1:11434/v1",
            max_output_tokens=20,
            context_window_tokens=10_000,
            model_max_output_tokens=100,
        )
    )
    routes = []

    def factory(route, *, environment):
        routes.append(route)
        return RecordingProvider(route.wire_model)

    session = ProjectSession.open(
        tmp_path,
        profile="one",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=factory,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    record_count = len(session._writer.state.records)

    result = session.set_output_budget(40)

    assert result.status.max_output_tokens == 40
    assert result.status.max_output_tokens_source == "runtime"
    assert len(session._writer.state.records) == record_count
    assert store.get_profile("one").max_output_tokens == 20
    assert routes[-1].max_output_tokens == 40

    assert session.prompt("uses override") == "model-one: uses override"
    committed = session._writer.state.records[-1]
    assert committed.binding.max_output_tokens == 40
    assert committed.binding.route_fingerprint == result.status.route_fingerprint
    session.close()


def test_manual_compaction_preserves_full_history_and_resumes_effective_checkpoint(
    tmp_path: Path,
) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="compact",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="compact-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )
    provider = RecordingProvider("runtime")
    session = ProjectSession.open(
        tmp_path,
        profile="compact",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    for index in range(4):
        session.prompt(f"turn-{index}")
    before_history = session.history
    before_turns = session.turns
    before_bytes = session.transcript_path.read_bytes()

    preview = session.preview_compaction()

    assert preview.eligible
    assert preview.summarized_turn_count == 2
    assert preview.retained_turn_count == 2
    assert preview.fit_report.decision == ContextFitDecision.FITS
    assert session.transcript_path.read_bytes() == before_bytes
    assert len(provider.requests) == 4
    assert provider.summary_requests == []

    result = session.compact_context()

    assert result.summarized_turn_count == 2
    assert result.retained_turn_count == 2
    assert result.after_input_tokens < result.before_input_tokens
    assert len(provider.summary_requests) == 1
    assert session.history == before_history
    assert session.turns == before_turns
    checkpoint = session._writer.state.latest_checkpoint
    assert checkpoint is not None
    assert len(checkpoint.provider_usage) == 1
    assert checkpoint.provider_usage[0].usage is None
    durable_usage = session.session_usage()
    assert durable_usage.totals.unknown_invocations == 5
    assert durable_usage.unavailable_operations == 0
    assert session.effective_history == before_history[-4:]
    assert session.inspect_context().summary_present
    assert session.inspect_context().context_id.startswith("ctx-v4-")
    assert session.transcript_path.read_bytes().startswith(before_bytes)
    assert session._writer.state.records[-1].record_type == "context_compacted"
    history = session.compaction_history(5)
    assert history.total_checkpoints == 1
    checkpoint = history.checkpoints[0]
    assert checkpoint.sequence == result.checkpoint_sequence
    assert checkpoint.trigger.value == "manual"
    assert checkpoint.summarized_turn_count == 2
    assert checkpoint.retained_turn_count == 2
    assert checkpoint.previous_checkpoint_sequence is None
    transcript = session.transcript_path
    session.close()

    resumed_provider = RecordingProvider("resumed")
    resumed = ProjectSession.open(
        tmp_path,
        resume=SESSION_ONE,
        profile="compact",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: resumed_provider,
        session_store_factory=session_store_factory(SESSION_TWO),
    )
    assert resumed.transcript_path == transcript
    assert resumed.history == before_history
    assert resumed.effective_history == before_history[-4:]
    assert resumed.inspect_context().summary_present
    resumed_history = resumed.compaction_history(1)
    assert resumed_history == history
    resumed.prompt("continue")
    assert resumed_provider.requests[-1].effective_summary is not None
    resumed.close()


def test_nonreducing_compaction_reports_comparable_token_evidence_without_commit(
    tmp_path: Path,
) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="compact",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="compact-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )

    class NonReducingProvider(RecordingProvider):
        def count_input_tokens(self, request):
            value = 1100 if request.effective_summary is not None else 1000
            return RequestTokenCount(value, RequestTokenCountMethod.ESTIMATED)

    provider = NonReducingProvider("compact")
    session = ProjectSession.open(
        tmp_path,
        profile="compact",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    for index in range(4):
        session.prompt(f"turn-{index}")
    before_bytes = session.transcript_path.read_bytes()
    before_context = session.inspect_context()

    with pytest.raises(CompactionCandidateError) as caught:
        session.compact_context()

    error = caught.value
    assert error.before_input_tokens == 1000
    assert error.after_input_tokens == 1100
    assert error.input_method == "estimated"
    assert "input 1000 -> 1100 tokens; estimated" in str(error)
    assert len(provider.summary_requests) == 1
    assert session.transcript_path.read_bytes().startswith(before_bytes)
    failure = session._writer.state.records[-1]
    assert isinstance(failure, CompactionFailed)
    assert failure.failure_kind == "CompactionCandidateError"
    assert len(failure.provider_usage) == 1
    assert failure.provider_usage[0].usage is None
    assert session.inspect_context() == before_context
    assert session.compaction_history(5).total_checkpoints == 0
    session.close()


def test_resume_screening_counts_compacted_effective_projection_only(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="compact",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="compact-model",
            base_url="http://127.0.0.1:11434/v1",
            context_window_tokens=100_000,
            model_max_output_tokens=4096,
        )
    )
    compact_provider = RecordingProvider("compact")
    session = ProjectSession.open(
        tmp_path,
        profile="compact",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: compact_provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    for index in range(4):
        session.prompt(f"turn-{index}")
    session.compact_context()
    session.close()

    class ProjectionProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.counted = []

        def count_input_tokens(self, request):
            self.counted.append(request)
            return RequestTokenCount(100, RequestTokenCountMethod.ESTIMATED)

    resumed_provider = ProjectionProvider("resumed")
    resumed = ProjectSession.open(
        tmp_path,
        resume=SESSION_ONE,
        profile="compact",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: resumed_provider,
        session_store_factory=session_store_factory(SESSION_TWO),
    )

    screened = resumed_provider.counted[0]
    assert screened.effective_summary is not None
    assert len(screened.history) == 4
    assert resumed.startup_resume_result.fit_report.decision == ContextFitDecision.FITS
    assert resumed.history != screened.history
    assert resumed_provider.requests == []
    resumed.close()


def test_unknown_target_compatibility_applies_resume_without_generation(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="unknown",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="unknown-model",
            base_url="http://127.0.0.1:11434/v1",
        )
    )
    first = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    first.prompt("hello")
    first.close()
    provider = RecordingProvider("unknown")

    resumed = ProjectSession.open(
        tmp_path,
        resume=SESSION_ONE,
        profile="unknown",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_TWO),
    )

    result = resumed.startup_resume_result
    assert result.fit_report.decision == ContextFitDecision.UNKNOWN
    assert provider.requests == []
    assert resumed.history[-1] == AssistantText("Fake response: hello")
    resumed.close()


def test_pre_turn_high_water_auto_compacts_once_and_preserves_pending_prompt(
    tmp_path: Path,
) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="auto",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="auto-model",
            base_url="http://127.0.0.1:11434/v1",
            max_output_tokens=20,
            context_window_tokens=100,
            model_max_output_tokens=100,
        )
    )

    class AutoProvider(RecordingProvider):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.summary_requests = []

        def count_input_tokens(self, request):
            if request.effective_summary is not None:
                return RequestTokenCount(30, RequestTokenCountMethod.ESTIMATED)
            if request.history and request.history[-1] == UserMessage("trigger"):
                return RequestTokenCount(60, RequestTokenCountMethod.ESTIMATED)
            return RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED)

        def count_compact_summary_input_tokens(self, request):
            return RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED)

        def summarize_compact(self, request):
            self.summary_requests.append(request)
            assert "trigger" not in request.source_text
            return AssistantText("summary")

    provider = AutoProvider("runtime")
    session = ProjectSession.open(
        tmp_path,
        profile="auto",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    for index in range(4):
        session.prompt(f"turn-{index}")
    events = []

    response = session.prompt("trigger", event_sink=events.append)

    assert response == "runtime: trigger"
    assert len(provider.summary_requests) == 1
    compaction_events = [
        event
        for event in events
        if isinstance(event, (AutoCompactionStarted, AutoCompactionCommitted))
    ]
    assert [type(event) for event in compaction_events] == [
        AutoCompactionStarted,
        AutoCompactionCommitted,
    ]
    assert compaction_events[-1].result.trigger.value == "high_water"
    assert session._writer.state.records[-2].record_type == "context_compacted"
    assert session._writer.state.records[-2].trigger.value == "high_water"
    assert session._writer.state.records[-2].high_water_percent == 80
    assert session._writer.state.records[-1].record_type == "turn_committed"
    assert provider.requests[-1].history[-1] == UserMessage("trigger")
    assert provider.requests[-1].history.count(UserMessage("trigger")) == 1
    assert session.history[-2:] == (
        UserMessage("trigger"),
        AssistantText("runtime: trigger"),
    )
    session.close()


def test_known_overflow_auto_compacts_before_sending_prompt(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="auto",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="auto-model",
            base_url="http://127.0.0.1:11434/v1",
            max_output_tokens=20,
            context_window_tokens=100,
            model_max_output_tokens=100,
        )
    )

    class OverflowProvider(RecordingProvider):
        def count_input_tokens(self, request):
            if request.effective_summary is not None:
                return RequestTokenCount(30, RequestTokenCountMethod.ESTIMATED)
            if request.history and request.history[-1] == UserMessage("overflow"):
                return RequestTokenCount(90, RequestTokenCountMethod.ESTIMATED)
            return RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED)

        def count_compact_summary_input_tokens(self, request):
            return RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED)

    provider = OverflowProvider("runtime")
    session = ProjectSession.open(
        tmp_path,
        profile="auto",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    for index in range(4):
        session.prompt(f"turn-{index}")
    events = []

    assert session.prompt("overflow", event_sink=events.append) == "runtime: overflow"

    assert events[0].trigger.value == "overflow"
    assert events[1].result.trigger.value == "overflow"
    assert session._writer.state.records[-2].trigger.value == "overflow"
    assert provider.requests[-1].history[-1] == UserMessage("overflow")
    session.close()


def test_known_overflow_without_compaction_eligibility_sends_no_generation(
    tmp_path: Path,
) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="auto",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="auto-model",
            base_url="http://127.0.0.1:11434/v1",
            max_output_tokens=20,
            context_window_tokens=100,
            model_max_output_tokens=100,
        )
    )

    class OverflowProvider(RecordingProvider):
        def count_input_tokens(self, request):
            return RequestTokenCount(90, RequestTokenCountMethod.ESTIMATED)

    provider = OverflowProvider("runtime")
    session = ProjectSession.open(
        tmp_path,
        profile="auto",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    events = []

    with pytest.raises(Exception, match="context preflight rejected"):
        session.prompt("overflow", event_sink=events.append)

    assert provider.requests == []
    assert isinstance(events[-1], AutoCompactionNotApplied)
    assert events[-1].prompt_continues is False
    assert session.history == ()
    session.close()


def test_proactive_auto_compact_failure_continues_known_fitting_turn(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "user.json", tmp_path / "project.json")
    store.add_profile(
        ProviderProfileSpec(
            name="auto",
            provider_id="custom",
            protocol=WireProtocol.OPENAI_CHAT_COMPLETIONS,
            model="auto-model",
            base_url="http://127.0.0.1:11434/v1",
            max_output_tokens=20,
            context_window_tokens=100,
            model_max_output_tokens=100,
        )
    )

    class NonReducingProvider(RecordingProvider):
        def count_input_tokens(self, request):
            return RequestTokenCount(60, RequestTokenCountMethod.ESTIMATED)

        def count_compact_summary_input_tokens(self, request):
            return RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED)

        def summarize_compact(self, request):
            return AssistantText("summary")

    provider = NonReducingProvider("runtime")
    session = ProjectSession.open(
        tmp_path,
        profile="auto",
        environment={},
        user_profile_path=store.user_path,
        project_profile_path=store.project_path,
        provider_factory=lambda route, *, environment: provider,
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    for index in range(4):
        session.prompt(f"turn-{index}")
    events = []

    assert session.prompt("continue", event_sink=events.append) == "runtime: continue"

    assert isinstance(events[0], AutoCompactionStarted)
    assert isinstance(events[1], AutoCompactionNotApplied)
    assert events[1].prompt_continues is True
    assert all(
        record.record_type != "context_compacted" for record in session._writer.state.records
    )
    session.close()


def test_project_session_durable_append_failure_does_not_commit_memory(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        session_store_factory=session_store_factory(SESSION_ONE),
    )
    session._writer.release()

    with pytest.raises(SessionStoreError, match="released"):
        session.prompt("lost")

    assert session.history == ()
    assert session.turns == ()
    session._closed = True
    session._manager.close()
