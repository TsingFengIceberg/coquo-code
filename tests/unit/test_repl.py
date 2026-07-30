from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

from leonervis_code.agent.loop import AgentLoop
from leonervis_code.agent.tool_events import (
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    AssistantToolTextReceived,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestStarted,
)
from leonervis_code.cli.presentation import render_recent_history, render_session_summary
from leonervis_code.cli.prompt_editor import (
    MAX_PROMPT_BYTES,
    PromptInputError,
    PromptRead,
    StreamPromptEditor,
    complete_command,
)
from leonervis_code.cli.repl import (
    _session_prompt_history,
    parse_history_count,
    run_repl,
)
from leonervis_code.core.contracts import (
    ToolArguments,
    AssistantText,
    ConversationTurn,
    ToolUse,
    UserMessage,
)
from leonervis_code.providers.fake import ScriptedFakeProvider
from leonervis_code.providers.errors import output_limit_error
from leonervis_code.providers.manager import (
    OutputBudgetUpdateResult,
    RuntimeStatus,
    RuntimeSwitchResult,
)
from leonervis_code.providers.usage import ProviderTokenUsage
from leonervis_code.providers.profile import NamedProviderProfile
from leonervis_code.providers.definitions import WireProtocol
from leonervis_code.session_records import BindingSnapshot, SessionNameSource
from leonervis_code.session_store import SessionInfo, ToolLedgerQueryResult
from leonervis_code.tools.glob import GlobTool
from leonervis_code.tools.grep import GrepTool
from leonervis_code.tools.list_directory import ListDirectoryTool
from leonervis_code.tools.read_file import ReadFileTool


class RecordingLoop:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"reply: {prompt}"


class InterruptingInput(io.StringIO):
    def readline(self, size: int = -1) -> str:
        raise KeyboardInterrupt


class ScriptedPromptEditor:
    def __init__(self, *outcomes: PromptRead) -> None:
        self.outcomes = list(outcomes)
        self.prompts: list[str] = []
        self.toolbars: list[str | None] = []
        self.histories: list[tuple[str, ...]] = []

    def read(self, prompt: str, *, bottom_toolbar: str | None = None) -> PromptRead:
        self.prompts.append(prompt)
        self.toolbars.append(bottom_toolbar)
        return self.outcomes.pop(0)

    def set_history(self, entries: tuple[str, ...]) -> None:
        self.histories.append(entries)


class RejectingPromptEditor:
    def __init__(self) -> None:
        self.calls = 0

    def read(self, _prompt: str, *, bottom_toolbar: str | None = None) -> PromptRead:
        del bottom_toolbar
        self.calls += 1
        if self.calls == 1:
            raise PromptInputError("prompt exceeds test boundary")
        return PromptRead.exit()

    def set_history(self, _entries: tuple[str, ...]) -> None:
        pass


def test_tab_completion_returns_existing_slash_commands() -> None:
    assert complete_command("/e", 0) == "/exit"
    assert complete_command("/h", 0) == "/help"
    assert complete_command("/h", 1) == "/history"
    assert complete_command("/q", 0) == "/quit"
    assert complete_command("/", 0) == "/help"
    assert complete_command("/", 1) == "/history"
    assert complete_command("/", 2) == "/actions"
    assert complete_command("/", 3) == "/tools"
    assert complete_command("/", 4) == "/tool-details"
    assert complete_command("/", 5) == "/changes"
    assert complete_command("/", 6) == "/commit"
    assert complete_command("/", 7) == "/commits"
    assert complete_command("/", 8) == "/exit"
    assert complete_command("/", 9) == "/quit"
    assert complete_command("/", 10) == "/status"
    assert complete_command("/", 11) == "/context"
    assert complete_command("/", 12) == "/usage"
    assert complete_command("/", 13) == "/output"
    assert complete_command("/", 14) == "/compact"
    assert complete_command("/", 15) == "/compactions"
    assert complete_command("/", 16) == "/provider"
    assert complete_command("/", 17) == "/model"
    assert complete_command("/", 18) == "/session"
    assert complete_command("/", 19) == "/resume"
    assert complete_command("/", 20) == "/clear"
    assert complete_command("/", 21) is None
    assert complete_command("/commit", 0) == "/commit"
    assert complete_command("ordinary prompt", 0) is None


