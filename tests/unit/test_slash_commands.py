from __future__ import annotations

from dataclasses import dataclass

from leonervis_code.agent.loop import AgentLoop
from leonervis_code.cli.presentation import ToolDetailMode
from leonervis_code.cli.slash import ToolDetailSettings, dispatch_slash
from leonervis_code.core.compaction import CompactionCandidateError
from leonervis_code.providers.manager import (
    CurrentTargetContextAssessment,
    OutputBudgetUpdateResult,
    RuntimeStatus,
    RuntimeSwitchResult,
)
from leonervis_code.providers.request_context import ContextFitDecision
from leonervis_code.providers.usage import ProviderUsageTotals, RuntimeUsageTracker
from leonervis_code.session import (
    CompactContextPreview,
    CompactContextResult,
    CompactionHistoryResult,
    DurableUsageSnapshot,
    EffectiveContextInspection,
    ResumeEffect,
    SessionResumeResult,
)
from leonervis_code.session_records import BindingSnapshot
from leonervis_code.session_store import LatestUpdateStatus, SessionInfo, ToolLedgerQueryResult
from leonervis_code.tools.glob import GlobTool
from leonervis_code.tools.grep import GrepTool
from leonervis_code.tools.list_directory import ListDirectoryTool
from leonervis_code.tools.read_file import ReadFileTool


@dataclass
class Text:
    text: str


@dataclass
class Turn:
    user: Text
    assistant: Text


@dataclass
class Profile:
    name: str = "one"
    provider_id: str = "custom"
    model: str = "model-one"


