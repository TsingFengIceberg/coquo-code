from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace

from leonervis_code.agent.loop import AgentLoop
from leonervis_code.cli.presentation import ToolDetailMode
from leonervis_code.cli.slash import SessionSwitchCatalog, ToolDetailSettings, dispatch_slash
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
from leonervis_code.core.contracts import ToolArguments
from leonervis_code.core.permissions import PermissionAction
from leonervis_code.session_records import ActionAuditStatus, BindingSnapshot, SessionNameSource
from leonervis_code.session_store import (
    LatestUpdateStatus,
    SessionInfo,
    SessionPreview,
    SessionStoreError,
    ToolLedgerQueryResult,
)
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
        self.audits = ()
        self.sessions = None
        self.name = "Current work"
        self.name_source = SessionNameSource.AUTO
        self.archived = False
        self.pinned = False

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
            name=self.name,
            name_source=self.name_source,
            archived=self.archived,
            pinned=self.pinned,
        )

    def session_info(self):
        return self._info(self.current)

    def action_audits(self):
        return self.audits

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

    def inspect_session(self, selector):
        session_id = self.latest if selector == "latest" else selector
        for info in self.list_sessions():
            if info.session_id == session_id:
                return info
        raise SessionStoreError(f"session transcript does not exist: {session_id}")

    def preview_session(self, selector, limit):
        info = self.inspect_session(selector)
        return SessionPreview(info, len(self.turns), self.turns[-limit:])

    def list_sessions(self):
        return self.sessions if self.sessions is not None else (self.session_info(),)

    def new_session(self):
        self.current = "22345678-1234-4234-9234-123456789abc"
        self.latest = self.current
        self.name = "New session 2"
        self.name_source = SessionNameSource.DEFAULT
        return self.session_info()

    def rename_session(self, name=None):
        self.name = " ".join(name.split()) if name is not None else "Automatic title"
        self.name_source = SessionNameSource.MANUAL if name is not None else SessionNameSource.AUTO
        return self.session_info()

    def set_session_archived(self, archived):
        self.archived = archived
        return self.session_info()

    def set_session_pinned(self, pinned):
        self.pinned = pinned
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
    assert "Host command groups:" in dispatch_slash("/help", session).message
    assert "Tool and audit commands:" in dispatch_slash("/help tools", session).message
    assert "Read-only Git commands:" in dispatch_slash("/help git", session).message
    assert "Input controls:" in dispatch_slash("/help input", session).message
    assert dispatch_slash("/help unknown", session).message == (
        "Usage: /help [session|tools|git|context|provider|input]"
    )
    unknown = dispatch_slash("/session wat", session)
    assert unknown.kind == "warning"
    assert unknown.message == (
        "Unknown session command: wat\nUsage: "
        "/session <show|preview|list|new|rename|archive|unarchive|pin|unpin|switch>"
    )
    assert dispatch_slash("/session show one two", session).message == (
        "Usage: /session show [latest|session-id]"
    )
    assert dispatch_slash("/session show ../outside", session).message == (
        "Usage: /session show [latest|session-id]"
    )
    assert dispatch_slash("/session preview", session).message == (
        "Usage: /session preview <latest|session-id> [1-10]"
    )
    assert dispatch_slash("/session preview ../outside", session).message == (
        "Usage: /session preview <latest|session-id> [1-10]"
    )
    assert dispatch_slash("/session rename", session).message == (
        "Usage: /session rename <name> | /session rename --auto"
    )
    renamed = dispatch_slash("/session rename  Release   review ", session)
    assert renamed.kind == "success"
    assert renamed.message == "Session name: Release review (manual)"
    restored = dispatch_slash("/session rename --auto", session)
    assert restored.message == "Session name: Automatic title (auto)"
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
    actions_usage = "Usage: /actions [1-100] [status=<status>] [tool=<name>]"
    assert dispatch_slash("/actions 0", session).message == actions_usage
    assert dispatch_slash("/actions 101", session).message == actions_usage
    assert dispatch_slash("/actions two", session).message == actions_usage
    assert dispatch_slash("/actions status=unknown", session).message == actions_usage
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