def test_stream_prompt_editor_uses_injected_streams_without_tty_editor() -> None:
    output = io.StringIO()

    assert StreamPromptEditor(io.StringIO("Hello\n"), output).read("leonervis> ").text == "Hello"
    assert output.getvalue() == "leonervis> "


def test_parse_history_count_accepts_positive_integer_only() -> None:
    assert parse_history_count("/history 2") == 2
    assert parse_history_count("/history") is None
    assert parse_history_count("/history 0") is None
    assert parse_history_count("/history -1") is None
    assert parse_history_count("/history 1.5") is None
    assert parse_history_count("/history ٢") is None
    assert parse_history_count("/history 2 extra") is None


def test_render_recent_history_shows_complete_turns_in_chronological_order() -> None:
    turns = (
        ConversationTurn(UserMessage("first prompt"), AssistantText("first reply")),
        ConversationTurn(UserMessage("second prompt"), AssistantText("second reply")),
        ConversationTurn(UserMessage("third prompt"), AssistantText("third reply")),
    )

    assert render_recent_history(turns, 2) == (
        "User: second prompt\nAssistant: second reply\n\nUser: third prompt\nAssistant: third reply"
    )
    assert render_recent_history((), 2) == "No conversation turns yet."


def test_render_session_summary_marks_pointers_state_and_turn_plurality(tmp_path) -> None:
    session_id = "12345678-1234-4234-9234-123456789abc"
    info = SessionInfo(
        session_id=session_id,
        path=tmp_path / f"{session_id}.jsonl",
        workspace=str(tmp_path),
        workspace_fingerprint="v1-" + "a" * 64,
        created_at="2026-07-17T12:00:00.000000Z",
        record_count=3,
        turn_count=1,
        closed=True,
        binding=BindingSnapshot.fake(),
        name="Review provider adapters",
        name_source=SessionNameSource.AUTO,
    )

    assert render_session_summary(
        info,
        current_session_id=session_id,
        latest_session_id=session_id,
    ) == (
        f"'Review provider adapters' [current] [latest] ({session_id}): 1 turn, closed, "
        "created 2026-07-17T12:00:00.000000Z, runtime fake/<none>"
    )
    assert render_session_summary(
        SessionInfo(**{**info.__dict__, "turn_count": 0, "closed": False})
    ).endswith("0 turns, open, created 2026-07-17T12:00:00.000000Z, runtime fake/<none>")