class Session:
    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.turns = (Turn(Text("hello"), Text("reply")),)
        self.current = "12345678-1234-4234-9234-123456789abc"
        self.latest = self.current
        self.prompts = []

    def status(self):
        return RuntimeStatus(
            mode="fake",
            profile=None,
            selection_source="default",
            provider_id="fake",
            protocol=None,
            selected_model=None,
            wire_model=None,
            base_url=None,
            base_url_source=None,
            credential_required=False,
            credential_present=False,
        )

    def inspect_context(self):
        loop = AgentLoop(
            None,
            ReadFileTool(self.tmp_path),
            GlobTool(self.tmp_path),
            GrepTool(self.tmp_path),
            ListDirectoryTool(self.tmp_path),
        )
        assessment = CurrentTargetContextAssessment(
            self.status(),
            None,
            "provider input assessment is unavailable for fake runtime",
        )
        return EffectiveContextInspection(loop.effective_context_snapshot(), assessment)

    def compact_context(self):
        return CompactContextResult(
            session_id=self.current,
            checkpoint_sequence=4,
            source_context_id="ctx-v1-" + "a" * 64,
            result_context_id="ctx-v2-" + "b" * 64,
            summarized_turn_count=2,
            retained_turn_count=2,
            full_turn_count=4,
            before_input_tokens=100,
            after_input_tokens=40,
            input_method="estimated",
            fit_decision=ContextFitDecision.FITS,
        )

    def preview_compaction(self):
        return CompactContextPreview(
            source_context_id="ctx-v3-" + "a" * 64,
            full_turn_count=4,
            effective_turn_count=4,
            summary_present=False,
            eligible=True,
            reason=None,
            summarized_turn_count=2,
            retained_turn_count=2,
            target_assessment=CurrentTargetContextAssessment(
                self.status(),
                None,
                "provider input assessment is unavailable for fake runtime",
            ),
        )

    def compaction_history(self, limit):
        assert 1 <= limit <= 20
        return CompactionHistoryResult(0, ())

    def _info(self, session_id):
        return SessionInfo(
            session_id=session_id,
            path=self.tmp_path / f"{session_id}.jsonl",
            workspace=str(self.tmp_path),
            workspace_fingerprint="v1-" + "a" * 64,
            created_at="2026-07-18T00:00:00.000000Z",
            record_count=1,
            turn_count=1,
            closed=False,
            binding=BindingSnapshot.fake(),
        )

    def session_info(self):
        return self._info(self.current)

    def action_audits(self):
        return ()

    def tool_ledgers(self, limit):
        assert 1 <= limit <= 20
        return ToolLedgerQueryResult(0, ())

    def git_status(self):
        return type(
            "GitStatus",
            (),
            {
                "entries": (
                    type(
                        "Entry",
                        (),
                        {
                            "path": "note.txt",
                            "index": "clean",
                            "worktree": "modified",
                            "original_path": None,
                        },
                    )(),
                ),
                "truncated": False,
            },
        )()

    def git_diff(self, scope):
        return type(
            "GitDiff",
            (),
            {
                "scope": type("Scope", (), {"value": scope})(),
                "content": "+change\n",
                "truncated": False,
            },
        )()

    def git_log(self, limit, path):
        assert 1 <= limit <= 50
        return type(
            "GitLog",
            (),
            {
                "entries": (
                    type(
                        "Entry",
                        (),
                        {
                            "commit_id": "a" * 40,
                            "parent_ids": (),
                            "committed_at": "2026-07-29T01:02:03+08:00",
                            "subject": "initial",
                            "subject_truncated": False,
                        },
                    )(),
                ),
                "path": path,
                "truncated": False,
            },
        )()

    def git_show(self, commit_id, path):
        return type(
            "GitShow",
            (),
            {
                "commit_id": commit_id,
                "parent_ids": (),
                "committed_at": "2026-07-29T01:02:03+08:00",
                "path": path,
                "message": "initial\n",
                "message_truncated": False,
                "patch": "+created\n",
                "patch_truncated": False,
            },
        )()

    def usage(self):
        return RuntimeUsageTracker().snapshot()

    def session_usage(self):
        return DurableUsageSnapshot((), ProviderUsageTotals(), 0)

    def turn_usage_history(self, limit=10):
        assert limit == 10
        return DurableUsageSnapshot((), ProviderUsageTotals(), 0)

    def latest_session_info(self):
        return self._info(self.latest)

    def list_sessions(self):
        return (self.session_info(),)

    def new_session(self):
        self.current = "22345678-1234-4234-9234-123456789abc"
        self.latest = self.current
        return self.session_info()

    def switch_session(self, selector):
        self.current = selector
        self.latest = selector
        assessment = CurrentTargetContextAssessment(
            self.status(),
            None,
            "provider input assessment is unavailable for fake runtime",
        )
        return SessionResumeResult(
            self.session_info(),
            ResumeEffect.APPLIED,
            assessment,
            "ctx-v1-" + "a" * 64,
            False,
            LatestUpdateStatus.UPDATED,
        )

    def list_profiles(self):
        return (Profile(),)

    def use_profile(self, name, *, scope):
        status = RuntimeStatus(**{**self.status().__dict__, "mode": "real", "profile": name})
        return RuntimeSwitchResult(status, None)

    def set_model(self, model):
        status = RuntimeStatus(**{**self.status().__dict__, "selected_model": model})
        return RuntimeSwitchResult(status, None)

    def prompt(self, text):
        self.prompts.append(text)


