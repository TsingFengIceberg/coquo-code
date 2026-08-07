"""Project-facing durable conversation facade for one workspace runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from leonervis_code.agent.loop import AgentLoop, PreparedAgentTurn
from leonervis_code.agent.task_control import (
    TASK_LIFECYCLE_KIND_BY_TOOL,
    TASK_PROPOSAL_KIND_BY_TOOL,
    TaskControlDispatchResult,
    TaskControlProposal,
    TaskLifecycleKind,
    TaskLifecycleRequest,
    TaskProposal,
    TaskProposalKind,
    TaskProposalSink,
    recover_task_control_request,
)
from leonervis_code.core.task_admission import (
    TASK_PROPOSE_START_TOOL_NAME,
    TaskAdmissionOutcome,
    TaskAdmissionProposal,
    canonical_task_admission_id,
    task_admission_receipt,
)
from leonervis_code.core.skill_authoring import (
    SKILL_ACCEPT_CREATE_TOOL_NAME,
    SKILL_AUTHORING_CONTROL_TOOL_NAMES,
    SKILL_PROPOSE_CREATE_TOOL_NAME,
    SkillCreationProposal,
    SkillInstallRequest,
    skill_proposal_receipt,
)
from leonervis_code.agent.tool_events import (
    AgentPromptEvent,
    HookLifecycleObserved,
    McpNotificationActivityReceived,
    TaskAdmissionProposed,
    TaskLifecycleCommitted,
    SkillCandidateCommitted,
    SkillCandidateInstalled,
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
from leonervis_code.core.cancellation import TurnCancellation, TurnCancelled
from leonervis_code.core.session_title import (
    SESSION_TITLE_MAX_ATTEMPTS,
    SessionTitleCandidateError,
    build_session_title_request,
    fallback_session_title,
    numbered_session_title,
    parse_session_title_response,
)
from leonervis_code.core.contracts import (
    AssistantToolBatch,
    CommittedTurn,
    ConversationItem,
    ConversationProvider,
    ConversationTurn,
    ToolAttemptUsage,
    ToolResult,
    ToolUse,
)
from leonervis_code.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from leonervis_code.hooks import (
    HookEvaluation,
    HookSetSnapshot,
    HookStore,
    apply_handler_results,
    evaluate_after_action,
    evaluate_before_action_authorization,
    evaluate_lifecycle_event,
)
from leonervis_code.hook_runner import (
    HOOK_HANDLER_ACTION_NAME,
    HookHandlerEvent,
    HookHandlerExecution,
    HookHandlerPreparationError,
    HookRunner,
)
from leonervis_code.core.hook_contracts import (
    HookActionOutcome,
    HookAuditEntry,
    HookAuditLedger,
    HookEffect,
    HookEvent,
    HookHandlerResult,
    aggregate_hook_effect,
)
from leonervis_code.core.project_instructions import (
    ProjectInstructionsLoader,
    ProjectInstructionsSnapshot,
)
from leonervis_code.core.orchestration import ProviderFailureKind
from leonervis_code.core.effective_context import (
    COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
    EffectiveContextSnapshot,
)
from leonervis_code.core.extensions import ExtensionSourceKind, ToolExecutionKind, ToolSetSnapshot
from leonervis_code.skills import (
    ActiveSkill,
    SkillActivationInspection,
    SkillInventoryLoader,
    SkillInventorySnapshot,
    SkillSourceKind,
    active_skills_from_history,
)
from leonervis_code.skill_candidates import (
    SkillCandidateInfo,
    SkillCandidateStore,
)
from leonervis_code.mcp import (
    McpClient,
    McpCallPreparationError,
    McpCatalogService,
    McpLiveProcessStatus,
    McpProcessManager,
    McpQuarantineCatalog,
    McpRuntimeExecution,
    McpRuntimeOutcome,
    McpServerStatus,
    McpServerStore,
    McpToolPolicyStore,
    PreparedMcpCall,
    prepare_mcp_call,
)
from leonervis_code.mcp.client import McpProbeResult
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
from leonervis_code.providers.native_search import (
    NativeSearchContextSize,
    NativeSearchMode,
    NativeSearchRuntimeOptions,
    canonical_native_search_domain,
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
    MAX_RECORDS,
    canonical_session_name,
    SessionNameSource,
    SessionTitleFallbackReason,
    SessionRecord,
    TurnCommitted,
    TURN_COMMITTED_USAGE_SCHEMA_VERSION,
    TurnFailed,
    TURN_FAILED_SCHEMA_VERSION,
)
from leonervis_code.session_store import (
    LatestUpdateStatus,
    SessionInfo,
    SessionConversationExport,
    SessionDiagnosis,
    SessionPreview,
    SessionRepairResult,
    SessionSearchResult,
    SessionTurnRange,
    SessionNameConflictError,
    SessionResumeStaleError,
    SessionStore,
    TaskAdmissionInfo,
    ToolLedgerQueryResult,
    query_tool_ledgers,
    SessionStoreError,
    SessionWriter,
)
from leonervis_code.task_records import (
    AcceptanceCheckOutcome,
    AcceptanceCriterionKind,
    AcceptanceVerificationSource,
    ReflectionRecommendation,
    StageCommitted,
    StageFailureReason,
    StageKind,
    StageUsage,
    TaskBudget,
    TaskBlockerCategory,
    TaskCompletionPolicy,
    TaskStatus,
    TaskTerminalOutcome,
    canonical_plan_steps,
    canonical_stage_objective,
    canonical_task_id,
)
from leonervis_code.task_runtime import (
    TaskPlanExecutionResult,
    TaskBlockerProposal,
    ParsedTaskResponse,
    TaskReflectionProposal,
    TaskReflectionExecutionResult,
    TaskDriveResult,
    TaskDriverStopReason,
    TaskNextAction,
    TaskProtocolEventFilter,
    TaskRunResult,
    TaskRuntimeError,
    TaskStageExecutionResult,
    TaskRunStopped,
    build_task_stage_prompt,
    parse_task_response,
)
from leonervis_code.task_store import (
    TaskAdmissionAcceptancePreview,
    TaskAdmissionConfiguration,
    TaskAppendCommitError,
    TaskInfo,
    TaskStageInfo,
    TaskStore,
    TaskStoreError,
    TaskWriter,
)
from leonervis_code.task_verification import (
    AcceptanceCheckResult,
    TaskVerificationResult,
    build_task_review_request,
    parse_task_review_response,
    run_host_acceptance_checks,
)
from leonervis_code.tools.delete_directory import (
    DELETE_DIRECTORY_TOOL_NAME,
    DeleteDirectoryOutcome,
    DeleteDirectoryPreparationError,
    DeleteDirectoryTool,
    PreparedDeleteDirectory,
)
from leonervis_code.tools.archive_list import ARCHIVE_LIST_TOOL_NAME, ArchiveListTool
from leonervis_code.tools.checksum_file import CHECKSUM_FILE_TOOL_NAME, ChecksumFileTool
from leonervis_code.tools.compare_files import COMPARE_FILES_TOOL_NAME, CompareFilesTool
from leonervis_code.tools.catalog import ORDINARY_PROMPT_TOOL_NAMES, ORDINARY_TOOL_NAMES
from leonervis_code.tools.task_coordination import (
    TASK_ACCEPT_ADMISSION_TOOL_NAME,
    TASK_ACCEPT_PLAN_TOOL_NAME,
    TASK_CONFIRM_COMPLETION_TOOL_NAME,
    TASK_CONTROL_TOOL_NAMES,
    TASK_PROPOSE_COMPLETION_TOOL_NAME,
    TASK_PROPOSE_PLAN_TOOL_NAME,
    TASK_REPORT_BLOCKER_TOOL_NAME,
    TASK_REPORT_REFLECTION_TOOL_NAME,
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
from leonervis_code.tools.download_file import (
    DOWNLOAD_FILE_TOOL_NAME,
    DownloadFileOutcome,
    DownloadFilePreparationError,
    DownloadFileTool,
    PreparedDownloadFile,
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
from leonervis_code.tools.git_blame import GIT_BLAME_TOOL_NAME, GitBlameTool
from leonervis_code.tools.git_refs import GIT_REFS_TOOL_NAME, GitRefsTool
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
from leonervis_code.tools.json_query import JSON_QUERY_TOOL_NAME, JsonQueryTool
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
from leonervis_code.tools.move_directory import (
    MOVE_DIRECTORY_TOOL_NAME,
    MoveDirectoryOutcome,
    MoveDirectoryPreparationError,
    MoveDirectoryTool,
    PreparedMoveDirectory,
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
    CommandSandboxInspection,
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
from leonervis_code.tools.web_search import (
    WEB_SEARCH_TOOL_NAME,
    PreparedWebSearch,
    WebSearchOutcome,
    WebSearchPreparationError,
    WebSearchSourceConfiguration,
    WebSearchTool,
)
from leonervis_code.tools.web_fetch import (
    WEB_FETCH_TOOL_NAME,
    PreparedWebFetch,
    WebFetchOutcome,
    WebFetchPreparationError,
    WebFetchTool,
)
from leonervis_code.tools.catalog import (
    MAX_PROVIDER_INVOCATIONS_PER_TURN,
    MAX_TOOL_CALLS_PER_RESPONSE,
    MAX_TOOL_REQUESTS_PER_TURN,
    TOOL_CATALOG,
)

_COMMIT_CONTROL_TOOL_NAMES = TASK_CONTROL_TOOL_NAMES + SKILL_AUTHORING_CONTROL_TOOL_NAMES
MAX_HOOK_HANDLER_EXECUTIONS_PER_EVENT = 4
MAX_HOOK_HANDLER_EXECUTIONS_PER_TURN = 12
_TASK_PLANNING_READ_TOOL_NAMES = (
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
    WEB_SEARCH_TOOL_NAME,
    WEB_FETCH_TOOL_NAME,
    COMPARE_FILES_TOOL_NAME,
    GIT_BLAME_TOOL_NAME,
    GIT_REFS_TOOL_NAME,
    JSON_QUERY_TOOL_NAME,
    CHECKSUM_FILE_TOOL_NAME,
    ARCHIVE_LIST_TOOL_NAME,
)


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
    active_skills_before: tuple[ActiveSkill, ...] = ()
    active_skills_after: tuple[ActiveSkill, ...] = ()
    removed_skills: tuple[ActiveSkill, ...] = ()
    action_tools_after: tuple[str, ...] = ()

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
class ProjectStatus:
    """One process-local Host workbench snapshot without provider invocation."""

    runtime: RuntimeStatus
    session: SessionInfo
    usage: RuntimeUsageSnapshot
    permission_mode: PermissionMode
    approval_mode: ApprovalMode
    sandbox: CommandSandboxInspection
    tool_count: int = len(TOOL_CATALOG)
    calls_per_response: int = MAX_TOOL_CALLS_PER_RESPONSE
    requests_per_turn: int = MAX_TOOL_REQUESTS_PER_TURN
    provider_invocations_per_turn: int = MAX_PROVIDER_INVOCATIONS_PER_TURN


@dataclass(frozen=True)
class TurnCommitStarted:
    """Signal the exact start of durable turn append without exposing turn content."""


@dataclass(frozen=True)
class SessionTitleGenerationStarted:
    """Signal one content-free automatic Session-title attempt."""

    attempt: int
    limit: int

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or type(self.limit) is not int:
            raise ValueError("Session title attempt values must be integers")
        if not 1 <= self.attempt <= self.limit <= SESSION_TITLE_MAX_ATTEMPTS:
            raise ValueError("Session title attempt is outside its bound")


@dataclass(frozen=True)
class SessionTitleFallbackApplied:
    """Signal one durably committed bounded Host title fallback reason."""

    reason: SessionTitleFallbackReason

    def __post_init__(self) -> None:
        if type(self.reason) is not SessionTitleFallbackReason:
            raise ValueError("Session title fallback reason is invalid")


@dataclass(frozen=True)
class SessionTitlePrepared:
    """Expose one transient first-turn title without claiming durable commit."""

    name: str
    source: SessionNameSource

    def __post_init__(self) -> None:
        canonical_session_name(self.name)
        if self.source not in {SessionNameSource.MODEL, SessionNameSource.FALLBACK}:
            raise ValueError("prepared Session title source is invalid")


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
    | SessionTitleGenerationStarted
    | SessionTitlePrepared
    | SessionTitleFallbackApplied
    | TurnCommitStarted
    | TurnUsageCompleted
    | TaskRunStopped
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


@dataclass(frozen=True)
class _PreparedSessionTitle:
    name: str
    source: SessionNameSource
    fallback_reason: SessionTitleFallbackReason | None = None


@dataclass(frozen=True)
class _TaskControlScope:
    task_id: str
    stage_id: str
    stage_number: int
    allowed_tool_names: tuple[str, ...]


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
        web_search: WebSearchTool | None = None,
        web_fetch: WebFetchTool | None = None,
        compare_files: CompareFilesTool | None = None,
        git_blame: GitBlameTool | None = None,
        git_refs: GitRefsTool | None = None,
        json_query: JsonQueryTool | None = None,
        checksum_file: ChecksumFileTool | None = None,
        archive_list: ArchiveListTool | None = None,
        move_directory: MoveDirectoryTool | None = None,
        download_file: DownloadFileTool | None = None,
        permission_mode: PermissionMode = PermissionMode.READ_ONLY,
        approval_mode: ApprovalMode = ApprovalMode.ASK,
        approval_handler: ApprovalHandler | None = None,
        action_uuid_factory: Callable[[], UUID | str] = uuid4,
        loop: AgentLoop | None = None,
        project_instructions_loader: ProjectInstructionsLoader | None = None,
        skill_inventory_loader: SkillInventoryLoader | None = None,
        skill_candidate_store: SkillCandidateStore | None = None,
        mcp_store: McpServerStore | None = None,
        mcp_client: object | None = None,
        mcp_policy_store: McpToolPolicyStore | None = None,
        mcp_process_manager: McpProcessManager | None = None,
        hook_store: HookStore | None = None,
        startup_resume_result: SessionResumeResult | None = None,
    ) -> None:
        self.workspace = workspace
        self._store = store
        self._manager = manager
        self._session_store = session_store
        self._task_store = TaskStore(workspace)
        self._writer = writer
        self._read_file = read_file
        self._glob = glob
        self._grep = grep
        self._list_directory = list_directory
        self._write_file = write_file or WriteFileTool(workspace)
        self._edit_file = edit_file or EditFileTool(workspace)
        self._run_command = run_command or RunCommandTool(workspace)
        self._hook_runner = HookRunner(workspace, self._run_command)
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
        self._web_search = web_search or WebSearchTool()
        self._web_fetch = web_fetch or WebFetchTool()
        self._compare_files = compare_files or CompareFilesTool(workspace)
        self._git_blame = git_blame or GitBlameTool(workspace)
        self._git_refs = git_refs or GitRefsTool(workspace)
        self._json_query = json_query or JsonQueryTool(workspace)
        self._checksum_file = checksum_file or ChecksumFileTool(workspace)
        self._archive_list = archive_list or ArchiveListTool(workspace)
        self._move_directory = move_directory or MoveDirectoryTool(workspace)
        self._download_file = download_file or DownloadFileTool(workspace)
        self._mcp_store = mcp_store or McpServerStore.for_workspace(workspace)
        self._mcp_client = mcp_client or McpClient(workspace)
        self._mcp_policy_store = mcp_policy_store or McpToolPolicyStore.for_workspace(workspace)
        self._mcp_catalog_service = McpCatalogService(
            self._mcp_store,
            self._mcp_client,
            self._mcp_policy_store,
        )
        self._mcp_process_manager = mcp_process_manager or McpProcessManager(
            self._mcp_store, self._mcp_client
        )
        self._web_search.disable_sources()
        self._search_source_order = (
            ("provider",) if self._manager.status().native_search_available else ()
        )
        self._native_search_options = NativeSearchRuntimeOptions()
        self._project_instructions_loader = (
            project_instructions_loader or ProjectInstructionsLoader(workspace)
        )
        self._skill_inventory_loader = skill_inventory_loader or SkillInventoryLoader(workspace)
        self._skill_candidate_store = skill_candidate_store or SkillCandidateStore(workspace)
        self._hook_store = hook_store or HookStore.for_workspace(workspace)
        if type(permission_mode) is not PermissionMode:
            raise ValueError("permission mode is invalid")
        if type(approval_mode) is not ApprovalMode:
            raise ValueError("approval mode is invalid")
        self._permission_mode = permission_mode
        self._approval_mode = approval_mode
        self._approval_handler = approval_handler or _cancel_approval
        self._action_uuid_factory = action_uuid_factory
        self._active_action_lease: ActionLease | None = None
        self._active_turn_context: EffectiveContextSnapshot | None = None
        self._active_action_binding: BindingSnapshot | None = None
        self._active_tool_set_snapshot: ToolSetSnapshot | None = None
        self._active_hook_set_snapshot: HookSetSnapshot | None = None
        self._active_hook_audit_entries: list[HookAuditEntry] = []
        self._active_hook_handler_executions = 0
        self._hook_handler_depth = 0
        self._active_usage_cursor: int | None = None
        self._active_turn_runtime: TurnRuntimeSnapshot | None = None
        self._active_cancellation: TurnCancellation | None = None
        self._active_event_sink: PromptEventSink | None = None
        self._active_session_title_source_text: str | None = None
        self._active_prepared_session_title: _PreparedSessionTitle | None = None
        self._active_task_control_scope: _TaskControlScope | None = None
        self._lock = RLock()
        self._closed = False
        self._active_compaction: _PreparedCompaction | None = None
        self._loop = loop or self._new_loop(writer)
        if loop is not None:
            self._loop.install_action_dispatcher(self._dispatch_action)
            self._loop.install_task_control_dispatcher(
                _COMMIT_CONTROL_TOOL_NAMES, self._dispatch_task_control
            )
            self._loop.install_tool_set_transition_dispatcher(self._transition_tool_set)
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
        user_mcp_path: Path | None = None,
        project_mcp_path: Path | None = None,
        user_mcp_policy_path: Path | None = None,
        project_mcp_policy_path: Path | None = None,
        user_hooks_path: Path | None = None,
        project_hooks_path: Path | None = None,
        provider_factory: Callable[..., ConversationProvider] | None = None,
        fake_provider_factory: Callable[[], ConversationProvider] | None = None,
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
        web_search_factory: Callable[[Mapping[str, str]], WebSearchTool] = WebSearchTool,
        web_fetch_factory: Callable[[], WebFetchTool] = WebFetchTool,
        compare_files_factory: Callable[[Path], CompareFilesTool] = CompareFilesTool,
        git_blame_factory: Callable[[Path], GitBlameTool] = GitBlameTool,
        git_refs_factory: Callable[[Path], GitRefsTool] = GitRefsTool,
        json_query_factory: Callable[[Path], JsonQueryTool] = JsonQueryTool,
        checksum_file_factory: Callable[[Path], ChecksumFileTool] = ChecksumFileTool,
        archive_list_factory: Callable[[Path], ArchiveListTool] = ArchiveListTool,
        move_directory_factory: Callable[[Path], MoveDirectoryTool] = MoveDirectoryTool,
        download_file_factory: Callable[[Path], DownloadFileTool] = DownloadFileTool,
        mcp_client_factory: Callable[..., object] = McpClient,
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
        if fake_provider_factory is not None:
            manager_arguments["fake_factory"] = fake_provider_factory
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
            web_search = web_search_factory(resolved_environment)
            web_fetch = web_fetch_factory()
            compare_files = compare_files_factory(resolved_workspace)
            git_blame = git_blame_factory(resolved_workspace)
            git_refs = git_refs_factory(resolved_workspace)
            json_query = json_query_factory(resolved_workspace)
            checksum_file = checksum_file_factory(resolved_workspace)
            archive_list = archive_list_factory(resolved_workspace)
            move_directory = move_directory_factory(resolved_workspace)
            download_file = download_file_factory(resolved_workspace)
            project_instructions_loader = ProjectInstructionsLoader(resolved_workspace)
            skill_inventory_loader = SkillInventoryLoader(resolved_workspace, resolved_environment)
            skill_candidate_store = SkillCandidateStore(resolved_workspace, resolved_environment)
            mcp_store = McpServerStore.for_workspace(
                resolved_workspace,
                environment=resolved_environment,
                user_path=user_mcp_path,
                project_path=project_mcp_path,
            )
            mcp_client = mcp_client_factory(
                resolved_workspace,
                environment=resolved_environment,
            )
            mcp_policy_store = McpToolPolicyStore.for_workspace(
                resolved_workspace,
                environment=resolved_environment,
                user_path=user_mcp_policy_path,
                project_path=project_mcp_policy_path,
            )
            hook_store = HookStore.for_workspace(
                resolved_workspace,
                environment=resolved_environment,
                user_path=user_hooks_path,
                project_path=project_hooks_path,
            )
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
                    web_search=web_search,
                    web_fetch=web_fetch,
                    compare_files=compare_files,
                    git_blame=git_blame,
                    git_refs=git_refs,
                    json_query=json_query,
                    checksum_file=checksum_file,
                    archive_list=archive_list,
                    move_directory=move_directory,
                    download_file=download_file,
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                    approval_handler=approval_handler,
                    action_uuid_factory=action_uuid_factory,
                    project_instructions_loader=project_instructions_loader,
                    skill_inventory_loader=skill_inventory_loader,
                    skill_candidate_store=skill_candidate_store,
                    mcp_store=mcp_store,
                    mcp_client=mcp_client,
                    mcp_policy_store=mcp_policy_store,
                    hook_store=hook_store,
                )
            prepared = session_store.prepare_resume(resume)
            writer_holder: dict[str, SessionWriter] = {}
            session_holder: dict[str, ProjectSession] = {}
            try:
                resume_mcp_catalog = McpCatalogService(
                    mcp_store,
                    mcp_client,
                    mcp_policy_store,
                )
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
                    compare_files,
                    git_blame,
                    git_refs,
                    json_query,
                    checksum_file,
                    archive_list,
                    commit_turn=lambda turn: session_holder["session"]._commit_turn(
                        writer_holder["writer"], turn
                    ),
                    project_instructions_factory=project_instructions_loader.load,
                    tool_registry_factory=resume_mcp_catalog.registry_snapshot,
                    skill_inventory_factory=skill_inventory_loader.load,
                    hook_set_factory=hook_store.snapshot,
                    skill_resource_reader=skill_inventory_loader.read_resource,
                )
                snapshot = loop.effective_context_snapshot()
                with manager.provider_for_context_transition() as runtime:
                    assessment = runtime.assess_context(snapshot.to_conversation_request())
                    report = assessment.fit_report
                    if report is not None and rejects_context_transition(report.decision):
                        raise SessionResumeContextError(prepared.info, snapshot.context_id, report)
                    committed = prepared.commit(binding=binding_from_status(runtime.status))
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
                    web_search=web_search,
                    web_fetch=web_fetch,
                    compare_files=compare_files,
                    git_blame=git_blame,
                    git_refs=git_refs,
                    json_query=json_query,
                    checksum_file=checksum_file,
                    archive_list=archive_list,
                    move_directory=move_directory,
                    download_file=download_file,
                    permission_mode=permission_mode,
                    approval_mode=approval_mode,
                    approval_handler=approval_handler,
                    action_uuid_factory=action_uuid_factory,
                    loop=loop,
                    project_instructions_loader=project_instructions_loader,
                    skill_inventory_loader=skill_inventory_loader,
                    skill_candidate_store=skill_candidate_store,
                    mcp_store=mcp_store,
                    mcp_client=mcp_client,
                    mcp_policy_store=mcp_policy_store,
                    hook_store=hook_store,
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

    def inspect_mcp_servers(self) -> tuple[McpServerStatus, ...]:
        """Inspect configured MCP readiness without starting a process or changing state."""
        with self._lock:
            self._ensure_open()
            return tuple(
                self._mcp_client.inspect_status(entry) for entry in self._mcp_store.list_servers()
            )

    def inspect_mcp_server(self, name: str) -> McpServerStatus:
        """Inspect one configured MCP server without starting it."""
        with self._lock:
            self._ensure_open()
            return self._mcp_client.inspect_status(self._mcp_store.get_server(name))

    def probe_mcp_server(self, name: str) -> McpProbeResult:
        """Start one temporary confined server for initialize and tools/list only."""
        with self._lock:
            self._ensure_open()
            return self._mcp_client.probe(self._mcp_store.get_server(name))

    def inspect_mcp_catalog(self) -> McpQuarantineCatalog:
        """Refresh and return the redaction-safe MCP quarantine catalog."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            catalog = self._mcp_catalog_service.snapshot(refresh=True)
            if not self._mcp_process_manager.synchronize_catalog(catalog.catalog_id):
                raise RuntimeError("stale MCP process cleanup is incomplete")
            return catalog

    def inspect_mcp_runtime(self) -> tuple[McpLiveProcessStatus, ...]:
        """Return content-free lifecycle facts for current REPL-owned MCP processes."""
        with self._lock:
            self._ensure_open()
            return self._mcp_process_manager.statuses()

    def inspect_hooks(self) -> HookSetSnapshot:
        """Return the current strict Hook configuration without provider or Session effects."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._hook_store.snapshot()

    def hook_evaluations(self, limit: int = 20):
        """Inspect recent Hook evaluations for the current Session without mutation."""
        with self._lock:
            self._ensure_open()
            return self._session_store.hook_evaluations(self._writer.session_id, limit)

    def hook_handler_runs(self, limit: int = 20) -> tuple[ActionAuditState, ...]:
        """Inspect recent content-free Hook handler actions without mutation."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Hook handler run limit must be between 1 and 100")
        with self._lock:
            self._ensure_open()
            return tuple(
                audit
                for audit in self._writer.state.action_audits
                if audit.identity.tool_name == HOOK_HANDLER_ACTION_NAME
            )[-limit:]

    def task_hook_evaluations(self, task_id: str, limit: int = 20):
        """Inspect recent Hook evaluations for one Task without mutation."""
        with self._lock:
            self._ensure_open()
            return self._task_store.hook_evaluations(task_id, limit)

    def tool_ledgers(self, limit: int) -> ToolLedgerQueryResult:
        """Return bounded recent tool ledgers from the current replayed Session."""
        with self._lock:
            self._ensure_open()
            return query_tool_ledgers(self._writer.state, limit)

    def list_sessions(self) -> tuple[SessionInfo, ...]:
        self._ensure_open()
        return self._session_store.list()

    def create_task(
        self,
        objective: str,
        acceptance_criteria: tuple[str, ...] = (),
        *,
        structured_criteria: tuple[dict[str, object], ...] = (),
        completion_policy: TaskCompletionPolicy = TaskCompletionPolicy.MANUAL,
        name: str | None = None,
        budget: TaskBudget = TaskBudget(),
    ) -> TaskInfo:
        """Create a durable Task owned by the current Session without model invocation."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._task_store.create(
                objective,
                owner_session=self._writer.session_id,
                acceptance_criteria=acceptance_criteria,
                structured_criteria=structured_criteria,
                completion_policy=completion_policy,
                name=name,
                budget=budget,
            )

    def list_tasks(self) -> tuple[TaskInfo, ...]:
        """List workspace Tasks without changing Session or runtime state."""
        with self._lock:
            self._ensure_open()
            return self._task_store.list()

    def inspect_task(self, task_id: str) -> TaskInfo:
        """Strictly inspect one workspace Task without changing current state."""
        with self._lock:
            self._ensure_open()
            return self._task_store.inspect(task_id)

    def list_task_admissions(self) -> tuple[TaskAdmissionInfo, ...]:
        """List committed proposals for the current Session without mutation."""
        with self._lock:
            self._ensure_open()
            return self._session_store.task_admissions(self._writer.session_id)

    def inspect_task_admission(self, admission_id: str) -> TaskAdmissionInfo:
        """Inspect one current-Session admission proposal by exact deterministic ID."""
        with self._lock:
            self._ensure_open()
            return self._task_admission_info(admission_id)

    def preview_task_admission_acceptance(
        self,
        admission_id: str,
        configuration: TaskAdmissionConfiguration = TaskAdmissionConfiguration(),
    ) -> TaskAdmissionAcceptancePreview:
        """Prepare one exact no-write Task candidate for explicit user confirmation."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            admission = self._task_admission_info(admission_id)
            if admission.outcome is TaskAdmissionOutcome.REJECTED:
                raise SessionStoreError("Task admission proposal was already rejected")
            return self._task_store.prepare_admission_acceptance(
                admission.proposal,
                owner_session=self._writer.session_id,
                source_turn_record_sequence=admission.turn_record_sequence,
                configuration=configuration,
            )

    def accept_task_admission(
        self,
        admission_id: str,
        configuration: TaskAdmissionConfiguration = TaskAdmissionConfiguration(),
        *,
        confirmation_sha256: str,
    ) -> TaskInfo:
        """Idempotently create one sourced Task, then commit the accepted resolution."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            admission = self._task_admission_info(admission_id)
            if admission.outcome is TaskAdmissionOutcome.REJECTED:
                raise SessionStoreError("Task admission proposal was already rejected")
            existing = self._task_store.find_by_admission(admission.proposal.admission_id)
            if admission.outcome is TaskAdmissionOutcome.ACCEPTED:
                if existing is None or existing.task_id != admission.task_id:
                    raise TaskStoreError("accepted Task admission provenance is unavailable")
                self._task_store.validate_existing_admission_acceptance(
                    existing,
                    configuration,
                    confirmation_sha256,
                )
                return existing
            if existing is None:
                existing = self._task_store.create_from_admission(
                    admission.proposal,
                    owner_session=self._writer.session_id,
                    source_turn_record_sequence=admission.turn_record_sequence,
                    configuration=configuration,
                    confirmation_sha256=confirmation_sha256,
                )
            else:
                self._task_store.validate_existing_admission_acceptance(
                    existing,
                    configuration,
                    confirmation_sha256,
                )
            self._writer.resolve_task_admission(
                admission.proposal.admission_id,
                TaskAdmissionOutcome.ACCEPTED,
                task_id=existing.task_id,
            )
            return existing

    def accepted_task_for_admission(self, admission_id: str) -> TaskInfo:
        """Resolve one accepted current-Session proposal to its exact sourced Task."""
        with self._lock:
            self._ensure_open()
            admission = self._task_admission_info(admission_id)
            if admission.outcome is not TaskAdmissionOutcome.ACCEPTED or admission.task_id is None:
                raise SessionStoreError("Task admission proposal has not been accepted")
            task = self._task_store.find_by_admission(admission.proposal.admission_id)
            if task is None or task.task_id != admission.task_id:
                raise TaskStoreError("accepted Task admission provenance is unavailable")
            return task

    def reject_task_admission(
        self,
        admission_id: str,
        reason: str | None = None,
    ) -> TaskAdmissionInfo:
        """Durably reject one current-Session proposal without creating a Task."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            admission = self._task_admission_info(admission_id)
            if admission.outcome is TaskAdmissionOutcome.ACCEPTED:
                raise SessionStoreError("Task admission proposal was already accepted")
            self._writer.resolve_task_admission(
                admission.proposal.admission_id,
                TaskAdmissionOutcome.REJECTED,
                reason=reason,
            )
            return self._task_admission_info(admission.proposal.admission_id)

    def _task_admission_info(self, admission_id: str) -> TaskAdmissionInfo:
        try:
            canonical = canonical_task_admission_id(admission_id)
        except ValueError as error:
            raise SessionStoreError(str(error)) from None
        match = next(
            (
                item
                for item in self._session_store.task_admissions(self._writer.session_id)
                if item.proposal.admission_id == canonical
            ),
            None,
        )
        if match is None:
            raise SessionStoreError("Task admission proposal was not found in the current Session")
        return match

    def derive_task(
        self,
        parent_task_id: str,
        objective: str,
        *,
        acceptance_criteria: tuple[str, ...] = (),
        structured_criteria: tuple[dict[str, object], ...] = (),
        completion_policy: TaskCompletionPolicy = TaskCompletionPolicy.MANUAL,
        name: str | None = None,
        budget: TaskBudget = TaskBudget(),
    ) -> TaskInfo:
        """Create an independent current-Session Task with immutable parent provenance."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._task_store.derive(
                parent_task_id,
                objective,
                owner_session=self._writer.session_id,
                acceptance_criteria=acceptance_criteria,
                structured_criteria=structured_criteria,
                completion_policy=completion_policy,
                name=name,
                budget=budget,
            )

    def continue_task(
        self,
        task_id: str,
        stage_objective: str,
        *,
        event_sink: PromptEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> TaskStageExecutionResult:
        """Execute exactly one foreground ordinary Turn as one durable Task Stage."""
        return self._execute_task_stage(
            task_id,
            stage_objective,
            kind=StageKind.EXECUTION,
            event_sink=event_sink,
            include_tool_details=include_tool_details,
            cancellation=cancellation,
        )

    def plan_task(
        self,
        task_id: str,
        *,
        event_sink: PromptEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> TaskPlanExecutionResult:
        """Ask the current provider for one bounded plan and persist it as a proposal."""
        result = self._execute_task_stage(
            task_id,
            "Propose a bounded execution plan for this Task.",
            kind=StageKind.PLANNING,
            event_sink=event_sink,
            include_tool_details=include_tool_details,
            cancellation=cancellation,
        )
        task = self._task_store.inspect(task_id)
        plan = task.latest_plan
        if result.blocker is not None:
            return TaskPlanExecutionResult(task, result.response, (), result.blocker)
        if plan is None:
            raise TaskRuntimeError("planning Stage committed without a durable plan proposal")
        return TaskPlanExecutionResult(task, result.response, plan.steps)

    def reflect_task(
        self,
        task_id: str,
        *,
        event_sink: PromptEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> TaskReflectionExecutionResult:
        """Run one no-tools reflection Stage that can advise but cannot execute or accept."""
        task = self._task_store.inspect(task_id)
        if task.status is not TaskStatus.COMPLETION_PROPOSED:
            raise TaskStoreError("Task reflection requires a current completion proposal")
        if not any(
            check.outcome in {AcceptanceCheckOutcome.FAILED, AcceptanceCheckOutcome.ERROR}
            for check in task.acceptance_checks
        ):
            raise TaskStoreError("Task reflection requires current failed acceptance feedback")
        result = self._execute_task_stage(
            task_id,
            "Reflect on current acceptance feedback and recommend one bounded next action.",
            kind=StageKind.REFLECTION,
            event_sink=event_sink,
            include_tool_details=include_tool_details,
            cancellation=cancellation,
        )
        if result.blocker is not None:
            current = self._task_store.inspect(task_id)
            return TaskReflectionExecutionResult(current, result.response, None, result.blocker)
        if result.reflection is None:
            raise TaskRuntimeError("reflection Stage committed without a durable recommendation")
        current = self._task_store.inspect(task_id)
        return TaskReflectionExecutionResult(current, result.response, result.reflection)

    def correct_task(
        self,
        task_id: str,
        stage_objective: str | None = None,
        *,
        event_sink: PromptEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> TaskStageExecutionResult:
        """Execute one ordinary tool-capable correction Stage from current reflection advice."""
        task = self._task_store.inspect(task_id)
        reflection = task.latest_reflection
        if (
            reflection is None
            or reflection.recommendation is not ReflectionRecommendation.CORRECTION
            or not task.stages
            or task.stages[-1].stage_id != reflection.stage_id
        ):
            raise TaskStoreError("Task correction requires a current correction recommendation")
        objective = stage_objective or reflection.next_objective
        if objective is None:
            raise TaskStoreError("Task correction recommendation has no objective")
        return self._execute_task_stage(
            task_id,
            objective,
            kind=StageKind.CORRECTION,
            event_sink=event_sink,
            include_tool_details=include_tool_details,
            cancellation=cancellation,
        )

    def revise_task_plan(
        self,
        task_id: str,
        *,
        event_sink: PromptEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> TaskPlanExecutionResult:
        """Generate a replacement plan with explicit predecessor and reflection provenance."""
        task = self._task_store.inspect(task_id)
        reflection = task.latest_reflection
        if (
            task.latest_plan is None
            or reflection is None
            or reflection.recommendation is not ReflectionRecommendation.REVISE_PLAN
            or reflection.next_objective is None
            or not task.stages
            or task.stages[-1].stage_id != reflection.stage_id
        ):
            raise TaskStoreError(
                "Task plan revision requires current revise-plan reflection advice"
            )
        result = self._execute_task_stage(
            task_id,
            reflection.next_objective,
            kind=StageKind.PLANNING,
            event_sink=event_sink,
            include_tool_details=include_tool_details,
            cancellation=cancellation,
            plan_revision_reason=reflection.summary,
            plan_revision_reflection_id=reflection.reflection_id,
        )
        current = self._task_store.inspect(task_id)
        plan = current.latest_plan
        if result.blocker is not None:
            return TaskPlanExecutionResult(current, result.response, (), result.blocker)
        if plan is None:
            raise TaskRuntimeError("plan revision Stage committed without a durable plan proposal")
        return TaskPlanExecutionResult(current, result.response, plan.steps)

    def accept_task_plan(self, task_id: str) -> TaskInfo:
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            writer.accept_plan()
            return writer.info

    def run_task(
        self,
        task_id: str,
        *,
        max_stages: int = 16,
        event_sink: PromptEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> TaskRunResult:
        """Run accepted plan steps serially, one fresh ordinary Turn per Stage."""
        if type(max_stages) is not int or not 1 <= max_stages <= 16:
            raise ValueError("Task run Stage limit must be between 1 and 16")
        completed: list[TaskStageExecutionResult] = []
        reason = "run-limit"
        for _ in range(max_stages):
            if cancellation is not None:
                cancellation.check()
            task = self._task_store.inspect(task_id)
            if task.status in {
                TaskStatus.COMPLETED,
                TaskStatus.CANCELLED,
                TaskStatus.FAILED,
                TaskStatus.COMPLETION_PROPOSED,
            }:
                reason = task.status.value
                break
            if task.status is TaskStatus.INTERRUPTED:
                reason = "recovery-required"
                break
            plan = task.latest_plan
            if plan is None or not plan.accepted:
                raise TaskRuntimeError("Task run requires an accepted latest plan")
            if plan.completed_steps >= len(plan.steps):
                reason = "plan-exhausted"
                break
            if task.budget_exhausted:
                reason = "budget-exhausted"
                break
            stage = self.continue_task(
                task_id,
                plan.steps[plan.completed_steps],
                event_sink=event_sink,
                include_tool_details=include_tool_details,
                cancellation=cancellation,
            )
            completed.append(stage)
            if stage.completion_proposed:
                reason = "completion-proposed"
                break
        final = self._task_store.inspect(task_id)
        if reason == "run-limit":
            plan = final.latest_plan
            if plan is not None and plan.accepted and plan.completed_steps >= len(plan.steps):
                reason = "plan-exhausted"
            elif final.budget_exhausted:
                reason = "budget-exhausted"
        self._emit_prompt_event(event_sink, TaskRunStopped(len(completed), reason))
        return TaskRunResult(final, tuple(completed), reason)

    def set_task_driver_paused(
        self,
        task_id: str,
        paused: bool,
        reason: str | None = None,
    ) -> TaskInfo:
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            writer.set_paused(paused, reason)
            return writer.info

    def checkpoint_task(self, task_id: str) -> TaskInfo:
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            writer.create_context_checkpoint()
            return writer.info

    def preview_task_next(self, task_id: str) -> TaskNextAction:
        """Derive the next foreground-driver decision without provider work or mutation."""
        task = self._task_store.inspect(task_id)
        if task.status is TaskStatus.COMPLETED:
            return TaskNextAction(TaskDriverStopReason.COMPLETED, "Task is complete.", False, False)
        if task.status is TaskStatus.CANCELLED:
            return TaskNextAction(
                TaskDriverStopReason.CANCELLED, "Task is cancelled.", False, False
            )
        if task.status is TaskStatus.FAILED:
            return TaskNextAction(TaskDriverStopReason.FAILED, "Task has failed.", False, False)
        if task.driver_paused:
            return TaskNextAction(
                TaskDriverStopReason.PAUSED,
                "Foreground driver is durably paused; manual Stage commands remain available.",
                False,
                False,
            )
        if task.status is TaskStatus.INTERRUPTED:
            return TaskNextAction(
                TaskDriverStopReason.RECOVERY_REQUIRED,
                "Interrupted Stage must be reconciled before further work.",
                False,
                False,
            )
        if (
            task.latest_blocker is not None
            and task.stages
            and task.latest_blocker.stage_id == task.stages[-1].stage_id
        ):
            return TaskNextAction(
                TaskDriverStopReason.MODEL_BLOCKED,
                f"Model reported a {task.latest_blocker.category.value} blocker: "
                f"{task.latest_blocker.summary}",
                False,
                False,
            )
        if task.budget_exhausted:
            return TaskNextAction(
                TaskDriverStopReason.BUDGET_EXHAUSTED,
                "Cumulative Task budget blocks another Stage.",
                False,
                False,
            )
        if task.status is TaskStatus.COMPLETION_PROPOSED:
            verified = {item.criterion_index for item in task.acceptance_verifications}
            unresolved = [
                (index, criterion)
                for index, criterion in enumerate(task.criteria, start=1)
                if index not in verified
            ]
            host = [
                (index, criterion)
                for index, criterion in unresolved
                if criterion.kind
                not in {
                    AcceptanceCriterionKind.HUMAN,
                    AcceptanceCriterionKind.INDEPENDENT_REVIEWER,
                }
            ]
            if host:
                failed = any(
                    check.criterion_index in {index for index, _ in host}
                    and check.outcome
                    in {AcceptanceCheckOutcome.FAILED, AcceptanceCheckOutcome.ERROR}
                    for check in task.acceptance_checks
                )
                return TaskNextAction(
                    (
                        TaskDriverStopReason.HOST_VERIFICATION_FAILED
                        if failed
                        else TaskDriverStopReason.HOST_VERIFICATION_REQUIRED
                    ),
                    (
                        "Current Host checks failed; the driver will run a no-tools reflection."
                        if failed
                        else "Deterministic Host acceptance checks are ready to run."
                    ),
                    True,
                    failed,
                )
            reviewer = [
                criterion
                for _, criterion in unresolved
                if criterion.kind is AcceptanceCriterionKind.INDEPENDENT_REVIEWER
            ]
            if reviewer:
                return TaskNextAction(
                    TaskDriverStopReason.INDEPENDENT_REVIEW_REQUIRED,
                    "Independent review requires an explicit provider call with tools disabled and may consume API tokens or cost.",
                    False,
                    True,
                    sum(len(criterion.review_paths) for criterion in reviewer),
                )
            if unresolved:
                return TaskNextAction(
                    TaskDriverStopReason.HUMAN_VERIFICATION_REQUIRED,
                    "Human acceptance evidence is required.",
                    False,
                    False,
                )
            return TaskNextAction(
                TaskDriverStopReason.MANUAL_COMPLETION_REQUIRED,
                "All criteria are verified; manual completion policy requires /task complete.",
                False,
                False,
            )
        plan = task.latest_plan
        if plan is None:
            return TaskNextAction(
                TaskDriverStopReason.PLAN_REQUIRED,
                "A bounded plan proposal is required.",
                True,
                True,
            )
        if not plan.accepted:
            return TaskNextAction(
                TaskDriverStopReason.PLAN_ACCEPTANCE_REQUIRED,
                "The latest plan proposal requires explicit user acceptance.",
                False,
                False,
            )
        if plan.completed_steps >= len(plan.steps):
            return TaskNextAction(
                TaskDriverStopReason.PLAN_EXHAUSTED,
                "Accepted plan is exhausted without a current completion proposal.",
                False,
                False,
            )
        return TaskNextAction(
            TaskDriverStopReason.STAGE_INCOMPLETE,
            f"Run accepted plan step {plan.completed_steps + 1}: {plan.steps[plan.completed_steps]}",
            True,
            True,
        )

    def drive_task(
        self,
        task_id: str,
        *,
        max_stages: int = 16,
        event_sink: PromptEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> TaskDriveResult:
        """Advance one Task through a bounded adaptive foreground state machine."""
        if type(max_stages) is not int or not 1 <= max_stages <= 16:
            raise ValueError("Task drive Stage limit must be between 1 and 16")
        completed: list[TaskStageExecutionResult] = []
        reason = TaskDriverStopReason.STAGE_LIMIT
        while len(completed) < max_stages:
            if cancellation is not None:
                cancellation.check()
            action = self.preview_task_next(task_id)
            if action.reason is TaskDriverStopReason.PLAN_REQUIRED:
                planned = self.plan_task(
                    task_id,
                    event_sink=event_sink,
                    include_tool_details=include_tool_details,
                    cancellation=cancellation,
                )
                latest = planned.task.stages[-1]
                completed.append(_task_result_from_info(planned.task, planned.response, latest))
                self._checkpoint_after_driver_stage(task_id)
                if planned.blocker is not None:
                    reason = TaskDriverStopReason.MODEL_BLOCKED
                    break
                reason = TaskDriverStopReason.PLAN_ACCEPTANCE_REQUIRED
                break
            if action.reason is TaskDriverStopReason.STAGE_INCOMPLETE:
                task = self._task_store.inspect(task_id)
                plan = task.latest_plan
                assert plan is not None and plan.accepted
                stage = self.continue_task(
                    task_id,
                    plan.steps[plan.completed_steps],
                    event_sink=event_sink,
                    include_tool_details=include_tool_details,
                    cancellation=cancellation,
                )
                completed.append(stage)
                self._checkpoint_after_driver_stage(task_id)
                if not stage.completion_proposed:
                    continue
                continue
            if action.reason in {
                TaskDriverStopReason.HOST_VERIFICATION_REQUIRED,
                TaskDriverStopReason.HOST_VERIFICATION_FAILED,
            }:
                if action.reason is TaskDriverStopReason.HOST_VERIFICATION_REQUIRED:
                    self.verify_task_host(task_id)
                    self._checkpoint_after_driver_stage(task_id)
                    continue
                reflection = self.reflect_task(
                    task_id,
                    event_sink=event_sink,
                    include_tool_details=include_tool_details,
                    cancellation=cancellation,
                )
                latest = reflection.task.stages[-1]
                completed.append(
                    _task_result_from_info(reflection.task, reflection.response, latest)
                )
                self._checkpoint_after_driver_stage(task_id)
                if reflection.blocker is not None:
                    reason = TaskDriverStopReason.MODEL_BLOCKED
                    break
                assert reflection.reflection is not None
                recommendation = reflection.reflection.recommendation
                if recommendation is ReflectionRecommendation.CORRECTION:
                    if len(completed) >= max_stages:
                        reason = TaskDriverStopReason.STAGE_LIMIT
                        break
                    correction = self.correct_task(
                        task_id,
                        event_sink=event_sink,
                        include_tool_details=include_tool_details,
                        cancellation=cancellation,
                    )
                    completed.append(correction)
                    self._checkpoint_after_driver_stage(task_id)
                    if not correction.completion_proposed:
                        reason = TaskDriverStopReason.STAGE_INCOMPLETE
                        break
                    continue
                if recommendation is ReflectionRecommendation.REVISE_PLAN:
                    if len(completed) >= max_stages:
                        reason = TaskDriverStopReason.STAGE_LIMIT
                        break
                    revised = self.revise_task_plan(
                        task_id,
                        event_sink=event_sink,
                        include_tool_details=include_tool_details,
                        cancellation=cancellation,
                    )
                    latest = revised.task.stages[-1]
                    completed.append(_task_result_from_info(revised.task, revised.response, latest))
                    self._checkpoint_after_driver_stage(task_id)
                    if revised.blocker is not None:
                        reason = TaskDriverStopReason.MODEL_BLOCKED
                        break
                    reason = TaskDriverStopReason.PLAN_ACCEPTANCE_REQUIRED
                    break
                if recommendation is ReflectionRecommendation.CONTINUE:
                    if len(completed) >= max_stages:
                        reason = TaskDriverStopReason.STAGE_LIMIT
                        break
                    assert reflection.reflection.next_objective is not None
                    stage = self._execute_task_stage(
                        task_id,
                        reflection.reflection.next_objective,
                        kind=StageKind.EXECUTION,
                        event_sink=event_sink,
                        include_tool_details=include_tool_details,
                        cancellation=cancellation,
                    )
                    completed.append(stage)
                    self._checkpoint_after_driver_stage(task_id)
                    if not stage.completion_proposed:
                        reason = TaskDriverStopReason.STAGE_INCOMPLETE
                        break
                    continue
                reason = (
                    TaskDriverStopReason.REFLECTION_NEEDS_HUMAN
                    if recommendation is ReflectionRecommendation.NEEDS_HUMAN
                    else TaskDriverStopReason.REFLECTION_FAILED
                )
                break
            reason = action.reason
            break
        final = self._task_store.inspect(task_id)
        if final.status is TaskStatus.COMPLETED:
            reason = TaskDriverStopReason.COMPLETED
        self._emit_prompt_event(event_sink, TaskRunStopped(len(completed), reason.value))
        return TaskDriveResult(final, tuple(completed), reason)

    def _checkpoint_after_driver_stage(self, task_id: str) -> None:
        if self._task_store.inspect(task_id).terminal_outcome is not None:
            return
        with self._task_store.open(task_id) as writer:
            writer.create_context_checkpoint()

    def recover_task(self, task_id: str) -> TaskInfo:
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            sequence_before = writer.state.next_sequence
            if writer.state.active_stage is not None:
                committed_hook_audit = self._task_hook_audit(
                    HookEvent.TASK_STAGE_COMMITTED,
                    task_id,
                )
                failed_hook_audit = self._task_hook_audit(
                    HookEvent.TASK_STAGE_FAILED,
                    task_id,
                )
                recovered = writer.recover_stage(
                    committed_hook_audit=committed_hook_audit,
                    failed_hook_audit=failed_hook_audit,
                )
                recovered_event = (
                    HookEvent.TASK_STAGE_COMMITTED
                    if isinstance(recovered, StageCommitted)
                    else HookEvent.TASK_STAGE_FAILED
                )
                self._run_lifecycle_hook_handlers(
                    recovered_event,
                    task_id,
                    committed_hook_audit
                    if isinstance(recovered, StageCommitted)
                    else failed_hook_audit,
                )
            self._reconcile_task_stage_signal(writer)
            if writer.state.next_sequence == sequence_before:
                raise TaskStoreError("Task has no interrupted or unreconciled Stage to recover")
            return writer.info

    def _reconcile_task_stage_signal(self, writer: TaskWriter) -> None:
        state = writer.state
        if not state.stages or not isinstance(state.stages[-1].terminal, StageCommitted):
            return
        stage = state.stages[-1]
        terminal = stage.terminal
        assert isinstance(terminal, StageCommitted)
        if (
            state.latest_blocker is not None
            and state.latest_blocker.stage_id == stage.started.stage_id
        ):
            return
        if stage.started.kind is StageKind.PLANNING:
            latest_plan = state.latest_plan
            if latest_plan is not None and latest_plan.stage_id == stage.started.stage_id:
                return
        elif stage.started.kind is StageKind.REFLECTION:
            latest_reflection = state.latest_reflection
            if (
                latest_reflection is not None
                and latest_reflection.stage_id == stage.started.stage_id
            ):
                return
        elif (
            state.current_completion_proposal is not None
            and state.current_completion_proposal.stage_id == stage.started.stage_id
        ):
            return
        turn = self._session_store.committed_turn(
            state.header.owner_session_id,
            terminal.turn_record_sequence,
        )
        task_requests: list[ToolUse] = []
        for item in turn.items:
            if isinstance(item, ToolUse) and item.name in TASK_CONTROL_TOOL_NAMES:
                task_requests.append(item)
            elif isinstance(item, AssistantToolBatch):
                task_requests.extend(
                    request for request in item.tool_uses if request.name in TASK_CONTROL_TOOL_NAMES
                )
        proposal_tool_use_id: str | None = None
        if task_requests:
            if len(task_requests) != 1:
                raise TaskStoreError("committed Task Stage has ambiguous control calls")
            request = recover_task_control_request(turn, tool_name=task_requests[0].name)
            if request.name not in _task_control_names_for_stage(stage.started.kind):
                raise TaskStoreError("committed Task control call does not match its Stage")
            proposal_kind = TASK_PROPOSAL_KIND_BY_TOOL[request.name]
            parsed = _resolve_task_control_payload(
                turn.assistant.text,
                kind=stage.started.kind,
                proposal_kind=proposal_kind,
                values=request.arguments.as_mapping(),
            )
            proposal_tool_use_id = request.tool_use_id
        else:
            try:
                parsed = _resolve_task_stage_response(
                    turn.assistant.text,
                    kind=stage.started.kind,
                    proposal=None,
                )
            except TaskRuntimeError:
                return
        if parsed.blocker is not None:
            assert proposal_tool_use_id is not None
            blocked_hook_audit = self._task_hook_audit(
                HookEvent.TASK_BLOCKED,
                state.header.task_id,
            )
            writer.record_blocker(
                parsed.blocker.category,
                parsed.blocker.summary,
                proposal_tool_use_id=proposal_tool_use_id,
                hook_audit=blocked_hook_audit,
            )
            self._run_lifecycle_hook_handlers(
                HookEvent.TASK_BLOCKED,
                state.header.task_id,
                blocked_hook_audit,
            )
        elif stage.started.kind is StageKind.PLANNING:
            assert parsed.plan_steps is not None
            reflection = state.latest_reflection
            writer.propose_plan(
                parsed.plan_steps,
                revision_reason=(
                    reflection.summary
                    if state.latest_plan is not None
                    and reflection is not None
                    and reflection.recommendation is ReflectionRecommendation.REVISE_PLAN
                    else None
                ),
                reflection_id=(
                    reflection.reflection_id
                    if state.latest_plan is not None
                    and reflection is not None
                    and reflection.recommendation is ReflectionRecommendation.REVISE_PLAN
                    else None
                ),
                proposal_tool_use_id=proposal_tool_use_id,
            )
        elif stage.started.kind is StageKind.REFLECTION:
            assert parsed.reflection is not None
            writer.record_reflection(
                parsed.reflection.recommendation,
                parsed.reflection.summary,
                parsed.reflection.next_objective,
                proposal_tool_use_id=proposal_tool_use_id,
            )
        elif parsed.completion_proposed:
            writer.propose_completion(proposal_tool_use_id=proposal_tool_use_id)
            self._auto_complete_verified_task(writer)

    def verify_task_acceptance(
        self,
        task_id: str,
        criterion_index: int,
        evidence: str,
    ) -> TaskInfo:
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            writer.verify_acceptance(criterion_index, evidence)
            self._auto_complete_verified_task(writer)
            return writer.info

    def verify_task_host(self, task_id: str) -> TaskVerificationResult:
        """Run deterministic acceptance checks and persist only Host-observed evidence."""
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            self._ensure_not_compacting()
            already_verified = set(writer.state.verified_criteria)
            observed = run_host_acceptance_checks(
                self._task_store.workspace,
                writer.info,
            )
            recorded: list[AcceptanceCheckResult] = []
            for result in observed:
                if result.criterion_index in already_verified:
                    continue
                writer.record_acceptance_check(
                    result.criterion_index,
                    result.source,
                    result.outcome,
                    result.evidence,
                )
                recorded.append(result)
                if result.outcome is AcceptanceCheckOutcome.PASSED:
                    writer.verify_acceptance(
                        result.criterion_index,
                        result.evidence,
                        source=AcceptanceVerificationSource.HOST_CHECK,
                    )
            auto_completed = self._auto_complete_verified_task(writer)
            return TaskVerificationResult(writer.info, tuple(recorded), auto_completed)

    def review_task_acceptance(self, task_id: str) -> TaskVerificationResult:
        """Run one independent no-tools provider review outside Executor Session history."""
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            self._ensure_not_compacting()
            if writer.state.current_completion_proposal is None:
                raise TaskStoreError("Task review requires the current completion proposal")
            already_verified = set(writer.state.verified_criteria)
            indices = tuple(
                index
                for index, criterion in enumerate(writer.state.criteria, start=1)
                if criterion.kind is AcceptanceCriterionKind.INDEPENDENT_REVIEWER
                and index not in already_verified
            )
            if not indices:
                raise TaskStoreError("Task has no unverified independent-reviewer criteria")
            request = build_task_review_request(
                writer.info,
                self._task_store.workspace,
                reviewer_indices=indices,
            )
            try:
                with self._manager.provider_for_review() as runtime:
                    response = runtime.review(request)
                observed = parse_task_review_response(response, expected_indices=indices)
            except BaseException as error:
                evidence = f"review-error={type(error).__name__}"
                for index in indices:
                    writer.record_acceptance_check(
                        index,
                        AcceptanceVerificationSource.INDEPENDENT_REVIEWER,
                        AcceptanceCheckOutcome.ERROR,
                        evidence,
                    )
                raise
            for result in observed:
                writer.record_acceptance_check(
                    result.criterion_index,
                    result.source,
                    result.outcome,
                    result.evidence,
                )
                if result.outcome is AcceptanceCheckOutcome.PASSED:
                    writer.verify_acceptance(
                        result.criterion_index,
                        result.evidence,
                        source=AcceptanceVerificationSource.INDEPENDENT_REVIEWER,
                    )
            auto_completed = self._auto_complete_verified_task(writer)
            return TaskVerificationResult(writer.info, observed, auto_completed)

    def _auto_complete_verified_task(self, writer: TaskWriter) -> bool:
        state = writer.state
        if state.completion_policy is not TaskCompletionPolicy.AUTO_VERIFIED:
            return False
        if state.current_completion_proposal is None:
            return False
        if len(state.verified_criteria) != len(state.criteria):
            return False
        task_id = writer.state.header.task_id
        hook_audit = self._task_hook_audit(
            HookEvent.TASK_TERMINATED,
            task_id,
        )
        writer.terminate(
            TaskTerminalOutcome.COMPLETED,
            hook_audit=hook_audit,
        )
        self._run_lifecycle_hook_handlers(
            HookEvent.TASK_TERMINATED,
            task_id,
            hook_audit,
        )
        return True

    def complete_task(self, task_id: str) -> TaskInfo:
        return self._terminate_task(task_id, TaskTerminalOutcome.COMPLETED)

    def cancel_task(self, task_id: str, reason: str) -> TaskInfo:
        return self._terminate_task(task_id, TaskTerminalOutcome.CANCELLED, reason)

    def fail_task(self, task_id: str, reason: str) -> TaskInfo:
        return self._terminate_task(task_id, TaskTerminalOutcome.FAILED, reason)

    def rename_task(self, task_id: str, name: str) -> TaskInfo:
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            writer.rename(name)
            return writer.info

    def set_task_archived(self, task_id: str, archived: bool) -> TaskInfo:
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            writer.set_archived(archived)
            return writer.info

    def _terminate_task(
        self,
        task_id: str,
        outcome: TaskTerminalOutcome,
        reason: str | None = None,
    ) -> TaskInfo:
        with self._lock, self._task_store.open(task_id) as writer:
            self._ensure_open()
            active_stage_hook_audit = (
                self._task_hook_audit(HookEvent.TASK_STAGE_FAILED, task_id)
                if writer.state.active_stage is not None
                else HookAuditLedger()
            )
            terminated_hook_audit = self._task_hook_audit(
                HookEvent.TASK_TERMINATED,
                task_id,
            )
            writer.terminate(
                outcome,
                reason,
                hook_audit=terminated_hook_audit,
                active_stage_hook_audit=active_stage_hook_audit,
            )
            if active_stage_hook_audit.entries:
                self._run_lifecycle_hook_handlers(
                    HookEvent.TASK_STAGE_FAILED,
                    task_id,
                    active_stage_hook_audit,
                )
            self._run_lifecycle_hook_handlers(
                HookEvent.TASK_TERMINATED,
                task_id,
                terminated_hook_audit,
            )
            return writer.info

    def _execute_task_stage(
        self,
        task_id: str,
        stage_objective: str,
        *,
        kind: StageKind,
        event_sink: PromptEventSink | None,
        include_tool_details: bool,
        cancellation: TurnCancellation | None,
        plan_revision_reason: str | None = None,
        plan_revision_reflection_id: str | None = None,
    ) -> TaskStageExecutionResult:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            initial = self._task_store.inspect(task_id)
            if initial.owner_session_id != self._writer.session_id:
                raise TaskStoreError(
                    "Task owner Session is not current; switch to the owner Session before execution"
                )
            if initial.status is TaskStatus.INTERRUPTED:
                raise TaskStoreError("Task has an interrupted Stage; recover it before continuing")
            if initial.status in {
                TaskStatus.COMPLETED,
                TaskStatus.CANCELLED,
                TaskStatus.FAILED,
            }:
                raise TaskStoreError("Task is terminal and cannot continue")
            with self._task_store.open(task_id) as writer:
                stage_number = writer.state.next_stage_number
                prompt = build_task_stage_prompt(
                    writer.info,
                    stage_objective,
                    stage_number=stage_number,
                    kind=kind,
                )
                prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                session_record_before = self._writer.state.next_sequence - 1
                turn_count_before = len(self._writer.state.turns)
                started_hook_audit = self._task_hook_audit(
                    HookEvent.TASK_STAGE_STARTED,
                    task_id,
                    event_sink=event_sink,
                )
                started = writer.start_stage(
                    stage_objective,
                    kind=kind,
                    session_record_sequence_before=session_record_before,
                    session_turn_count_before=turn_count_before,
                    prompt_sha256=prompt_sha256,
                    hook_audit=started_hook_audit,
                )
                self._run_lifecycle_hook_handlers(
                    HookEvent.TASK_STAGE_STARTED,
                    task_id,
                    started_hook_audit,
                    event_sink=event_sink,
                )
                response: str | None = None
                task_proposal: TaskControlProposal | None = None
                failed_stage_usage: StageUsage | None = None

                def capture_failure_usage(
                    provider_usage: tuple[ProviderInvocationUsage, ...],
                    tool_usage: ToolAttemptUsage,
                ) -> None:
                    nonlocal failed_stage_usage
                    failed_stage_usage = _task_stage_usage(provider_usage, tool_usage)

                def capture_task_proposal(proposal: TaskControlProposal) -> None:
                    nonlocal task_proposal
                    if task_proposal is not None:
                        raise TaskRuntimeError("Task Stage produced more than one proposal")
                    if (
                        proposal.task_id != initial.task_id
                        or proposal.stage_id != started.stage_id
                        or proposal.stage_number != started.stage_number
                    ):
                        raise TaskRuntimeError("Task proposal does not match its active Stage")
                    task_proposal = proposal

                try:
                    task_event_sink = (
                        TaskProtocolEventFilter(event_sink, kind=kind)
                        if event_sink is not None
                        else None
                    )
                    self._active_task_control_scope = _TaskControlScope(
                        initial.task_id,
                        started.stage_id,
                        started.stage_number,
                        _task_control_names_for_stage(kind),
                    )
                    try:
                        response = self.prompt(
                            prompt,
                            event_sink=task_event_sink,
                            include_tool_details=include_tool_details,
                            cancellation=cancellation,
                            session_title_source_text=initial.objective,
                            _enabled_tool_names=_task_tool_names_for_stage(kind),
                            _task_proposal_sink=capture_task_proposal,
                            _failure_usage_sink=capture_failure_usage,
                        )
                    finally:
                        self._active_task_control_scope = None
                except BaseException as error:
                    committed = _latest_turn_after(
                        self._writer.state.records, session_record_before
                    )
                    if committed is not None:
                        committed_hook_audit = self._task_hook_audit(
                            HookEvent.TASK_STAGE_COMMITTED,
                            task_id,
                            event_sink=event_sink,
                        )
                        writer.commit_stage(
                            committed.sequence,
                            hook_audit=committed_hook_audit,
                        )
                        self._run_lifecycle_hook_handlers(
                            HookEvent.TASK_STAGE_COMMITTED,
                            task_id,
                            committed_hook_audit,
                            event_sink=event_sink,
                        )
                    else:
                        failed_hook_audit = self._task_hook_audit(
                            HookEvent.TASK_STAGE_FAILED,
                            task_id,
                            event_sink=event_sink,
                        )
                        writer.fail_stage(
                            _task_stage_failure_reason(error),
                            usage=failed_stage_usage,
                            hook_audit=failed_hook_audit,
                        )
                        self._run_lifecycle_hook_handlers(
                            HookEvent.TASK_STAGE_FAILED,
                            task_id,
                            failed_hook_audit,
                            event_sink=event_sink,
                        )
                    raise
                committed = _latest_turn_after(self._writer.state.records, session_record_before)
                if committed is None:
                    failed_hook_audit = self._task_hook_audit(
                        HookEvent.TASK_STAGE_FAILED,
                        task_id,
                        event_sink=event_sink,
                    )
                    writer.fail_stage(
                        StageFailureReason.TURN_NOT_COMMITTED,
                        hook_audit=failed_hook_audit,
                    )
                    self._run_lifecycle_hook_handlers(
                        HookEvent.TASK_STAGE_FAILED,
                        task_id,
                        failed_hook_audit,
                        event_sink=event_sink,
                    )
                    raise TaskRuntimeError("Task Stage returned without a committed Session Turn")
                try:
                    committed_hook_audit = self._task_hook_audit(
                        HookEvent.TASK_STAGE_COMMITTED,
                        task_id,
                        event_sink=event_sink,
                    )
                    stage_record = writer.commit_stage(
                        committed.sequence,
                        hook_audit=committed_hook_audit,
                    )
                except TaskAppendCommitError:
                    raise
                self._run_lifecycle_hook_handlers(
                    HookEvent.TASK_STAGE_COMMITTED,
                    task_id,
                    committed_hook_audit,
                    event_sink=event_sink,
                )
                parsed = _resolve_task_stage_response(
                    response,
                    kind=kind,
                    proposal=task_proposal,
                )
                if parsed.blocker is not None:
                    assert task_proposal is not None
                    blocked_hook_audit = self._task_hook_audit(
                        HookEvent.TASK_BLOCKED,
                        task_id,
                        event_sink=event_sink,
                    )
                    writer.record_blocker(
                        parsed.blocker.category,
                        parsed.blocker.summary,
                        proposal_tool_use_id=task_proposal.tool_use_id,
                        hook_audit=blocked_hook_audit,
                    )
                    self._run_lifecycle_hook_handlers(
                        HookEvent.TASK_BLOCKED,
                        task_id,
                        blocked_hook_audit,
                        event_sink=event_sink,
                    )
                elif kind is StageKind.PLANNING:
                    assert parsed.plan_steps is not None
                    writer.propose_plan(
                        parsed.plan_steps,
                        revision_reason=plan_revision_reason,
                        reflection_id=plan_revision_reflection_id,
                        proposal_tool_use_id=(
                            task_proposal.tool_use_id if task_proposal is not None else None
                        ),
                    )
                elif kind is StageKind.REFLECTION:
                    assert parsed.reflection is not None
                    writer.record_reflection(
                        parsed.reflection.recommendation,
                        parsed.reflection.summary,
                        parsed.reflection.next_objective,
                        proposal_tool_use_id=(
                            task_proposal.tool_use_id if task_proposal is not None else None
                        ),
                    )
                elif parsed.completion_proposed:
                    writer.propose_completion(
                        proposal_tool_use_id=(
                            task_proposal.tool_use_id if task_proposal is not None else None
                        )
                    )
                    self._auto_complete_verified_task(writer)
                return TaskStageExecutionResult(
                    task=writer.info,
                    stage_number=stage_record.stage_number,
                    response=parsed.display_text,
                    completion_proposed=parsed.completion_proposed,
                    session_turn_number=stage_record.turn_number,
                    session_turn_record_sequence=stage_record.turn_record_sequence,
                    reflection=parsed.reflection,
                    blocker=parsed.blocker,
                )

    def latest_session_info(self) -> SessionInfo:
        """Return the Session referenced by this workspace's latest pointer."""
        self._ensure_open()
        return self._session_store.show("latest")

    def inspect_session(self, selector: str | Path) -> SessionInfo:
        """Strictly inspect another Session without changing current or latest."""
        with self._lock:
            self._ensure_open()
            return self._session_store.inspect(selector)

    def preview_session(self, selector: str | Path, limit: int) -> SessionPreview:
        """Read bounded final-text turns without changing runtime or Session state."""
        with self._lock:
            self._ensure_open()
            return self._session_store.preview(selector, limit)

    def session_turn_range(
        self,
        selector: str | Path,
        start_turn: int,
        count: int,
    ) -> SessionTurnRange:
        """Read one bounded complete-turn range without changing current state."""
        with self._lock:
            self._ensure_open()
            return self._session_store.turn_range(selector, start_turn, count)

    def search_sessions(self, query: str, limit: int) -> SessionSearchResult:
        """Search bounded final dialogue text without invoking the provider."""
        with self._lock:
            self._ensure_open()
            return self._session_store.search(query, limit)

    def export_session(self, selector: str | Path) -> SessionConversationExport:
        """Project one complete bounded conversation for stdout export."""
        with self._lock:
            self._ensure_open()
            return self._session_store.conversation_export(selector)

    def diagnose_session(self, selector: str | Path) -> SessionDiagnosis:
        """Diagnose one transcript without mutation or repair."""
        with self._lock:
            self._ensure_open()
            return self._session_store.diagnose(selector)

    def repair_session(self, selector: str | Path) -> SessionRepairResult:
        """Explicitly back up and repair another Session's incomplete final tail."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._session_store.repair(selector)

    def new_session(self) -> SessionInfo:
        """Create and atomically select an empty Session without changing runtime."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            candidate = self._session_store.create(binding_from_status(self._manager.status()))
            try:
                loop = self._new_loop(candidate)
            except BaseException:
                candidate.release()
                raise
            old = self._writer
            self._writer = candidate
            self._loop = loop
            old.release()
            return candidate.info

    def fork_session(self, selector: str | Path, through_turn: int) -> SessionInfo:
        """Create and select a provenance-linked Session from complete parent turns."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            candidate = self._session_store.fork(
                selector,
                through_turn,
                binding=binding_from_status(self._manager.status()),
            )
            try:
                loop = self._new_loop(candidate)
            except BaseException:
                candidate.release()
                raise
            old = self._writer
            self._writer = candidate
            self._loop = loop
            old.release()
            return candidate.info

    def rename_session(self, name: str | None = None) -> SessionInfo:
        """Rename the current Session or restore its deterministic automatic name."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._writer.rename(name)

    def set_session_archived(self, archived: bool) -> SessionInfo:
        """Set reversible archive metadata without changing history, runtime, or latest."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._writer.set_archived(archived)

    def set_session_pinned(self, pinned: bool) -> SessionInfo:
        """Set reversible pin metadata without changing history, runtime, or latest."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._writer.set_pinned(pinned)

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
                    self._compare_files,
                    self._git_blame,
                    self._git_refs,
                    self._json_query,
                    self._checksum_file,
                    self._archive_list,
                    commit_turn=lambda turn: self._commit_turn(writer_holder["writer"], turn),
                    project_instructions_factory=self._project_instructions_loader.load,
                    tool_registry_factory=self._mcp_catalog_service.registry_snapshot,
                    skill_inventory_factory=self._skill_inventory_loader.load,
                    hook_set_factory=self._hook_store.snapshot,
                    skill_resource_reader=self._skill_inventory_loader.read_resource,
                )
                loop.install_action_dispatcher(self._dispatch_action)
                loop.install_task_control_dispatcher(
                    _COMMIT_CONTROL_TOOL_NAMES, self._dispatch_task_control
                )
                loop.install_tool_set_transition_dispatcher(self._transition_tool_set)
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
                    committed = prepared.commit(binding=binding_from_status(runtime.status))
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
        session_title_source_text: str | None = None,
        _allow_tools: bool = True,
        _enabled_tool_names: tuple[str, ...] | None = None,
        _task_proposal_sink: TaskProposalSink | None = None,
        _failure_usage_sink: Callable[[tuple[ProviderInvocationUsage, ...], ToolAttemptUsage], None]
        | None = None,
    ) -> str:
        """Run one serialized preflighted turn with one exact prepared-action lease."""
        if type(include_tool_details) is not bool:
            raise ValueError("tool detail event option is invalid")
        if type(_allow_tools) is not bool:
            raise ValueError("turn tool exposure flag is invalid")
        if _enabled_tool_names is not None and not _allow_tools:
            raise ValueError("disabled turn cannot select enabled tools")
        if _task_proposal_sink is not None and not callable(_task_proposal_sink):
            raise ValueError("Task proposal sink is invalid")
        if cancellation is not None and type(cancellation) is not TurnCancellation:
            raise ValueError("turn cancellation token is invalid")
        if session_title_source_text is not None and (
            not isinstance(session_title_source_text, str) or not session_title_source_text.strip()
        ):
            raise ValueError("Session title source text must be nonblank text")
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            enabled_tool_names = (
                ORDINARY_PROMPT_TOOL_NAMES if _enabled_tool_names is None else _enabled_tool_names
            )
            if not any(source in {"brave", "tavily"} for source in self._search_source_order):
                enabled_tool_names = tuple(
                    name for name in enabled_tool_names if name != WEB_SEARCH_TOOL_NAME
                )
            prepared = self._loop.prepare_turn(
                text,
                allow_tools=_allow_tools,
                enabled_tool_names=(enabled_tool_names if _allow_tools else None),
            )
            self._active_hook_set_snapshot = prepared.hook_set_snapshot
            self._active_hook_audit_entries = []
            self._active_hook_handler_executions = 0
            loop = self._loop
            binding: BindingSnapshot | None = None
            usage_cursor = self._manager.begin_turn_usage()
            tool_attempt_usage = ToolAttemptUsage()

            def observe_tool_usage(usage: ToolAttemptUsage) -> None:
                nonlocal tool_attempt_usage
                tool_attempt_usage = usage

            self._active_usage_cursor = usage_cursor
            self._active_cancellation = cancellation
            self._active_event_sink = event_sink
            self._active_session_title_source_text = session_title_source_text
            self._active_prepared_session_title = None
            try:
                if cancellation is not None:
                    cancellation.check()
                with self._manager.provider_for_turn() as runtime:
                    self._active_turn_runtime = runtime
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
                    self._active_turn_context = prepared.context
                    self._active_action_binding = binding
                    self._active_tool_set_snapshot = prepared.tool_set_snapshot
                    self._active_hook_set_snapshot = prepared.hook_set_snapshot
                    response = loop.run_prepared(
                        prepared,
                        provider=runtime,
                        event_sink=event_sink,
                        include_tool_details=include_tool_details,
                        cancellation=cancellation,
                        tool_usage_sink=(
                            observe_tool_usage if _failure_usage_sink is not None else None
                        ),
                        task_proposal_sink=(
                            _task_proposal_sink
                            if _task_proposal_sink is not None
                            else self._capture_task_admission_proposal
                        ),
                        first_provider_response_hook=(
                            lambda: self._prepare_first_turn_session_title(
                                runtime,
                                usage_cursor,
                                session_title_source_text or text,
                            )
                        )
                        if not self._writer.state.turns and self._writer.state.latest_name is None
                        else None,
                    )
                usage = self._manager.finish_turn_usage(usage_cursor)
                if usage.latest_invocation is not None:
                    self._emit_prompt_event(event_sink, TurnUsageCompleted(usage))
                return response
            except BaseException as error:
                self._manager.finish_turn_usage(usage_cursor)
                provider_usage = self._manager.usage_since(
                    usage_cursor,
                    kind=ProviderInvocationKind.TURN,
                )
                if _failure_usage_sink is not None:
                    try:
                        _failure_usage_sink(provider_usage, tool_attempt_usage)
                    except Exception:
                        pass
                self._record_failure(
                    binding or binding_from_status(self._manager.status()),
                    error,
                    provider_usage=provider_usage,
                )
                raise
            finally:
                self._active_action_lease = None
                self._active_turn_context = None
                self._active_action_binding = None
                self._active_tool_set_snapshot = None
                self._active_hook_set_snapshot = None
                self._active_hook_audit_entries = []
                self._active_hook_handler_executions = 0
                self._active_usage_cursor = None
                self._active_turn_runtime = None
                self._active_cancellation = None
                self._active_event_sink = None
                self._active_session_title_source_text = None
                self._active_prepared_session_title = None

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

    def inspect_web_search_sources(self) -> WebSearchSourceConfiguration:
        """Inspect process-local search source activation without provider or Session work."""
        with self._lock:
            self._ensure_open()
            external = self._web_search.source_configuration()
            status = self._manager.status()
            return replace(
                external,
                ordered_sources=self._search_source_order,
                provider_available=status.native_search_available,
                provider_active="provider" in self._search_source_order,
                provider_adapter=status.native_search_adapter,
                provider_mode=self._native_search_options.mode.value,
                provider_allowed_domains=self._native_search_options.allowed_domains,
                provider_context_size=(
                    self._native_search_options.context_size.value
                    if self._native_search_options.context_size is not None
                    else None
                ),
                error=(
                    None if self._search_source_order else "all web search sources are disabled"
                ),
            )

    def set_web_search_sources(self, sources: tuple[str, ...]) -> WebSearchSourceConfiguration:
        """Activate one primary and optional model-mediated independent fallbacks."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            if not isinstance(sources, tuple) or not sources or len(sources) > 3:
                raise WebSearchPreparationError("one or more web search sources are required")
            if len(set(sources)) != len(sources):
                raise WebSearchPreparationError("web search sources must not contain duplicates")
            supported = {"provider", "brave", "tavily"}
            if any(source not in supported for source in sources):
                raise WebSearchPreparationError(
                    "web search sources must be selected from: provider, brave, tavily"
                )
            status = self._manager.status()
            if "provider" in sources and not status.native_search_available:
                raise WebSearchPreparationError(
                    "web search source 'provider' requires a declared native-search adapter"
                )
            external_names = tuple(source for source in sources if source != "provider")
            available = {
                source.value for source in self._web_search.source_configuration().available_sources
            }
            for source in external_names:
                if source not in available:
                    raise WebSearchPreparationError(
                        f"web search source '{source}' requires a valid credential environment value"
                    )
            self._manager.set_native_search_enabled(sources[0] == "provider")
            if external_names:
                self._web_search.configure_sources(external_names)
            else:
                self._web_search.disable_sources()
            self._search_source_order = sources
            return self.inspect_web_search_sources()

    def reset_web_search_sources(self) -> WebSearchSourceConfiguration:
        """Restore provider-native default or disable all search when unavailable."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._web_search.disable_sources()
            status = self._manager.status()
            self._native_search_options = NativeSearchRuntimeOptions()
            self._manager.set_native_search_options(self._native_search_options)
            self._manager.set_native_search_enabled(status.native_search_available)
            self._search_source_order = ("provider",) if status.native_search_available else ()
            return self.inspect_web_search_sources()

    def _search_execution_source(self) -> str | None:
        return self._search_source_order[0] if self._search_source_order else None

    def set_native_search_mode(self, mode: str) -> WebSearchSourceConfiguration:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            try:
                selected = NativeSearchMode(mode)
            except ValueError:
                raise WebSearchPreparationError(
                    "provider search mode must be auto or required"
                ) from None
            candidate = replace(self._native_search_options, mode=selected)
            self._manager.set_native_search_options(candidate)
            self._native_search_options = candidate
            return self.inspect_web_search_sources()

    def set_native_search_domains(
        self, domains: tuple[str, ...] | None
    ) -> WebSearchSourceConfiguration:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            if domains is None:
                canonical = ()
            else:
                if not domains or len(domains) > 20:
                    raise WebSearchPreparationError(
                        "provider search requires between 1 and 20 allowed domains"
                    )
                try:
                    canonical = tuple(canonical_native_search_domain(value) for value in domains)
                except ValueError as error:
                    raise WebSearchPreparationError(str(error)) from None
                if len(set(canonical)) != len(canonical):
                    raise WebSearchPreparationError(
                        "provider search allowed domains must not contain duplicates"
                    )
            candidate = replace(self._native_search_options, allowed_domains=canonical)
            self._manager.set_native_search_options(candidate)
            self._native_search_options = candidate
            return self.inspect_web_search_sources()

    def set_native_search_context(self, size: str | None) -> WebSearchSourceConfiguration:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            if size is None:
                selected = None
            else:
                try:
                    selected = NativeSearchContextSize(size)
                except ValueError:
                    raise WebSearchPreparationError(
                        "provider search context must be low, medium, high, or reset"
                    ) from None
            candidate = replace(self._native_search_options, context_size=selected)
            self._manager.set_native_search_options(candidate)
            self._native_search_options = candidate
            return self.inspect_web_search_sources()

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
            active_before = active_skills_from_history(snapshot.effective_history)
            current_tool_set = self._loop.tool_set_snapshot_for_effective_history(
                snapshot.effective_history
            )
            current_action_tools = _action_tool_names(current_tool_set)
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
                    active_skills_before=active_before,
                    active_skills_after=active_before,
                    action_tools_after=current_action_tools,
                )
            retained_turns = snapshot.effective_turns[-plan.retained_turn_count :]
            retained_history = tuple(item for turn in retained_turns for item in turn.items)
            active_after = active_skills_from_history(retained_history)
            retained_identities = {(skill.name, skill.fingerprint) for skill in active_after}
            removed = tuple(
                skill
                for skill in active_before
                if (skill.name, skill.fingerprint) not in retained_identities
            )
            after_tool_set = self._loop.tool_set_snapshot_for_effective_history(retained_history)
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
                active_skills_before=active_before,
                active_skills_after=active_after,
                removed_skills=removed,
                action_tools_after=_action_tool_names(after_tool_set),
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
        candidate_tool_set = prepared.loop.tool_set_snapshot_for_effective_history(
            prepared.plan.retained_history
        )
        candidate = EffectiveContextSnapshot(
            representation_version=COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
            source=EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
            system_prompt=source.system_prompt,
            project_instructions=source.project_instructions,
            tool_definitions=candidate_tool_set.definitions,
            tool_set_id=candidate_tool_set.snapshot_id,
            skill_inventory_id=source.skill_inventory_id,
            hook_set_id=source.hook_set_id,
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

    def _resolve_hook_handlers(
        self,
        evaluation: HookEvaluation,
        snapshot: HookSetSnapshot,
        *,
        event: HookEvent,
        subject_id: str,
        tool_name: str | None = None,
        permission_action: str | None = None,
        source: str | None = None,
        action_outcome: HookActionOutcome | None = None,
    ) -> HookEvaluation:
        """Synchronously resolve matched executable rules through audited actions."""
        executable = tuple(
            entry for entry in evaluation.matched_entries if entry.rule.handler is not None
        )
        if not executable:
            return evaluation
        results: dict[str, HookHandlerResult] = {}
        inside_turn = self._active_action_lease is not None
        for index, entry in enumerate(executable):
            if index >= MAX_HOOK_HANDLER_EXECUTIONS_PER_EVENT or (
                inside_turn
                and self._active_hook_handler_executions >= MAX_HOOK_HANDLER_EXECUTIONS_PER_TURN
            ):
                results[entry.rule.hook_id] = self._hook_handler_failure_result(
                    event,
                    entry.rule.hook_id,
                    "hook_handler_budget_exhausted",
                )
                continue
            if inside_turn:
                self._active_hook_handler_executions += 1
            try:
                result, code = self._execute_hook_handler(
                    entry,
                    snapshot,
                    HookHandlerEvent(
                        event=event,
                        hook_set_id=snapshot.snapshot_id,
                        subject_id=subject_id,
                        tool_name=tool_name,
                        permission_action=permission_action,
                        source=source,
                        action_outcome=action_outcome,
                    ),
                )
            except Exception:
                result = None
                code = "hook_handler_internal_error"
            results[entry.rule.hook_id] = (
                result
                if result is not None
                else self._hook_handler_failure_result(event, entry.rule.hook_id, code)
            )
        return apply_handler_results(evaluation, results)

    @staticmethod
    def _hook_handler_failure_result(
        event: HookEvent,
        hook_id: str,
        code: str,
    ) -> HookHandlerResult:
        message = f"Hook handler [{hook_id}] failed ({code})"
        return HookHandlerResult(
            HookEffect.DENY
            if event is HookEvent.BEFORE_ACTION_AUTHORIZATION
            else HookEffect.ADVISORY,
            message,
        )

    def _execute_hook_handler(
        self,
        entry,
        snapshot: HookSetSnapshot,
        event: HookHandlerEvent,
    ) -> tuple[HookHandlerResult | None, str]:
        """Run one handler as a dangerous Action without recursive Hook dispatch."""
        if self._hook_handler_depth:
            return None, "hook_handler_recursion_denied"
        active = (
            self._active_action_lease is not None
            and self._active_action_binding is not None
            and self._active_hook_set_snapshot is not None
            and self._active_hook_set_snapshot.snapshot_id == snapshot.snapshot_id
        )
        if active:
            assert self._active_action_lease is not None
            assert self._active_action_binding is not None
            lease = self._active_action_lease
            binding = self._active_action_binding
            standalone_context_id = None
        else:
            context = self._loop.effective_context_snapshot()
            if context.hook_set_id != snapshot.snapshot_id:
                return None, "hook_handler_hook_set_stale"
            status = self._manager.status()
            lease = ActionLease(
                session_id=self._writer.session_id,
                lease_id=_uuid4_text(self._action_uuid_factory(), "Hook handler lease ID"),
                runtime_generation=status.generation,
                context_id=context.context_id,
            )
            binding = binding_from_status(status)
            standalone_context_id = context.context_id
        request_id = _uuid4_text(self._action_uuid_factory(), "Hook handler request ID")
        tool_use_id = f"hook-handler-{request_id}"
        try:
            prepared = self._hook_runner.prepare(entry, event, tool_use_id=tool_use_id)
        except HookHandlerPreparationError as error:
            return None, str(error)
        identity = ActionIdentity(
            request_id=request_id,
            tool_use_id=tool_use_id,
            tool_name=HOOK_HANDLER_ACTION_NAME,
            arguments=prepared.identity_arguments,
            action=PermissionAction.DANGEROUS,
            workspace_fingerprint=self._session_store.workspace_fingerprint,
            lease=lease,
            precondition=prepared.precondition,
        )
        preview = build_metadata_preview(
            action_digest=identity.digest,
            kind=ApprovalPreviewKind.HOOK_HANDLER,
        )
        execution: HookHandlerExecution | None = None

        def revalidate(current: ActionIdentity) -> ActionIdentity:
            if active:
                self._assert_action_lease(lease)
            else:
                current_context = self._loop.effective_context_snapshot()
                if (
                    self._writer.session_id != lease.session_id
                    or self._manager.status().generation != lease.runtime_generation
                    or current_context.context_id != standalone_context_id
                    or current_context.hook_set_id != snapshot.snapshot_id
                ):
                    raise RuntimeError("Hook handler action lease is stale")
            return current

        def execute(_current: ActionIdentity) -> ActionExecutionResult:
            nonlocal execution
            execution = self._hook_runner.execute(
                prepared,
                cancellation=self._active_cancellation,
            )
            return ActionExecutionResult(
                execution.tool_result,
                execution.outcome,
                execution.result_code,
                execution.audit_message,
            )

        self._hook_handler_depth += 1
        try:
            coordinated = ActionCoordinator(
                writer=self._writer,
                approval_handler=self._approval_handler,
                uuid_factory=self._action_uuid_factory,
            ).run(
                identity=identity,
                binding=binding,
                permission_mode=self._permission_mode,
                approval_mode=self._approval_mode,
                revalidate=revalidate,
                execute=execute,
                approval_preview=preview,
            )
        finally:
            self._hook_handler_depth -= 1
        if not coordinated.executed:
            if coordinated.permission_result.decision.value == "deny":
                return None, coordinated.permission_result.reason.value
            if coordinated.approval_resolution is ApprovalResolution.REJECT:
                return None, "hook_handler_approval_rejected"
            return None, "hook_handler_approval_cancelled"
        if execution is None or execution.handler_result is None:
            return None, coordinated.result_code or "hook_handler_execution_failed"
        return execution.handler_result, execution.result_code

    def _run_lifecycle_hook_handlers(
        self,
        event: HookEvent,
        subject_id: str,
        hook_audit: HookAuditLedger,
        *,
        event_sink: PromptEventSink | None = None,
    ) -> None:
        """Run observation handlers only after their authoritative event commits."""
        entry = next((item for item in reversed(hook_audit.entries) if item.event is event), None)
        if entry is None:
            return
        try:
            snapshot = (
                self._active_hook_set_snapshot
                if self._active_hook_set_snapshot is not None
                and self._active_hook_set_snapshot.snapshot_id == entry.hook_set_id
                else self._hook_store.snapshot()
            )
            if snapshot.snapshot_id != entry.hook_set_id:
                return
            base = evaluate_lifecycle_event(snapshot, event=event)
            if not any(item.rule.handler is not None for item in base.matched_entries):
                return
            resolved = self._resolve_hook_handlers(
                base,
                snapshot,
                event=event,
                subject_id=subject_id,
            )
            handler_matches = tuple(
                match
                for match in resolved.matches
                if snapshot.get(match.hook_id).rule.handler is not None
            )
            if handler_matches:
                self._emit_prompt_event(
                    event_sink,
                    HookLifecycleObserved(
                        event=event,
                        hook_set_id=snapshot.snapshot_id,
                        result=aggregate_hook_effect(handler_matches),
                        matched_hook_ids=tuple(match.hook_id for match in handler_matches),
                        advisory=resolved.advisory_text,
                    ),
                )
        except Exception:
            return

    def _task_hook_audit(
        self,
        event: HookEvent,
        task_id: str,
        *,
        event_sink: PromptEventSink | None = None,
    ) -> HookAuditLedger:
        """Evaluate one Task lifecycle event against one exact current Hook snapshot."""
        snapshot = self._hook_store.snapshot()
        evaluation = evaluate_lifecycle_event(snapshot, event=event)
        ledger = HookAuditLedger(
            (
                evaluation.audit_entry(
                    event=event,
                    hook_set_id=snapshot.snapshot_id,
                    subject_id=task_id,
                ),
            )
        )
        static_matches = tuple(
            match
            for match in evaluation.matches
            if snapshot.get(match.hook_id).rule.handler is None
        )
        if static_matches:
            self._emit_prompt_event(
                event_sink,
                HookLifecycleObserved(
                    event=event,
                    hook_set_id=snapshot.snapshot_id,
                    result=aggregate_hook_effect(static_matches),
                    matched_hook_ids=tuple(match.hook_id for match in static_matches),
                    advisory=evaluation.advisory_text,
                ),
            )
        return ledger

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

    def inspect_project_instructions(self) -> ProjectInstructionsSnapshot | None:
        """Read current instruction metadata without provider or Session effects."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._project_instructions_loader.load()

    def inspect_skills(self) -> SkillActivationInspection:
        """Inspect retained Skill activations and their effective action intersection."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            snapshot = self._loop.effective_context_snapshot()
            tool_set = self._loop.tool_set_snapshot_for_effective_history(
                snapshot.effective_history
            )
            action_tools = tuple(
                contract.name
                for contract in tool_set.contracts
                if contract.execution_kind
                in {ToolExecutionKind.HOST_ACTION, ToolExecutionKind.MCP_REMOTE}
            )
            assert snapshot.skill_inventory_id is not None
            return SkillActivationInspection(
                inventory_id=snapshot.skill_inventory_id,
                active=active_skills_from_history(snapshot.effective_history),
                action_tools=action_tools,
            )

    def inspect_skill_inventory(
        self,
    ) -> tuple[
        SkillInventorySnapshot,
        tuple[tuple[SkillSourceKind, Path], ...],
    ]:
        """Return one current immutable inventory and its exact configured roots."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._skill_inventory_loader.load(), self._skill_inventory_loader.roots

    def fetch_skill_candidate(self, url: str) -> SkillCandidateInfo:
        """Fetch one public Skill package into inactive workspace quarantine."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            if self._permission_mode is not PermissionMode.DANGER_FULL_ACCESS:
                raise RuntimeError("Skill fetch requires danger-full-access mode")
            return self._skill_candidate_store.fetch(url)

    def list_skill_candidates(self) -> tuple[SkillCandidateInfo, ...]:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._skill_candidate_store.list()

    def inspect_skill_candidate(self, candidate_id: str) -> SkillCandidateInfo:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._skill_candidate_store.inspect(candidate_id)

    def reject_skill_candidate(self, candidate_id: str) -> SkillCandidateInfo:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._skill_candidate_store.reject(candidate_id)

    def install_skill_candidate(
        self, candidate_id: str, *, scope: str | None = None
    ) -> SkillCandidateInfo:
        """Install an explicitly selected candidate without changing this Turn's snapshot."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            if self._permission_mode is PermissionMode.READ_ONLY:
                raise RuntimeError("Skill installation is denied in read-only mode")
            self._skill_candidate_store.install(candidate_id, scope=scope)
            return self._skill_candidate_store.inspect(candidate_id)

    def status(self) -> RuntimeStatus:
        self._ensure_open()
        return self._manager.status()

    def project_status(self) -> ProjectStatus:
        """Return local runtime, Session, policy, budget, and sandbox readiness facts."""
        with self._lock:
            self._ensure_open()
            return ProjectStatus(
                runtime=self._manager.status(),
                session=self._writer.info,
                usage=self._manager.usage_snapshot(),
                permission_mode=self._permission_mode,
                approval_mode=self._approval_mode,
                sandbox=self._run_command.inspect_sandbox(),
            )

    def inspect_command_sandbox(self) -> CommandSandboxInspection:
        """Verify the production sandbox with one fixed, non-user-controlled command."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            return self._run_command.inspect_sandbox(verify_activation=True)

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
                if not self._mcp_process_manager.close():
                    raise RuntimeError("MCP process cleanup is incomplete")
                return
            self._ensure_not_compacting()
            self._closed = True
            mcp_cleanup_complete = self._mcp_process_manager.close()
            try:
                self._writer.close()
            finally:
                self._manager.close()
            if not mcp_cleanup_complete:
                raise RuntimeError("MCP process cleanup is incomplete")

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
        compare_files,
        git_blame,
        git_refs,
        json_query,
        checksum_file,
        archive_list,
        *,
        commit_turn,
        project_instructions_factory,
        tool_registry_factory=None,
        skill_inventory_factory=None,
        hook_set_factory=None,
        skill_resource_reader=None,
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
            compare_files=compare_files,
            git_blame=git_blame,
            git_refs=git_refs,
            json_query=json_query,
            checksum_file=checksum_file,
            archive_list=archive_list,
            initial_history=state.history,
            initial_effective_history=state.effective_history,
            initial_effective_summary=state.effective_summary,
            initial_effective_source=state.effective_source,
            commit_turn=commit_turn,
            project_instructions_factory=project_instructions_factory,
            **(
                {}
                if tool_registry_factory is None
                else {"tool_registry_factory": tool_registry_factory}
            ),
            **(
                {}
                if skill_inventory_factory is None
                else {"skill_inventory_factory": skill_inventory_factory}
            ),
            **({} if hook_set_factory is None else {"hook_set_factory": hook_set_factory}),
            **(
                {}
                if skill_resource_reader is None
                else {"skill_resource_reader": skill_resource_reader}
            ),
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
            self._compare_files,
            self._git_blame,
            self._git_refs,
            self._json_query,
            self._checksum_file,
            self._archive_list,
            commit_turn=lambda turn: self._commit_turn(writer, turn),
            project_instructions_factory=self._project_instructions_loader.load,
            tool_registry_factory=self._mcp_catalog_service.registry_snapshot,
            skill_inventory_factory=self._skill_inventory_loader.load,
            hook_set_factory=self._hook_store.snapshot,
            skill_resource_reader=self._skill_inventory_loader.read_resource,
        )
        loop.install_action_dispatcher(self._dispatch_action)
        loop.install_task_control_dispatcher(
            _COMMIT_CONTROL_TOOL_NAMES, self._dispatch_task_control
        )
        loop.install_tool_set_transition_dispatcher(self._transition_tool_set)
        return loop

    def _transition_tool_set(
        self,
        prepared: PreparedAgentTurn,
        snapshot: ToolSetSnapshot,
    ) -> PreparedAgentTurn:
        """Retire the active lease and issue an exact replacement for a later ToolSet epoch."""
        lease = prepared.action_lease
        if lease is None:
            raise RuntimeError("active ToolSet transition requires an action lease")
        self._assert_action_lease(lease)
        if self._active_turn_runtime is None or self._active_action_binding is None:
            raise RuntimeError("active ToolSet transition requires a pinned runtime")
        has_mcp = any(
            contract.source.kind is ExtensionSourceKind.MCP
            for contract in prepared.registry_snapshot.contracts
        )
        if has_mcp:
            current = self._mcp_catalog_service.registry_snapshot()
            if (
                current.snapshot_id != prepared.registry_snapshot.snapshot_id
                or current.generation != prepared.registry_snapshot.generation
            ):
                raise RuntimeError("MCP registry changed before ToolSet promotion")
        advanced = prepared.retire_action_lease().advance_tool_set(snapshot)
        new_lease = ActionLease(
            session_id=self._writer.session_id,
            lease_id=_uuid4_text(self._action_uuid_factory(), "action lease ID"),
            runtime_generation=self._active_turn_runtime.status.generation,
            context_id=advanced.context.context_id,
        )
        transitioned = advanced.with_action_lease(new_lease)
        self._active_action_lease = new_lease
        self._active_turn_context = transitioned.context
        self._active_tool_set_snapshot = transitioned.tool_set_snapshot
        return transitioned

    def _dispatch_task_control(
        self, request: ToolUse, context_id: str
    ) -> TaskControlDispatchResult:
        """Validate one commit-coupled proposal without mutating durable state."""
        scope = self._active_task_control_scope
        if request.name == SKILL_PROPOSE_CREATE_TOOL_NAME:
            if scope is not None:
                raise RuntimeError("Skill authoring is unavailable inside a Task Stage")
            proposal = SkillCreationProposal.from_request(request, context_id)
            return TaskControlDispatchResult(
                ToolDispatchResult(
                    ToolResult(request.tool_use_id, skill_proposal_receipt(proposal)),
                    ToolEventStatus.SUCCEEDED,
                    "skill_candidate_proposed",
                ),
                proposal,
            )
        if request.name == SKILL_ACCEPT_CREATE_TOOL_NAME:
            if scope is not None:
                raise RuntimeError("Skill installation is unavailable inside a Task Stage")
            if self._permission_mode is PermissionMode.READ_ONLY:
                raise RuntimeError("Skill installation is denied in read-only mode")
            values = request.arguments.as_mapping()
            candidate_id = values.get("candidate_id")
            if not isinstance(candidate_id, str):
                raise RuntimeError("Skill candidate ID is invalid")
            candidate = self._skill_candidate_store.inspect(candidate_id)
            if candidate.owner_session_id != self._writer.session_id:
                raise RuntimeError("Skill candidate belongs to another Session")
            install = SkillInstallRequest.from_request(
                request,
                context_id,
                expected_fingerprint=candidate.manifest.fingerprint,
            )
            return TaskControlDispatchResult(
                ToolDispatchResult(
                    ToolResult(
                        request.tool_use_id,
                        '{"skill_install":"accepted_for_turn_commit"}',
                    ),
                    ToolEventStatus.SUCCEEDED,
                    "skill_install_requested",
                ),
                install,
            )
        if request.name == TASK_PROPOSE_START_TOOL_NAME:
            if scope is not None:
                raise RuntimeError("Task admission cannot be proposed from an active Task Stage")
            admission = TaskAdmissionProposal.from_request(request, context_id)
            return TaskControlDispatchResult(
                ToolDispatchResult(
                    ToolResult(request.tool_use_id, task_admission_receipt(admission)),
                    ToolEventStatus.SUCCEEDED,
                    "task_admission_proposed",
                ),
                admission,
            )
        lifecycle_kind = TASK_LIFECYCLE_KIND_BY_TOOL.get(request.name)
        if lifecycle_kind is not None:
            if scope is not None:
                raise RuntimeError("Task lifecycle requests are unavailable inside a Task Stage")
            lifecycle = self._prepare_task_lifecycle_request(
                request,
                context_id=context_id,
                kind=lifecycle_kind,
            )
            return TaskControlDispatchResult(
                ToolDispatchResult(
                    ToolResult(
                        request.tool_use_id,
                        '{"lifecycle_request":"accepted_for_turn_commit"}',
                    ),
                    ToolEventStatus.SUCCEEDED,
                    "task_lifecycle_requested",
                ),
                lifecycle,
            )
        if scope is None or request.name not in scope.allowed_tool_names:
            raise RuntimeError("Task control request is outside an active compatible Stage")
        kind = TASK_PROPOSAL_KIND_BY_TOOL.get(request.name)
        if kind is None:
            raise RuntimeError("Task control request name is unsupported")
        proposal = TaskControlProposal(
            kind=kind,
            task_id=scope.task_id,
            stage_id=scope.stage_id,
            stage_number=scope.stage_number,
            context_id=context_id,
            tool_use_id=request.tool_use_id,
            payload=request.arguments,
        )
        return TaskControlDispatchResult(
            ToolDispatchResult(
                ToolResult(request.tool_use_id, '{"proposal":"received"}'),
                ToolEventStatus.SUCCEEDED,
                "task_proposal_received",
            ),
            proposal,
        )

    def _prepare_task_lifecycle_request(
        self,
        request: ToolUse,
        *,
        context_id: str,
        kind: TaskLifecycleKind,
    ) -> TaskLifecycleRequest:
        values = request.arguments.as_mapping()
        if kind is TaskLifecycleKind.ACCEPT_ADMISSION:
            admission_id = values.get("admission_id")
            if not isinstance(admission_id, str):
                raise RuntimeError("Task admission acceptance ID is invalid")
            admission = self.inspect_task_admission(admission_id)
            if admission.outcome is not None:
                raise RuntimeError("Task admission proposal is no longer pending")
            preview = self.preview_task_admission_acceptance(admission_id)
            subject_id = preview.proposal.admission_id
            expected_identity = preview.confirmation_sha256
        else:
            task_id = values.get("task_id")
            if not isinstance(task_id, str):
                raise RuntimeError("Task lifecycle Task ID is invalid")
            task = self.inspect_task(canonical_task_id(task_id))
            if task.owner_session_id != self._writer.session_id:
                raise RuntimeError("Task lifecycle request belongs to another Session")
            if task.terminal_outcome is not None:
                raise RuntimeError("Task lifecycle request cannot mutate a terminal Task")
            subject_id = task.task_id
            if kind is TaskLifecycleKind.ACCEPT_PLAN:
                plan = task.latest_plan
                if plan is None:
                    raise RuntimeError("Task has no plan proposal to accept")
                if plan.accepted:
                    raise RuntimeError("latest Task plan is already accepted")
                expected_identity = plan.plan_id
            else:
                with self._task_store.open(task.task_id) as writer:
                    if writer.state.terminal is not None:
                        raise RuntimeError("Task lifecycle request cannot mutate a terminal Task")
                    proposal = writer.state.current_completion_proposal
                    if proposal is None:
                        raise RuntimeError("Task has no current completion proposal")
                    unresolved_non_human = tuple(
                        index
                        for index, criterion in enumerate(writer.state.criteria, start=1)
                        if index not in writer.state.verified_criteria
                        and criterion.kind is not AcceptanceCriterionKind.HUMAN
                    )
                    if unresolved_non_human:
                        joined = ",".join(str(index) for index in unresolved_non_human)
                        raise RuntimeError(
                            "Task completion still has unverified non-human criteria: " + joined
                        )
                    expected_identity = proposal.stage_id
        return TaskLifecycleRequest(
            kind=kind,
            context_id=context_id,
            tool_use_id=request.tool_use_id,
            subject_id=subject_id,
            expected_identity=expected_identity,
        )

    def _capture_task_admission_proposal(self, proposal: TaskProposal) -> None:
        """Apply ordinary-Prompt coordination only after the Session Turn commits."""
        if type(proposal) is SkillCreationProposal:
            candidate = self._skill_candidate_store.create_generated(
                proposal,
                owner_session_id=self._writer.session_id,
                turn_sequence=len(self._writer.state.turns),
            )
            self._emit_prompt_event(
                self._active_event_sink,
                SkillCandidateCommitted.from_candidate(candidate),
            )
            return
        if type(proposal) is SkillInstallRequest:
            self._commit_skill_install_request(proposal)
            return
        if type(proposal) is TaskAdmissionProposal:
            self._emit_prompt_event(
                self._active_event_sink,
                TaskAdmissionProposed.from_proposal(proposal),
            )
            return
        if type(proposal) is not TaskLifecycleRequest:
            raise RuntimeError("ordinary Prompt produced a Stage-scoped Task proposal")
        self._commit_task_lifecycle_request(proposal)

    def _commit_skill_install_request(self, request: SkillInstallRequest) -> None:
        """Revalidate committed causality and install one exact generated candidate."""
        committed = next(
            (
                record
                for record in reversed(self._writer.state.records)
                if isinstance(record, TurnCommitted)
                and any(
                    getattr(item, "tool_use_id", None) == request.tool_use_id
                    or (
                        isinstance(item, AssistantToolBatch)
                        and any(tool.tool_use_id == request.tool_use_id for tool in item.tool_uses)
                    )
                    for item in record.items
                )
            ),
            None,
        )
        if committed is None:
            raise RuntimeError("Skill install request has no committed Session Turn")
        recovered = recover_task_control_request(
            CommittedTurn(
                committed.items,
                committed.items[0],
                committed.items[-1],
                committed.tool_ledger,
                committed.hook_audit,
            ),
            tool_name=SKILL_ACCEPT_CREATE_TOOL_NAME,
        )
        if recovered.tool_use_id != request.tool_use_id:
            raise RuntimeError("Skill install request does not match committed causality")
        candidate = self._skill_candidate_store.inspect(request.candidate_id)
        if (
            candidate.owner_session_id != self._writer.session_id
            or candidate.manifest.fingerprint != request.expected_fingerprint
        ):
            raise RuntimeError("Skill candidate changed before installation commit")
        result = self._skill_candidate_store.install(
            request.candidate_id,
            expected_owner_session_id=self._writer.session_id,
        )
        self._emit_prompt_event(
            self._active_event_sink,
            SkillCandidateInstalled.from_result(request.candidate_id, result),
        )

    def _commit_task_lifecycle_request(self, request: TaskLifecycleRequest) -> None:
        tool_name = {
            TaskLifecycleKind.ACCEPT_ADMISSION: TASK_ACCEPT_ADMISSION_TOOL_NAME,
            TaskLifecycleKind.ACCEPT_PLAN: TASK_ACCEPT_PLAN_TOOL_NAME,
            TaskLifecycleKind.CONFIRM_COMPLETION: TASK_CONFIRM_COMPLETION_TOOL_NAME,
        }[request.kind]
        committed = next(
            (
                record
                for record in reversed(self._writer.state.records)
                if isinstance(record, TurnCommitted)
                and any(
                    getattr(item, "tool_use_id", None) == request.tool_use_id
                    or (
                        isinstance(item, AssistantToolBatch)
                        and any(tool.tool_use_id == request.tool_use_id for tool in item.tool_uses)
                    )
                    for item in record.items
                )
            ),
            None,
        )
        if committed is None:
            raise RuntimeError("Task lifecycle request has no committed Session Turn")
        recovered = recover_task_control_request(
            CommittedTurn(
                committed.items,
                committed.items[0],
                committed.items[-1],
                committed.tool_ledger,
                committed.hook_audit,
            ),
            tool_name=tool_name,
        )
        if recovered.tool_use_id != request.tool_use_id:
            raise RuntimeError("Task lifecycle request does not match committed causality")

        max_stages: int | None = None
        if request.kind is TaskLifecycleKind.ACCEPT_ADMISSION:
            admission = self.inspect_task_admission(request.subject_id)
            if admission.outcome is not None:
                raise RuntimeError("Task admission changed before lifecycle commit")
            preview = self.preview_task_admission_acceptance(request.subject_id)
            if preview.confirmation_sha256 != request.expected_identity:
                raise RuntimeError("Task admission candidate changed before lifecycle commit")
            task = self.accept_task_admission(
                request.subject_id,
                confirmation_sha256=request.expected_identity,
            )
            max_stages = min(16, task.budget.max_stages)
        elif request.kind is TaskLifecycleKind.ACCEPT_PLAN:
            task = self.inspect_task(request.subject_id)
            plan = task.latest_plan
            if (
                task.owner_session_id != self._writer.session_id
                or task.terminal_outcome is not None
                or plan is None
                or plan.accepted
                or plan.plan_id != request.expected_identity
            ):
                raise RuntimeError("Task plan changed before lifecycle commit")
            task = self.accept_task_plan(task.task_id)
            max_stages = min(16, task.budget.max_stages - len(task.stages))
            if max_stages < 1:
                raise RuntimeError("Task has no remaining Stage budget after plan acceptance")
        else:
            with self._task_store.open(request.subject_id) as writer:
                if writer.state.header.owner_session_id != self._writer.session_id:
                    raise RuntimeError("Task completion belongs to another Session")
                if writer.state.terminal is not None:
                    raise RuntimeError("Task completed before lifecycle commit")
                completion = writer.state.current_completion_proposal
                if completion is None or completion.stage_id != request.expected_identity:
                    raise RuntimeError("Task completion proposal changed before lifecycle commit")
                unresolved_non_human = tuple(
                    index
                    for index, criterion in enumerate(writer.state.criteria, start=1)
                    if index not in writer.state.verified_criteria
                    and criterion.kind is not AcceptanceCriterionKind.HUMAN
                )
                if unresolved_non_human:
                    raise RuntimeError("Task completion evidence changed before lifecycle commit")
                evidence = (
                    f"session={self._writer.session_id};turn-record={committed.sequence};"
                    f"tool-use={request.tool_use_id};request={request.request_sha256}"
                )
                for index, criterion in enumerate(writer.state.criteria, start=1):
                    if (
                        criterion.kind is AcceptanceCriterionKind.HUMAN
                        and index not in writer.state.verified_criteria
                    ):
                        writer.verify_acceptance(
                            index,
                            evidence,
                            source=AcceptanceVerificationSource.USER,
                        )
                if len(writer.state.verified_criteria) != len(writer.state.criteria):
                    raise RuntimeError("Task completion criteria are not fully verified")
                terminated_hook_audit = self._task_hook_audit(
                    HookEvent.TASK_TERMINATED,
                    request.subject_id,
                    event_sink=self._active_event_sink,
                )
                writer.terminate(
                    TaskTerminalOutcome.COMPLETED,
                    hook_audit=terminated_hook_audit,
                )
                self._run_lifecycle_hook_handlers(
                    HookEvent.TASK_TERMINATED,
                    request.subject_id,
                    terminated_hook_audit,
                    event_sink=self._active_event_sink,
                )
                task = writer.info

        self._emit_prompt_event(
            self._active_event_sink,
            TaskLifecycleCommitted(request.kind.value, task.task_id, max_stages),
        )

    def _dispatch_action(self, request: ToolUse, lease: ActionLease) -> ToolDispatchResult:
        """Prepare and run one model tool request through the exact Host boundary."""
        self._assert_action_lease(lease)
        tool_set = self._active_tool_set_snapshot
        if tool_set is None:
            raise RuntimeError("prepared tool set snapshot is unavailable")
        contract = tool_set.contract(request.name)
        if contract.execution_kind not in {
            ToolExecutionKind.HOST_ACTION,
            ToolExecutionKind.MCP_REMOTE,
        }:
            raise RuntimeError("tool contract does not use an action boundary")
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
        prepared_web_search: PreparedWebSearch | None = None
        prepared_web_fetch: PreparedWebFetch | None = None
        prepared_move_directory: PreparedMoveDirectory | None = None
        prepared_download: PreparedDownloadFile | None = None
        prepared_mcp: PreparedMcpCall | None = None
        if contract.execution_kind is ToolExecutionKind.MCP_REMOTE:
            catalog = self._mcp_catalog_service.snapshot()
            candidate = next(
                (
                    item
                    for item in catalog.accepted
                    if item.qualified_name == request.name
                    and item.contract is not None
                    and item.contract.contract_id == contract.contract_id
                ),
                None,
            )
            if candidate is None:
                return _invalid_tool_request(
                    request,
                    McpCallPreparationError("MCP tool catalog identity is stale"),
                )
            try:
                prepared_mcp = prepare_mcp_call(
                    candidate,
                    catalog.catalog_id,
                    request.arguments.as_mapping(),
                )
            except McpCallPreparationError as error:
                return _invalid_tool_request(request, error)
            if len(contract.permission_actions) != 1:
                raise RuntimeError("MCP tool contract must classify exactly one permission action")
            action = contract.permission_actions[0]
            precondition = ActionPrecondition.expected_configuration(
                prepared_mcp.precondition_sha256
            )
        elif request.name in {
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
            COMPARE_FILES_TOOL_NAME,
            GIT_BLAME_TOOL_NAME,
            GIT_REFS_TOOL_NAME,
            JSON_QUERY_TOOL_NAME,
            CHECKSUM_FILE_TOOL_NAME,
            ARCHIVE_LIST_TOOL_NAME,
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
        elif request.name == WEB_SEARCH_TOOL_NAME:
            if not any(source in {"brave", "tavily"} for source in self._search_source_order):
                return _invalid_tool_request(
                    request,
                    WebSearchPreparationError(
                        "independent web search is not an active primary or fallback source"
                    ),
                )
            try:
                prepared_web_search = self._web_search.prepare(request)
            except WebSearchPreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_web_search.action
            precondition = prepared_web_search.precondition
        elif request.name == WEB_FETCH_TOOL_NAME:
            try:
                prepared_web_fetch = self._web_fetch.prepare(request)
            except WebFetchPreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_web_fetch.action
            precondition = prepared_web_fetch.precondition
        elif request.name == MOVE_DIRECTORY_TOOL_NAME:
            try:
                prepared_move_directory = self._move_directory.prepare(request)
            except MoveDirectoryPreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_move_directory.action
            precondition = prepared_move_directory.precondition
        elif request.name == DOWNLOAD_FILE_TOOL_NAME:
            try:
                prepared_download = self._download_file.prepare(request)
            except DownloadFilePreparationError as error:
                return _invalid_tool_request(request, error)
            action = prepared_download.action
            precondition = prepared_download.precondition
        else:
            action = PermissionAction.UNKNOWN
            precondition = ActionPrecondition.none()

        if not contract.permits(action):
            raise RuntimeError("tool action classification violates its extension contract")

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
        hook_set = self._active_hook_set_snapshot
        if hook_set is None:
            raise RuntimeError("prepared Hook set snapshot is unavailable")
        hook_evaluation = evaluate_before_action_authorization(
            hook_set,
            tool_name=request.name,
            action=action,
            arguments=request.arguments,
            source_kind=contract.source.kind,
        )
        hook_source = "mcp" if contract.source.kind is ExtensionSourceKind.MCP else "builtin"
        hook_evaluation = self._resolve_hook_handlers(
            hook_evaluation,
            hook_set,
            event=HookEvent.BEFORE_ACTION_AUTHORIZATION,
            subject_id=request.tool_use_id,
            tool_name=request.name,
            permission_action=action.value,
            source=hook_source,
        )
        preauthorization_audit = hook_evaluation.audit_entry(
            event=HookEvent.BEFORE_ACTION_AUTHORIZATION,
            hook_set_id=hook_set.snapshot_id,
            subject_id=request.tool_use_id,
            tool_name=request.name,
            permission_action=action.value,
            source=hook_source,
        )
        if hook_evaluation.denied_by is not None:
            after_evaluation = evaluate_after_action(
                hook_set,
                tool_name=request.name,
                action=action,
                arguments=request.arguments,
                source_kind=contract.source.kind,
                outcome=HookActionOutcome.DENIED,
            )
            after_evaluation = self._resolve_hook_handlers(
                after_evaluation,
                hook_set,
                event=HookEvent.AFTER_ACTION,
                subject_id=request.tool_use_id,
                tool_name=request.name,
                permission_action=action.value,
                source=hook_source,
                action_outcome=HookActionOutcome.DENIED,
            )
            content = (
                f"Hook denied action [{hook_evaluation.denied_by}]: {hook_evaluation.deny_message}"
            )
            if after_evaluation.advisory_text is not None:
                content += f"\n\n{after_evaluation.advisory_text}"
            dispatch = ToolDispatchResult(
                ToolResult(
                    request.tool_use_id,
                    content,
                    is_error=True,
                ),
                ToolEventStatus.DENIED,
                "hook_denied",
                hook_audit=HookAuditLedger(
                    (
                        preauthorization_audit,
                        after_evaluation.audit_entry(
                            event=HookEvent.AFTER_ACTION,
                            hook_set_id=hook_set.snapshot_id,
                            subject_id=request.tool_use_id,
                            tool_name=request.name,
                            permission_action=action.value,
                            source=hook_source,
                            action_outcome=HookActionOutcome.DENIED,
                        ),
                    )
                ),
            )
            self._active_hook_audit_entries.extend(dispatch.hook_audit.entries)
            return dispatch
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
        elif prepared_web_search is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.WEB_SEARCH,
                backend=prepared_web_search.backend.value,
            )
        elif prepared_web_fetch is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.WEB_FETCH,
            )
        elif prepared_move_directory is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.DIRECTORY_MOVE,
            )
        elif prepared_download is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.FILE_DOWNLOAD,
            )
        elif prepared_mcp is not None:
            mcp_entry = self._mcp_store.get_server(
                prepared_mcp.candidate.configured_name,
                scope=prepared_mcp.candidate.scope,
            )
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.MCP_TOOL,
                transport=mcp_entry.configuration.transport.value,
            )
        coordinator = ActionCoordinator(
            writer=self._writer,
            approval_handler=self._approval_handler,
            uuid_factory=self._action_uuid_factory,
        )
        command_observation: RunCommandExecutionObservation | None = None
        mcp_observation: McpRuntimeExecution | None = None

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
            if prepared_web_search is not None:
                refreshed = self._web_search.revalidate(prepared_web_search)
                return replace(current, precondition=refreshed)
            if prepared_web_fetch is not None:
                refreshed = self._web_fetch.revalidate(prepared_web_fetch)
                return replace(current, precondition=refreshed)
            if prepared_move_directory is not None:
                refreshed = self._move_directory.refresh_precondition(prepared_move_directory)
                return replace(current, precondition=refreshed)
            if prepared_download is not None:
                refreshed = self._download_file.refresh_precondition(prepared_download)
                return replace(current, precondition=refreshed)
            if prepared_mcp is not None:
                catalog = self._mcp_catalog_service.snapshot()
                candidate = next(
                    (
                        item
                        for item in catalog.accepted
                        if item.qualified_name == request.name
                        and item.contract is not None
                        and item.contract.contract_id == contract.contract_id
                    ),
                    None,
                )
                if candidate is None:
                    return replace(
                        current,
                        precondition=ActionPrecondition.expected_configuration("0" * 64),
                    )
                refreshed = prepare_mcp_call(
                    candidate,
                    catalog.catalog_id,
                    request.arguments.as_mapping(),
                )
                return replace(
                    current,
                    precondition=ActionPrecondition.expected_configuration(
                        refreshed.precondition_sha256
                    ),
                )
            return current

        def execute(current: ActionIdentity) -> ActionExecutionResult:
            nonlocal command_observation, mcp_observation
            self._assert_action_lease(lease)
            if cancellation is not None:
                cancellation.check()
            if prepared_mcp is not None:
                mcp_result = self._mcp_process_manager.execute(
                    prepared_mcp,
                    cancellation=cancellation,
                    notification_sink=lambda kind: self._emit_prompt_event(
                        self._active_event_sink,
                        McpNotificationActivityReceived(kind),
                    ),
                )
                mcp_observation = mcp_result
                if mcp_result.catalog_invalidated:
                    self._mcp_catalog_service.invalidate()
                outcome = {
                    McpRuntimeOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    McpRuntimeOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    McpRuntimeOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[mcp_result.outcome]
                return ActionExecutionResult(
                    tool_result=ToolResult(
                        request.tool_use_id,
                        mcp_result.content,
                        is_error=mcp_result.is_error,
                        truncated=mcp_result.truncated,
                    ),
                    outcome=outcome,
                    result_code=mcp_result.result_code,
                    audit_message=mcp_result.audit_message,
                )
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
            elif request.name == COMPARE_FILES_TOOL_NAME:
                result = self._compare_files.execute(request)
            elif request.name == GIT_BLAME_TOOL_NAME:
                result = self._git_blame.execute(request)
            elif request.name == GIT_REFS_TOOL_NAME:
                result = self._git_refs.execute(request)
            elif request.name == JSON_QUERY_TOOL_NAME:
                result = self._json_query.execute(request)
            elif request.name == CHECKSUM_FILE_TOOL_NAME:
                result = self._checksum_file.execute(request)
            elif request.name == ARCHIVE_LIST_TOOL_NAME:
                result = self._archive_list.execute(request)
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
            elif request.name == WEB_SEARCH_TOOL_NAME and prepared_web_search is not None:
                search_result = self._web_search.execute_detailed(prepared_web_search)
                outcome = {
                    WebSearchOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    WebSearchOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    WebSearchOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[search_result.outcome]
                return ActionExecutionResult(
                    tool_result=search_result.tool_result,
                    outcome=outcome,
                    result_code=search_result.result_code,
                    audit_message=search_result.audit_message,
                )
            elif request.name == WEB_FETCH_TOOL_NAME and prepared_web_fetch is not None:
                fetch_result = self._web_fetch.execute_detailed(prepared_web_fetch)
                outcome = {
                    WebFetchOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    WebFetchOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    WebFetchOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[fetch_result.outcome]
                return ActionExecutionResult(
                    tool_result=fetch_result.tool_result,
                    outcome=outcome,
                    result_code=fetch_result.result_code,
                    audit_message=fetch_result.audit_message,
                )
            elif request.name == MOVE_DIRECTORY_TOOL_NAME and prepared_move_directory is not None:
                move_directory_result = self._move_directory.execute_detailed(
                    prepared_move_directory
                )
                outcome = {
                    MoveDirectoryOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    MoveDirectoryOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    MoveDirectoryOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[move_directory_result.outcome]
                return ActionExecutionResult(
                    tool_result=move_directory_result.tool_result,
                    outcome=outcome,
                    result_code=move_directory_result.result_code,
                    audit_message=move_directory_result.audit_message,
                )
            elif request.name == DOWNLOAD_FILE_TOOL_NAME and prepared_download is not None:
                download_result = self._download_file.execute_detailed(prepared_download)
                outcome = {
                    DownloadFileOutcome.SUCCEEDED: ActionExecutionOutcome.SUCCEEDED,
                    DownloadFileOutcome.FAILED: ActionExecutionOutcome.FAILED,
                    DownloadFileOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
                }[download_result.outcome]
                return ActionExecutionResult(
                    tool_result=download_result.tool_result,
                    outcome=outcome,
                    result_code=download_result.result_code,
                    audit_message=download_result.audit_message,
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
            approval_mode=(
                ApprovalMode.ASK if hook_evaluation.requires_ask else self._approval_mode
            ),
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
            else (
                _mcp_result_details(mcp_observation)
                if coordinated.executed and mcp_observation is not None
                else None
            )
        )
        tool_result = coordinated.tool_result
        action_outcome = HookActionOutcome(status.value)
        after_evaluation = evaluate_after_action(
            hook_set,
            tool_name=request.name,
            action=action,
            arguments=request.arguments,
            source_kind=contract.source.kind,
            outcome=action_outcome,
        )
        after_evaluation = self._resolve_hook_handlers(
            after_evaluation,
            hook_set,
            event=HookEvent.AFTER_ACTION,
            subject_id=request.tool_use_id,
            tool_name=request.name,
            permission_action=action.value,
            source=hook_source,
            action_outcome=action_outcome,
        )
        advisories = tuple(
            text
            for text in (
                hook_evaluation.advisory_text,
                after_evaluation.advisory_text,
            )
            if text is not None
        )
        if advisories:
            tool_result = replace(
                tool_result,
                content=(f"{tool_result.content.rstrip(chr(10))}\n\n" + "\n".join(advisories)),
            )
        dispatch = ToolDispatchResult(
            tool_result,
            status,
            result_code,
            result_details,
            HookAuditLedger(
                (
                    preauthorization_audit,
                    after_evaluation.audit_entry(
                        event=HookEvent.AFTER_ACTION,
                        hook_set_id=hook_set.snapshot_id,
                        subject_id=request.tool_use_id,
                        tool_name=request.name,
                        permission_action=action.value,
                        source=hook_source,
                        action_outcome=action_outcome,
                    ),
                )
            ),
        )
        self._active_hook_audit_entries.extend(dispatch.hook_audit.entries)
        return dispatch

    def _assert_action_lease(self, lease: ActionLease) -> None:
        active = self._active_action_lease
        context = self._active_turn_context
        tool_set = self._active_tool_set_snapshot
        hook_set = self._active_hook_set_snapshot
        if (
            active != lease
            or context is None
            or tool_set is None
            or hook_set is None
            or context.context_id != lease.context_id
            or context.tool_set_id != tool_set.snapshot_id
            or context.hook_set_id != hook_set.snapshot_id
        ):
            raise RuntimeError("prepared action lease is stale")
        current_context = self._loop.effective_context_snapshot_with_project_instructions(
            context.project_instructions,
            tool_set_snapshot=tool_set,
            hook_set_snapshot=hook_set,
        )
        if (
            self._writer.session_id != lease.session_id
            or self._manager.status().generation != lease.runtime_generation
            or current_context.context_id != lease.context_id
        ):
            raise RuntimeError("prepared action lease no longer matches runtime context")

    def _commit_turn(self, writer: SessionWriter, turn: CommittedTurn) -> None:
        if writer is not self._writer:
            raise SessionStoreError("conversation session changed before turn commit")
        usage_cursor = self._active_usage_cursor
        runtime = self._active_turn_runtime
        if usage_cursor is None or runtime is None:
            raise SessionStoreError("provider usage cursor is unavailable before turn commit")
        if not writer.state.turns and writer.state.latest_name is None:
            self._commit_first_turn_with_title(writer, turn, runtime, usage_cursor)
            return
        self._append_committed_turn(writer, turn, usage_cursor)

    def _commit_first_turn_with_title(
        self,
        writer: SessionWriter,
        turn: CommittedTurn,
        runtime: TurnRuntimeSnapshot,
        usage_cursor: int,
    ) -> None:
        del runtime
        prepared = self._active_prepared_session_title
        if prepared is None:
            prepared = _PreparedSessionTitle(
                fallback_session_title(self._active_session_title_source_text or turn.user.text),
                SessionNameSource.FALLBACK,
                SessionTitleFallbackReason.INVOCATION_BUDGET,
            )
        fallback_base = prepared.name
        for _ in range(MAX_RECORDS + 1):
            try:
                self._append_committed_turn(
                    writer,
                    turn,
                    usage_cursor,
                    session_name=prepared.name,
                    session_name_source=prepared.source,
                    session_title_fallback_reason=prepared.fallback_reason,
                )
                if prepared.fallback_reason is not None:
                    self._emit_prompt_event(
                        self._active_event_sink,
                        SessionTitleFallbackApplied(prepared.fallback_reason),
                    )
                return
            except SessionNameConflictError:
                prepared = _PreparedSessionTitle(
                    self._available_session_title(fallback_base, force_number=True),
                    SessionNameSource.FALLBACK,
                    SessionTitleFallbackReason.DUPLICATE_TITLE,
                )
        raise SessionStoreError("could not commit a unique Session name")

    def _prepare_first_turn_session_title(
        self,
        runtime: TurnRuntimeSnapshot,
        usage_cursor: int,
        title_source_text: str,
    ) -> int:
        if self._active_prepared_session_title is not None:
            return 0
        rejected: list[str] = []
        fallback_base: str | None = None
        fallback_reason = SessionTitleFallbackReason.INVOCATION_BUDGET
        attempts = 0
        for attempt in range(1, SESSION_TITLE_MAX_ATTEMPTS + 1):
            used = len(self._manager.usage_since(usage_cursor, kind=ProviderInvocationKind.TURN))
            if used >= MAX_PROVIDER_INVOCATIONS_PER_TURN:
                break
            if self._active_cancellation is not None:
                self._active_cancellation.check()
            self._emit_prompt_event(
                self._active_event_sink,
                SessionTitleGenerationStarted(attempt, SESSION_TITLE_MAX_ATTEMPTS),
            )
            attempts += 1
            try:
                response = runtime.generate_session_title(
                    build_session_title_request(
                        title_source_text,
                        rejected_titles=tuple(rejected),
                    )
                )
            except ProviderAdapterError as error:
                fallback_reason = (
                    SessionTitleFallbackReason.PROVIDER_OUTPUT_LIMIT
                    if error.failure.kind == ProviderFailureKind.OUTPUT_LIMIT
                    else SessionTitleFallbackReason.PROVIDER_FAILURE
                )
                break
            except Exception:
                fallback_reason = SessionTitleFallbackReason.PROVIDER_FAILURE
                break
            if self._active_cancellation is not None:
                self._active_cancellation.check()
            try:
                candidate = parse_session_title_response(response)
            except SessionTitleCandidateError:
                fallback_reason = SessionTitleFallbackReason.INVALID_CANDIDATE
                continue
            fallback_base = candidate
            if self._session_title_exists(candidate):
                fallback_reason = SessionTitleFallbackReason.DUPLICATE_TITLE
                if candidate not in rejected:
                    rejected.append(candidate)
                continue
            prepared = _PreparedSessionTitle(candidate, SessionNameSource.MODEL)
            self._install_prepared_session_title(prepared)
            return attempts

        base = fallback_base or fallback_session_title(title_source_text)
        prepared = _PreparedSessionTitle(
            self._available_session_title(base),
            SessionNameSource.FALLBACK,
            fallback_reason,
        )
        self._install_prepared_session_title(prepared)
        return attempts

    def _install_prepared_session_title(self, prepared: _PreparedSessionTitle) -> None:
        self._active_prepared_session_title = prepared
        self._emit_prompt_event(
            self._active_event_sink,
            SessionTitlePrepared(prepared.name, prepared.source),
        )

    def _session_title_exists(self, name: str) -> bool:
        key = name.casefold()
        return any(
            info.session_id != self._writer.session_id and info.name.casefold() == key
            for info in self._session_store.list()
        )

    def _available_session_title(self, base: str, *, force_number: bool = False) -> str:
        for number in range(1, MAX_RECORDS + 2):
            candidate = base if number == 1 else numbered_session_title(base, number)
            if force_number and number == 1:
                continue
            if not self._session_title_exists(candidate):
                return candidate
        raise SessionStoreError("could not allocate a unique Session name")

    def _append_committed_turn(
        self,
        writer: SessionWriter,
        turn: CommittedTurn,
        usage_cursor: int,
        *,
        session_name: str | None = None,
        session_name_source: SessionNameSource | None = None,
        session_title_fallback_reason: SessionTitleFallbackReason | None = None,
    ) -> None:
        self._emit_prompt_event(self._active_event_sink, TurnCommitStarted())
        writer.append_turn(
            turn.items,
            binding=binding_from_status(self._manager.status()),
            tool_ledger=turn.tool_ledger,
            hook_audit=turn.hook_audit,
            provider_usage=self._manager.usage_since(
                usage_cursor,
                kind=ProviderInvocationKind.TURN,
            ),
            session_name=session_name,
            session_name_source=session_name_source,
            session_title_fallback_reason=session_title_fallback_reason,
        )
        terminal = next(
            (
                entry
                for entry in reversed(turn.hook_audit.entries)
                if entry.event is HookEvent.TURN_COMMITTED
            ),
            None,
        )
        if terminal is not None:
            self._run_lifecycle_hook_handlers(
                HookEvent.TURN_COMMITTED,
                terminal.subject_id,
                turn.hook_audit,
                event_sink=self._active_event_sink,
            )

    def _record_runtime_switch(self, result: RuntimeSwitchResult, reason: str) -> None:
        self._web_search.disable_sources()
        self._native_search_options = NativeSearchRuntimeOptions()
        self._manager.set_native_search_options(self._native_search_options)
        self._manager.set_native_search_enabled(result.status.native_search_available)
        self._search_source_order = ("provider",) if result.status.native_search_available else ()
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
        hook_audit = HookAuditLedger()
        hook_set = self._active_hook_set_snapshot
        if hook_set is not None:
            evaluation = evaluate_lifecycle_event(
                hook_set,
                event=HookEvent.TURN_FAILED,
            )
            hook_audit = HookAuditLedger(
                (
                    *self._active_hook_audit_entries,
                    evaluation.audit_entry(
                        event=HookEvent.TURN_FAILED,
                        hook_set_id=hook_set.snapshot_id,
                        subject_id=self._writer.session_id,
                    ),
                )
            )
            static_matches = tuple(
                match
                for match in evaluation.matches
                if hook_set.get(match.hook_id).rule.handler is None
            )
            if static_matches:
                self._emit_prompt_event(
                    self._active_event_sink,
                    HookLifecycleObserved(
                        event=HookEvent.TURN_FAILED,
                        hook_set_id=hook_set.snapshot_id,
                        result=aggregate_hook_effect(static_matches),
                        matched_hook_ids=tuple(match.hook_id for match in static_matches),
                        advisory=evaluation.advisory_text,
                    ),
                )
        try:
            self._writer.turn_failed(
                binding=binding,
                failure_kind=type(error).__name__,
                message=_safe_failure_message(error),
                provider_usage=provider_usage,
                hook_audit=hook_audit,
            )
        except SessionStoreError:
            pass
        else:
            self._run_lifecycle_hook_handlers(
                HookEvent.TURN_FAILED,
                self._writer.session_id,
                hook_audit,
                event_sink=self._active_event_sink,
            )

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
                if record.schema_version >= TURN_COMMITTED_USAGE_SCHEMA_VERSION
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


def _task_control_names_for_stage(kind: StageKind) -> tuple[str, ...]:
    if kind is StageKind.PLANNING:
        return (TASK_PROPOSE_PLAN_TOOL_NAME, TASK_REPORT_BLOCKER_TOOL_NAME)
    if kind is StageKind.REFLECTION:
        return (TASK_REPORT_REFLECTION_TOOL_NAME, TASK_REPORT_BLOCKER_TOOL_NAME)
    if kind in {StageKind.EXECUTION, StageKind.CORRECTION}:
        return (TASK_REPORT_BLOCKER_TOOL_NAME, TASK_PROPOSE_COMPLETION_TOOL_NAME)
    raise TaskRuntimeError("Task Stage kind is invalid")


def _task_tool_names_for_stage(kind: StageKind) -> tuple[str, ...]:
    controls = _task_control_names_for_stage(kind)
    if kind is StageKind.PLANNING:
        return _TASK_PLANNING_READ_TOOL_NAMES + controls
    if kind is StageKind.REFLECTION:
        return controls
    return ORDINARY_TOOL_NAMES + controls


def _resolve_task_stage_response(
    response: str,
    *,
    kind: StageKind,
    proposal: TaskControlProposal | None,
) -> ParsedTaskResponse:
    if proposal is None:
        try:
            return parse_task_response(response, kind=kind)
        except TaskRuntimeError:
            if kind in {StageKind.EXECUTION, StageKind.CORRECTION} and not any(
                signal in response
                for signal in (
                    "TASK_COMPLETION_PROPOSAL:",
                    "TASK_PLAN_JSON:",
                    "TASK_REFLECTION_JSON:",
                )
            ):
                return ParsedTaskResponse(response, False, None)
            raise
    return _resolve_task_control_payload(
        response,
        kind=kind,
        proposal_kind=proposal.kind,
        values=proposal.payload.as_mapping(),
    )


def _resolve_task_control_payload(
    response: str,
    *,
    kind: StageKind,
    proposal_kind: TaskProposalKind,
    values: dict[str, object],
) -> ParsedTaskResponse:
    expected = {
        StageKind.PLANNING: {TaskProposalKind.PLAN, TaskProposalKind.BLOCKER},
        StageKind.REFLECTION: {TaskProposalKind.REFLECTION, TaskProposalKind.BLOCKER},
        StageKind.EXECUTION: {TaskProposalKind.COMPLETION, TaskProposalKind.BLOCKER},
        StageKind.CORRECTION: {TaskProposalKind.COMPLETION, TaskProposalKind.BLOCKER},
    }
    if proposal_kind not in expected[kind]:
        raise TaskRuntimeError("Task proposal kind does not match its Stage")
    if proposal_kind is TaskProposalKind.PLAN:
        try:
            steps = canonical_plan_steps(values["steps"])
        except (KeyError, ValueError) as error:
            raise TaskRuntimeError(f"Task plan proposal is invalid: {error}") from None
        return ParsedTaskResponse(response, False, steps)
    if proposal_kind is TaskProposalKind.REFLECTION:
        try:
            recommendation = ReflectionRecommendation(values["recommendation"])
            summary = values["summary"]
            next_objective = values["next_objective"]
            if not isinstance(summary, str):
                raise ValueError("summary is invalid")
            if next_objective is not None:
                next_objective = canonical_stage_objective(next_objective)
        except (KeyError, TypeError, ValueError) as error:
            raise TaskRuntimeError(f"Task reflection proposal is invalid: {error}") from None
        return ParsedTaskResponse(
            response,
            False,
            None,
            TaskReflectionProposal(recommendation, summary, next_objective),
        )
    if proposal_kind is TaskProposalKind.BLOCKER:
        try:
            category = TaskBlockerCategory(values["category"])
            summary = values["summary"]
            if not isinstance(summary, str):
                raise ValueError("summary is invalid")
        except (KeyError, TypeError, ValueError) as error:
            raise TaskRuntimeError(f"Task blocker proposal is invalid: {error}") from None
        return ParsedTaskResponse(
            response,
            False,
            None,
            blocker=TaskBlockerProposal(category, summary),
        )
    if values:
        raise TaskRuntimeError("Task completion proposal arguments are invalid")
    return ParsedTaskResponse(response, True, None)


def _latest_turn_after(
    records: tuple[SessionRecord, ...],
    record_sequence: int,
) -> TurnCommitted | None:
    matches = tuple(
        record
        for record in records
        if isinstance(record, TurnCommitted) and record.sequence > record_sequence
    )
    if len(matches) > 1:
        raise TaskRuntimeError("Task Stage produced more than one committed Session Turn")
    return matches[0] if matches else None


def _task_result_from_info(
    task: TaskInfo,
    response: str,
    stage: TaskStageInfo,
) -> TaskStageExecutionResult:
    if stage.turn_number is None or stage.turn_record_sequence is None:
        raise TaskRuntimeError("committed Task Stage is missing Session Turn evidence")
    return TaskStageExecutionResult(
        task=task,
        stage_number=stage.stage_number,
        response=response,
        completion_proposed=task.status is TaskStatus.COMPLETION_PROPOSED,
        session_turn_number=stage.turn_number,
        session_turn_record_sequence=stage.turn_record_sequence,
        blocker=(
            TaskBlockerProposal(task.latest_blocker.category, task.latest_blocker.summary)
            if task.latest_blocker is not None and task.latest_blocker.stage_id == stage.stage_id
            else None
        ),
    )


def _task_stage_failure_reason(error: BaseException) -> StageFailureReason:
    if isinstance(error, TurnCancelled):
        return StageFailureReason.CANCELLED
    if isinstance(error, ProviderAdapterError):
        return StageFailureReason.PROVIDER_ERROR
    if isinstance(error, SessionStoreError):
        return StageFailureReason.TURN_NOT_COMMITTED
    return StageFailureReason.HOST_ERROR


def _task_stage_usage(
    provider_usage: tuple[ProviderInvocationUsage, ...],
    tool_usage: ToolAttemptUsage,
) -> StageUsage:
    input_tokens = 0
    output_tokens = 0
    known = 0
    unknown = 0
    for invocation in provider_usage:
        if invocation.usage is None:
            unknown += 1
        else:
            known += 1
            input_tokens += invocation.usage.input_tokens
            output_tokens += invocation.usage.output_tokens
    return StageUsage(
        provider_invocations=len(provider_usage),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        known_token_invocations=known,
        unknown_token_invocations=unknown,
        tool_requests=tool_usage.requested,
        tool_admitted=tool_usage.admitted,
        tool_dispatched=tool_usage.dispatched,
        tool_succeeded=tool_usage.succeeded,
        tool_unsuccessful=tool_usage.unsuccessful,
    )


def _action_tool_names(snapshot: ToolSetSnapshot) -> tuple[str, ...]:
    return tuple(
        contract.name
        for contract in snapshot.contracts
        if contract.execution_kind in {ToolExecutionKind.HOST_ACTION, ToolExecutionKind.MCP_REMOTE}
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


def _mcp_result_details(observation: McpRuntimeExecution) -> ToolResultDetails:
    generation = (
        "unavailable"
        if observation.process_generation is None
        else str(observation.process_generation)
    )
    duration = "unavailable" if observation.duration_ms is None else f"{observation.duration_ms}ms"
    reused = (
        "unavailable"
        if observation.process_reused is None
        else str(observation.process_reused).lower()
    )
    blocks = "unavailable" if observation.result_blocks is None else str(observation.result_blocks)
    notifications = observation.notifications
    notification_summary = (
        f"notifications=progress:{notifications.progress_count},"
        f"message:{notifications.message_count},"
        f"list-changed:{notifications.tools_list_changed_count},"
        f"resources-list-changed:{notifications.resources_list_changed_count},"
        f"resource-updated:{notifications.resource_updated_count},"
        f"prompts-list-changed:{notifications.prompts_list_changed_count},"
        f"ignored:{notifications.ignored_count}"
    )
    compact = (
        f"process-generation={generation} reused={reused} duration={duration} "
        f"blocks={blocks} cleanup={str(observation.cleanup_complete).lower()} "
        f"{notification_summary} catalog-invalidated="
        f"{str(observation.catalog_invalidated).lower()}"
    )
    return ToolResultDetails(
        compact,
        (
            f"process_generation: {generation}",
            f"process_reused: {reused}",
            f"duration: {duration}",
            f"result_blocks: {blocks}",
            notification_summary,
            f"catalog_invalidated: {str(observation.catalog_invalidated).lower()} "
            f"cleanup_complete: {str(observation.cleanup_complete).lower()}",
        ),
    )


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