def test_session_list_browses_recent_state_and_exact_model(tmp_path) -> None:
    session = Session(tmp_path)
    first = session._info(session.current)
    second = SessionInfo(
        **{
            **first.__dict__,
            "session_id": "22345678-1234-4234-9234-123456789abc",
            "created_at": "2026-07-17T00:00:00.000000Z",
            "closed": True,
            "archived": True,
            "pinned": True,
            "name": "Adapter Review",
            "binding": replace(BindingSnapshot.fake(), selected_model="model-a"),
        }
    )
    third = SessionInfo(
        **{
            **first.__dict__,
            "session_id": "32345678-1234-4234-9234-123456789abc",
            "created_at": "2026-07-16T00:00:00.000000Z",
            "closed": True,
            "binding": replace(BindingSnapshot.fake(), selected_model="model-b"),
        }
    )
    session.sessions = (first, second, third)

    limited = dispatch_slash("/session list 2", session).message
    assert "Showing 2 most recent of 3 matching Sessions." in limited
    assert first.session_id in limited
    assert second.session_id in limited
    assert third.session_id not in limited

    filtered = dispatch_slash("/session list closed model=model-a", session).message
    assert second.session_id in filtered
    assert "runtime fake/model-a" in filtered
    assert first.session_id not in filtered
    assert dispatch_slash("/session list open model=model-a", session).message == (
        "No durable sessions match the selected filters."
    )
    archived = dispatch_slash("/session list archived name=adapter", session).message
    assert second.session_id in archived
    assert "archived" in archived
    assert first.session_id not in archived
    assert dispatch_slash("/session list active name=adapter", session).message == (
        "No durable sessions match the selected filters."
    )
    pinned = dispatch_slash("/session list pinned model=model-a", session).message
    assert second.session_id in pinned
    assert "pinned" in pinned
    assert dispatch_slash("/session list unpinned model=model-a", session).message == (
        "No durable sessions match the selected filters."
    )
    assert dispatch_slash("/session list 0", session).message == (
        "Usage: /session list [1-100] [open|closed] [active|archived] [pinned|unpinned] "
        "[model=<name>] [name=<text>]"
    )


def test_session_show_and_preview_inspect_exact_target_without_switching(tmp_path) -> None:
    session = Session(tmp_path)
    current = session.session_info()
    target = SessionInfo(
        **{
            **current.__dict__,
            "session_id": "22345678-1234-4234-9234-123456789abc",
            "name": "Earlier review",
            "closed": True,
            "turn_count": 1,
        }
    )
    session.sessions = (current, target)
    before = (session.current, session.latest, session.prompts)

    shown = dispatch_slash(f"/session show {target.session_id}", session)
    preview = dispatch_slash(f"/session preview {target.session_id} 1", session)

    assert shown.kind == "info"
    assert "Session: Earlier review" in shown.message
    assert f"Session ID: {target.session_id}" in shown.message
    assert preview.kind == "info"
    assert "Session preview: Earlier review" in preview.message
    assert "Showing latest 1 of 1 complete turns (read-only)." in preview.message
    assert "User:\n  hello" in preview.message
    assert "Assistant:\n  reply" in preview.message
    assert (session.current, session.latest, session.prompts) == before
    assert dispatch_slash(f"/session preview {target.session_id} 0", session).message == (
        "Usage: /session preview <latest|session-id> [1-10]"
    )
    failure = dispatch_slash("/session show 32345678-1234-4234-9234-123456789abc", session)
    assert failure.kind == "error"
    assert "Session inspection failed" in failure.message


