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
    ProjectStatus,
    ProjectSession,
    ResumeEffect,
    SessionResumeResult,
)
from leonervis_code.core.contracts import ToolArguments, ToolUse
from leonervis_code.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from leonervis_code.mcp.client import (
    McpListedTool,
    McpLiveProcessStatus,
    McpProbeResult,
    McpServerStatus,
)
from leonervis_code.mcp.catalog import build_mcp_quarantine_catalog
from leonervis_code.mcp.config import McpServerConfiguration, McpServerEntry
from leonervis_code.hooks import HookEffect, HookEntry, HookRule, HookSetSnapshot
from leonervis_code.core.task_admission import (
    TASK_PROPOSE_START_TOOL_NAME,
    TaskAdmissionOutcome,
    TaskAdmissionProposal,
)
from leonervis_code.session_records import ActionAuditStatus, BindingSnapshot, SessionNameSource
from leonervis_code.skills import SkillActivationInspection, SkillInventoryLoader
from leonervis_code.skill_candidates import SkillCandidateStore
from leonervis_code.session_store import (
    LatestUpdateStatus,
    SessionConversationExport,
    SessionDiagnosis,
    SessionDiagnosisStatus,
    SessionInfo,
    SessionPreview,
    SessionRepairResult,
    SessionSearchMatch,
    SessionSearchResult,
    SessionStoreError,
    SessionTurnRange,
    TaskAdmissionInfo,
    ToolLedgerQueryResult,
)
from leonervis_code.tools.glob import GlobTool
from leonervis_code.tools.grep import GrepTool
from leonervis_code.tools.list_directory import ListDirectoryTool
from leonervis_code.tools.read_file import ReadFileTool
from leonervis_code.tools.command_sandbox import CommandSandboxDependencies
from leonervis_code.tools.run_command import CommandSandboxInspection
from leonervis_code.tools.catalog import TOOL_CATALOG
from leonervis_code.tools.web_search import (
    BRAVE_SEARCH_API_KEY_ENV,
    TAVILY_SEARCH_API_KEY_ENV,
    WebSearchTool,
)
from leonervis_code.task_records import (
    AcceptanceCheckOutcome,
    AcceptanceVerificationSource,
    TaskBudget,
    TaskCompletionPolicy,
    TaskScope,
    TaskStatus,
    TaskTerminalOutcome,
)
from leonervis_code.task_runtime import TaskDriverStopReason, TaskNextAction


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
        self.tasks = []
        self.web_search = WebSearchTool(
            {
                BRAVE_SEARCH_API_KEY_ENV: "brave-secret",
                TAVILY_SEARCH_API_KEY_ENV: "tavily-secret",
            }
        )
        self.mcp_entry = McpServerEntry(
            "project",
            McpServerConfiguration(
                name="fixture",
                command="/usr/bin/python3",
                args=("server.py",),
                enabled=True,
            ),
        )
        self.hooks = HookSetSnapshot(
            (
                HookEntry(
                    "project",
                    HookRule(
                        "protect-config",
                        HookEffect.DENY,
                        message="Configuration requires review.",
                        tool_names=("write_file",),
                        enabled=True,
                    ),
                ),
            )
        )
        proposal = TaskAdmissionProposal.from_request(
            ToolUse(
                "admission-1",
                TASK_PROPOSE_START_TOOL_NAME,
                ToolArguments.from_mapping(
                    {
                        "objective": "Implement a multi-stage feature",
                        "reason": "Planning and verification are required.",
                        "acceptance_criteria": ["Tests pass"],
                    }
                ),
            ),
            "ctx-v5-" + "a" * 64,
        )
        self.admissions = [
            TaskAdmissionInfo(
                proposal,
                self.current,
                self.name,
                1,
                1,
                "2026-07-31T01:02:03.000004Z",
            )
        ]

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

    def inspect_hooks(self):
        return self.hooks

    def project_status(self):
        return ProjectStatus(
            runtime=self.status(),
            session=self.session_info(),
            usage=self.usage(),
            permission_mode=PermissionMode.DANGER_FULL_ACCESS,
            approval_mode=ApprovalMode.ASK,
            sandbox=self.inspect_command_sandbox(),
        )

    def inspect_command_sandbox(self):
        return CommandSandboxInspection(
            CommandSandboxDependencies(
                platform="linux",
                platform_supported=True,
                bubblewrap_path="/usr/bin/bwrap",
                bubblewrap_available=True,
                seccomp_available=True,
            ),
            True,
            "command_succeeded",
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

    def inspect_project_instructions(self):
        return None

    def inspect_skills(self):
        inventory = SkillInventoryLoader(self.tmp_path, {}).load()
        return SkillActivationInspection(inventory.snapshot_id, (), ("read_file", "grep"))

    def inspect_skill_inventory(self):
        loader = SkillInventoryLoader(self.tmp_path, {})
        return loader.load(), loader.roots

    def list_skill_candidates(self):
        return SkillCandidateStore(self.tmp_path, {}).list()

    def inspect_skill_candidate(self, candidate_id):
        return SkillCandidateStore(self.tmp_path, {}).inspect(candidate_id)

    def reject_skill_candidate(self, candidate_id):
        return SkillCandidateStore(self.tmp_path, {}).reject(candidate_id)

    def install_skill_candidate(self, candidate_id, *, scope=None):
        store = SkillCandidateStore(self.tmp_path, {})
        store.install(candidate_id, scope=scope)
        return store.inspect(candidate_id)

    def inspect_web_search_sources(self):
        return self.web_search.source_configuration()

    def inspect_mcp_servers(self):
        return (McpServerStatus(self.mcp_entry, True, ()),)

    def inspect_mcp_server(self, name):
        if name != "fixture":
            raise ValueError(f"unknown MCP server: {name}")
        return self.inspect_mcp_servers()[0]

    def probe_mcp_server(self, name):
        self.inspect_mcp_server(name)
        return McpProbeResult(
            configured_name=name,
            protocol_version="2025-06-18",
            server_name="fixture-server",
            server_version="1.0",
            capability_names=("tools",),
            tools=(
                McpListedTool(
                    "read_widget",
                    None,
                    "UNTRUSTED_DESCRIPTION",
                    '{"type":"object"}',
                    None,
                    None,
                ),
            ),
            pages=1,
            duration_ms=4,
            stderr_bytes=0,
            stderr_truncated=False,
            cleanup_complete=True,
        )

    def inspect_mcp_catalog(self):
        return build_mcp_quarantine_catalog(
            (self.mcp_entry,),
            SimpleNamespace(probe=lambda entry: self.probe_mcp_server(entry.configuration.name)),
        )

    def inspect_mcp_runtime(self):
        return (
            McpLiveProcessStatus(
                "fixture",
                "project",
                1,
                "2025-06-18",
                2,
                3,
                True,
                0,
                False,
            ),
        )

    def set_web_search_sources(self, sources):
        return self.web_search.configure_sources(sources)

    def reset_web_search_sources(self):
        return self.web_search.reset_source_configuration()

    def set_native_search_mode(self, mode):
        return replace(self.inspect_web_search_sources(), provider_mode=mode)

    def set_native_search_domains(self, domains):
        return replace(
            self.inspect_web_search_sources(),
            provider_allowed_domains=() if domains is None else domains,
        )

    def set_native_search_context(self, size):
        return replace(self.inspect_web_search_sources(), provider_context_size=size)

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

    def session_turn_range(self, selector, start_turn, count):
        info = self.inspect_session(selector)
        return SessionTurnRange(
            info,
            len(self.turns),
            start_turn,
            self.turns[start_turn - 1 : start_turn - 1 + count],
        )

    def search_sessions(self, query, limit):
        info = self.session_info()
        matches = (
            (SessionSearchMatch(info, 1, "user", 1, self.turns[0].user.text),)
            if query in self.turns[0].user.text
            else ()
        )
        return SessionSearchResult(query, 1, 1, 100, matches[:limit], False)

    def export_session(self, selector):
        return SessionConversationExport(self.inspect_session(selector), self.turns)

    def diagnose_session(self, selector):
        info = self.inspect_session(selector)
        return SessionDiagnosis(
            info.session_id,
            SessionDiagnosisStatus.VALID,
            "ok",
            100,
            info.record_count,
            info.turn_count,
        )

    def repair_session(self, selector):
        info = self.inspect_session(selector)
        return SessionRepairResult(info, 12, self.tmp_path / f"{info.session_id}.bak")

    def fork_session(self, selector, through_turn):
        source = self.inspect_session(selector)
        self.current = "32345678-1234-4234-9234-123456789abc"
        self.latest = self.current
        self.name = "Fork of Current work"
        info = self.session_info()
        return replace(
            info,
            forked_from_session_id=source.session_id,
            forked_from_turn=through_turn,
        )

    def list_sessions(self):
        return self.sessions if self.sessions is not None else (self.session_info(),)

    def create_task(self, objective, acceptance_criteria=()):
        info = SimpleNamespace(
            task_id="42345678-1234-4234-9234-123456789abc",
            path=self.tmp_path / "task.jsonl",
            workspace=str(self.tmp_path),
            workspace_fingerprint="v1-" + "a" * 64,
            owner_session_id=self.current,
            objective=objective,
            acceptance_criteria=acceptance_criteria,
            created_at="2026-07-31T01:02:03.000004Z",
            scope=TaskScope.WORKSPACE,
            status=TaskStatus.READY,
            record_count=1,
            stages=(),
            name=objective,
            archived=False,
            parent_task_id=None,
            budget=TaskBudget(),
            usage=SimpleNamespace(
                provider_invocations=0,
                input_tokens=0,
                output_tokens=0,
                known_token_invocations=0,
                unknown_token_invocations=0,
                tool_requests=0,
                unavailable_stages=0,
            ),
            budget_exhausted=(),
            latest_plan=None,
            acceptance_verifications=(),
            criteria=(),
            completion_policy=TaskCompletionPolicy.MANUAL,
            acceptance_checks=(),
            terminal_outcome=None,
            terminal_reason=None,
            driver_paused=False,
            latest_reflection=None,
            latest_checkpoint=None,
        )
        self.tasks.append(info)
        return info

    def list_tasks(self):
        return tuple(reversed(self.tasks))

    def inspect_task(self, task_id):
        for info in self.tasks:
            if info.task_id == task_id:
                return info
        raise SessionStoreError(f"task transcript does not exist: {task_id}")

    def hook_evaluations(self, limit=20):
        return ()

    def hook_handler_runs(self, limit=20):
        return ()

    def task_hook_evaluations(self, task_id, limit=20):
        return ()

    def list_task_admissions(self):
        return tuple(self.admissions)

    def inspect_task_admission(self, admission_id):
        for info in self.admissions:
            if info.proposal.admission_id == admission_id:
                return info
        raise SessionStoreError("Task admission proposal was not found")

    def preview_task_admission_acceptance(self, admission_id, configuration):
        info = self.inspect_task_admission(admission_id)
        return SimpleNamespace(
            proposal=info.proposal,
            name=configuration.name or info.proposal.objective,
            budget=configuration.budget,
            completion_policy=configuration.completion_policy,
            criteria=tuple(
                SimpleNamespace(description=value, kind=SimpleNamespace(value="human"))
                for value in info.proposal.acceptance_criteria
            ),
            configuration_sha256="b" * 64,
            confirmation_sha256="a" * 64,
        )

    def accept_task_admission(
        self,
        admission_id,
        configuration,
        *,
        confirmation_sha256,
    ):
        assert confirmation_sha256 == "a" * 64
        info = self.inspect_task_admission(admission_id)
        task = self.create_task(info.proposal.objective, info.proposal.acceptance_criteria)
        self.admissions[0] = replace(
            info,
            outcome=TaskAdmissionOutcome.ACCEPTED,
            task_id=task.task_id,
            resolved_at="2026-07-31T01:03:00.000000Z",
        )
        return task

    def accepted_task_for_admission(self, admission_id):
        info = self.inspect_task_admission(admission_id)
        if info.task_id is None:
            raise SessionStoreError("Task admission proposal has not been accepted")
        return self.inspect_task(info.task_id)

    def reject_task_admission(self, admission_id, reason=None):
        info = self.inspect_task_admission(admission_id)
        self.admissions[0] = replace(
            info,
            outcome=TaskAdmissionOutcome.REJECTED,
            rejection_reason=reason,
            resolved_at="2026-07-31T01:03:00.000000Z",
        )
        return self.admissions[0]

    def derive_task(self, parent_task_id, objective):
        parent = self.inspect_task(parent_task_id)
        child = self.create_task(objective)
        child.task_id = "52345678-1234-4234-9234-123456789abc"
        child.parent_task_id = parent.task_id
        return child

    def recover_task(self, task_id):
        return self.inspect_task(task_id)

    def accept_task_plan(self, task_id):
        info = self.inspect_task(task_id)
        info.latest_plan = SimpleNamespace(
            plan_id="62345678-1234-4234-9234-123456789abc",
            steps=("First",),
            accepted=True,
            completed_steps=0,
        )
        return info

    def verify_task_acceptance(self, task_id, criterion_index, evidence):
        info = self.inspect_task(task_id)
        info.acceptance_verifications = (
            *info.acceptance_verifications,
            SimpleNamespace(
                criterion_index=criterion_index,
                evidence=evidence,
                verified_at="2026-07-31T01:03:00.000000Z",
                source=AcceptanceVerificationSource.USER,
            ),
        )
        return info

    def verify_task_host(self, task_id):
        info = self.inspect_task(task_id)
        return SimpleNamespace(
            task=info,
            checks=(
                SimpleNamespace(
                    criterion_index=1,
                    source=AcceptanceVerificationSource.HOST_CHECK,
                    outcome=AcceptanceCheckOutcome.PASSED,
                    evidence="path=artifact.txt expected=file observed=file",
                ),
            ),
            auto_completed=False,
        )

    def review_task_acceptance(self, task_id):
        info = self.inspect_task(task_id)
        return SimpleNamespace(
            task=info,
            checks=(
                SimpleNamespace(
                    criterion_index=1,
                    source=AcceptanceVerificationSource.INDEPENDENT_REVIEWER,
                    outcome=AcceptanceCheckOutcome.NEEDS_HUMAN,
                    evidence="The bounded snapshot is insufficient.",
                ),
            ),
            auto_completed=False,
        )

    def preview_task_next(self, task_id):
        self.inspect_task(task_id)
        return TaskNextAction(
            TaskDriverStopReason.PLAN_REQUIRED,
            "A bounded plan proposal is required.",
            True,
            True,
        )

    def checkpoint_task(self, task_id):
        info = self.inspect_task(task_id)
        info.latest_checkpoint = SimpleNamespace(
            checkpoint_id="72345678-1234-4234-9234-123456789abc",
            source_sequence=info.record_count - 1,
            unresolved_criterion_indices=(),
        )
        info.record_count += 1
        return info

    def set_task_driver_paused(self, task_id, paused, reason=None):
        info = self.inspect_task(task_id)
        info.driver_paused = paused
        return info

    def complete_task(self, task_id):
        info = self.inspect_task(task_id)
        info.status = TaskStatus.COMPLETED
        info.terminal_outcome = TaskTerminalOutcome.COMPLETED
        return info

    def cancel_task(self, task_id, reason):
        info = self.inspect_task(task_id)
        info.status = TaskStatus.CANCELLED
        info.terminal_outcome = TaskTerminalOutcome.CANCELLED
        info.terminal_reason = reason
        return info

    def fail_task(self, task_id, reason):
        info = self.inspect_task(task_id)
        info.status = TaskStatus.FAILED
        info.terminal_outcome = TaskTerminalOutcome.FAILED
        info.terminal_reason = reason
        return info

    def rename_task(self, task_id, name):
        info = self.inspect_task(task_id)
        info.name = name
        return info

    def set_task_archived(self, task_id, archived):
        info = self.inspect_task(task_id)
        info.archived = archived
        return info

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
    task_help = dispatch_slash("/task", session).message
    assert "Task commands:" in task_help
    assert "/task proposal accept <admission-id> confirm <sha256>" in task_help
    assert "/task proposal drive <admission-id> [1-16]" in task_help
    assert "Provider commands:" in dispatch_slash("/provider", session).message
    assert "Search source commands:" in dispatch_slash("/search", session).message
    assert "Host command groups:" in dispatch_slash("/help", session).message
    assert "Tool and audit commands:" in dispatch_slash("/help tools", session).message
    assert "Read-only Git commands:" in dispatch_slash("/help git", session).message
    assert "Input controls:" in dispatch_slash("/help input", session).message
    assert "Policy commands:" in dispatch_slash("/help policy", session).message
    assert "Search source commands:" in dispatch_slash("/help search", session).message
    assert "MCP inspection commands:" in dispatch_slash("/help mcp", session).message
    assert "Skill inspection commands:" in dispatch_slash("/help skills", session).message
    assert "Hook inspection commands:" in dispatch_slash("/help hooks", session).message
    assert dispatch_slash("/help unknown", session).message == (
        "Usage: /help [session|task|tools|git|context|provider|search|mcp|skills|hooks|policy|input]"
    )
    unknown = dispatch_slash("/session wat", session)
    assert unknown.kind == "warning"
    assert unknown.message == (
        "Unknown session command: wat\nUsage: "
        "/session <show|preview|turns|search|export|fork|doctor|repair|list|new|rename|archive|unarchive|pin|unpin|switch>"
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
    assert dispatch_slash("/session turns latest 0", session).message == (
        "Usage: /session turns <latest|session-id> <start> [1-10]"
    )
    assert dispatch_slash("/session search", session).message == (
        "Usage: /session search <literal text>"
    )
    assert dispatch_slash("/session export latest yaml", session).message == (
        "Usage: /session export <latest|session-id> [markdown|json]"
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
    status = dispatch_slash("/status", session).message
    assert "Session: Automatic title" in status
    assert "Permission mode: danger-full-access" in status
    assert "Command sandbox: ready; activation verified" in status
    assert dispatch_slash("/status extra", session).message == "Usage: /status"
    permissions = dispatch_slash("/permissions", session).message
    assert "Current policy: permission=danger-full-access, approval=ask" in permissions
    assert "Command sandbox: ready; activation verified" in permissions
    assert "Permission decisions describe policy only" in permissions
    assert "workspace-create: ask (approval_required_workspace_create)" in permissions
    assert "network-read: ask (approval_required_network_read)" in permissions
    assert "network-write: ask (approval_required_network_write)" in permissions
    assert "dangerous: ask (approval_required_dangerous)" in permissions
    preview = dispatch_slash("/permissions workspace-write auto", session).message
    assert "Policy preview (not applied): permission=workspace-write, approval=auto" in preview
    assert "workspace-create: allow (allowed_workspace_create_auto)" in preview
    assert "network-read: deny (denied_network_access_mode)" in preview
    assert "network-write: deny (denied_network_access_mode)" in preview
    assert "dangerous: deny (denied_workspace_write_mode)" in preview
    assert session.project_status().permission_mode == PermissionMode.DANGER_FULL_ACCESS
    permissions_usage = (
        "Usage: /permissions | /permissions "
        "<read-only|workspace-write|danger-full-access> [ask|auto]"
    )
    assert dispatch_slash("/permissions auto", session).message == permissions_usage
    assert dispatch_slash("/permissions workspace-write later", session).message == (
        permissions_usage
    )
    sandbox = dispatch_slash("/sandbox check", session).message
    assert "Activation probe: verified" in sandbox
    assert "Probe result: command_succeeded" in sandbox
    assert dispatch_slash("/sandbox extra", session).message == "Usage: /sandbox check"
    context = dispatch_slash("/context", session)
    assert context.kind == "warning"
    assert "Context ID: ctx-v21-" in context.message
    assert dispatch_slash("/context extra", session).message == "Usage: /context"
    instructions = dispatch_slash("/instructions", session)
    assert instructions.kind == "info"
    assert instructions.message == "Project instructions: absent\nPath: AGENTS.md"
    assert dispatch_slash("/instructions extra", session).message == "Usage: /instructions"
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
    actions_usage = "Usage: /actions last | /actions [1-100] [status=<status>] [tool=<name>]"
    assert dispatch_slash("/actions 0", session).message == actions_usage
    assert dispatch_slash("/actions 101", session).message == actions_usage
    assert dispatch_slash("/actions two", session).message == actions_usage
    assert dispatch_slash("/actions status=unknown", session).message == actions_usage
    assert dispatch_slash("/tools", session).message == "No committed turns yet."
    assert dispatch_slash("/tools 10", session).message == "No committed turns yet."
    assert dispatch_slash("/tools details", session).message == "No committed turns yet."
    assert dispatch_slash("/tools details 10", session).message == "No committed turns yet."
    catalog = dispatch_slash("/tools catalog", session).message
    assert f"Model-visible tools: {len(TOOL_CATALOG)} in canonical order" in catalog
    assert "Registry snapshot: registry-v1-" in catalog
    assert " generation=5" in catalog
    assert " 6. run_command: dangerous; available (ask; sandbox required)" in catalog
    assert (
        "22. web_search: network-read; available "
        "(ask; BRAVE_SEARCH_API_KEY or TAVILY_API_KEY required)" in catalog
    )
    run_command = dispatch_slash("/tools catalog run_command", session).message
    assert f"Tool 6/{len(TOOL_CATALOG)}: run_command" in run_command
    assert "Contract: tool-v1-" in run_command
    assert "Source: builtin:leonervis-code generation=5" in run_command
    assert "Exposure: direct" in run_command
    assert "argv: array<string> [1..64 items]; required" in run_command
    assert "timeout_seconds: integer [1..300]; required" in run_command
    assert "Linux sandbox required" in run_command
    for definition in TOOL_CATALOG:
        detail = dispatch_slash(f"/tools catalog {definition.name}", session).message
        assert f": {definition.name}" in detail
        assert "Hard boundaries:" in detail
    unknown_tool = dispatch_slash("/tools catalog git_stats", session).message
    assert "Unknown model-visible tool: git_stats" in unknown_tool
    assert "Did you mean git_status?" in unknown_tool
    assert dispatch_slash("/tools 0", session).message == (
        "Usage: /tools catalog [tool-name] | /tools [1-20] | /tools details [1-20]"
    )
    assert dispatch_slash("/tools details 21", session).message == (
        "Usage: /tools catalog [tool-name] | /tools [1-20] | /tools details [1-20]"
    )


def test_skills_slash_commands_are_read_only_bounded_inspections(tmp_path) -> None:
    package = tmp_path / ".agents" / "skills" / "demo"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nmanifest-version: 1\nname: demo\ndescription: Demo workflow\n---\nDo it.\n",
        encoding="utf-8",
    )
    (package / "guide.md").write_text("Guide.\n", encoding="utf-8")
    session = Session(tmp_path)

    active = dispatch_slash("/skills", session)
    listed = dispatch_slash("/skills list", session)
    shown = dispatch_slash("/skills show demo", session)
    searched = dispatch_slash("/skills search demo workflow", session)
    conflicts = dispatch_slash("/skills conflicts", session)
    doctor = dispatch_slash("/skills doctor", session)

    assert "Active Skills: 0/4" in active.message
    assert "No Skill load pair is retained" in active.message
    assert "demo [active]" in listed.message
    assert "Resources: 1" in shown.message
    assert "guide.md" in shown.message
    assert "Instructions:\nDo it.\n" in shown.message
    assert "Matches: 1" in searched.message
    assert "demo [active]" in searched.message
    assert "Skill conflicts: 0" in conflicts.message
    assert "Issues: 0" in doctor.message
    assert dispatch_slash("/skills nope", session).kind == "warning"


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


def test_session_search_turns_export_doctor_repair_and_fork_commands(tmp_path) -> None:
    session = Session(tmp_path)
    session.turns = (Turn(Text("hello alpha"), Text("reply")),)

    turns = dispatch_slash(f"/session turns {session.current} 1 3", session)
    search = dispatch_slash("/session search alpha", session)
    exported = dispatch_slash(f"/session export {session.current} json", session)
    doctor = dispatch_slash(f"/session doctor {session.current}", session)
    repair = dispatch_slash(f"/session repair {session.current}", session)
    forked = dispatch_slash(f"/session fork {session.current} 1", session)

    assert turns.kind == "info"
    assert "Showing 1 turns from #1" in turns.message
    assert "Turn #1" in search.message
    assert '"schema_version": 1' in exported.message
    assert "Status: valid" in doctor.message
    assert repair.kind == "success"
    assert "Truncated incomplete tail bytes: 12" in repair.message
    assert forked.kind == "success"
    assert "Forked and selected Session" in forked.message
    assert session.current == "32345678-1234-4234-9234-123456789abc"


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
    latest = dispatch_slash("/actions last", session).message
    assert "Action #3: read_file" in latest
    assert "Action #2" not in latest


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


def test_task_commands_are_host_only_and_bind_creation_to_current_session(tmp_path) -> None:
    session = Session(tmp_path)

    empty = dispatch_slash("/task list", session)
    created = dispatch_slash("/task start Implement durable stages", session)
    listed = dispatch_slash("/task list", session)
    shown = dispatch_slash(
        "/task show 42345678-1234-4234-9234-123456789abc",
        session,
    )

    assert empty.message == "No durable Tasks found."
    assert created.kind == "success"
    assert "Task: Implement durable stages" in created.message
    assert f"Owner Session: {session.current}" in created.message
    assert "Implement durable stages" in listed.message
    assert shown.kind == "info"
    assert "Status: ready" in shown.message
    assert session.prompts == []
    assert dispatch_slash("/task start", session).message == "Usage: /task start <objective>"
    assert dispatch_slash("/task list extra", session).message == (
        "Usage: /task list [1-100] [status=<status>] [active|archived] [name=<text>]"
    )
    assert dispatch_slash("/task list 20 30", session).message == (
        "Usage: /task list [1-100] [status=<status>] [active|archived] [name=<text>]"
    )
    assert dispatch_slash("/task show bad", session).message == "Usage: /task show <task-id>"


def test_task_admission_commands_are_host_only_and_require_exact_id(tmp_path) -> None:
    session = Session(tmp_path)
    admission_id = session.admissions[0].proposal.admission_id

    listed = dispatch_slash("/task proposals", session)
    shown = dispatch_slash(f"/task proposal show {admission_id}", session)
    preview = dispatch_slash(f"/task proposal accept {admission_id}", session)
    assert session.tasks == []
    accepted = dispatch_slash(
        f"/task proposal accept {admission_id} confirm {'a' * 64}",
        session,
    )

    assert listed.kind == "info"
    assert admission_id in listed.message
    assert "Status: pending" in shown.message
    assert preview.kind == "info"
    assert "no Task created" in preview.message
    assert accepted.kind == "success"
    assert "created durable Task" in accepted.message
    assert session.admissions[0].status == "accepted"
    driven = dispatch_slash(f"/task proposal drive {admission_id} 3", session)
    assert driven.task_request is not None
    assert driven.task_request.operation == "drive"
    assert driven.task_request.max_stages == 3
    assert session.prompts == []
    assert dispatch_slash("/task proposal show bad", session).message == (
        "Usage: /task proposal show <admission-id>"
    )

    rejected_session = Session(tmp_path)
    rejected_id = rejected_session.admissions[0].proposal.admission_id
    rejected = dispatch_slash(
        f"/task proposal reject {rejected_id} defer this work",
        rejected_session,
    )
    assert rejected.kind == "success"
    assert "Rejection reason: defer this work" in rejected.message
    assert rejected_session.list_tasks() == ()

    pending_session = Session(tmp_path)
    pending_id = pending_session.admissions[0].proposal.admission_id
    not_accepted = dispatch_slash(f"/task proposal drive {pending_id}", pending_session)
    assert not_accepted.kind == "error"
    assert "has not been accepted" in not_accepted.message

    configured_session = Session(tmp_path)
    configured_id = configured_session.admissions[0].proposal.admission_id
    configured = dispatch_slash(
        f"/task proposal accept {configured_id} "
        '\'{"name":"Reviewed Task","budget":{"max_stages":4}}\'',
        configured_session,
    )
    assert configured.kind == "info"
    assert "Task name: Reviewed Task" in configured.message
    assert "stages=4" in configured.message
    assert configured_session.list_tasks() == ()


def test_task_execution_commands_are_deferred_and_host_management_stays_local(tmp_path) -> None:
    session = Session(tmp_path)
    task_id = (
        dispatch_slash("/task start Ship the Task runtime", session)
        .message.split("Task ID: ", 1)[1]
        .splitlines()[0]
    )

    continued = dispatch_slash(f"/task continue {task_id} Implement one Stage", session)
    planned = dispatch_slash(f"/task plan {task_id}", session)
    run = dispatch_slash(f"/task run {task_id} 4", session)
    reflected = dispatch_slash(f"/task reflect {task_id}", session)
    corrected = dispatch_slash(f"/task correct {task_id} Repair the artifact", session)
    revised = dispatch_slash(f"/task revise {task_id}", session)
    driven = dispatch_slash(f"/task drive {task_id} 3", session)

    assert continued.task_request.operation == "continue"
    assert continued.task_request.task_id == task_id
    assert continued.task_request.stage_objective == "Implement one Stage"
    assert planned.task_request.operation == "plan"
    assert run.task_request.operation == "run"
    assert run.task_request.max_stages == 4
    assert reflected.task_request.operation == "reflect"
    assert corrected.task_request.operation == "correct"
    assert corrected.task_request.stage_objective == "Repair the artifact"
    assert revised.task_request.operation == "revise"
    assert driven.task_request.operation == "drive"
    assert driven.task_request.max_stages == 3
    assert session.prompts == []

    assert dispatch_slash(f"/task plan accept {task_id}", session).kind == "success"
    assert dispatch_slash(f"/task recover {task_id}", session).kind == "success"
    assert dispatch_slash(f"/task verify {task_id} 1 pytest-passed", session).kind == "success"
    host = dispatch_slash(f"/task verify host {task_id}", session)
    assert host.kind == "success"
    assert "passed by host-check" in host.message
    review = dispatch_slash(f"/task review {task_id}", session)
    assert review.kind == "success"
    assert "needs-human by independent-reviewer" in review.message
    assert dispatch_slash(f"/task rename {task_id} Release-ready", session).kind == "success"
    assert dispatch_slash(f"/task archive {task_id}", session).kind == "success"
    assert "Release-ready" in dispatch_slash("/task list archived name=release", session).message
    assert dispatch_slash(f"/task unarchive {task_id}", session).kind == "success"
    assert dispatch_slash(f"/task timeline {task_id}", session).kind == "info"
    assert (
        "Next Task decision: plan-required"
        in dispatch_slash(f"/task next {task_id}", session).message
    )
    assert dispatch_slash(f"/task pause {task_id} study-break", session).kind == "success"
    assert session.inspect_task(task_id).driver_paused is True
    assert dispatch_slash(f"/task checkpoint {task_id}", session).kind == "success"
    assert dispatch_slash(f"/task resume {task_id}", session).kind == "success"
    assert session.inspect_task(task_id).driver_paused is False
    derived = dispatch_slash(f"/task derive {task_id} Follow-up checks", session)
    assert derived.kind == "success"
    assert f"Derived from: {task_id}" in derived.message
    assert session.prompts == []


def test_task_terminal_commands_and_usage_errors_are_explicit(tmp_path) -> None:
    session = Session(tmp_path)
    dispatch_slash("/task start Terminal Task", session)
    task_id = session.tasks[0].task_id

    completed = dispatch_slash(f"/task complete {task_id}", session)
    assert completed.kind == "success"
    assert "Terminal outcome: completed" in completed.message

    other = session.create_task("Other Task")
    cancelled = dispatch_slash(f"/task cancel {other.task_id} superseded", session)
    assert "Terminal outcome: cancelled" in cancelled.message
    other.status = TaskStatus.READY
    other.terminal_outcome = None
    failed = dispatch_slash(f"/task fail {other.task_id} unrecoverable", session)
    assert "Terminal outcome: failed" in failed.message

    assert dispatch_slash("/task continue bad objective", session).kind == "warning"
    assert dispatch_slash(f"/task run {task_id} 17", session).kind == "warning"
    assert dispatch_slash(f"/task verify {task_id} 0 evidence", session).kind == "warning"
    assert dispatch_slash("/task verify host bad", session).message == (
        "Usage: /task verify host <task-id>"
    )
    assert dispatch_slash("/task review bad", session).message == "Usage: /task review <task-id>"
    assert dispatch_slash(f"/task cancel {task_id}", session).kind == "warning"
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


def test_search_commands_switch_primary_and_reserve_ordered_multi_source_activation(
    tmp_path,
) -> None:
    session = Session(tmp_path)

    initial = dispatch_slash("/search status", session)
    assert initial.kind == "info"
    assert "brave: available" in initial.message
    assert "tavily: available" in initial.message
    assert "Active order: none" in initial.message

    multiple = dispatch_slash("/search use tavily brave", session)
    assert multiple.kind == "success"
    assert "tavily: available, active, primary" in multiple.message
    assert "brave: available, active" in multiple.message
    assert "Active order: tavily, brave" in multiple.message
    assert "Current execution: primary-only (tavily)" in multiple.message
    assert "Host never guesses a query or calls them automatically" not in multiple.message

    mode = dispatch_slash("/search mode required", session)
    assert mode.kind == "success"
    assert "Provider mode: required" in mode.message

    domains = dispatch_slash("/search domains openai.com platform.openai.com", session)
    assert domains.kind == "success"
    assert "Provider allowed domains: openai.com, platform.openai.com" in domains.message

    context = dispatch_slash("/search context high", session)
    assert context.kind == "success"
    assert "Provider search context: high" in context.message

    switched = dispatch_slash("/search use brave", session)
    assert switched.kind == "success"
    assert "Active order: brave" in switched.message
    assert "Current execution: primary-only (brave)" in switched.message
    assert dispatch_slash("/search sources", session).message == switched.message

    reset = dispatch_slash("/search reset", session)
    assert reset.kind == "success"
    assert "Active order: none" in reset.message
    assert "Configuration issue:" in reset.message

    for invalid in (
        "/search use",
        "/search use unknown",
        "/search use tavily tavily",
        "/search status extra",
        "/search reset extra",
    ):
        assert dispatch_slash(invalid, session).kind == "warning"
    typo = dispatch_slash("/search us tavily", session)
    assert "Unknown search command: us" in typo.message
    assert "Did you mean use?" in typo.message
    assert session.prompts == []


def test_mcp_commands_are_host_only_and_probe_does_not_expose_tools(tmp_path) -> None:
    session = Session(tmp_path)
    original_turns = session.turns

    listed = dispatch_slash("/mcp list", session)
    assert listed.handled and listed.kind == "info"
    assert "fixture: project, enabled, ready, r1" in listed.message
    status = dispatch_slash("/mcp status", session)
    assert "generation 2; calls 3; alive" in status.message
    shown = dispatch_slash("/mcp show fixture", session)
    assert "normalized deferred candidates" in shown.message
    probed = dispatch_slash("/mcp probe fixture", session)
    assert probed.kind == "success"
    assert "read_widget" in probed.message
    assert "UNTRUSTED_DESCRIPTION" not in probed.message
    assert "use mcp catalog to inspect normalized quarantine candidates" in probed.message
    catalog = dispatch_slash("/mcp catalog", session)
    assert catalog.kind == "info"
    assert "Candidates: 1 accepted, 0 rejected" in catalog.message
    assert "UNTRUSTED_DESCRIPTION" not in catalog.message
    assert session.turns == original_turns

    assert dispatch_slash("/mcp show", session).message == "Usage: /mcp show <server-name>"
    assert dispatch_slash("/mcp probe extra value", session).message == (
        "Usage: /mcp probe <server-name>"
    )
    assert "Did you mean probe?" in dispatch_slash("/mcp proeb", session).message


def test_hook_commands_are_host_only_read_only_inspections(tmp_path) -> None:
    session = Session(tmp_path)
    original_turns = session.turns

    active = dispatch_slash("/hooks", session)
    assert active.handled and active.kind == "info"
    assert "Active Hooks: 1" in active.message
    listed = dispatch_slash("/hooks list", session)
    assert (
        "protect-config: project, before_action_authorization, deny, enabled, r1" in listed.message
    )
    shown = dispatch_slash("/hooks show protect-config", session)
    assert "Message: Configuration requires review." in shown.message
    doctor = dispatch_slash("/hooks doctor", session)
    assert "Handler readiness requires standalone hooks doctor." in doctor.message
    assert dispatch_slash("/hooks evaluations 5", session).message == (
        "No durable Hook evaluations found."
    )
    assert dispatch_slash("/hooks runs 5", session).message == (
        "No audited Hook handler runs found."
    )
    assert (
        dispatch_slash(
            "/hooks task 12345678-1234-4234-9234-123456789abc 5",
            session,
        ).message
        == "No durable Hook evaluations found."
    )
    assert session.turns == original_turns
    assert session.prompts == []

    assert dispatch_slash("/hooks show", session).message == "Usage: /hooks show <hook-id>"
    assert dispatch_slash("/hooks evaluations 0", session).message == (
        "Usage: /hooks evaluations [1-100]"
    )
    assert dispatch_slash("/hooks runs 0", session).message == "Usage: /hooks runs [1-100]"
    assert "Did you mean doctor?" in dispatch_slash("/hooks doctro", session).message


def test_real_search_commands_are_process_local_and_do_not_invoke_provider_or_write_session(
    tmp_path,
) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={TAVILY_SEARCH_API_KEY_ENV: "tavily-secret"},
    )
    transcript = session.transcript_path
    before = (
        transcript.read_bytes(),
        session.history,
        session.action_audits(),
        session.usage(),
    )
    try:
        inspected = dispatch_slash("/search status", session)
        assert inspected.kind == "info"
        assert "tavily: available" in inspected.message
        assert "Active order: none" in inspected.message

        selected = dispatch_slash("/search use tavily", session)
        assert selected.kind == "success"
        assert "Selection source: runtime" in selected.message
        assert (
            transcript.read_bytes(),
            session.history,
            session.action_audits(),
            session.usage(),
        ) == before
    finally:
        session.close()


def test_real_mcp_inspection_does_not_mutate_session_or_tool_surface(tmp_path) -> None:
    project_mcp_path = tmp_path / ".leonervis-code" / "mcp-test.json"
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_mcp_path=tmp_path / "user-mcp.json",
        project_mcp_path=project_mcp_path,
    )
    transcript = session.transcript_path
    before = (
        transcript.read_bytes(),
        session.history,
        session.action_audits(),
        session.usage(),
        session.project_status().tool_count,
    )
    try:
        listed = dispatch_slash("/mcp list", session)
        assert listed.kind == "info"
        assert listed.message == "No MCP servers configured."
        catalog = dispatch_slash("/mcp catalog", session)
        assert catalog.kind == "info"
        assert "Candidates: 0 accepted, 0 rejected" in catalog.message
        assert (
            transcript.read_bytes(),
            session.history,
            session.action_audits(),
            session.usage(),
            session.project_status().tool_count,
        ) == before
    finally:
        session.close()