def test_group_help_and_targeted_usage(tmp_path) -> None:
    session = Session(tmp_path)
    tool_details = ToolDetailSettings()

    assert "Session commands:" in dispatch_slash("/session", session).message
    assert "Provider commands:" in dispatch_slash("/provider", session).message
    unknown = dispatch_slash("/session wat", session)
    assert unknown.kind == "warning"
    assert unknown.message == ("Unknown session command: wat\nUsage: /session <show|list|new>")
    assert dispatch_slash("/session show extra", session).message == "Usage: /session show"
    assert dispatch_slash("/provider use", session).message == "Usage: /provider use <name>"
    assert dispatch_slash("/status extra", session).message == "Usage: /status"
    context = dispatch_slash("/context", session)
    assert context.kind == "warning"
    assert "Context ID: ctx-v3-" in context.message
    assert dispatch_slash("/context extra", session).message == "Usage: /context"
    assert dispatch_slash("/usage", session).message == (
        "No provider generation usage recorded for the current runtime."
    )
    assert "Session usage: 0 in / 0 out" in dispatch_slash("/usage session", session).message
    assert dispatch_slash("/usage turns", session).message == (
        "No committed or failed turn usage is available in this Session."
    )
    assert dispatch_slash("/usage extra", session).message == "Usage: /usage [session|turns]"
    assert dispatch_slash("/tool-details", session, tool_details=tool_details).message == (
        "Live tool details: compact (process-local)."
    )
    enabled = dispatch_slash("/tool-details full", session, tool_details=tool_details)
    assert enabled.kind == "warning"
    assert "structured argv" in enabled.message
    assert "sensitive values" in enabled.message
    assert tool_details.mode == ToolDetailMode.FULL
    disabled = dispatch_slash("/tool-details compact", session, tool_details=tool_details)
    assert disabled.kind == "info"
    assert tool_details.mode == ToolDetailMode.COMPACT
    assert dispatch_slash("/tool-details verbose", session, tool_details=tool_details).message == (
        "Usage: /tool-details <compact|full>"
    )
    assert "path=note.txt" in dispatch_slash("/changes", session).message
    assert dispatch_slash("/changes unstaged", session).message == (
        "Git diff (unstaged):\n+change\n"
    )
    assert dispatch_slash("/changes staged", session).message == ("Git diff (staged):\n+change\n")
    assert dispatch_slash("/changes both", session).message == ("Usage: /changes [unstaged|staged]")
    commit_id = "a" * 40
    assert commit_id in dispatch_slash("/commits", session).message
    assert "path=src/app.py" in dispatch_slash("/commits 5 src/app.py", session).message
    assert dispatch_slash("/commits 0", session).message == "Usage: /commits [1-50] [path]"
    assert f"Git commit: {commit_id}" in dispatch_slash(f"/commit {commit_id}", session).message
    assert "Path: src/app.py" in dispatch_slash(f"/commit {commit_id} src/app.py", session).message
    assert dispatch_slash("/commit", session).message == ("Usage: /commit <full-commit-id> [path]")
    assert "real provider runtime" in dispatch_slash("/output", session).message
    compact = dispatch_slash("/compact", session)
    assert compact.kind == "success"
    assert "Full transcript and /history were preserved" in compact.message
    preview = dispatch_slash("/compact preview", session)
    assert preview.kind == "warning"
    assert "Selection: summarize 2" in preview.message
    assert dispatch_slash("/compact extra", session).message == (
        "Usage: /compact | /compact preview"
    )
    assert dispatch_slash("/compactions", session).message == (
        "No durable compaction checkpoints yet."
    )
    assert dispatch_slash("/compactions 20", session).message == (
        "No durable compaction checkpoints yet."
    )
    assert dispatch_slash("/compactions 21", session).message == "Usage: /compactions [1-20]"
    assert dispatch_slash("/actions", session).message == "No action audits yet."
    assert dispatch_slash("/actions 10", session).message == "No action audits yet."
    assert dispatch_slash("/actions 0", session).message == "Usage: /actions [1-100]"
    assert dispatch_slash("/actions 101", session).message == "Usage: /actions [1-100]"
    assert dispatch_slash("/actions two", session).message == "Usage: /actions [1-100]"
    assert dispatch_slash("/tools", session).message == "No committed turns yet."
    assert dispatch_slash("/tools 10", session).message == "No committed turns yet."
    assert dispatch_slash("/tools details", session).message == "No committed turns yet."
    assert dispatch_slash("/tools details 10", session).message == "No committed turns yet."
    assert dispatch_slash("/tools 0", session).message == (
        "Usage: /tools [1-20] | /tools details [1-20]"
    )
    assert dispatch_slash("/tools details 21", session).message == (
        "Usage: /tools [1-20] | /tools details [1-20]"
    )


