"""Project-facing durable conversation facade for one workspace runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from leonervis_code.agent.loop import AgentLoop, PreparedAgentTurn
from leonervis_code.agent.tool_events import (
    AgentPromptEvent,
    ToolDispatchResult,
    ToolEventStatus,
    ToolResultDetails,
)
from leonervis_code.core.action_coordinator import (
    ActionCoordinator,
    ActionExecutionResult,
    ApprovalHandler,
    ApprovalResolution,
)
from leonervis_code.core.approval_preview import (
    ApprovalPreview,
    ApprovalPreviewKind,
    build_file_change_preview,
    build_metadata_preview,
)
from leonervis_code.core.actions import ActionIdentity, ActionLease, ActionPrecondition
from leonervis_code.core.compaction import (
    AUTO_COMPACT_HIGH_WATER_PERCENT,
    COMPACT_MAX_OUTPUT_TOKENS,
    COMPACT_RETAINED_TURNS,
    CompactSummaryPlan,
    CompactSummaryRequest,
    CompactionCandidateError,
    CompactionConflictError,
    CompactionNotEligibleError,
    CompactionTrigger,
    EffectiveContextSummary,
    build_compact_prompt,
    build_compact_source_text,
    decide_auto_compaction,
    plan_compaction,
)
from leonervis_code.core.cancellation import TurnCancellation
from leonervis_code.core.contracts import (
    CommittedTurn,
    ConversationItem,
    ConversationProvider,
    ConversationTurn,
    ToolResult,
    ToolUse,
)
from leonervis_code.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from leonervis_code.core.effective_context import (
    COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
    EffectiveContextSnapshot,
)
from leonervis_code.providers.manager import (
    CompactionRuntimeSnapshot,
    CurrentTargetContextAssessment,
    OutputBudgetUpdateResult,
    RuntimeProviderManager,
    RuntimeStatus,
    RuntimeSwitchAuditError,
    RuntimeSwitchResult,
    TurnRuntimeSnapshot,
)
from leonervis_code.providers.errors import ProviderAdapterError
from leonervis_code.providers.profile import NamedProviderProfile
from leonervis_code.providers.profile_store import ProviderProfileStore
from leonervis_code.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    ContextPreflightError,
    rejects_context_transition,
    raise_for_context_fit,
)
from leonervis_code.providers.usage import (
    ProviderInvocationKind,
    ProviderInvocationUsage,
    ProviderUsageTotals,
    RuntimeUsageSnapshot,
)
from leonervis_code.session_records import (
    ActionAuditState,
    ActionExecutionOutcome,
    BindingSnapshot,
    CompactionFailed,
    ContextCompacted,
    CONTEXT_COMPACTED_SCHEMA_VERSION,
    SessionRecord,
    TurnCommitted,
    TURN_COMMITTED_SCHEMA_VERSION,
    TurnFailed,
    TURN_FAILED_SCHEMA_VERSION,
)
from leonervis_code.session_store import (
    LatestUpdateStatus,
    SessionInfo,
    SessionResumeStaleError,
    SessionStore,
    ToolLedgerQueryResult,
    query_tool_ledgers,
    SessionStoreError,
    SessionWriter,
)
from leonervis_code.tools.delete_directory import (
    DELETE_DIRECTORY_TOOL_NAME,
    DeleteDirectoryOutcome,
    DeleteDirectoryPreparationError,
    DeleteDirectoryTool,
    PreparedDeleteDirectory,
)
from leonervis_code.tools.copy_file import (
    COPY_FILE_TOOL_NAME,
    CopyFileOutcome,
    CopyFilePreparationError,
    CopyFileTool,
    PreparedCopyFile,
)
from leonervis_code.tools.delete_file import (
    DELETE_FILE_TOOL_NAME,
    DeleteFileOutcome,
    DeleteFilePreparationError,
    DeleteFileTool,
    PreparedDeleteFile,
)
from leonervis_code.tools.edit_file import (
    EDIT_FILE_TOOL_NAME,
    EditFileOutcome,
    EditFilePreparationError,
    EditFileTool,
    PreparedEditFile,
)
from leonervis_code.tools.glob import GlobTool
from leonervis_code.tools.grep import GrepTool
from leonervis_code.tools.grep_regex import GREP_REGEX_TOOL_NAME, GrepRegexTool
from leonervis_code.tools.git_diff import (
    GIT_DIFF_TOOL_NAME,
    GitDiffScope,
    GitDiffSnapshot,
    GitDiffTool,
)
from leonervis_code.tools.git_log import (
    DEFAULT_GIT_LOG_LIMIT,
    GIT_LOG_TOOL_NAME,
    GitLogSnapshot,
    GitLogTool,
)
from leonervis_code.tools.git_show import (
    GIT_SHOW_TOOL_NAME,
    GitShowSnapshot,
    GitShowTool,
)
from leonervis_code.tools.git_status import (
    GIT_STATUS_TOOL_NAME,
    GitStatusSnapshot,
    GitStatusTool,
)
from leonervis_code.tools.list_directory import (
    LIST_DIRECTORY_TOOL_NAME,
    ListDirectoryTool,
)
from leonervis_code.tools.list_tree import LIST_TREE_TOOL_NAME, ListTreeTool
from leonervis_code.tools.mkdir import (
    MKDIR_TOOL_NAME,
    MkdirOutcome,
    MkdirPreparationError,
    MkdirTool,
    PreparedMkdir,
)
from leonervis_code.tools.move_file import (
    MOVE_FILE_TOOL_NAME,
    MoveFileOutcome,
    MoveFilePreparationError,
    MoveFileTool,
    PreparedMoveFile,
)
from leonervis_code.tools.patch_file import (
    PATCH_FILE_TOOL_NAME,
    PatchFileOutcome,
    PatchFilePreparationError,
    PatchFileTool,
    PreparedPatchFile,
)
from leonervis_code.tools.read_file import READ_FILE_TOOL_NAME, ReadFileTool
from leonervis_code.tools.read_file_lines import READ_FILE_LINES_TOOL_NAME, ReadFileLinesTool
from leonervis_code.tools.glob import GLOB_TOOL_NAME
from leonervis_code.tools.grep import GREP_TOOL_NAME
from leonervis_code.tools.run_command import (
    RUN_COMMAND_TOOL_NAME,
    PreparedRunCommand,
    RunCommandExecutionObservation,
    RunCommandOutcome,
    RunCommandPreparationError,
    RunCommandStreamObservation,
    RunCommandTool,
)
from leonervis_code.tools.write_file import (
    WRITE_FILE_TOOL_NAME,
    PreparedWriteFile,
    WriteFileOutcome,
    WriteFilePreparationError,
    WriteFileTool,
)
from leonervis_code.tools.stat_path import STAT_PATH_TOOL_NAME, StatPathTool


class ResumeEffect(StrEnum):
    ALREADY_CURRENT = "already_current"
    APPLIED = "applied"
    APPLIED_LATEST_FAILED = "applied_latest_failed"
    APPLIED_LATEST_DURABILITY_UNKNOWN = "applied_latest_durability_unknown"


@dataclass(frozen=True)
class SessionResumeResult:
    info: SessionInfo
    effect: ResumeEffect
    target_assessment: CurrentTargetContextAssessment | None
    context_id: str
    recovery_applied: bool
    latest_status: LatestUpdateStatus
    diagnostic: str | None = None

    @property
    def session_id(self) -> str:
        return self.info.session_id

    @property
    def fit_report(self) -> ContextFitReport | None:
        assessment = self.target_assessment
        return assessment.fit_report if assessment is not None else None


class SessionResumeContextError(RuntimeError):
    """Raised when a destination Session is known not to fit the current target."""

    def __init__(self, info: SessionInfo, context_id: str, report: ContextFitReport) -> None:
        self.info = info
        self.context_id = context_id
        self.report = report
        super().__init__("destination Session is incompatible with the current runtime")


class SessionResumeConflictError(RuntimeError):
    """Raised when a prepared target or current source becomes stale."""


@dataclass(frozen=True)
class CompactContextResult:
    """One committed compaction and its comparable current-target evidence."""

    session_id: str
    checkpoint_sequence: int
    source_context_id: str
    result_context_id: str
    summarized_turn_count: int
    retained_turn_count: int
    full_turn_count: int
    before_input_tokens: int
    after_input_tokens: int
    input_method: str
    fit_decision: ContextFitDecision
    trigger: CompactionTrigger = CompactionTrigger.MANUAL


@dataclass(frozen=True)
class CompactContextPreview:
    """Read-only eligibility, selection, and current-target pressure evidence."""

    source_context_id: str
    full_turn_count: int
    effective_turn_count: int
    summary_present: bool
    eligible: bool
    reason: str | None
    summarized_turn_count: int
    retained_turn_count: int
    target_assessment: CurrentTargetContextAssessment

    @property
    def fit_report(self) -> ContextFitReport | None:
        return self.target_assessment.fit_report


@dataclass(frozen=True)
class CompactionHistoryEntry:
    """Redacted durable checkpoint facts derived from strict Session replay."""

    sequence: int
    occurred_at: str
    schema_version: int
    trigger: CompactionTrigger
    high_water_percent: int | None
    full_turn_count: int
    summarized_turn_count: int
    retained_turn_count: int
    previous_checkpoint_sequence: int | None


@dataclass(frozen=True)
class CompactionHistoryResult:
    total_checkpoints: int
    checkpoints: tuple[CompactionHistoryEntry, ...]


@dataclass(frozen=True)
class AutoCompactionStarted:
    trigger: CompactionTrigger
    source_context_id: str
    input_tokens: int
    input_method: str
    requested_output_tokens: int
    context_window_tokens: int
    high_water_percent: int | None


@dataclass(frozen=True)
class AutoCompactionCommitted:
    trigger: CompactionTrigger
    result: CompactContextResult


@dataclass(frozen=True)
class AutoCompactionNotApplied:
    trigger: CompactionTrigger
    reason: str
    prompt_continues: bool


@dataclass(frozen=True)
class TurnUsageCompleted:
    usage: RuntimeUsageSnapshot


@dataclass(frozen=True)
class DurableUsageOperation:
    record_sequence: int
    occurred_at: str
    operation: str
    outcome: str
    provider_id: str
    model: str | None
    invocations: tuple[ProviderInvocationUsage, ...] | None

    @property
    def totals(self) -> ProviderUsageTotals | None:
        if self.invocations is None:
            return None
        totals = ProviderUsageTotals()
        for invocation in self.invocations:
            totals = totals.add(invocation.usage)
        return totals


@dataclass(frozen=True)
class DurableUsageSnapshot:
    operations: tuple[DurableUsageOperation, ...]
    totals: ProviderUsageTotals
    unavailable_operations: int


PromptEvent = (
    AutoCompactionStarted
    | AutoCompactionCommitted
    | AutoCompactionNotApplied
    | TurnUsageCompleted
    | AgentPromptEvent
)
PromptEventSink = Callable[[PromptEvent], None]


@dataclass(frozen=True)
class _PreparedCompaction:
    writer: SessionWriter
    loop: AgentLoop
    source: EffectiveContextSnapshot
    plan: CompactSummaryPlan
    captured_sequence: int
    captured_checkpoint: ContextCompacted | None
    captured_full: tuple[ConversationItem, ...]
    captured_effective: tuple[ConversationItem, ...]
    captured_summary: EffectiveContextSummary | None
    captured_source: str
    retained_from_full_turn: int
    trigger: CompactionTrigger


class AutoCompactionRequiredError(ContextPreflightError):
    """Raised when one mandatory automatic compaction cannot make the turn fit."""


@dataclass(frozen=True)
class EffectiveContextInspection:
    """One frozen provider-neutral context and coherent current-target assessment."""

    snapshot: EffectiveContextSnapshot
    target_assessment: CurrentTargetContextAssessment
    checkpoint: ContextCompacted | None = None

    @property
    def source(self) -> str:
        return self.snapshot.source

    @property
    def context_id(self) -> str:
        return self.snapshot.context_id

    @property
    def full_turn_count(self) -> int:
        return self.snapshot.full_turn_count

    @property
    def full_item_count(self) -> int:
        return self.snapshot.full_item_count

    @property
    def effective_turn_count(self) -> int:
        return self.snapshot.effective_turn_count

    @property
    def effective_item_count(self) -> int:
        return self.snapshot.effective_item_count

    @property
    def summary_present(self) -> bool:
        return self.snapshot.effective_summary is not None

    @property
    def retained_turn_count(self) -> int:
        return self.snapshot.effective_turn_count

    @property
    def latest_checkpoint_sequence(self) -> int | None:
        return self.checkpoint.sequence if self.checkpoint is not None else None

    @property
    def latest_checkpoint_trigger(self) -> CompactionTrigger | None:
        if self.checkpoint is None:
            return None
        return self.checkpoint.trigger

    @property
    def fit_report(self):
        return self.target_assessment.fit_report

    @property
    def fit_decision(self) -> ContextFitDecision:
        report = self.fit_report
        return report.decision if report is not None else ContextFitDecision.UNKNOWN

    @property
    def remaining_capacity(self) -> int | None:
        report = self.fit_report
        if (
            report is None
            or report.input_count.input_tokens is None
            or report.context_window_limit is None
        ):
            return None
        return (
            report.context_window_limit
            - report.input_count.input_tokens
            - report.requested_output_tokens
        )


class ProjectSession:
    """Keep one runtime and one switchable durable conversation for a workspace."""

    def __init__(
        self,
        workspace: Path,
        store: ProviderProfileStore,
        manager: RuntimeProviderManager,
        session_store: SessionStore,
        writer: SessionWriter,
        read_file: ReadFileTool,
        glob: GlobTool,
        grep: GrepTool,
        list_directory: ListDirectoryTool,
        write_file: WriteFileTool | None = None,
        edit_file: EditFileTool | None = None,
        run_command: RunCommandTool | None = None,
        mkdir: MkdirTool | None = None,
        move_file: MoveFileTool | None = None,
        delete_file: DeleteFileTool | None = None,
        delete_directory: DeleteDirectoryTool | None = None,
        copy_file: CopyFileTool | None = None,
        *,
        read_file_lines: ReadFileLinesTool | None = None,
        stat_path: StatPathTool | None = None,
        list_tree: ListTreeTool | None = None,
        grep_regex: GrepRegexTool | None = None,
        patch_file: PatchFileTool | None = None,
        git_status: GitStatusTool | None = None,
        git_diff: GitDiffTool | None = None,
        git_log: GitLogTool | None = None,
        git_show: GitShowTool | None = None,
        permission_mode: PermissionMode = PermissionMode.READ_ONLY,
        approval_mode: ApprovalMode = ApprovalMode.ASK,
        approval_handler: ApprovalHandler | None = None,
        action_uuid_factory: Callable[[], UUID | str] = uuid4,
        loop: AgentLoop | None = None,
        startup_resume_result: SessionResumeResult | None = None,
    ) -> None:
        self.workspace = workspace
        self._store = store
        self._manager = manager
        self._session_store = session_store
        self._writer = writer
        self._read_file = read_file
        self._glob = glob
        self._grep = grep
        self._list_directory = list_directory
        self._write_file = write_file or WriteFileTool(workspace)
        self._edit_file = edit_file or EditFileTool(workspace)
        self._run_command = run_command or RunCommandTool(workspace)
        self._mkdir = mkdir or MkdirTool(workspace)
        self._move_file = move_file or MoveFileTool(workspace)
        self._delete_file = delete_file or DeleteFileTool(workspace)
        self._delete_directory = delete_directory or DeleteDirectoryTool(workspace)
        self._copy_file = copy_file or CopyFileTool(workspace)
        self._read_file_lines = read_file_lines or ReadFileLinesTool(workspace)
        self._stat_path = stat_path or StatPathTool(workspace)
        self._list_tree = list_tree or ListTreeTool(workspace)
        self._grep_regex = grep_regex or GrepRegexTool(workspace)
        self._patch_file = patch_file or PatchFileTool(workspace)
        self._git_status = git_status or GitStatusTool(workspace)
        self._git_diff = git_diff or GitDiffTool(workspace)
        self._git_log = git_log or GitLogTool(workspace)
        self._git_show = git_show or GitShowTool(workspace)
        if type(permission_mode) is not PermissionMode:
            raise ValueError("permission mode is invalid")
        if type(approval_mode) is not ApprovalMode:
            raise ValueError("approval mode is invalid")
        self._permission_mode = permission_mode
        self._approval_mode = approval_mode
        self._approval_handler = approval_handler or _cancel_approval
        self._action_uuid_factory = action_uuid_factory
        self._active_action_lease: ActionLease | None = None
        self._active_action_binding: BindingSnapshot | None = None
        self._active_usage_cursor: int | None = None
        self._active_cancellation: TurnCancellation | None = None
        self._lock = RLock()
        self._closed = False
        self._active_compaction: _PreparedCompaction | None = None
        self._loop = loop or self._new_loop(writer)
        if loop is not None:
            self._loop.install_action_dispatcher(self._dispatch_action)
        self._startup_resume_result = startup_resume_result

    @classmethod
    def open(
        cls,
        workspace: Path,
        *,
        resume: str | Path | None = None,
        profile: str | None = None,
        profile_id: str | None = None,
        model: str | None = None,
        custom_protocol: str | None = None,
        custom_base_url: str | None = None,
        custom_api_key_env: str | None = None,
        max_output_tokens: int | None = None,
        environment: Mapping[str, str] | None = None,
        user_profile_path: Path | None = None,
        project_profile_path: Path | None = None,
        provider_factory: Callable[..., ConversationProvider] | None = None,
        read_file_factory: Callable[[Path], ReadFileTool] = ReadFileTool,
        glob_factory: Callable[[Path], GlobTool] = GlobTool,
        grep_factory: Callable[[Path], GrepTool] = GrepTool,
        list_directory_factory: Callable[[Path], ListDirectoryTool] = ListDirectoryTool,
        write_file_factory: Callable[[Path], WriteFileTool] = WriteFileTool,
        edit_file_factory: Callable[[Path], EditFileTool] = EditFileTool,
        run_command_factory: Callable[[Path, Mapping[str, str]], RunCommandTool] = RunCommandTool,
        mkdir_factory: Callable[[Path], MkdirTool] = MkdirTool,
        move_file_factory: Callable[[Path], MoveFileTool] = MoveFileTool,
        delete_file_factory: Callable[[Path], DeleteFileTool] = DeleteFileTool,
        delete_directory_factory: Callable[[Path], DeleteDirectoryTool] = DeleteDirectoryTool,
        copy_file_factory: Callable[[Path], CopyFileTool] = CopyFileTool,
        read_file_lines_factory: Callable[[Path], ReadFileLinesTool] = ReadFileLinesTool,
        stat_path_factory: Callable[[Path], StatPathTool] = StatPathTool,
        list_tree_factory: Callable[[Path], ListTreeTool] = ListTreeTool,
        grep_regex_factory: Callable[[Path], GrepRegexTool] = GrepRegexTool,
        patch_file_factory: Callable[[Path], PatchFileTool] = PatchFileTool,
        git_status_factory: Callable[[Path], GitStatusTool] = GitStatusTool,
        git_diff_factory: Callable[[Path], GitDiffTool] = GitDiffTool,
        git_log_factory: Callable[[Path], GitLogTool] = GitLogTool,
        git_show_factory: Callable[[Path], GitShowTool] = GitShowTool,
        permission_mode: PermissionMode = PermissionMode.READ_ONLY,
        approval_mode: ApprovalMode = ApprovalMode.ASK,
        approval_handler: ApprovalHandler | None = None,
        action_uuid_factory: Callable[[], UUID | str] = uuid4,
        session_store_factory: Callable[[Path], SessionStore] = SessionStore,
    ) -> ProjectSession:
        """Create or resume durable history while selecting runtime independently."""
        resolved_workspace = Path(workspace).resolve()
        resolved_environment = environment if environment is not None else os.environ
        store = ProviderProfileStore.for_workspace(
            resolved_workspace,
            environment=resolved_environment,
            user_path=user_profile_path,
            project_path=project_profile_path,
        )
        if profile is not None and profile_id is not None:
            raise ValueError("profile and profile_id cannot be combined")
        selected_profile = profile
        if profile_id is not None:
            selected_profile = store.get_profile_by_id(profile_id).name
        manager_arguments: dict[str, object] = {
            "environment": resolved_environment,
            "profile": selected_profile,
            "model": model,
            "custom_protocol": custom_protocol,
            "custom_base_url": custom_base_url,
            "custom_api_key_env": custom_api_key_env,
            "max_output_tokens": max_output_tokens,
        }
        if provider_factory is not None:
            manager_arguments["provider_factory"] = provider_factory
        manager = RuntimeProviderManager(store, **manager_arguments)  # type: ignore[arg-type]
        writer: SessionWriter | None = None
        try:
            read_file = read_file_factory(resolved_workspace)
            glob = glob_factory(resolved_workspace)
            grep = grep_factory(resolved_workspace)
            list_directory = list_directory_factory(resolved_workspace)
            write_file = write_file_factory(resolved_workspace)
            edit_file = edit_file_factory(resolved_workspace)
            run_command = run_command_factory(resolved_workspace, resolved_environment)
            mkdir = mkdir_factory(resolved_workspace)
            move_file = move_file_factory(resolved_workspace)
            delete_file = delete_file_factory(resolved_workspace)
            delete_directory = delete_directory_factory(resolved_workspace)
            copy_file = copy_file_factory(resolved_workspace)
            read_file_lines = read_file_lines_factory(resolved_workspace)
            stat_path = stat_path_factory(resolved_workspace)
            list_tree = list_tree_factory(resolved_workspace)
            grep_regex = grep_regex_factory(resolved_workspace)
            patch_file = patch_file_factory(resolved_workspace)
            git_status = git_status_factory(resolved_workspace)
            git_diff = git_diff_factory(resolved_workspace)
            git_log = git_log_factory(resolved_workspace)
            git_show = git_show_factory(resolved_workspace)
            session_store = session_store_factory(resolved_workspace)
            binding = binding_from_status(manager.status())
            if resume is None:
                writer = session_store.create(binding)
                return cls(
                    resolved_workspace,
                    store,
                    manager,
                    session_store,
                    writer,
                    read_file,
                    glob,
                    grep,
                    list_directory,
                    write_file,
                    edit_file,
                    run_command,
                    mkdir,
                    move_file,
                    delete_file,
                    delete_directory,
                    copy_file,
                    read_file_lines=read_file_lines,
                    stat_path=stat_path,
                    list_tree=list_tree,
                    grep_regex=grep_regex,
                    patch_file=patch_file,
                    git_status=git_status,
                    git_diff=git_diff,
                    git_log=git_log,
                    git_show=git_show,
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                    approval_handler=approval_handler,
                    action_uuid_factory=action_uuid_factory,
                )
            prepared = session_store.prepare_resume(resume)
            writer_holder: dict[str, SessionWriter] = {}
            session_holder: dict[str, ProjectSession] = {}
            try:
                loop = cls._loop_from_state(
                    prepared.state,
                    read_file,
                    glob,
                    grep,
                    list_directory,
                    read_file_lines,
                    stat_path,
                    list_tree,
                    grep_regex,
                    git_status,
                    git_diff,
                    git_log,
                    git_show,
                    commit_turn=lambda turn: session_holder["session"]._commit_turn(
                        writer_holder["writer"], turn
                    ),
                )
                snapshot = loop.effective_context_snapshot()
                with manager.provider_for_context_transition() as runtime:
                    assessment = runtime.assess_context(snapshot.to_conversation_request())
                    report = assessment.fit_report
                    if report is not None and rejects_context_transition(report.decision):
                        raise SessionResumeContextError(prepared.info, snapshot.context_id, report)
                    committed = prepared.commit()
                writer = committed.writer
                writer_holder["writer"] = writer
                result = _resume_result(
                    writer.info,
                    snapshot.context_id,
                    assessment,
                    committed.recovery_applied,
                    committed.latest_status,
                    committed.latest_diagnostic,
                )
                session = cls(
                    resolved_workspace,
                    store,
                    manager,
                    session_store,
                    writer,
                    read_file,
                    glob,
                    grep,
                    list_directory,
                    write_file,
                    edit_file,
                    run_command,
                    mkdir,
                    move_file,
                    delete_file,
                    delete_directory,
                    copy_file,
                    read_file_lines=read_file_lines,
                    stat_path=stat_path,
                    list_tree=list_tree,
                    grep_regex=grep_regex,
                    patch_file=patch_file,
                    git_status=git_status,
                    git_diff=git_diff,
                    git_log=git_log,
                    git_show=git_show,
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                    approval_handler=approval_handler,
                    action_uuid_factory=action_uuid_factory,
                    loop=loop,
                    startup_resume_result=result,
                )
                session_holder["session"] = session
                return session
            except BaseException:
                prepared.abort()
                raise
        except BaseException:
            if writer is not None:
                writer.release()
            manager.close()
            raise

    @property
    def startup_resume_result(self) -> SessionResumeResult | None:
        return self._startup_resume_result

    @property
    def session_id(self) -> str:
        with self._lock:
            return self._writer.session_id

    @property
    def transcript_path(self) -> Path:
        with self._lock:
            return self._writer.path

    @property
    def history(self) -> tuple[ConversationItem, ...]:
        with self._lock:
            return self._loop.history

    @property
    def effective_history(self) -> tuple[ConversationItem, ...]:
        with self._lock:
            return self._loop.effective_history

    @property
    def turns(self) -> tuple[ConversationTurn, ...]:
        with self._lock:
            return self._loop.turns

    def session_info(self) -> SessionInfo:
        self._ensure_open()
        return self._writer.info

    def action_audits(self) -> tuple[ActionAuditState, ...]:
        """Return the current Session's replayed Host-only action lifecycles."""
        with self._lock:
            self._ensure_open()
            return self._writer.state.action_audits

    def git_status(self) -> GitStatusSnapshot:
        """Observe current repository status without model invocation or Session mutation."""
        with self._lock:
            self._ensure_open()
            return self._git_status.observe()

    def git_diff(self, scope: GitDiffScope | str) -> GitDiffSnapshot:
        """Observe one root-scoped repository patch without changing durable state."""
        with self._lock:
            self._ensure_open()
            return self._git_diff.observe(scope, ".")

    def git_log(self, limit: int = DEFAULT_GIT_LOG_LIMIT, path: str = ".") -> GitLogSnapshot:
        """Observe current-HEAD history without model invocation or Session mutation."""
        with self._lock:
            self._ensure_open()
            return self._git_log.observe(limit, path)

    def git_show(self, commit_id: str, path: str = ".") -> GitShowSnapshot:
        """Observe one reachable commit without model invocation or Session mutation."""
        with self._lock:
            self._ensure_open()
            return self._git_show.observe(commit_id, path)

    def tool_ledgers(self, limit: int) -> ToolLedgerQueryResult:
        """Return bounded recent tool ledgers from the current replayed Session."""
        with self._lock:
            self._ensure_open()
            return query_tool_ledgers(self._writer.state, limit)

    def list_sessions(self) -> tuple[SessionInfo, ...]:
        self._ensure_open()
        return self._session_store.list()

    def latest_session_info(self) -> SessionInfo:
        """Return the Session referenced by this workspace's latest pointer."""
        self._ensure_open()
        return self._session_store.show("latest")

    def new_session(self) -> SessionInfo:
        """Create and atomically select an empty Session without changing runtime."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            candidate = self._session_store.create(binding_from_status(self._manager.status()))
            loop = self._new_loop(candidate)
            old = self._writer
            self._writer = candidate
            self._loop = loop
            old.release()
            return candidate.info

    def switch_session(self, selector: str | Path) -> SessionResumeResult:
        """Screen and atomically swap durable history without changing runtime."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            if _selector_matches_current(selector, self._writer, self._session_store):
                snapshot = self._loop.effective_context_snapshot()
                return SessionResumeResult(
                    self._writer.info,
                    ResumeEffect.ALREADY_CURRENT,
                    None,
                    snapshot.context_id,
                    False,
                    LatestUpdateStatus.UPDATED,
                )
            old = self._writer
            old_loop = self._loop
            old_sequence = old.state.next_sequence
            old_context_id = old_loop.effective_context_snapshot().context_id
            prepared = self._session_store.prepare_resume(selector)
            writer_holder: dict[str, SessionWriter] = {}
            try:
                loop = self._loop_from_state(
                    prepared.state,
                    self._read_file,
                    self._glob,
                    self._grep,
                    self._list_directory,
                    self._read_file_lines,
                    self._stat_path,
                    self._list_tree,
                    self._grep_regex,
                    self._git_status,
                    self._git_diff,
                    self._git_log,
                    self._git_show,
                    commit_turn=lambda turn: self._commit_turn(writer_holder["writer"], turn),
                )
                loop.install_action_dispatcher(self._dispatch_action)
                snapshot = loop.effective_context_snapshot()
                with self._manager.provider_for_context_transition() as runtime:
                    assessment = runtime.assess_context(snapshot.to_conversation_request())
                    report = assessment.fit_report
                    if report is not None and rejects_context_transition(report.decision):
                        raise SessionResumeContextError(prepared.info, snapshot.context_id, report)
                    if (
                        self._writer is not old
                        or self._loop is not old_loop
                        or old.state.next_sequence != old_sequence
                        or old_loop.effective_context_snapshot().context_id != old_context_id
                        or self._manager.status().generation != runtime.status.generation
                    ):
                        raise SessionResumeConflictError(
                            "current Session or runtime changed during resume screening"
                        )
                    committed = prepared.commit()
                writer_holder["writer"] = committed.writer
                self._writer = committed.writer
                self._loop = loop
                old.release()
                return _resume_result(
                    committed.writer.info,
                    snapshot.context_id,
                    assessment,
                    committed.recovery_applied,
                    committed.latest_status,
                    committed.latest_diagnostic,
                )
            except SessionResumeStaleError as error:
                raise SessionResumeConflictError(str(error)) from None
            finally:
                prepared.abort()

    def prompt(
        self,
        text: str,
        *,
        event_sink: PromptEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> str:
        """Run one serialized preflighted turn with one exact prepared-action lease."""
        if type(include_tool_details) is not bool:
            raise ValueError("tool detail event option is invalid")
        if cancellation is not None and type(cancellation) is not TurnCancellation:
            raise ValueError("turn cancellation token is invalid")
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            prepared = self._loop.prepare_turn(text)
            loop = self._loop
            binding: BindingSnapshot | None = None
            usage_cursor = self._manager.begin_turn_usage()
            self._active_usage_cursor = usage_cursor
            self._active_cancellation = cancellation
            try:
                if cancellation is not None:
                    cancellation.check()
                with self._manager.provider_for_turn() as runtime:
                    binding = binding_from_status(runtime.status)
                    assessment = runtime.assess_context(prepared.initial_request)
                    if cancellation is not None:
                        cancellation.check()
                    report = assessment.fit_report
                    if (
                        report is not None
                        and report.decision == ContextFitDecision.MODEL_OUTPUT_EXCEEDED
                    ):
                        raise_for_context_fit(report)
                    decision = decide_auto_compaction(report)
                    if decision.trigger is not None:
                        prepared = self._auto_compact_turn(
                            prepared,
                            loop=loop,
                            runtime=runtime,
                            trigger=decision.trigger,
                            mandatory=decision.mandatory,
                            source_report=report,
                            event_sink=event_sink,
                            cancellation=cancellation,
                        )
                    lease = ActionLease(
                        session_id=self._writer.session_id,
                        lease_id=_uuid4_text(self._action_uuid_factory(), "action lease ID"),
                        runtime_generation=runtime.status.generation,
                        context_id=prepared.context.context_id,
                    )
                    prepared = prepared.with_action_lease(lease)
                    self._active_action_lease = lease
                    self._active_action_binding = binding
                    response = loop.run_prepared(
                        prepared,
                        provider=runtime,
                        event_sink=event_sink,
                        include_tool_details=include_tool_details,
                        cancellation=cancellation,
                    )
                usage = self._manager.finish_turn_usage(usage_cursor)
                if usage.latest_invocation is not None:
                    self._emit_prompt_event(event_sink, TurnUsageCompleted(usage))
                return response
            except BaseException as error:
                self._manager.finish_turn_usage(usage_cursor)
                self._record_failure(
                    binding or binding_from_status(self._manager.status()),
                    error,
                    provider_usage=self._manager.usage_since(
                        usage_cursor,
                        kind=ProviderInvocationKind.TURN,
                    ),
                )
                raise
            finally:
                self._active_action_lease = None
                self._active_action_binding = None
                self._active_usage_cursor = None
                self._active_cancellation = None

    def list_profiles(self) -> tuple[NamedProviderProfile, ...]:
        self._ensure_open()
        return self._store.list_profiles()

    def use_profile(self, name: str, *, scope: str = "project") -> RuntimeSwitchResult:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            result = self._manager.use_profile(
                name,
                scope=scope,
                committed_context=self._loop.effective_context_snapshot(),
            )
            self._record_runtime_switch(result, "provider_profile")
            return result

    def use_profile_id(self, profile_id: str, *, scope: str = "project") -> RuntimeSwitchResult:
        profile = self._store.get_profile_by_id(profile_id)
        return self.use_profile(profile.name, scope=scope)

    def clear_active(self, *, scope: str = "project") -> RuntimeSwitchResult:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            result = self._manager.clear_active(
                scope=scope,
                committed_context=self._loop.effective_context_snapshot(),
            )
            self._record_runtime_switch(result, "provider_clear")
            return result

    def set_model(self, model: str) -> RuntimeSwitchResult:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            result = self._manager.set_model(
                model,
                committed_context=self._loop.effective_context_snapshot(),
            )
            self._record_runtime_switch(result, "model_override")
            return result

    def set_output_budget(self, max_output_tokens: int | None) -> OutputBudgetUpdateResult:
        """Set or reset a process-local budget without persisting a runtime selection."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._manager.set_output_budget(
                max_output_tokens,
                committed_context=self._loop.effective_context_snapshot(),
            )

    def compact_context(self) -> CompactContextResult:
        """Run the shared controlled-compaction transaction manually."""
        prepared = self._prepare_compaction(CompactionTrigger.MANUAL)
        try:
            with self._manager.provider_for_compaction() as runtime:
                usage_cursor = runtime.usage_tracker.turn_cursor()
                try:
                    return self._execute_compaction(
                        prepared,
                        runtime,
                        pending_items=(),
                        usage_cursor=usage_cursor,
                    )
                except BaseException as error:
                    self._record_compaction_failure(
                        prepared,
                        runtime,
                        error,
                        usage_cursor=usage_cursor,
                    )
                    raise
        finally:
            self._finish_compaction(prepared)

    def preview_compaction(self) -> CompactContextPreview:
        """Inspect fixed compaction selection without generation or durable mutation."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            snapshot = self._loop.effective_context_snapshot()
            assessment = self._manager.assess_current_context(snapshot.to_conversation_request())
            try:
                plan = plan_compaction(
                    source_summary=self._loop.effective_summary,
                    effective_turns=snapshot.effective_turns,
                )
            except CompactionNotEligibleError as error:
                return CompactContextPreview(
                    source_context_id=snapshot.context_id,
                    full_turn_count=snapshot.full_turn_count,
                    effective_turn_count=snapshot.effective_turn_count,
                    summary_present=snapshot.effective_summary is not None,
                    eligible=False,
                    reason=str(error),
                    summarized_turn_count=0,
                    retained_turn_count=min(
                        snapshot.effective_turn_count,
                        COMPACT_RETAINED_TURNS,
                    ),
                    target_assessment=assessment,
                )
            return CompactContextPreview(
                source_context_id=snapshot.context_id,
                full_turn_count=snapshot.full_turn_count,
                effective_turn_count=snapshot.effective_turn_count,
                summary_present=snapshot.effective_summary is not None,
                eligible=True,
                reason=None,
                summarized_turn_count=plan.summarized_turn_count,
                retained_turn_count=plan.retained_turn_count,
                target_assessment=assessment,
            )

    def compaction_history(self, limit: int) -> CompactionHistoryResult:
        """Return bounded redacted checkpoint history from current replayed state."""
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("compaction history limit must be between 1 and 20")
        with self._lock:
            self._ensure_open()
            records = tuple(
                record
                for record in self._writer.state.records
                if isinstance(record, ContextCompacted)
            )
            selected = records[-limit:]
            return CompactionHistoryResult(
                total_checkpoints=len(records),
                checkpoints=tuple(
                    CompactionHistoryEntry(
                        sequence=record.sequence,
                        occurred_at=record.occurred_at,
                        schema_version=record.schema_version,
                        trigger=record.trigger,
                        high_water_percent=record.high_water_percent,
                        full_turn_count=record.source_full_turn_count,
                        summarized_turn_count=(
                            record.source_effective_turn_count
                            - (record.source_full_turn_count - record.retained_from_full_turn)
                        ),
                        retained_turn_count=(
                            record.source_full_turn_count - record.retained_from_full_turn
                        ),
                        previous_checkpoint_sequence=record.previous_checkpoint_sequence,
                    )
                    for record in selected
                ),
            )

    def _prepare_compaction(self, trigger: CompactionTrigger) -> _PreparedCompaction:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            writer = self._writer
            loop = self._loop
            source = loop.effective_context_snapshot()
            compact_plan = plan_compaction(
                source_summary=loop.effective_summary,
                effective_turns=source.effective_turns,
            )
            prepared = _PreparedCompaction(
                writer=writer,
                loop=loop,
                source=source,
                plan=compact_plan,
                captured_sequence=writer.state.next_sequence,
                captured_checkpoint=writer.state.latest_checkpoint,
                captured_full=loop.history,
                captured_effective=loop.effective_history,
                captured_summary=loop.effective_summary,
                captured_source=loop.effective_source,
                retained_from_full_turn=(len(source.full_turns) - compact_plan.retained_turn_count),
                trigger=trigger,
            )
            self._active_compaction = prepared
            return prepared

    def _execute_compaction(
        self,
        prepared: _PreparedCompaction,
        runtime: CompactionRuntimeSnapshot | TurnRuntimeSnapshot,
        *,
        pending_items: tuple[ConversationItem, ...],
        usage_cursor: int,
        cancellation: TurnCancellation | None = None,
    ) -> CompactContextResult:
        source = prepared.source
        status = runtime.status
        output_limit = min(
            COMPACT_MAX_OUTPUT_TOKENS,
            status.max_output_tokens or COMPACT_MAX_OUTPUT_TOKENS,
            status.model_max_output_tokens or COMPACT_MAX_OUTPUT_TOKENS,
        )
        source_report = runtime.assess_context(
            source.to_conversation_request(pending_items=pending_items)
        )
        if isinstance(source_report, CurrentTargetContextAssessment):
            if source_report.fit_report is None:
                raise CompactionCandidateError(
                    "source context input count is unavailable; compaction was not committed"
                )
            source_report = source_report.fit_report
        if source_report.decision == ContextFitDecision.MODEL_OUTPUT_EXCEEDED:
            raise CompactionCandidateError("source context output reserve exceeds the model limit")
        before = source_report.input_count.input_tokens
        if source_report.decision == ContextFitDecision.UNKNOWN or before is None:
            raise CompactionCandidateError(
                "source context input count is unknown; compaction was not committed"
            )
        summary_request = CompactSummaryRequest(
            prompt=build_compact_prompt(),
            source_text=build_compact_source_text(
                previous_summary=prepared.plan.source_summary,
                summarized_history=prepared.plan.summarized_history,
            ),
            max_output_tokens=output_limit,
        )
        if cancellation is not None:
            cancellation.check()
        summary_response = runtime.summarize(summary_request)
        if cancellation is not None:
            cancellation.check()
        summary = EffectiveContextSummary(summary_response.text.strip())
        candidate = EffectiveContextSnapshot(
            representation_version=COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
            source=EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
            system_prompt=source.system_prompt,
            tool_definitions=source.tool_definitions,
            full_history=source.full_history,
            effective_history=prepared.plan.retained_history,
            effective_summary=summary,
        )
        candidate_report = runtime.assess_context(
            candidate.to_conversation_request(pending_items=pending_items)
        )
        if isinstance(candidate_report, CurrentTargetContextAssessment):
            if candidate_report.fit_report is None:
                raise CompactionCandidateError(
                    "candidate context compatibility is unavailable; compaction was not committed"
                )
            candidate_report = candidate_report.fit_report
        after = candidate_report.input_count.input_tokens
        if candidate_report.decision != ContextFitDecision.FITS or after is None:
            raise CompactionCandidateError(
                "candidate context compatibility is not a known fit; compaction was not committed"
            )
        if source_report.input_count.method != candidate_report.input_count.method:
            raise CompactionCandidateError("source and candidate input counts are not comparable")
        if after >= before:
            raise CompactionCandidateError(
                "candidate context did not reduce provider input tokens",
                before_input_tokens=before,
                after_input_tokens=after,
                input_method=candidate_report.input_count.method.value,
            )

        if cancellation is not None:
            cancellation.check()

        with self._lock:
            self._ensure_open()
            current = prepared.loop.effective_context_snapshot()
            if (
                self._writer is not prepared.writer
                or self._loop is not prepared.loop
                or prepared.writer.state.next_sequence != prepared.captured_sequence
                or prepared.loop.history != prepared.captured_full
                or prepared.loop.effective_history != prepared.captured_effective
                or prepared.loop.effective_summary != prepared.captured_summary
                or prepared.loop.effective_source != prepared.captured_source
                or current.context_id != source.context_id
                or self._active_compaction is not prepared
            ):
                raise CompactionConflictError("compaction source changed; rerun /compact")
            prompt = summary_request.prompt
            checkpoint = ContextCompacted(
                sequence=prepared.captured_sequence,
                occurred_at=prepared.writer.now(),
                binding=binding_from_status(status),
                source_context_id=source.context_id,
                result_context_id=candidate.context_id,
                source_full_turn_count=len(source.full_turns),
                source_effective_turn_count=len(source.effective_turns),
                retained_from_full_turn=prepared.retained_from_full_turn,
                previous_checkpoint_sequence=(
                    prepared.captured_checkpoint.sequence
                    if prepared.captured_checkpoint is not None
                    else None
                ),
                summary=summary.text,
                compact_prompt_version=prompt.version,
                compact_prompt_fingerprint=prompt.fingerprint,
                continuation_version=summary.continuation_version,
                continuation_fingerprint=summary.continuation_fingerprint,
                effective_context_representation_version=candidate.representation_version,
                provider_usage=runtime.usage_tracker.records_since(
                    usage_cursor,
                    kind=ProviderInvocationKind.COMPACTION,
                ),
                trigger=prepared.trigger,
                high_water_percent=(
                    AUTO_COMPACT_HIGH_WATER_PERCENT
                    if prepared.trigger == CompactionTrigger.HIGH_WATER
                    else None
                ),
            )
            prepared.writer.append_context_compacted(checkpoint)
            prepared.loop.install_compaction(
                summary=summary,
                retained_history=prepared.plan.retained_history,
            )
        return CompactContextResult(
            session_id=prepared.writer.session_id,
            checkpoint_sequence=checkpoint.sequence,
            source_context_id=source.context_id,
            result_context_id=candidate.context_id,
            summarized_turn_count=prepared.plan.summarized_turn_count,
            retained_turn_count=prepared.plan.retained_turn_count,
            full_turn_count=len(source.full_turns),
            before_input_tokens=before,
            after_input_tokens=after,
            input_method=candidate_report.input_count.method.value,
            fit_decision=candidate_report.decision,
            trigger=prepared.trigger,
        )

    def _record_compaction_failure(
        self,
        prepared: _PreparedCompaction,
        runtime: CompactionRuntimeSnapshot | TurnRuntimeSnapshot,
        error: BaseException,
        *,
        usage_cursor: int,
    ) -> None:
        with self._lock:
            if (
                self._writer is not prepared.writer
                or self._active_compaction is not prepared
                or prepared.writer.state.next_sequence != prepared.captured_sequence
            ):
                return
            prepared.writer.compaction_failed(
                binding=binding_from_status(runtime.status),
                trigger=prepared.trigger,
                failure_kind=type(error).__name__,
                message=_safe_failure_message(error),
                provider_usage=runtime.usage_tracker.records_since(
                    usage_cursor,
                    kind=ProviderInvocationKind.COMPACTION,
                ),
            )

    def _finish_compaction(self, prepared: _PreparedCompaction) -> None:
        with self._lock:
            if self._active_compaction is prepared:
                self._active_compaction = None

    def _auto_compact_turn(
        self,
        turn: PreparedAgentTurn,
        *,
        loop: AgentLoop,
        runtime: TurnRuntimeSnapshot,
        trigger: CompactionTrigger,
        mandatory: bool,
        source_report: ContextFitReport,
        event_sink: PromptEventSink | None,
        cancellation: TurnCancellation | None,
    ) -> PreparedAgentTurn:
        self._emit_prompt_event(
            event_sink,
            AutoCompactionStarted(
                trigger=trigger,
                source_context_id=turn.context.context_id,
                input_tokens=source_report.input_count.input_tokens or 0,
                input_method=source_report.input_count.method.value,
                requested_output_tokens=source_report.requested_output_tokens,
                context_window_tokens=source_report.context_window_limit or 0,
                high_water_percent=(
                    AUTO_COMPACT_HIGH_WATER_PERCENT
                    if trigger == CompactionTrigger.HIGH_WATER
                    else None
                ),
            ),
        )
        try:
            prepared = self._prepare_compaction(trigger)
        except CompactionNotEligibleError as error:
            self._emit_prompt_event(
                event_sink,
                AutoCompactionNotApplied(trigger, str(error), not mandatory),
            )
            if mandatory:
                raise_for_context_fit(source_report)
            return turn
        try:
            usage_cursor = runtime.usage_tracker.turn_cursor()
            try:
                result = self._execute_compaction(
                    prepared,
                    runtime,
                    pending_items=turn.pending_items,
                    usage_cursor=usage_cursor,
                    cancellation=cancellation,
                )
            except (
                CompactionCandidateError,
                ContextPreflightError,
                ProviderAdapterError,
            ) as error:
                self._record_compaction_failure(
                    prepared,
                    runtime,
                    error,
                    usage_cursor=usage_cursor,
                )
                self._emit_prompt_event(
                    event_sink,
                    AutoCompactionNotApplied(trigger, _safe_failure_message(error), not mandatory),
                )
                if mandatory:
                    raise_for_context_fit(source_report)
                return turn
            self._emit_prompt_event(
                event_sink,
                AutoCompactionCommitted(trigger, result),
            )
            if loop is not self._loop:
                raise CompactionConflictError("conversation session changed after compaction")
            return turn.rebase(loop.effective_context_snapshot())
        finally:
            self._finish_compaction(prepared)

    @staticmethod
    def _emit_prompt_event(
        sink: PromptEventSink | None,
        event: PromptEvent,
    ) -> None:
        if sink is None:
            return
        try:
            sink(event)
        except Exception:
            pass

    def inspect_context(self) -> EffectiveContextInspection:
        """Inspect current effective context without generation or durable mutation."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            snapshot = self._loop.effective_context_snapshot()
            assessment = self._manager.assess_current_context(snapshot.to_conversation_request())
            return EffectiveContextInspection(
                snapshot,
                assessment,
                checkpoint=self._writer.state.latest_checkpoint,
            )

    def status(self) -> RuntimeStatus:
        self._ensure_open()
        return self._manager.status()

    def usage(self) -> RuntimeUsageSnapshot:
        self._ensure_open()
        return self._manager.usage_snapshot()

    def session_usage(self) -> DurableUsageSnapshot:
        """Return durable usage totals across all replayed Session operations."""
        with self._lock:
            self._ensure_open()
            return _durable_usage_snapshot(self._writer.state.records)

    def turn_usage_history(self, limit: int = 10) -> DurableUsageSnapshot:
        """Return bounded recent committed and failed turn usage."""
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("turn usage history limit must be between 1 and 20")
        with self._lock:
            self._ensure_open()
            operations = tuple(
                operation
                for operation in _durable_usage_operations(self._writer.state.records)
                if operation.operation == "turn"
            )[-limit:]
            return _usage_snapshot_from_operations(operations)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._ensure_not_compacting()
            self._closed = True
            try:
                self._writer.close()
            finally:
                self._manager.close()

    def __enter__(self) -> ProjectSession:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _loop_from_state(
        state,
        read_file,
        glob,
        grep,
        list_directory,
        read_file_lines,
        stat_path,
        list_tree,
        grep_regex,
        git_status,
        git_diff,
        git_log,
        git_show,
        *,
        commit_turn,
    ) -> AgentLoop:
        return AgentLoop(
            None,
            read_file,
            glob,
            grep,
            list_directory,
            read_file_lines,
            stat_path,
            list_tree,
            grep_regex,
            git_status=git_status,
            git_diff=git_diff,
            git_log=git_log,
            git_show=git_show,
            initial_history=state.history,
            initial_effective_history=state.effective_history,
            initial_effective_summary=state.effective_summary,
            initial_effective_source=state.effective_source,
            commit_turn=commit_turn,
        )

    def _new_loop(self, writer: SessionWriter) -> AgentLoop:
        loop = self._loop_from_state(
            writer.state,
            self._read_file,
            self._glob,
            self._grep,
            self._list_directory,
            self._read_file_lines,
            self._stat_path,
            self._list_tree,
            self._grep_regex,
            self._git_status,
            self._git_diff,
            self._git_log,
            self._git_show,
            commit_turn=lambda turn: self._commit_turn(writer, turn),
        )
        loop.install_action_dispatcher(self._dispatch_action)
        return loop

    def _dispatch_action(self, request: ToolUse, lease: ActionLease) -> ToolDispatchResult:
        """Prepare and run one model tool request through the exact Host boundary."""
        self._assert_action_lease(lease)
        cancellation = self._active_cancellation
        if cancellation is not None:
            cancellation.check()
        binding = self._active_action_binding
        if binding is None:
            raise RuntimeError("action binding is unavailable")

        prepared_write: PreparedWriteFile | None = None
        prepared_edit: PreparedEditFile | None = None
        prepared_command: PreparedRunCommand | None = None
        prepared_mkdir: PreparedMkdir | None = None
        prepared_move: PreparedMoveFile | None = None
        prepared_delete: PreparedDeleteFile | None = None
        prepared_delete_directory: PreparedDeleteDirectory | None = None
        prepared_copy: PreparedCopyFile | None = None
        prepared_patch: PreparedPatchFile | None = None
        if request.name in {
            READ_FILE_TOOL_NAME,
            GLOB_TOOL_NAME,
            GREP_TOOL_NAME,
            LIST_DIRECTORY_TOOL_NAME,
            READ_FILE_LINES_TOOL_NAME,
            STAT_PATH_TOOL_NAME,
            LIST_TREE_TOOL_NAME,
            GREP_REGEX_TOOL_NAME,
            GIT_STATUS_TOOL_NAME,
            GIT_DIFF_TOOL_NAME,
            GIT_LOG_TOOL_NAME,
            GIT_SHOW_TOOL_NAME,
        }:
            action = PermissionAction.WORKSPACE_READ
            precondition = ActionPrecondition.none()
        elif request.name == WRITE_FILE_TOOL_NAME:
            try:
                prepared_write = self._write_file.prepare(request)
            except WriteFilePreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_write.action
            precondition = prepared_write.precondition
        elif request.name == EDIT_FILE_TOOL_NAME:
            try:
                prepared_edit = self._edit_file.prepare(request)
            except EditFilePreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_edit.action
            precondition = prepared_edit.precondition
        elif request.name == RUN_COMMAND_TOOL_NAME:
            try:
                prepared_command = self._run_command.prepare(request)
            except RunCommandPreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_command.action
            precondition = prepared_command.precondition
        elif request.name == MKDIR_TOOL_NAME:
            try:
                prepared_mkdir = self._mkdir.prepare(request)
            except MkdirPreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_mkdir.action
            precondition = prepared_mkdir.precondition
        elif request.name == MOVE_FILE_TOOL_NAME:
            try:
                prepared_move = self._move_file.prepare(request)
            except MoveFilePreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_move.action
            precondition = prepared_move.precondition
        elif request.name == DELETE_FILE_TOOL_NAME:
            try:
                prepared_delete = self._delete_file.prepare(request)
            except DeleteFilePreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_delete.action
            precondition = prepared_delete.precondition
        elif request.name == DELETE_DIRECTORY_TOOL_NAME:
            try:
                prepared_delete_directory = self._delete_directory.prepare(request)
            except DeleteDirectoryPreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_delete_directory.action
            precondition = prepared_delete_directory.precondition
        elif request.name == COPY_FILE_TOOL_NAME:
            try:
                prepared_copy = self._copy_file.prepare(request)
            except CopyFilePreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_copy.action
            precondition = prepared_copy.precondition
        elif request.name == PATCH_FILE_TOOL_NAME:
            try:
                prepared_patch = self._patch_file.prepare(request)
            except PatchFilePreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_patch.action
            precondition = prepared_patch.precondition
        else:
            action = PermissionAction.UNKNOWN
            precondition = ActionPrecondition.none()

        identity = ActionIdentity(
            request_id=_uuid4_text(self._action_uuid_factory(), "action request ID"),
            tool_use_id=request.tool_use_id,
            tool_name=request.name,
            arguments=request.arguments,
            action=action,
            workspace_fingerprint=self._session_store.workspace_fingerprint,
            lease=lease,
            precondition=precondition,
        )
        approval_preview: ApprovalPreview | None = None
        if prepared_write is not None:
            approval_preview = build_file_change_preview(
                action_digest=identity.digest,
                path=prepared_write.relative_path,
                before=prepared_write.original_content,
                after=prepared_write.content,
            )
        elif prepared_edit is not None:
            approval_preview = build_file_change_preview(
                action_digest=identity.digest,
                path=prepared_edit.relative_path,
                before=prepared_edit.original_content,
                after=prepared_edit.content,
            )
        elif prepared_patch is not None:
            approval_preview = build_file_change_preview(
                action_digest=identity.digest,
                path=prepared_patch.relative_path,
                before=prepared_patch.original_content,
                after=prepared_patch.content,
            )
        elif prepared_command is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.COMMAND,
            )
        elif prepared_mkdir is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.DIRECTORY_CREATE,
            )
        elif prepared_move is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.FILE_MOVE,
                byte_count=prepared_move.source_state.size,
            )
        elif prepared_delete is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.FILE_DELETE,
                byte_count=prepared_delete.target_state.size,
            )
        elif prepared_delete_directory is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.DIRECTORY_DELETE,
            )
        elif prepared_copy is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.FILE_COPY,
                byte_count=len(prepared_copy.content),
            )
        coordinator = ActionCoordinator(
            writer=self._writer,
            approval_handler=self._approval_handler,
            uuid_factory=self._action_uuid_factory,
        )
        command_observation: RunCommandExecutionObservation | None = None

        def revalidate(current: ActionIdentity) -> ActionIdentity:
            self._assert_action_lease(lease)
            if cancellation is not None:
                cancellation.check()
            if prepared_write is not None:
                refreshed = self._write_file.refresh_precondition(prepared_write)
                return replace(current, precondition=refreshed)
            if prepared_edit is not None:
                refreshed = self._edit_file.refresh_precondition(prepared_edit)
                return replace(current, precondition=refreshed)
            if prepared_command is not None:
                refreshed = self._run_command.revalidate(prepared_command)
                return replace(current, precondition=refreshed)
            if prepared_mkdir is not None:
                refreshed = self._mkdir.refresh_precondition(prepared_mkdir)
                return replace(current, precondition=refreshed)
            if prepared_move is not None:
                refreshed = self._move_file.refresh_precondition(prepared_move)
                return replace(current, precondition=refreshed)
            if prepared_delete is not None:
                refreshed = self._delete_file.refresh_precondition(prepared_delete)
                return replace(current, precondition=refreshed)
            if prepared_delete_directory is not None:
                refreshed = self._delete_directory.refresh_precondition(prepared_delete_directory)
                return replace(current, precondition=refreshed)
            if prepared_copy is not None:
                refreshed = self._copy_file.refresh_precondition(prepared_copy)
                return replace(current, precondition=refreshed)
            if prepared_patch is not None:
                refreshed = self._patch_file.refresh_precondition(prepared_patch)
                return replace(current, precondition=refreshed)
            return current

        def execute(current: ActionIdentity) -> ActionExecutionResult:
            nonlocal command_observation
            self._assert_action_lease(lease)
            if cancellation is not None:
                cancellation.check()
            if request.name == READ_FILE_TOOL_NAME:
                result = self._read_file.execute(request)
            elif request.name == GLOB_TOOL_NAME:
                result = self._glob.execute(request)
            elif request.name == GREP_TOOL_NAME:
                result = self._grep.execute(request)
            elif request.name == LIST_DIRECTORY_TOOL_NAME:
                result = self._list_directory.execute(request)
            elif request.name == READ_FILE_LINES_TOOL_NAME:
                result = self._read_file_lines.execute(request)
            elif request.name == STAT_PATH_TOOL_NAME:
                result = self._stat_path.execute(request)
            elif request.name == LIST_TREE_TOOL_NAME:
                result = self._list_tree.execute(request)
            elif request.name == GREP_REGEX_TOOL_NAME:
                result = self._grep_regex.execute(request)
            elif request.name == GIT_STATUS_TOOL_NAME:
                result = self._git_status.execute(request)
            elif request.name == GIT_DIFF_TOOL_NAME:
                result = self._git_diff.execute(request)
            elif request.name == GIT_LOG_TOOL_NAME:
                result = self._git_log.execute(request)
            elif request.name == GIT_SHOW_TOOL_NAME:
                result = self._git_show.execute(request)
            elif request.name == WRITE_FILE_TOOL_NAME and prepared_write is not None:
                write_result = self._write_file.execute_detailed(prepared_write)
                outcome = {
                    WriteFileOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    WriteFileOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    WriteFileOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[write_result.outcome]
                return ActionExecutionResult(
                    tool_result=write_result.tool_result,
                    outcome=outcome,
                    result_code=write_result.result_code,
                    audit_message=write_result.audit_message,
                )
            elif request.name == EDIT_FILE_TOOL_NAME and prepared_edit is not None:
                edit_result = self._edit_file.execute_detailed(prepared_edit)
                outcome = {
                    EditFileOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    EditFileOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    EditFileOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[edit_result.outcome]
                return ActionExecutionResult(
                    tool_result=edit_result.tool_result,
                    outcome=outcome,
                    result_code=edit_result.result_code,
                    audit_message=edit_result.audit_message,
                )
            elif request.name == RUN_COMMAND_TOOL_NAME and prepared_command is not None:
                command_result = self._run_command.execute_detailed(
                    prepared_command,
                    cancellation=cancellation,
                )
                command_observation = command_result.observation
                outcome = {
                    RunCommandOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    RunCommandOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    RunCommandOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[command_result.outcome]
                return ActionExecutionResult(
                    tool_result=command_result.tool_result,
                    outcome=outcome,
                    result_code=command_result.result_code,
                    audit_message=command_result.audit_message,
                )
            elif request.name == MKDIR_TOOL_NAME and prepared_mkdir is not None:
                mkdir_result = self._mkdir.execute_detailed(prepared_mkdir)
                outcome = {
                    MkdirOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    MkdirOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    MkdirOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[mkdir_result.outcome]
                return ActionExecutionResult(
                    tool_result=mkdir_result.tool_result,
                    outcome=outcome,
                    result_code=mkdir_result.result_code,
                    audit_message=mkdir_result.audit_message,
                )
            elif request.name == MOVE_FILE_TOOL_NAME and prepared_move is not None:
                move_result = self._move_file.execute_detailed(prepared_move)
                outcome = {
                    MoveFileOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    MoveFileOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    MoveFileOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[move_result.outcome]
                return ActionExecutionResult(
                    tool_result=move_result.tool_result,
                    outcome=outcome,
                    result_code=move_result.result_code,
                    audit_message=move_result.audit_message,
                )
            elif request.name == DELETE_FILE_TOOL_NAME and prepared_delete is not None:
                delete_result = self._delete_file.execute_detailed(prepared_delete)
                outcome = {
                    DeleteFileOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    DeleteFileOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    DeleteFileOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[delete_result.outcome]
                return ActionExecutionResult(
                    tool_result=delete_result.tool_result,
                    outcome=outcome,
                    result_code=delete_result.result_code,
                    audit_message=delete_result.audit_message,
                )
            elif (
                request.name == DELETE_DIRECTORY_TOOL_NAME and prepared_delete_directory is not None
            ):
                delete_result = self._delete_directory.execute_detailed(prepared_delete_directory)
                outcome = {
                    DeleteDirectoryOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    DeleteDirectoryOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    DeleteDirectoryOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[delete_result.outcome]
                return ActionExecutionResult(
                    tool_result=delete_result.tool_result,
                    outcome=outcome,
                    result_code=delete_result.result_code,
                    audit_message=delete_result.audit_message,
                )
            elif request.name == COPY_FILE_TOOL_NAME and prepared_copy is not None:
                copy_result = self._copy_file.execute_detailed(prepared_copy)
                outcome = {
                    CopyFileOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    CopyFileOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    CopyFileOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[copy_result.outcome]
                return ActionExecutionResult(
                    tool_result=copy_result.tool_result,
                    outcome=outcome,
                    result_code=copy_result.result_code,
                    audit_message=copy_result.audit_message,
                )
            elif request.name == PATCH_FILE_TOOL_NAME and prepared_patch is not None:
                patch_result = self._patch_file.execute_detailed(prepared_patch)
                outcome = {
                    PatchFileOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    PatchFileOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    PatchFileOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[patch_result.outcome]
                return ActionExecutionResult(
                    tool_result=patch_result.tool_result,
                    outcome=outcome,
                    result_code=patch_result.result_code,
                    audit_message=patch_result.audit_message,
                )
            else:
                result = ToolResult(
                    request.tool_use_id, f"unknown tool: {request.name}", is_error=True
                )
            outcome = (
                ActionExecutionOutcome.FAILED
                if result.is_error
                else ActionExecutionOutcome.SUCCEEDED
            )
            return ActionExecutionResult(
                tool_result=result,
                outcome=outcome,
                result_code="tool_error" if result.is_error else "ok",
                audit_message=f"{request.name} {'failed' if result.is_error else 'succeeded'}",
            )

        coordinated = coordinator.run(
            identity=identity,
            binding=binding,
            permission_mode=self._permission_mode,
            approval_mode=self._approval_mode,
            revalidate=revalidate,
            execute=execute,
            approval_preview=approval_preview,
        )
        if not coordinated.executed:
            if coordinated.permission_result.decision.value == "deny":
                status = ToolEventStatus.DENIED
                result_code = coordinated.permission_result.reason.value
            elif coordinated.approval_resolution == ApprovalResolution.REJECT:
                status = ToolEventStatus.REJECTED
                result_code = "approval_rejected"
            elif coordinated.approval_resolution == ApprovalResolution.CANCEL:
                status = ToolEventStatus.CANCELLED
                result_code = "approval_cancelled"
            else:
                raise RuntimeError("unexecuted action has no terminal resolution")
        else:
            status = {
                ActionExecutionOutcome.SUCCEEDED: ToolEventStatus.SUCCEEDED,
                ActionExecutionOutcome.FAILED: ToolEventStatus.FAILED,
                ActionExecutionOutcome.PARTIAL: ToolEventStatus.PARTIAL,
            }[coordinated.execution_outcome]
            result_code = coordinated.result_code
        result_details = (
            _command_result_details(command_observation)
            if coordinated.executed and command_observation is not None
            else None
        )
        return ToolDispatchResult(
            coordinated.tool_result,
            status,
            result_code,
            result_details,
        )

    def _assert_action_lease(self, lease: ActionLease) -> None:
        active = self._active_action_lease
        if active != lease:
            raise RuntimeError("prepared action lease is stale")
        if (
            self._writer.session_id != lease.session_id
            or self._manager.status().generation != lease.runtime_generation
            or self._loop.effective_context_snapshot().context_id != lease.context_id
        ):
            raise RuntimeError("prepared action lease no longer matches runtime context")

    def _commit_turn(self, writer: SessionWriter, turn: CommittedTurn) -> None:
        if writer is not self._writer:
            raise SessionStoreError("conversation session changed before turn commit")
        usage_cursor = self._active_usage_cursor
        if usage_cursor is None:
            raise SessionStoreError("provider usage cursor is unavailable before turn commit")
        writer.append_turn(
            turn.items,
            binding=binding_from_status(self._manager.status()),
            tool_ledger=turn.tool_ledger,
            provider_usage=self._manager.usage_since(
                usage_cursor,
                kind=ProviderInvocationKind.TURN,
            ),
        )

    def _record_runtime_switch(self, result: RuntimeSwitchResult, reason: str) -> None:
        try:
            self._writer.runtime_changed(binding_from_status(result.status), reason=reason)
        except Exception as error:
            raise RuntimeSwitchAuditError(result) from error

    def _record_failure(
        self,
        binding: BindingSnapshot,
        error: BaseException,
        *,
        provider_usage: tuple[ProviderInvocationUsage, ...] = (),
    ) -> None:
        try:
            self._writer.turn_failed(
                binding=binding,
                failure_kind=type(error).__name__,
                message=_safe_failure_message(error),
                provider_usage=provider_usage,
            )
        except SessionStoreError:
            pass

    def _ensure_not_compacting(self) -> None:
        if self._active_compaction is not None:
            raise CompactionConflictError("a controlled compaction transaction is active")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("project session is closed")


def _cancel_approval(_request) -> ApprovalResolution:
    return ApprovalResolution.CANCEL


def _durable_usage_snapshot(records: tuple[SessionRecord, ...]) -> DurableUsageSnapshot:
    return _usage_snapshot_from_operations(_durable_usage_operations(records))


def _durable_usage_operations(
    records: tuple[SessionRecord, ...],
) -> tuple[DurableUsageOperation, ...]:
    operations: list[DurableUsageOperation] = []
    for record in records:
        if isinstance(record, TurnCommitted):
            operation = "turn"
            outcome = "committed"
            occurred_at = record.committed_at
            invocations = (
                record.provider_usage
                if record.schema_version == TURN_COMMITTED_SCHEMA_VERSION
                else None
            )
        elif isinstance(record, TurnFailed):
            operation = "turn"
            outcome = "failed"
            occurred_at = record.occurred_at
            invocations = (
                record.provider_usage
                if record.schema_version == TURN_FAILED_SCHEMA_VERSION
                else None
            )
        elif isinstance(record, ContextCompacted):
            operation = "compaction"
            outcome = "committed"
            occurred_at = record.occurred_at
            invocations = (
                record.provider_usage
                if record.schema_version == CONTEXT_COMPACTED_SCHEMA_VERSION
                else None
            )
        elif isinstance(record, CompactionFailed):
            operation = "compaction"
            outcome = "failed"
            occurred_at = record.occurred_at
            invocations = record.provider_usage
        else:
            continue
        operations.append(
            DurableUsageOperation(
                record_sequence=record.sequence,
                occurred_at=occurred_at,
                operation=operation,
                outcome=outcome,
                provider_id=record.binding.provider_id,
                model=record.binding.wire_model or record.binding.selected_model,
                invocations=invocations,
            )
        )
    return tuple(operations)


def _usage_snapshot_from_operations(
    operations: tuple[DurableUsageOperation, ...],
) -> DurableUsageSnapshot:
    totals = ProviderUsageTotals()
    unavailable = 0
    for operation in operations:
        if operation.invocations is None:
            unavailable += 1
            continue
        for invocation in operation.invocations:
            totals = totals.add(invocation.usage)
    return DurableUsageSnapshot(operations, totals, unavailable)


def _uuid4_text(value: UUID | str, label: str) -> str:
    try:
        parsed = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{label} must be a canonical UUID4") from None
    if parsed.version != 4 or str(parsed) != str(value):
        raise ValueError(f"{label} must be a canonical UUID4")
    return str(parsed)


def _resume_result(
    info: SessionInfo,
    context_id: str,
    assessment: CurrentTargetContextAssessment,
    recovery_applied: bool,
    latest_status: LatestUpdateStatus,
    diagnostic: str | None,
) -> SessionResumeResult:
    effect = ResumeEffect.APPLIED
    if latest_status == LatestUpdateStatus.FAILED_UNCHANGED:
        effect = ResumeEffect.APPLIED_LATEST_FAILED
    elif latest_status == LatestUpdateStatus.REPLACED_DURABILITY_UNKNOWN:
        effect = ResumeEffect.APPLIED_LATEST_DURABILITY_UNKNOWN
    return SessionResumeResult(
        info,
        effect,
        assessment,
        context_id,
        recovery_applied,
        latest_status,
        diagnostic,
    )


def _selector_matches_current(
    selector: str | Path,
    writer: SessionWriter,
    session_store: SessionStore,
) -> bool:
    if isinstance(selector, Path):
        candidate = selector if selector.is_absolute() else Path.cwd() / selector
        return candidate.absolute() == writer.path.absolute()
    if selector == writer.session_id:
        return True
    if selector == "latest":
        try:
            return session_store.show("latest").session_id == writer.session_id
        except SessionStoreError:
            return False
    return False


def binding_from_status(status: RuntimeStatus) -> BindingSnapshot:
    """Build non-secret per-turn provenance without influencing future runtime selection."""
    if status.mode == "fake":
        return BindingSnapshot.fake(
            generation=status.generation,
            source=status.selection_source,
        )
    if status.route_fingerprint is None:
        raise SessionStoreError("real runtime status is missing its route fingerprint")
    return BindingSnapshot(
        profile_id=status.profile_id,
        profile_revision=status.profile_revision,
        profile_name=status.profile,
        profile_fingerprint=status.profile_fingerprint,
        provider_id=status.provider_id,
        protocol=status.protocol,
        selected_model=status.selected_model,
        wire_model=status.wire_model,
        base_url=status.base_url,
        base_url_source=status.base_url_source,
        source=status.selection_source,
        credential_env=status.credential_env,
        max_output_tokens=status.max_output_tokens,
        temperature=status.temperature,
        generation=status.generation,
        adapter_version=f"route-contract-v{status.adapter_contract_version}",
        route_fingerprint=status.route_fingerprint,
    )


def _command_result_details(
    observation: RunCommandExecutionObservation,
) -> ToolResultDetails:
    compact: list[str] = []
    if observation.status.value == "exited" and observation.exit_code is not None:
        compact.append(f"exit={observation.exit_code}")
    else:
        compact.append(f"status={observation.status.value}")
        if observation.signal is not None:
            compact.append(f"signal={observation.signal}")
        elif observation.exit_code is not None:
            compact.append(f"exit={observation.exit_code}")
    compact.append(
        "duration=unavailable"
        if observation.duration_ms is None
        else f"duration={observation.duration_ms}ms"
    )
    compact.extend(
        (
            f"stdout={_compact_stream_bytes(observation.stdout)}",
            f"stderr={_compact_stream_bytes(observation.stderr)}",
        )
    )
    if not observation.cleanup_complete:
        compact.append("cleanup=false")

    details = [f"status: {observation.status.value}"]
    if observation.exit_code is not None:
        details.append(f"exit_code: {observation.exit_code}")
    elif observation.signal is not None:
        details.append(f"signal: {observation.signal}")
    details.extend(
        (
            "duration_ms: unavailable"
            if observation.duration_ms is None
            else f"duration_ms: {observation.duration_ms}",
            _full_stream_details("stdout", observation.stdout),
            _full_stream_details("stderr", observation.stderr),
            f"cleanup_complete: {str(observation.cleanup_complete).lower()}",
        )
    )
    return ToolResultDetails(" ".join(compact), tuple(details))


def _compact_stream_bytes(stream: RunCommandStreamObservation) -> str:
    if stream.truncated:
        return f"{stream.bytes_captured}/{stream.bytes_total}B(truncated)"
    return f"{stream.bytes_captured}B"


def _full_stream_details(name: str, stream: RunCommandStreamObservation) -> str:
    return (
        f"{name}: captured={stream.bytes_captured} total={stream.bytes_total} "
        f"truncated={str(stream.truncated).lower()}"
    )


def _invalid_tool_request(request: ToolUse, error: Exception) -> ToolDispatchResult:
    return ToolDispatchResult(
        ToolResult(request.tool_use_id, str(error), is_error=True),
        ToolEventStatus.ERROR,
        "invalid_request",
    )


def _safe_failure_message(error: BaseException) -> str:
    if isinstance(error, ContextPreflightError):
        return str(error)[:4096]
    if isinstance(error, ProviderAdapterError):
        return error.failure.message[:4096]
    if isinstance(error, SessionStoreError):
        return str(error)[:4096]
    return type(error).__name__