def test_repl_routes_each_nonblank_prompt_and_prints_banner(tmp_path) -> None:
    loop = RecordingLoop()
    output = io.StringIO()

    status = run_repl(
        loop,
        stdin=io.StringIO("Hello\n   \nWorld\n/exit\n"),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    rendered = output.getvalue()
    assert status == 0
    assert loop.prompts == ["Hello", "World"]
    assert rendered.count("LEONERVIS CODE v0.1.0") == 1
    assert "reply: Hello\n" in rendered
    assert "reply: World\n" in rendered


def test_repl_submits_exact_multiline_buffer_as_one_model_turn(tmp_path) -> None:
    loop = RecordingLoop()
    output = io.StringIO()
    editor = ScriptedPromptEditor(
        PromptRead.submit("  explain this:\n    value = 1\n"),
        PromptRead.exit(),
    )

    status = run_repl(
        loop,
        stdin=io.StringIO(),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
        prompt_editor=editor,
    )

    assert status == 0
    assert loop.prompts == ["  explain this:\n    value = 1\n"]
    assert editor.prompts == ["› ", "› "]
    assert editor.toolbars == [f"  {tmp_path}", f"  {tmp_path}"]
    assert output.getvalue().count("reply:   explain this:") == 1


def test_repl_cancelled_draft_and_multiline_slash_prefix_do_not_dispatch_host_command(
    tmp_path,
) -> None:
    loop = RecordingLoop()
    editor = ScriptedPromptEditor(
        PromptRead.cancel(),
        PromptRead.submit("/help\nthis is literal model text"),
        PromptRead.exit(),
    )

    run_repl(
        loop,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        version="0.1.0",
        cwd=tmp_path,
        color=False,
        prompt_editor=editor,
    )

    assert loop.prompts == ["/help\nthis is literal model text"]


def test_repl_input_error_does_not_call_model_and_returns_to_editor(tmp_path) -> None:
    loop = RecordingLoop()
    output = io.StringIO()

    status = run_repl(
        loop,
        stdin=io.StringIO(),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
        prompt_editor=RejectingPromptEditor(),
    )

    assert status == 0
    assert loop.prompts == []
    assert "Input error: prompt exceeds test boundary" in output.getvalue()


def test_repl_clear_only_clears_the_terminal_and_does_not_call_model(tmp_path) -> None:
    loop = RecordingLoop()
    output = io.StringIO()
    editor = ScriptedPromptEditor(PromptRead.submit("/clear"), PromptRead.exit())

    run_repl(
        loop,
        stdin=io.StringIO(),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
        prompt_editor=editor,
    )

    assert loop.prompts == []
    assert "\x1b[2J\x1b[H" in output.getvalue()
    assert editor.histories == [(), ()]


def test_repl_editor_history_is_derived_from_committed_session_turns(tmp_path) -> None:
    class HistorySession:
        def __init__(self) -> None:
            self.turns = (ConversationTurn(UserMessage("older"), AssistantText("old reply")),)

        def prompt(self, prompt, *, event_sink=None):
            del event_sink
            self.turns += (ConversationTurn(UserMessage(prompt), AssistantText("new reply")),)
            return "new reply"

    session = HistorySession()
    editor = ScriptedPromptEditor(
        PromptRead.submit("/help"),
        PromptRead.submit("new prompt"),
        PromptRead.exit(),
    )

    run_repl(
        session,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        version="0.1.0",
        cwd=tmp_path,
        color=False,
        prompt_editor=editor,
    )

    assert editor.histories == [
        ("older",),
        ("older",),
        ("older", "new prompt"),
    ]


def test_session_prompt_history_keeps_the_newest_entries_within_both_bounds() -> None:
    turns = tuple(
        ConversationTurn(UserMessage(f"prompt-{index}"), AssistantText("reply"))
        for index in range(1002)
    )

    history = _session_prompt_history(type("Session", (), {"turns": turns})())

    assert len(history) == 1000
    assert history[0] == "prompt-2"
    assert history[-1] == "prompt-1001"

    large_turns = tuple(
        ConversationTurn(
            UserMessage(str(index) + "x" * (MAX_PROMPT_BYTES - 2)), AssistantText("reply")
        )
        for index in range(17)
    )
    large_history = _session_prompt_history(type("Session", (), {"turns": large_turns})())
    assert len(large_history) == 16
    assert large_history[0].startswith("1x")
    assert large_history[-1].startswith("16x")


def test_repl_session_new_replaces_editor_history_before_the_next_prompt(tmp_path) -> None:
    class SwitchingSession:
        def __init__(self) -> None:
            self.turns = (
                ConversationTurn(UserMessage("old session prompt"), AssistantText("reply")),
            )

        def new_session(self):
            self.turns = ()
            return SimpleNamespace(session_id="new-session")

    session = SwitchingSession()
    editor = ScriptedPromptEditor(PromptRead.submit("/session new"), PromptRead.exit())

    run_repl(
        session,
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        version="0.1.0",
        cwd=tmp_path,
        color=False,
        prompt_editor=editor,
    )

    assert editor.histories == [("old session prompt",), ()]


def test_repl_catches_keyboard_interrupt_during_slash_operation(tmp_path) -> None:
    class InterruptingSession(RecordingLoop):
        turns = ()

        def compact_context(self):
            raise KeyboardInterrupt

    output = io.StringIO()
    status = run_repl(
        InterruptingSession(),
        stdin=io.StringIO("/compact\n/exit\n"),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    assert status == 0
    assert "Operation cancelled; no uncommitted state was installed." in output.getvalue()


def test_repl_displays_only_completed_turns_without_creating_a_turn(tmp_path) -> None:
    (tmp_path / "README.md").write_text("contents", encoding="utf-8")
    provider = ScriptedFakeProvider(
        [
            ToolUse(
                tool_use_id="read-1",
                name="read_file",
                arguments=ToolArguments.from_mapping({"path": "README.md"}),
            ),
            AssistantText(text="first reply"),
            AssistantText(text="second reply"),
        ]
    )
    loop = AgentLoop(
        provider,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
    )
    output = io.StringIO()

    run_repl(
        loop,
        stdin=io.StringIO("first prompt\nsecond prompt\n/history 2\n/exit\n"),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    rendered = output.getvalue()
    assert "User: first prompt\nAssistant: first reply" in rendered
    assert "User: second prompt\nAssistant: second reply" in rendered
    assert "README.md" not in rendered
    assert "contents" not in rendered
    assert len(provider.received_requests) == 3
    assert len(loop.turns) == 2


def test_repl_rejects_invalid_history_commands_without_creating_a_turn(tmp_path) -> None:
    loop = RecordingLoop()
    output = io.StringIO()

    run_repl(
        loop,
        stdin=io.StringIO("/history\n/history 0\n/history two\n/exit\n"),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    assert loop.prompts == []
    assert output.getvalue().count("Usage: /history <positive integer>") == 3


def test_repl_keeps_history_for_its_single_loop_lifetime(tmp_path) -> None:
    provider = ScriptedFakeProvider([AssistantText("first reply"), AssistantText("second reply")])
    output = io.StringIO()

    run_repl(
        AgentLoop(
            provider,
            ReadFileTool(tmp_path),
            GlobTool(tmp_path),
            GrepTool(tmp_path),
            ListDirectoryTool(tmp_path),
        ),
        stdin=io.StringIO("first prompt\nsecond prompt\n/exit\n"),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    assert provider.received_requests[1].history == (
        UserMessage(text="first prompt"),
        AssistantText(text="first reply"),
        UserMessage(text="second prompt"),
    )

    loop = RecordingLoop()
    output = io.StringIO()

    run_repl(
        loop,
        stdin=io.StringIO("/help\n/unknown\n/quit\n"),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    rendered = output.getvalue()
    assert loop.prompts == []
    assert "Host command groups:" in rendered
    assert "/help session   Session history, browsing, and resume" in rendered
    assert "Unknown command: /unknown. Type /help for controls." in rendered


def test_repl_provider_commands_switch_without_entering_model_history(tmp_path) -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.prompts = []
            self.turns = ()
            self.used = []
            self.models = []
            self.output_tokens = 1024
            self.output_updates = []

        def status(self):
            return RuntimeStatus(
                mode="real",
                profile="one",
                selection_source="project",
                provider_id="custom",
                protocol="openai_chat_completions",
                selected_model="model-one",
                wire_model="model-one",
                base_url="http://127.0.0.1:11434/v1",
                base_url_source="profile",
                credential_required=False,
                credential_present=False,
                max_output_tokens=self.output_tokens,
                default_max_output_tokens=1024,
                max_output_tokens_source=("profile" if self.output_tokens == 1024 else "runtime"),
                model_max_output_tokens=8192,
            )

        def list_profiles(self):
            return (
                NamedProviderProfile(
                    "one",
                    "custom",
                    WireProtocol.OPENAI_CHAT_COMPLETIONS,
                    "model-one",
                    "http://127.0.0.1:11434/v1",
                ),
            )

        def use_profile(self, name, *, scope):
            self.used.append((name, scope))
            return RuntimeSwitchResult(self.status(), None)

        def set_model(self, model):
            self.models.append(model)
            status = self.status()
            switched = RuntimeStatus(**{**status.__dict__, "selected_model": model})
            return RuntimeSwitchResult(switched, None)

        def set_output_budget(self, value):
            previous = self.output_tokens
            self.output_tokens = 1024 if value is None else value
            self.output_updates.append(value)
            return OutputBudgetUpdateResult(
                self.status(), None, previous, previous != self.output_tokens
            )

        def prompt(self, prompt, *, event_sink=None):
            self.prompts.append(prompt)
            return f"reply: {prompt}"

    session = RecordingSession()
    output = io.StringIO()
    run_repl(
        session,
        stdin=io.StringIO(
            "/status\n/provider list\n/provider current\n/provider use one\n/model model-two\n"
            "/output\n/output 2048\n/output reset\nHello\n/exit\n"
        ),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    rendered = output.getvalue()
    assert session.used == [("one", "project")]
    assert session.models == ["model-two"]
    assert session.output_updates == [2048, None]
    assert session.prompts == ["Hello"]
    assert "Credential: not required" in rendered
    assert "one: custom/model-one" in rendered
    assert "profile was not modified" in rendered
    assert "Effective output budget: 1024 tokens (profile)" in rendered
    assert "Output budget changed: 1024 -> 2048 tokens (runtime)" in rendered
    assert "reply: Hello" in rendered


def test_invalid_prefix_commands_are_not_treated_as_switches(tmp_path) -> None:
    loop = RecordingLoop()
    output = io.StringIO()

    run_repl(
        loop,
        stdin=io.StringIO("/modelx gpt-5\n/provider usex one\n/exit\n"),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    rendered = output.getvalue()
    assert loop.prompts == []
    assert "Unknown command: /modelx gpt-5" in rendered
    assert "Unknown provider command: usex" in rendered
    assert "Usage: /provider <list|current|use>" in rendered


def test_repl_renders_tool_events_before_the_final_response(tmp_path) -> None:
    class EventSession:
        def prompt(self, prompt, *, event_sink=None, include_tool_details=False):
            assert include_tool_details is True
            event_sink(AssistantToolTextReceived("I will search first."))
            event_sink(ToolRequestStarted("grep", 1, 6, "include='*.py' query_bytes=6"))
            event_sink(ToolRequestFinished("grep", 1, 6, ToolEventStatus.SUCCEEDED, "ok"))
            return f"reply: {prompt}"

    output = io.StringIO()

    run_repl(
        EventSession(),
        stdin=io.StringIO("/tool-details full\nsearch\n/exit\n"),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    rendered = output.getvalue()
    assert "Live tool details: full" in rendered
    assert "I will search first.\n[tool 1/6] grep\n  include='*.py' query_bytes=6\n" in rendered
    assert "[tool 1/6] succeeded code=ok\nreply: search\n" in rendered


def test_repl_streams_final_text_once_and_continues_after_interrupted_partial_text(
    tmp_path,
) -> None:
    class StreamingSession:
        def __init__(self) -> None:
            self.calls = 0

        def prompt(self, _prompt, *, event_sink=None):
            self.calls += 1
            if self.calls == 1:
                event_sink(AssistantResponseTextDeltaReceived("partial"))
                raise KeyboardInterrupt
            event_sink(AssistantResponseTextDeltaReceived("final"))
            event_sink(AssistantFinalTextStreamCommitted("final"))
            return "final"

    output = io.StringIO()
    session = StreamingSession()

    assert (
        run_repl(
            session,
            stdin=io.StringIO("first\nsecond\n/exit\n"),
            stdout=output,
            version="0.1.0",
            cwd=tmp_path,
            color=False,
        )
        == 0
    )

    rendered = output.getvalue()
    assert "partial\nGeneration cancelled; partial assistant text was not committed.\n" in rendered
    assert rendered.count("final\n") == 1
    assert session.calls == 2


def test_repl_explains_output_limit_and_keeps_partial_text_uncommitted(tmp_path) -> None:
    class LimitedSession:
        def prompt(self, _prompt, *, event_sink=None):
            event_sink(AssistantResponseTextDeltaReceived("partial"))
            raise output_limit_error(
                provider_id="compatible",
                model_id="model",
                message="provider response reached the configured output-token limit",
                requested_output_tokens=4096,
                usage=ProviderTokenUsage(4900, 4096),
                partial_response_observed=True,
            )

    output = io.StringIO()

    assert (
        run_repl(
            LimitedSession(),
            stdin=io.StringIO("long answer\n/exit\n"),
            stdout=output,
            version="0.1.0",
            cwd=tmp_path,
            color=False,
        )
        == 0
    )

    rendered = output.getvalue()
    assert "partial\nPartial assistant text was not committed.\n" in rendered
    assert "Provider error [output_limit]" in rendered
    assert "Output limit: requested 4096 tokens" in rendered
    assert "No turn was committed" in rendered
    assert "remain in Action Audit" in rendered


def test_repl_renders_nonstream_final_markdown_when_terminal_rendering_is_enabled(
    tmp_path,
) -> None:
    class MarkdownSession:
        def prompt(self, _prompt, *, event_sink=None):
            return "# Result\n\nThis is **bold**."

    output = io.StringIO()

    assert (
        run_repl(
            MarkdownSession(),
            stdin=io.StringIO("inspect\n/exit\n"),
            stdout=output,
            version="0.1.0",
            cwd=tmp_path,
            color=False,
            render_markdown=True,
        )
        == 0
    )

    rendered = output.getvalue()
    assert "Result" in rendered
    assert "This is bold." in rendered
    assert "# Result" not in rendered
    assert "**bold**" not in rendered


def test_repl_session_commands_switch_without_entering_model_history(tmp_path) -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.prompts = []
            self.turns = ()
            self.current = "12345678-1234-4234-9234-123456789abc"
            self.latest = "22345678-1234-4234-9234-123456789abc"
            self.switched = []
            self.created = 0
            self.names = {
                self.current: ("Current work", SessionNameSource.MANUAL),
                self.latest: ("Latest work", SessionNameSource.AUTO),
            }

        def session_info(self):
            return self._info(self.current)

        def action_audits(self):
            return ()

        def tool_ledgers(self, limit):
            return ToolLedgerQueryResult(0, ())

        def latest_session_info(self):
            return self._info(self.latest)

        def list_sessions(self):
            return (
                self._info("12345678-1234-4234-9234-123456789abc"),
                self._info("22345678-1234-4234-9234-123456789abc"),
            )

        def new_session(self):
            self.created += 1
            self.current = "32345678-1234-4234-9234-123456789abc"
            self.latest = self.current
            self.names[self.current] = ("New session 3", SessionNameSource.DEFAULT)
            return self.session_info()

        def rename_session(self, name=None):
            self.names[self.current] = (
                (name, SessionNameSource.MANUAL)
                if name is not None
                else ("Automatic title", SessionNameSource.AUTO)
            )
            return self.session_info()

        def switch_session(self, selector):
            self.switched.append(selector)
            self.current = "22345678-1234-4234-9234-123456789abc"
            self.latest = self.current
            return self.session_info()

        def prompt(self, prompt, *, event_sink=None):
            self.prompts.append(prompt)
            return f"reply: {prompt}"

        def _info(self, session_id):
            return SessionInfo(
                session_id=session_id,
                path=tmp_path / f"{session_id}.jsonl",
                workspace=str(tmp_path),
                workspace_fingerprint="v1-" + "a" * 64,
                created_at="2026-07-17T12:00:00.000000Z",
                record_count=1,
                turn_count=0,
                closed=False,
                binding=BindingSnapshot.fake(),
                name=self.names[session_id][0],
                name_source=self.names[session_id][1],
            )

    session = RecordingSession()
    output = io.StringIO()

    run_repl(
        session,
        stdin=io.StringIO(
            "/session show\n/session list\n/actions\n/tools details\n/session new\n"
            "/session rename Sprint notes\n/session rename --auto\n/session show\n"
            "/session switch\nHello\n/session switch 1\n"
            "/resume 22345678-1234-4234-9234-123456789abc\n/exit\n"
        ),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    rendered = output.getvalue()
    assert session.created == 1
    assert session.switched == ["22345678-1234-4234-9234-123456789abc"]
    assert session.prompts == ["Hello"]
    assert "Auto-save: enabled" in rendered
    assert "Started 'New session 3'" in rendered
    assert "Session name: Sprint notes (manual)" in rendered
    assert "Session name: Automatic title (auto)" in rendered
    assert "runtime provider unchanged" in rendered
    assert "No action audits yet." in rendered
    assert "No committed turns yet." in rendered
    assert "'Current work' [current]" in rendered
    assert "'Latest work' [latest]" in rendered
    assert "Session picker snapshot:" in rendered
    assert "build a fresh numbered snapshot" in rendered


def test_repl_exits_cleanly_at_end_of_input(tmp_path) -> None:
    output = io.StringIO()

    status = run_repl(
        RecordingLoop(),
        stdin=io.StringIO(),
        stdout=output,
        version="0.1.0",
        cwd=tmp_path,
        color=False,
    )

    assert status == 0
    assert output.getvalue().endswith("› \n")


def test_repl_exits_cleanly_on_keyboard_interrupt(tmp_path) -> None:
    output = io.StringIO()

    status = run_repl(
        RecordingLoop(),
        stdin=InterruptingInput(),
        stdout=output,
        version="0.1.0",
        cwd=Path(tmp_path),
        color=False,
    )

    assert status == 0
    assert output.getvalue().endswith("› \n")