def test_session_archive_commands_are_idempotent_and_preserve_identity(tmp_path) -> None:
    session = Session(tmp_path)
    session_id = session.current
    latest_id = session.latest

    archived = dispatch_slash("/session archive", session)
    assert archived.kind == "success"
    assert "marked archived" in archived.message
    assert session.archived is True
    assert session.current == session_id
    assert session.latest == latest_id
    assert "already archived" in dispatch_slash("/session archive", session).message

    active = dispatch_slash("/session unarchive", session)
    assert "marked active" in active.message
    assert session.archived is False
    assert dispatch_slash("/session archive extra", session).message == (
        "Usage: /session archive | /session unarchive"
    )

    pinned = dispatch_slash("/session pin", session)
    assert "marked pinned" in pinned.message
    assert session.pinned is True
    assert session.current == session_id
    assert session.latest == latest_id
    assert "already pinned" in dispatch_slash("/session pin", session).message
    assert "marked unpinned" in dispatch_slash("/session unpin", session).message
    assert session.pinned is False
    assert dispatch_slash("/session pin extra", session).message == (
        "Usage: /session pin | /session unpin"
    )


def test_session_switch_catalog_maps_one_snapshot_number_to_exact_id(tmp_path) -> None:
    session = Session(tmp_path)
    current = session._info(session.current)
    target = SessionInfo(
        **{
            **current.__dict__,
            "session_id": "22345678-1234-4234-9234-123456789abc",
            "created_at": "2026-07-17T00:00:00.000000Z",
            "name": "Adapter Review",
            "archived": True,
            "pinned": True,
            "turn_count": 4,
            "binding": replace(BindingSnapshot.fake(), selected_model="model-a"),
        }
    )
    session.sessions = (current, target)
    catalog = SessionSwitchCatalog()

    listing = dispatch_slash(
        "/session switch list 10 archived pinned name=adapter",
        session,
        session_switch=catalog,
    )
    assert listing.kind == "info"
    assert "1. 'Adapter Review'" in listing.message
    assert "4 turns, open, archived, pinned" in listing.message
    assert "runtime fake/model-a" in listing.message
    assert catalog.session_ids == (target.session_id,)

    switched = dispatch_slash("/session switch 1", session, session_switch=catalog)
    assert switched.kind == "warning"
    assert f"Resumed session {target.session_id}" in switched.message
    assert session.current == target.session_id
    assert catalog.session_ids == ()
    unavailable = dispatch_slash("/session switch 1", session, session_switch=catalog)
    assert unavailable.kind == "warning"
    assert "build a fresh numbered snapshot" in unavailable.message

    missing_state = dispatch_slash("/session switch", session)
    assert missing_state.kind == "error"
    assert "catalog is unavailable" in missing_state.message
    catalog.replace((target.session_id,))
    usage = dispatch_slash("/session switch list 21", session, session_switch=catalog)
    assert usage.kind == "warning"
    assert "Usage: /session switch" in usage.message
    assert catalog.session_ids == ()


def test_action_audit_filters_use_replayed_status_and_tool_name(tmp_path) -> None:
    session = Session(tmp_path)

    def audit(sequence, tool_name, status):
        return SimpleNamespace(
            identity=SimpleNamespace(
                tool_name=tool_name,
                action=PermissionAction.WORKSPACE_READ,
                arguments=ToolArguments.from_mapping({"path": f"file-{sequence}.txt"}),
            ),
            permission_result=None,
            approval_outcome=None,
            status=status,
            result_code="ok" if status == ActionAuditStatus.SUCCEEDED else "invalid_request",
            requested_sequence=sequence,
        )

    session.audits = (
        audit(1, "read_file", ActionAuditStatus.SUCCEEDED),
        audit(2, "write_file", ActionAuditStatus.FAILED),
        audit(3, "read_file", ActionAuditStatus.FAILED),
    )

    rendered = dispatch_slash("/actions 10 status=failed tool=read_file", session).message
    assert "Action #3: read_file" in rendered
    assert "Action #1" not in rendered
    assert "Action #2" not in rendered
    assert dispatch_slash("/actions status=denied", session).message == (
        "No action audits match the selected filters."
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