def test_prefixes_remain_unknown_top_level_or_group_commands(tmp_path) -> None:
    session = Session(tmp_path)

    assert "Unknown command: /modelx one" in dispatch_slash("/modelx one", session).message
    group = dispatch_slash("/provider usex one", session)
    assert "Unknown provider command: usex" in group.message
    assert "Did you mean use?" in group.message
    typo = dispatch_slash("/sandox", session)
    assert "Unknown command: /sandox" in typo.message
    assert "Did you mean /sandbox?" in typo.message
    assert session.prompts == []


def test_host_discovery_commands_do_not_mutate_real_session_or_invoke_runtime(
    tmp_path,
) -> None:
    session = ProjectSession.open(tmp_path, environment={})
    transcript = session.transcript_path
    before = (
        transcript.read_bytes(),
        session.history,
        session.action_audits(),
        session.usage(),
        session.session_info(),
    )

    assert dispatch_slash("/permissions", session).handled
    assert dispatch_slash("/permissions workspace-write auto", session).handled
    assert dispatch_slash("/tools catalog run_command", session).handled
    assert dispatch_slash("/sandox", session).handled

    after = (
        transcript.read_bytes(),
        session.history,
        session.action_audits(),
        session.usage(),
        session.session_info(),
    )
    session.close()
    assert after == before


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