def test_manual_compaction_failure_shows_nonreducing_token_evidence(tmp_path) -> None:
    session = Session(tmp_path)

    def fail_compaction():
        raise CompactionCandidateError(
            "candidate context did not reduce provider input tokens",
            before_input_tokens=4900,
            after_input_tokens=5100,
            input_method="estimated",
        )

    session.compact_context = fail_compaction
    result = dispatch_slash("/compact", session)

    assert result.kind == "error"
    assert "input 4900 -> 5100 tokens; estimated" in result.message
    assert result.message.endswith("Full history and effective context are unchanged.")
    assert dispatch_slash("/clear", session).clear_screen is True
    assert dispatch_slash("/clear extra", session).message == "Usage: /clear"
    assert session.prompts == []


def test_compact_failure_reports_unchanged_state(tmp_path) -> None:
    session = Session(tmp_path)

    def fail():
        from leonervis_code.core.compaction import CompactionNotEligibleError

        raise CompactionNotEligibleError("too few turns")

    session.compact_context = fail
    result = dispatch_slash("/compact", session)

    assert result.kind == "error"
    assert "too few turns" in result.message
    assert "Full history and effective context are unchanged." in result.message


def test_valid_session_commands_do_not_enter_model_history(tmp_path) -> None:
    session = Session(tmp_path)

    created = dispatch_slash("/session new", session)
    resumed = dispatch_slash("/resume 32345678-1234-4234-9234-123456789abc", session)

    assert created.kind == "success"
    assert "runtime provider unchanged" in created.message
    assert resumed.kind == "warning"
    assert "fake runtime" in resumed.message
    assert session.current == "32345678-1234-4234-9234-123456789abc"
    assert session.prompts == []


def test_valid_provider_commands_and_history(tmp_path) -> None:
    session = Session(tmp_path)

    assert "one: custom/model-one" in dispatch_slash("/provider list", session).message
    assert dispatch_slash("/provider use one", session).kind == "success"
    assert dispatch_slash("/model model-two", session).kind == "success"
    history = dispatch_slash("/history 1", session)
    assert history.message == "User: hello\nAssistant: reply"
    assert dispatch_slash("/history 0", session).kind == "warning"
    assert session.prompts == []


def test_prefixes_remain_unknown_top_level_or_group_commands(tmp_path) -> None:
    session = Session(tmp_path)

    assert "Unknown command: /modelx one" in dispatch_slash("/modelx one", session).message
    group = dispatch_slash("/provider usex one", session)
    assert "Unknown provider command: usex" in group.message
    assert session.prompts == []


def test_output_command_inspects_sets_resets_and_validates_budget(tmp_path) -> None:
    class OutputSession(Session):
        def __init__(self, path) -> None:
            super().__init__(path)
            self.output_tokens = 1024
            self.source = "profile"
            self.updates = []

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
                max_output_tokens_source=self.source,
                model_max_output_tokens=8192,
            )

        def set_output_budget(self, value):
            previous = self.output_tokens
            self.output_tokens = 1024 if value is None else value
            self.source = "profile" if value is None else "runtime"
            self.updates.append(value)
            return OutputBudgetUpdateResult(
                self.status(), None, previous, previous != self.output_tokens
            )

    session = OutputSession(tmp_path)

    inspected = dispatch_slash("/output", session)
    assert inspected.kind == "info"
    assert "Effective output budget: 1024 tokens (profile)" in inspected.message
    assert "Configured default: 1024 tokens" in inspected.message

    changed = dispatch_slash("/output 4096", session)
    assert changed.kind == "success"
    assert "1024 -> 4096 tokens (runtime)" in changed.message
    assert session.updates == [4096]

    reset = dispatch_slash("/output reset", session)
    assert reset.kind == "success"
    assert "4096 -> 1024 tokens (profile)" in reset.message
    assert session.updates == [4096, None]

    for invalid in ("/output 0", "/output -1", "/output 100000001", "/output 1 2"):
        assert dispatch_slash(invalid, session).message == "Usage: /output [1-100000000|reset]"


def test_non_slash_text_is_not_handled(tmp_path) -> None:
    result = dispatch_slash("hello", Session(tmp_path))

    assert not result.handled
    assert not result.exit
    assert result.message is None
