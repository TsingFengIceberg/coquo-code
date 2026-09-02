"""Project-facing durable conversation facade for one workspace runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import os
import time
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from coquo.agent.loop import AgentLoop, PreparedAgentTurn
from coquo.agent.child_control import ChildControlDispatchResult
from coquo.agent.team_control import TeamControlDispatchResult
from coquo.agent.runtime import (
    AgentRuntime,
    AgentRuntimeCallbacks,
    AgentRuntimeFactory,
    AgentRuntimeServices,
    AgentTurnRequest,
)
from coquo.observability import ObservationContext, ObservationStream
from coquo.child_run_records import ChildRunDelegated, ChildRunStatus
from coquo.child_run_store import ChildRunInfo, ChildRunStore, ChildRunStoreError
from coquo.session_records import workspace_fingerprint
from coquo.memory import MemoryAccessContext
from coquo.memory_config import MemoryConfigStore
from coquo.memory_store import MemoryStoreError
from coquo.memory_observability import MemoryObservationLedger
from coquo.memory_recall import MemoryRecallService, empty_memory_recall
from coquo.memory_extraction import MemoryCandidateExtractor
from coquo.evolution import EvolutionController, EvolutionError, EvolutionOutcome, EvolutionTarget
from coquo.skill_evolution import SkillEvolutionService
from coquo.memory_evolution import MemoryEvolutionService
from coquo.strategy_evolution import StrategyEvolutionService
from coquo.child_supervisor import ChildRunSupervisor
from coquo.team_records import TeamAssignmentPhase, TeamMemberState, TeamStatus
from coquo.team_store import TeamInfo, TeamStore, TeamStoreError
from coquo.team_messaging import TeamMessagingService
from coquo.team_records import TeamWorkItemState
from coquo.team_work import TeamWorkList, TeamWorkService
from coquo.team_service import TeamAssignmentInfo, TeamAssignmentService, TeamRecoveryResult
from coquo.team_records import TeamScheduleSource, TeamScheduleState
from coquo.team_schedule import TeamScheduleError, TeamScheduleService
from coquo.worktree_service import WorktreeDiff, WorktreeService, WorktreeServiceError
from coquo.worktree_store import WorktreeStoreError
from coquo.team_supervisor import (
    TeamScheduleNotification,
    TeamScheduleSupervisor,
    TeamScheduleSupervisorError,
)
from coquo.child_runtime import (
    CHILD_DEADLINE_SECONDS,
    CHILD_MAX_OUTPUT_TOKENS,
    CHILD_MAX_PROVIDER_INVOCATIONS,
    CHILD_MAX_TOOL_REQUESTS,
    CHILD_TOOL_NAMES,
    build_child_runtime_spec,
    build_child_runtime_spec_from_binding,
    child_role_descriptor,
    role_allowed_by_parent,
    TEAM_CHILD_ROLE_CONTRACT_VERSION,
    child_tool_set,
)
from coquo.agent.task_control import (
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
from coquo.core.task_admission import (
    TASK_PROPOSE_START_TOOL_NAME,
    TaskAdmissionOutcome,
    TaskAdmissionProposal,
    canonical_task_admission_id,
    task_admission_receipt,
)
from coquo.core.skill_authoring import (
    SKILL_ACCEPT_CREATE_TOOL_NAME,
    SKILL_AUTHORING_CONTROL_TOOL_NAMES,
    SKILL_PROPOSE_CREATE_TOOL_NAME,
    SkillCreationProposal,
    SkillInstallRequest,
    skill_proposal_receipt,
)
from coquo.agent.tool_events import (
    AgentPromptEvent,
    HookLifecycleObserved,
    McpNotificationActivityReceived,
    ProviderInvocationFinished,
    ProviderInvocationOutcome,
    ProviderInvocationPurpose,
    ProviderInvocationStarted,
    TaskAdmissionProposed,
    TaskLifecycleCommitted,
    SkillCandidateCommitted,
    SkillCandidateInstalled,
    ToolDispatchResult,
    ToolEventStatus,
    ToolResultDetails,
)
from coquo.core.action_coordinator import (
    ActionCoordinator,
    ActionExecutionResult,
    ApprovalHandler,
    ApprovalResolution,
)
from coquo.core.delegation_approval import (
    DelegationApprovalIdentity,
    DelegationApprovalPreview,
    DelegationApprovalRequest,
    delegation_decision_sha256,
)
from coquo.core.team_approval import (
    TeamControlApprovalIdentity,
    TeamControlApprovalPreview,
    TeamControlApprovalRequest,
    canonical_team_arguments_sha256,
    team_control_decision_sha256,
)
from coquo.core.approval_preview import (
    ApprovalPreview,
    ApprovalPreviewKind,
    build_file_change_preview,
    build_metadata_preview,
)
from coquo.core.actions import ActionIdentity, ActionLease, ActionPrecondition
from coquo.worktree_integration import WorktreeIntegrationError, WorktreeIntegrationService
from coquo.core.compaction import (
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
    plan_compaction,
)
from coquo.core.cancellation import TurnCancellation, TurnCancelled
from coquo.core.session_title import (
    SESSION_TITLE_MAX_ATTEMPTS,
    SessionTitleCandidateError,
    build_session_title_request,
    fallback_session_title,
    numbered_session_title,
    parse_session_title_response,
)
from coquo.core.contracts import (
    AssistantToolBatch,
    CommittedTurn,
    ConversationItem,
    ConversationProvider,
    ConversationTurn,
    ToolArguments,
    ToolAttemptUsage,
    ToolResult,
    ToolUse,
)
from coquo.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from coquo.hooks import (
    HookEvaluation,
    HookSetSnapshot,
    HookStore,
    apply_handler_results,
    evaluate_after_action,
    evaluate_before_action_authorization,
    evaluate_lifecycle_event,
)
from coquo.hook_runner import (
    HOOK_HANDLER_ACTION_NAME,
    HookHandlerEvent,
    HookHandlerExecution,
    HookHandlerPreparationError,
    HookRunner,
)
from coquo.core.hook_contracts import (
    HookActionOutcome,
    HookAuditEntry,
    HookAuditLedger,
    HookEffect,
    HookEvent,
    HookHandlerResult,
    aggregate_hook_effect,
)
from coquo.core.project_instructions import (
    ProjectInstructionsLoader,
    ProjectInstructionsSnapshot,
)
from coquo.core.orchestration import ProviderFailureKind
from coquo.core.effective_context import (
    COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
    EffectiveContextSnapshot,
)
from coquo.core.extensions import ExtensionSourceKind, ToolExecutionKind, ToolSetSnapshot
from coquo.core.execution_scope import ExecutionScope
from coquo.recursive_orchestration import (
    RecursiveNode,
    RecursiveNodeKind,
    RecursiveNodeStatus,
    RecursiveOrchestrationError,
    RecursiveOrchestrationStore,
)
from coquo.skills import (
    ActiveSkill,
    SkillActivationInspection,
    SkillInventoryLoader,
    SkillInventorySnapshot,
    SkillSourceKind,
    active_skills_from_history,
)
from coquo.skill_candidates import (
    SkillCandidateInfo,
    SkillCandidateStore,
)
from coquo.mcp import (
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
from coquo.mcp.client import McpProbeResult
from coquo.providers.manager import (
    CompactionRuntimeSnapshot,
    CurrentTargetContextAssessment,
    OutputBudgetUpdateResult,
    ReasoningEffortUpdateResult,
    RuntimeProviderManager,
    RuntimeStatus,
    RuntimeSwitchAuditError,
    RuntimeSwitchResult,
    TurnRuntimeSnapshot,
)
from coquo.providers.reliability import (
    ProviderReliabilityBudgetError,
    ProviderReliabilityPolicy,
)
from coquo.providers.definitions import ReasoningEffort
from coquo.providers.native_search import (
    NativeSearchContextSize,
    NativeSearchMode,
    NativeSearchRuntimeOptions,
    canonical_native_search_domain,
)
from coquo.providers.errors import ProviderAdapterError
from coquo.providers.profile import NamedProviderProfile
from coquo.providers.profile_store import ProviderProfileStore
from coquo.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    ContextPreflightError,
    rejects_context_transition,
    raise_for_context_fit,
)
from coquo.providers.usage import (
    ProviderInvocationKind,
    ProviderInvocationUsage,
    ProviderUsageTotals,
    RuntimeUsageSnapshot,
)
from coquo.session_records import (
    ActionAuditState,
    ActionAuditStatus,
    ActionExecutionOutcome,
    BindingSnapshot,
    ChildDelegationDecided,
    TeamControlDecided,
    ApprovalAuditOutcome,
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
from coquo.tools.child_control import (
    CHILD_CANCEL_TOOL_NAME,
    CHILD_CONTROL_TOOL_NAMES,
    CHILD_SPAWN_TOOL_NAME,
    CHILD_STATUS_TOOL_NAME,
    CHILD_WAIT_TOOL_NAME,
    parse_child_control,
)
from coquo.tools.team_control import (
    TEAM_ADD_MEMBER_TOOL_NAME,
    TEAM_CLOSE_TOOL_NAME,
    TEAM_CONTROL_TOOL_NAMES,
    TEAM_CREATE_TOOL_NAME,
    TEAM_MESSAGE_READ_TOOL_NAME,
    TEAM_MESSAGE_SEND_TOOL_NAME,
    TEAM_MESSAGE_SHOW_TOOL_NAME,
    TEAM_SCHEDULE_START_TOOL_NAME,
    TEAM_SCHEDULE_WAIT_TOOL_NAME,
    TEAM_STATUS_TOOL_NAME,
    MAX_TEAM_RESULT_BYTES,
    TEAM_WORK_CREATE_TOOL_NAME,
    TEAM_WORK_REVIEW_TOOL_NAME,
    parse_team_control,
)
from coquo.tools.team_worktree_integrate import (
    TEAM_WORKTREE_INTEGRATE_TOOL_NAME,
    parse_team_worktree_integrate,
)
from coquo.session_store import (
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
from coquo.task_records import (
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
from coquo.task_runtime import (
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
from coquo.task_store import (
    TaskAdmissionAcceptancePreview,
    TaskAdmissionConfiguration,
    TaskAppendCommitError,
    TaskInfo,
    TaskStageInfo,
    TaskStore,
    TaskStoreError,
    TaskWriter,
)
from coquo.task_verification import (
    AcceptanceCheckResult,
    TaskVerificationResult,
    build_task_review_request,
    parse_task_review_response,
    run_host_acceptance_checks,
)
from coquo.tools.delete_directory import (
    DELETE_DIRECTORY_TOOL_NAME,
    DeleteDirectoryOutcome,
    DeleteDirectoryPreparationError,
    DeleteDirectoryTool,
    PreparedDeleteDirectory,
)
from coquo.tools.archive_list import ARCHIVE_LIST_TOOL_NAME, ArchiveListTool
from coquo.tools.checksum_file import CHECKSUM_FILE_TOOL_NAME, ChecksumFileTool
from coquo.tools.compare_files import COMPARE_FILES_TOOL_NAME, CompareFilesTool
from coquo.tools.catalog import (
    ORDINARY_PROMPT_TOOL_NAMES,
    ORDINARY_TOOL_NAMES,
    BROWSER_ACTION_TOOL_NAME,
    registry_snapshot_with_browser,
    registry_snapshot_with_memory,
)
from coquo.browser import (
    BrowserAction,
    BrowserAutomation,
    BrowserAutomationError,
    BrowserObservation,
)
from coquo.tools.browser import parse_browser_action
from coquo.tools.task_coordination import (
    TASK_ACCEPT_ADMISSION_TOOL_NAME,
    TASK_ACCEPT_PLAN_TOOL_NAME,
    TASK_CONFIRM_COMPLETION_TOOL_NAME,
    TASK_CONTROL_TOOL_NAMES,
    TASK_PROPOSE_COMPLETION_TOOL_NAME,
    TASK_PROPOSE_PLAN_TOOL_NAME,
    TASK_REPORT_BLOCKER_TOOL_NAME,
    TASK_REPORT_REFLECTION_TOOL_NAME,
)

from coquo.tools.copy_file import (
    COPY_FILE_TOOL_NAME,
    CopyFileOutcome,
    CopyFilePreparationError,
    CopyFileTool,
    PreparedCopyFile,
)
from coquo.tools.delete_file import (
    DELETE_FILE_TOOL_NAME,
    DeleteFileOutcome,
    DeleteFilePreparationError,
    DeleteFileTool,
    PreparedDeleteFile,
)
from coquo.tools.edit_file import (
    EDIT_FILE_TOOL_NAME,
    EditFileOutcome,
    EditFilePreparationError,
    EditFileTool,
    PreparedEditFile,
)
from coquo.tools.download_file import (
    DOWNLOAD_FILE_TOOL_NAME,
    DownloadFileOutcome,
    DownloadFilePreparationError,
    DownloadFileTool,
    PreparedDownloadFile,
)
from coquo.tools.glob import GlobTool
from coquo.tools.grep import GrepTool
from coquo.tools.grep_regex import GREP_REGEX_TOOL_NAME, GrepRegexTool
from coquo.tools.git_diff import (
    GIT_DIFF_TOOL_NAME,
    GitDiffScope,
    GitDiffSnapshot,
    GitDiffTool,
)
from coquo.tools.git_blame import GIT_BLAME_TOOL_NAME, GitBlameTool
from coquo.tools.git_refs import GIT_REFS_TOOL_NAME, GitRefsTool
from coquo.tools.git_log import (
    DEFAULT_GIT_LOG_LIMIT,
    GIT_LOG_TOOL_NAME,
    GitLogSnapshot,
    GitLogTool,
)
from coquo.tools.git_show import (
    GIT_SHOW_TOOL_NAME,
    GitShowSnapshot,
    GitShowTool,
)
from coquo.tools.git_status import (
    GIT_STATUS_TOOL_NAME,
    GitStatusSnapshot,
    GitStatusTool,
)
from coquo.tools.list_directory import (
    LIST_DIRECTORY_TOOL_NAME,
    ListDirectoryTool,
)
from coquo.tools.list_tree import LIST_TREE_TOOL_NAME, ListTreeTool
from coquo.tools.json_query import JSON_QUERY_TOOL_NAME, JsonQueryTool
from coquo.tools.mkdir import (
    MKDIR_TOOL_NAME,
    MkdirOutcome,
    MkdirPreparationError,
    MkdirTool,
    PreparedMkdir,
)
from coquo.tools.move_file import (
    MOVE_FILE_TOOL_NAME,
    MoveFileOutcome,
    MoveFilePreparationError,
    MoveFileTool,
    PreparedMoveFile,
)
from coquo.tools.move_directory import (
    MOVE_DIRECTORY_TOOL_NAME,
    MoveDirectoryOutcome,
    MoveDirectoryPreparationError,
    MoveDirectoryTool,
    PreparedMoveDirectory,
)
from coquo.tools.patch_file import (
    PATCH_FILE_TOOL_NAME,
    PatchFileOutcome,
    PatchFilePreparationError,
    PatchFileTool,
    PreparedPatchFile,
)
from coquo.tools.read_file import READ_FILE_TOOL_NAME, ReadFileTool
from coquo.tools.read_file_lines import READ_FILE_LINES_TOOL_NAME, ReadFileLinesTool
from coquo.tools.glob import GLOB_TOOL_NAME
from coquo.tools.grep import GREP_TOOL_NAME
from coquo.tools.run_command import (
    CommandSandboxInspection,
    RUN_COMMAND_TOOL_NAME,
    PreparedRunCommand,
    RunCommandExecutionObservation,
    RunCommandOutcome,
    RunCommandPreparationError,
    RunCommandStreamObservation,
    RunCommandTool,
)
from coquo.tools.write_file import (
    WRITE_FILE_TOOL_NAME,
    PreparedWriteFile,
    WriteFileOutcome,
    WriteFilePreparationError,
    WriteFileTool,
)
from coquo.tools.stat_path import STAT_PATH_TOOL_NAME, StatPathTool
from coquo.tools.web_search import (
    WEB_SEARCH_TOOL_NAME,
    PreparedWebSearch,
    WebSearchOutcome,
    WebSearchPreparationError,
    WebSearchSourceConfiguration,
    WebSearchTool,
)
from coquo.tools.web_fetch import (
    WEB_FETCH_TOOL_NAME,
    PreparedWebFetch,
    WebFetchOutcome,
    WebFetchPreparationError,
    WebFetchTool,
)
from coquo.tools.memory import (
    MEMORY_TOOL_NAMES,
    MemoryTool,
    PreparedMemoryAction,
)
from coquo.tools.catalog import (
    MAX_PROVIDER_INVOCATIONS_PER_TURN,
    MAX_TOOL_CALLS_PER_RESPONSE,
    MAX_TOOL_REQUESTS_PER_TURN,
    TOOL_CATALOG,
    TOOL_REGISTRY_SNAPSHOT,
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


@dataclass(frozen=True)
class ChildStartObservation:
    """Host-observed identity for one Child background submission."""

    child: ChildRunInfo
    backend: str
    submission_id: str | None = None
    queue_state: str | None = None
    worker_started: bool = False
    worker_pid: int | None = None
    launch_error: str | None = None


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
class TurnCommitCompleted:
    """Signal that the Session turn append has crossed its durable commit point."""


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
    | TurnCommitCompleted
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
        browser: BrowserAutomation | None = None,
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
        child_mode: bool = False,
        execution_scope: ExecutionScope | None = None,
        child_action_names: tuple[str, ...] = (),
        child_depth: int = 0,
        parent_child_run_id: str | None = None,
        root_child_run_id: str | None = None,
        child_delegation_allowed: bool = False,
        current_child_run_id: str | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self._execution_scope = execution_scope or ExecutionScope.authority(self.workspace)
        if self._execution_scope.authority_workspace != self.workspace:
            raise ValueError("execution scope authority does not match Session workspace")
        self.execution_workspace = self._execution_scope.execution_root
        self._child_mode = child_mode
        if type(child_depth) is not int or not 0 <= child_depth <= 2:
            raise ValueError("Child delegation depth is invalid")
        if not child_mode and child_depth != 0:
            raise ValueError("Child delegation depth requires child mode")
        if child_depth == 2 or (child_mode and child_depth == 1 and not child_delegation_allowed):
            child_delegation_allowed = False
        if child_depth == 2 and not parent_child_run_id:
            raise ValueError("grandchild sessions require a parent Child Run ID")
        if child_depth == 2 and not root_child_run_id:
            raise ValueError("grandchild sessions require a root Child Run ID")
        if child_depth == 0 and current_child_run_id is not None:
            raise ValueError("current Child Run ID requires a Child session")
        if child_depth > 0 and not current_child_run_id:
            raise ValueError("Child sessions require their current Child Run ID")
        self._child_depth = child_depth
        self._parent_child_run_id = parent_child_run_id
        self._root_child_run_id = root_child_run_id
        self._child_delegation_allowed = child_delegation_allowed
        self._current_child_run_id = current_child_run_id
        if type(child_action_names) is not tuple or any(
            type(name) is not str or not name for name in child_action_names
        ):
            raise ValueError("Child action whitelist is invalid")
        if len(set(child_action_names)) != len(child_action_names):
            raise ValueError("Child action whitelist contains duplicate names")
        if not child_mode and child_action_names:
            raise ValueError("Child action whitelist requires child mode")
        self._child_action_names = child_action_names
        self._store = store
        self._manager = manager
        self._session_store = session_store
        self._task_store = TaskStore(self.workspace)
        self._evolution = EvolutionController(self.workspace)
        self._child_run_store = ChildRunStore(self.workspace)
        # The recursive ledger is a Host-owned projection.  Child, Team, and
        # Task ledgers remain the execution source of truth; this store only
        # binds their durable identities into one replayable lineage tree.
        self._recursive_store = RecursiveOrchestrationStore(self.workspace)
        self._team_store = TeamStore(self.workspace)
        self._team_service = TeamAssignmentService(self.workspace)
        self._team_messaging = TeamMessagingService(self.workspace)
        self._team_work = TeamWorkService(self.workspace)
        self._team_schedule_service = TeamScheduleService(self.workspace)
        self._worktree_integration = WorktreeIntegrationService(self.workspace)
        self._memory_observations = MemoryObservationLedger()
        self._memory_tool = MemoryTool(
            self.workspace,
            observation_ledger=self._memory_observations,
        )
        self._child_supervisor: ChildRunSupervisor | None = None
        self._team_schedule_supervisor: TeamScheduleSupervisor | None = None
        self._writer = writer
        self._read_file = read_file
        self._glob = glob
        self._grep = grep
        self._list_directory = list_directory
        self._write_file = write_file or WriteFileTool(self.execution_workspace)
        self._edit_file = edit_file or EditFileTool(self.execution_workspace)
        self._run_command = run_command or RunCommandTool(
            self.execution_workspace,
            read_only_paths=(
                (self.execution_workspace / ".git",)
                if self._execution_scope.kind == "team-worktree"
                else ()
            ),
        )
        self._hook_runner = HookRunner(self.execution_workspace, self._run_command)
        self._mkdir = mkdir or MkdirTool(self.execution_workspace)
        self._move_file = move_file or MoveFileTool(self.execution_workspace)
        self._delete_file = delete_file or DeleteFileTool(self.execution_workspace)
        self._delete_directory = delete_directory or DeleteDirectoryTool(self.execution_workspace)
        self._copy_file = copy_file or CopyFileTool(self.execution_workspace)
        self._read_file_lines = read_file_lines or ReadFileLinesTool(self.execution_workspace)
        self._stat_path = stat_path or StatPathTool(self.execution_workspace)
        self._list_tree = list_tree or ListTreeTool(self.execution_workspace)
        self._grep_regex = grep_regex or GrepRegexTool(self.execution_workspace)
        self._patch_file = patch_file or PatchFileTool(self.execution_workspace)
        self._git_status = git_status or GitStatusTool(self.execution_workspace)
        self._git_diff = git_diff or GitDiffTool(self.execution_workspace)
        self._git_log = git_log or GitLogTool(self.execution_workspace)
        self._git_show = git_show or GitShowTool(self.execution_workspace)
        self._web_search = web_search or WebSearchTool()
        self._web_fetch = web_fetch or WebFetchTool()
        self._compare_files = compare_files or CompareFilesTool(self.execution_workspace)
        self._git_blame = git_blame or GitBlameTool(self.execution_workspace)
        self._git_refs = git_refs or GitRefsTool(self.execution_workspace)
        self._json_query = json_query or JsonQueryTool(self.execution_workspace)
        self._checksum_file = checksum_file or ChecksumFileTool(self.execution_workspace)
        self._archive_list = archive_list or ArchiveListTool(self.execution_workspace)
        self._move_directory = move_directory or MoveDirectoryTool(self.execution_workspace)
        self._download_file = download_file or DownloadFileTool(self.execution_workspace)
        if browser is not None and not isinstance(browser, BrowserAutomation):
            raise ValueError("browser runtime is invalid")
        if browser is not None and child_mode:
            raise ValueError("Child sessions cannot own a browser runtime")
        self._browser = browser
        self._mcp_store = mcp_store or McpServerStore.for_workspace(self.workspace)
        self._mcp_client = mcp_client or McpClient(self.workspace)
        self._mcp_policy_store = mcp_policy_store or McpToolPolicyStore.for_workspace(
            self.workspace
        )
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
            project_instructions_loader or ProjectInstructionsLoader(self.execution_workspace)
        )
        self._skill_inventory_loader = skill_inventory_loader or SkillInventoryLoader(
            self.execution_workspace
        )
        self._skill_candidate_store = skill_candidate_store or SkillCandidateStore(
            self.execution_workspace
        )
        self._memory_scope_id = workspace_fingerprint(self.workspace)
        self._skill_evolution = (
            None
            if self._child_mode
            else SkillEvolutionService(
                self.execution_workspace,
                evolution=self._evolution,
                candidates=self._skill_candidate_store,
            )
        )
        self._memory_evolution = (
            None
            if self._child_mode
            else MemoryEvolutionService(
                self.execution_workspace,
                evolution=self._evolution,
                scope_id=self._memory_scope_id,
            )
        )
        self._strategy_evolution = (
            None
            if self._child_mode
            else StrategyEvolutionService(
                self.execution_workspace,
                evolution=self._evolution,
            )
        )
        self._hook_store = hook_store or HookStore.for_workspace(self.workspace)
        self._memory_team_scope_id: str | None = None
        self._memory_recall_service = (
            None
            if self._child_mode
            else MemoryRecallService(
                self.workspace,
                access_factory=self._memory_access_context,
                observation_ledger=self._memory_observations,
            )
        )
        self._memory_candidate_extractor = (
            None
            if self._child_mode
            else MemoryCandidateExtractor(
                self.workspace,
                access_factory=self._memory_access_context,
                observation_ledger=self._memory_observations,
            )
        )
        if type(permission_mode) is not PermissionMode:
            raise ValueError("permission mode is invalid")
        if type(approval_mode) is not ApprovalMode:
            raise ValueError("approval mode is invalid")
        self._permission_mode = permission_mode
        self._approval_mode = approval_mode
        self._approval_handler = approval_handler or _cancel_approval
        self._action_uuid_factory = action_uuid_factory
        self._restore_team_memory_scope()
        self._active_action_lease: ActionLease | None = None
        self._active_turn_context: EffectiveContextSnapshot | None = None
        self._active_observation_context: ObservationContext | None = None
        self._observation_stream = ObservationStream(
            source_id=writer.session_id,
            context=ObservationContext.new(session_id=writer.session_id),
        )
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
        self._runtime = (
            AgentRuntimeFactory.create(
                writer.state,
                self._runtime_services(),
                self._runtime_callbacks(writer),
            )
            if loop is None
            else AgentRuntime(
                loop,
                self._runtime_services(),
                self._runtime_callbacks(writer),
            )
        )
        self._loop = self._runtime.loop
        if loop is not None and (not self._child_mode or self._child_action_names):
            self._loop.install_action_dispatcher(
                self._dispatch_restricted_child_action
                if self._child_mode
                else self._dispatch_action
            )
        if loop is not None and (not self._child_mode or self._child_delegation_allowed):
            self._loop.install_task_control_dispatcher(
                _COMMIT_CONTROL_TOOL_NAMES, self._dispatch_task_control
            )
            self._loop.install_child_control_dispatcher(
                CHILD_CONTROL_TOOL_NAMES, self._dispatch_child_control
            )
            self._loop.install_tool_set_transition_dispatcher(self._transition_tool_set)
        self._startup_resume_result = startup_resume_result

    def _runtime_services(self) -> AgentRuntimeServices:
        skill_inventory_factory = (
            (lambda: SkillInventorySnapshot((), ()))
            if self._child_mode
            else self._skill_inventory_loader.load
        )
        hook_set_factory = (
            (lambda: HookSetSnapshot(())) if self._child_mode else self._hook_store.snapshot
        )
        return AgentRuntimeServices(
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
            self._project_instructions_loader.load,
            (lambda: TOOL_REGISTRY_SNAPSHOT)
            if self._child_mode
            else lambda: registry_snapshot_with_browser(
                self.workspace,
                registry_snapshot_with_memory(
                    self.workspace, self._mcp_catalog_service.registry_snapshot()
                ),
                enabled=self._browser is not None,
            ),
            skill_inventory_factory,
            hook_set_factory,
            self._skill_inventory_loader.read_resource,
            provider_manager=self._manager,
            memory_recall_factory=(
                empty_memory_recall
                if self._memory_recall_service is None
                else self._memory_recall_service.recall
            ),
        )

    def _memory_access_context(self) -> MemoryAccessContext:
        """Resolve memory capability from Host runtime identity only."""
        if self._child_mode:
            return MemoryAccessContext.child(self._current_child_run_id or self._writer.session_id)
        task_id = (
            self._active_task_control_scope.task_id
            if self._active_task_control_scope is not None
            else None
        )
        return MemoryAccessContext.host(
            self._memory_scope_id,
            task_id=task_id,
            team_id=self._memory_team_scope_id,
        )

    def _memory_tools_enabled(self) -> bool:
        if self._child_mode:
            return False
        return MemoryConfigStore(self.workspace).load().effective_tools

    def memory_observations(self, limit: int = 256) -> tuple:
        """Return bounded, content-free process-local memory observations."""
        return self._memory_observations.snapshot(limit)

    def _restore_team_memory_scope(self) -> None:
        """Replay the latest successful Host grant/revoke, failing closed on drift."""
        selected: str | None = None
        for audit in self._writer.state.action_audits:
            if audit.status is not ActionAuditStatus.SUCCEEDED:
                continue
            values = audit.identity.arguments.as_mapping()
            if audit.identity.tool_name == "memory_team_scope_grant":
                team_id = values.get("team_id")
                if isinstance(team_id, str):
                    selected = team_id
            elif audit.identity.tool_name == "memory_team_scope_revoke":
                team_id = values.get("team_id")
                if selected == team_id:
                    selected = None
        if selected is None:
            return
        try:
            info = self._team_store.inspect(selected)
        except TeamStoreError:
            self._memory_observations.record(
                "team_scope_restore", "denied", actor="host", reason="team_missing"
            )
            return
        if info.owner_session_id != self._writer.session_id:
            self._memory_observations.record(
                "team_scope_restore", "denied", actor="host", reason="owner_mismatch"
            )
            return
        self._memory_team_scope_id = selected

    def _runtime_callbacks(self, writer: SessionWriter) -> AgentRuntimeCallbacks:
        return AgentRuntimeCallbacks(
            commit_turn=lambda turn: self._commit_turn(writer, turn),
            action_dispatcher=(
                self._dispatch_restricted_child_action
                if self._child_mode and self._child_action_names
                else (None if self._child_mode else self._dispatch_action)
            ),
            task_control_names=() if self._child_mode else _COMMIT_CONTROL_TOOL_NAMES,
            task_control_dispatcher=None if self._child_mode else self._dispatch_task_control,
            child_control_names=(
                CHILD_CONTROL_TOOL_NAMES
                if not self._child_mode or self._child_delegation_allowed
                else ()
            ),
            child_control_dispatcher=(
                self._dispatch_child_control
                if not self._child_mode or self._child_delegation_allowed
                else None
            ),
            team_control_names=() if self._child_mode else TEAM_CONTROL_TOOL_NAMES,
            team_control_dispatcher=None if self._child_mode else self._dispatch_team_control,
            tool_set_transition_dispatcher=None if self._child_mode else self._transition_tool_set,
            activate_turn=self._activate_runtime_turn,
            bind_provider=self._bind_runtime_provider,
            issue_action_lease=self._issue_runtime_action_lease,
            auto_compact_turn=self._auto_compact_turn,
            emit_usage=self._emit_runtime_usage,
            record_failure=self._record_runtime_failure,
            prepare_first_response_hook=None
            if self._child_mode
            else self._prepare_runtime_first_response_hook,
            binding_for_provider=lambda status: binding_from_status(status),
        )

    def _activate_runtime_turn(self, state, clear: bool) -> None:
        if clear:
            self._active_action_lease = None
            self._active_turn_context = None
            self._active_observation_context = None
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
            return
        self._active_hook_set_snapshot = state.hook_set_snapshot
        self._active_hook_audit_entries = []
        self._active_hook_handler_executions = 0
        self._active_usage_cursor = state.usage_cursor
        self._active_cancellation = state.cancellation
        self._active_event_sink = state.event_sink
        self._active_session_title_source_text = state.session_title_source_text
        self._active_observation_context = state.observation_context
        self._active_prepared_session_title = None
        state.child_control_state.depth = self._child_depth
        state.child_control_state.parent_child_run_id = self._parent_child_run_id
        state.child_control_state.root_child_run_id = self._root_child_run_id
        state.child_control_state.delegation_allowed = self._child_delegation_allowed

    def _bind_runtime_provider(self, state) -> None:
        self._active_turn_runtime = state.provider_runtime
        self._active_action_binding = state.binding

    def _issue_runtime_action_lease(self, prepared, provider, binding):
        lease = ActionLease(
            session_id=self._writer.session_id,
            lease_id=_uuid4_text(self._action_uuid_factory(), "action lease ID"),
            runtime_generation=provider.status.generation,
            context_id=prepared.context.context_id,
        )
        prepared = prepared.with_action_lease(lease)
        self._active_action_lease = lease
        self._active_turn_context = prepared.context
        self._active_action_binding = binding
        self._active_tool_set_snapshot = prepared.tool_set_snapshot
        self._active_hook_set_snapshot = prepared.hook_set_snapshot
        return prepared

    def _action_scope_fields(self) -> dict[str, object]:
        return {
            "execution_scope": self._execution_scope.kind,
            "execution_root_fingerprint": (
                self._execution_scope.execution_root_fingerprint
                or self._session_store.workspace_fingerprint
            ),
            "worktree_id": self._execution_scope.worktree_id,
            "version": 2,
        }

    def _emit_runtime_usage(self, usage, event_sink) -> None:
        if usage.latest_invocation is not None:
            self._emit_prompt_event(event_sink, TurnUsageCompleted(usage))

    def _record_runtime_failure(self, binding, error, provider_usage) -> None:
        self._record_failure(
            binding or binding_from_status(self._manager.status()),
            error,
            provider_usage=provider_usage,
        )
        try:
            self._evolution.record_trace(
                EvolutionTarget.WORKFLOW,
                EvolutionOutcome.EXTERNAL_ERROR
                if isinstance(error, ProviderAdapterError)
                else EvolutionOutcome.FAILED,
                f"uncommitted turn failed with {type(error).__name__}",
                source_session_id=self._writer.session_id,
                source_turn=len(self._writer.state.turns) + 1,
                metrics={"success_rate": 0.0},
            )
        except (EvolutionError, OSError):
            # Evolution is diagnostic state; a telemetry failure must not alter
            # the durable failure semantics of the Session.
            return

    def _prepare_runtime_first_response_hook(self, runtime, usage_cursor, title_source_text):
        if self._writer.state.turns or self._writer.state.latest_name is not None:
            return None
        return lambda: self._prepare_first_turn_session_title(
            runtime,
            usage_cursor,
            title_source_text,
        )

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
        reasoning_effort: ReasoningEffort | None = None,
        reliability_policy: ProviderReliabilityPolicy | None = None,
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
        publish_latest: bool = True,
        child_mode: bool = False,
        execution_scope: ExecutionScope | None = None,
        child_action_names: tuple[str, ...] = (),
        child_depth: int = 0,
        parent_child_run_id: str | None = None,
        root_child_run_id: str | None = None,
        child_delegation_allowed: bool = False,
        current_child_run_id: str | None = None,
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
        browser: BrowserAutomation | None = None,
        mcp_client_factory: Callable[..., object] = McpClient,
        permission_mode: PermissionMode = PermissionMode.READ_ONLY,
        approval_mode: ApprovalMode = ApprovalMode.ASK,
        approval_handler: ApprovalHandler | None = None,
        action_uuid_factory: Callable[[], UUID | str] = uuid4,
        session_store_factory: Callable[[Path], SessionStore] = SessionStore,
    ) -> ProjectSession:
        """Create or resume durable history while selecting runtime independently."""
        resolved_workspace = Path(workspace).resolve()
        if execution_scope is not None:
            if execution_scope.authority_workspace != resolved_workspace:
                raise ValueError("execution scope authority does not match workspace")
            selected_execution_scope = execution_scope
        else:
            selected_execution_scope = ExecutionScope.authority(resolved_workspace)
        execution_workspace = selected_execution_scope.execution_root
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
            "reasoning_effort": reasoning_effort,
            "reliability_policy": reliability_policy,
        }
        if provider_factory is not None:
            manager_arguments["provider_factory"] = provider_factory
        if fake_provider_factory is not None:
            manager_arguments["fake_factory"] = fake_provider_factory
        manager = RuntimeProviderManager(store, **manager_arguments)  # type: ignore[arg-type]
        writer: SessionWriter | None = None
        try:
            read_file = read_file_factory(execution_workspace)
            glob = glob_factory(execution_workspace)
            grep = grep_factory(execution_workspace)
            list_directory = list_directory_factory(execution_workspace)
            write_file = write_file_factory(execution_workspace)
            edit_file = edit_file_factory(execution_workspace)
            if (
                run_command_factory is RunCommandTool
                and selected_execution_scope.kind == "team-worktree"
            ):
                run_command = RunCommandTool(
                    execution_workspace,
                    resolved_environment,
                    read_only_paths=(execution_workspace / ".git",),
                )
            else:
                run_command = run_command_factory(execution_workspace, resolved_environment)
            mkdir = mkdir_factory(execution_workspace)
            move_file = move_file_factory(execution_workspace)
            delete_file = delete_file_factory(execution_workspace)
            delete_directory = delete_directory_factory(execution_workspace)
            copy_file = copy_file_factory(execution_workspace)
            read_file_lines = read_file_lines_factory(execution_workspace)
            stat_path = stat_path_factory(execution_workspace)
            list_tree = list_tree_factory(execution_workspace)
            grep_regex = grep_regex_factory(execution_workspace)
            patch_file = patch_file_factory(execution_workspace)
            git_status = git_status_factory(execution_workspace)
            git_diff = git_diff_factory(execution_workspace)
            git_log = git_log_factory(execution_workspace)
            git_show = git_show_factory(execution_workspace)
            web_search = web_search_factory(resolved_environment)
            web_fetch = web_fetch_factory()
            compare_files = compare_files_factory(execution_workspace)
            git_blame = git_blame_factory(execution_workspace)
            git_refs = git_refs_factory(execution_workspace)
            json_query = json_query_factory(execution_workspace)
            checksum_file = checksum_file_factory(execution_workspace)
            archive_list = archive_list_factory(execution_workspace)
            move_directory = move_directory_factory(execution_workspace)
            download_file = download_file_factory(execution_workspace)
            project_instructions_loader = ProjectInstructionsLoader(execution_workspace)
            skill_inventory_loader = SkillInventoryLoader(execution_workspace, resolved_environment)
            skill_candidate_store = SkillCandidateStore(execution_workspace, resolved_environment)
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
                    browser=browser,
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
                    child_mode=child_mode,
                    execution_scope=selected_execution_scope,
                    child_action_names=child_action_names,
                    child_depth=child_depth,
                    parent_child_run_id=parent_child_run_id,
                    root_child_run_id=root_child_run_id,
                    child_delegation_allowed=child_delegation_allowed,
                    current_child_run_id=current_child_run_id,
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
                    tool_registry_factory=(
                        (lambda: TOOL_REGISTRY_SNAPSHOT)
                        if child_mode
                        else lambda: registry_snapshot_with_browser(
                            resolved_workspace,
                            registry_snapshot_with_memory(
                                resolved_workspace, resume_mcp_catalog.registry_snapshot()
                            ),
                            enabled=browser is not None,
                        )
                    ),
                    skill_inventory_factory=(
                        (lambda: SkillInventorySnapshot((), ()))
                        if child_mode
                        else skill_inventory_loader.load
                    ),
                    hook_set_factory=(
                        (lambda: HookSetSnapshot(())) if child_mode else hook_store.snapshot
                    ),
                    skill_resource_reader=skill_inventory_loader.read_resource,
                )
                snapshot = loop.effective_context_snapshot()
                with manager.provider_for_context_transition() as runtime:
                    assessment = runtime.assess_context(snapshot.to_conversation_request())
                    report = assessment.fit_report
                    if report is not None and rejects_context_transition(report.decision):
                        raise SessionResumeContextError(prepared.info, snapshot.context_id, report)
                    committed = prepared.commit(
                        binding=binding_from_status(runtime.status),
                        publish_latest=publish_latest,
                    )
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
                    browser=browser,
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
                    child_mode=child_mode,
                    execution_scope=selected_execution_scope,
                    child_action_names=child_action_names,
                    child_depth=child_depth,
                    parent_child_run_id=parent_child_run_id,
                    root_child_run_id=root_child_run_id,
                    child_delegation_allowed=child_delegation_allowed,
                    current_child_run_id=current_child_run_id,
                )
                session_holder["session"] = session
                if not child_mode:
                    from coquo.child_recovery import ChildRunRecoveryService

                    session._startup_child_recovery = ChildRunRecoveryService(
                        resolved_workspace
                    ).recover(parent_session_id=session.session_id)
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
    def execution_scope(self) -> ExecutionScope:
        """Return the immutable authority/tool-root binding for this Session."""
        return self._execution_scope

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
            task = self._task_store.create(
                objective,
                owner_session=self._writer.session_id,
                acceptance_criteria=acceptance_criteria,
                structured_criteria=structured_criteria,
                completion_policy=completion_policy,
                name=name,
                budget=budget,
            )
            self._project_recursive_task(task)
            return task

    def drive_workflow(
        self,
        workflow_id: str,
        *,
        policy=None,
        cancellation: TurnCancellation | None = None,
    ):
        """Drive one Host-owned Workflow with this live Session as executor."""
        from coquo.workflow_orchestration import WorkflowOrchestrator

        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
        return WorkflowOrchestrator(self.workspace).drive_until_review(
            workflow_id,
            self,
            policy=policy,
            cancellation=cancellation,
        )

    def list_tasks(self) -> tuple[TaskInfo, ...]:
        """List workspace Tasks without changing Session or runtime state."""
        with self._lock:
            self._ensure_open()
            return self._task_store.list()

    def create_team(self, name: str) -> TeamInfo:
        """Create one Host-owned Team bound to the current Session."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            team = self._team_store.create(name, owner_session=self._writer.session_id)
            self._project_recursive_team(team)
            return team

    def create_team_preallocated(self, team_id: str, name: str) -> TeamInfo:
        """Create one owned Team at an ID reserved by a validated control approval."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            team = self._team_store.create(
                name,
                owner_session=self._writer.session_id,
                team_id=team_id,
            )
            self._project_recursive_team(team)
            return team

    def list_teams(self, *, status: TeamStatus | None = None) -> tuple[TeamInfo, ...]:
        """List durable workspace Teams without changing Session or runtime state."""
        with self._lock:
            self._ensure_open()
            return self._team_store.list(status=status)

    def inspect_team(self, team_id: str) -> TeamInfo:
        with self._lock:
            self._ensure_open()
            return self._team_store.inspect(team_id)

    def close_team(self, team_id: str) -> TeamInfo:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            team = self._team_service.close(team_id)
            self._project_recursive_team(team)
            return team

    def add_team_member(
        self,
        team_id: str,
        name: str,
        *,
        role_contract: str = "read-only-investigator-v1",
    ) -> TeamMemberState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_store.add_member(team_id, name, role_contract=role_contract)

    def list_team_members(self, team_id: str) -> tuple[TeamMemberState, ...]:
        with self._lock:
            self._ensure_open()
            return self._team_store.inspect(team_id).members

    def inspect_team_member(self, team_id: str, member_id: str) -> TeamMemberState:
        with self._lock:
            self._ensure_open()
            return self._team_store.member(team_id, member_id)

    def disable_team_member(self, team_id: str, member_id: str, reason: str) -> TeamMemberState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_store.disable_member(team_id, member_id, reason)

    def enable_team_member(self, team_id: str, member_id: str) -> TeamMemberState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_store.enable_member(team_id, member_id)

    def leave_team_member(self, team_id: str, member_id: str, reason: str) -> TeamMemberState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_service.leave_member(team_id, member_id, reason)

    def inspect_team_worktree(self, worktree_id: str):
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            service = WorktreeService(self.workspace)
            info = service.store.inspect(worktree_id)
            self._ensure_team_worktree_owner(info)
            return info

    def diff_team_worktree(self, worktree_id: str, *, max_bytes: int = 64 * 1024) -> WorktreeDiff:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            service = WorktreeService(self.workspace)
            info = service.store.inspect(worktree_id)
            self._ensure_team_worktree_owner(info)
            return service.diff(worktree_id, max_bytes=max_bytes)

    def recover_team_worktree(self, worktree_id: str):
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            service = WorktreeService(self.workspace)
            info = service.store.inspect(worktree_id)
            self._ensure_team_worktree_owner(info)
            return service.recover(worktree_id)

    def retire_team_worktree(self, worktree_id: str):
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            service = WorktreeService(self.workspace)
            info = service.store.inspect(worktree_id)
            self._ensure_team_worktree_owner(info)
            return service.retire(worktree_id)

    def _ensure_team_worktree_owner(self, info) -> None:
        team = self._team_store.inspect(info.header.team_id)
        if team.owner_session_id != self._writer.session_id:
            raise TeamStoreError("Team worktree belongs to another parent Session")
        assignment = next(
            (item for item in team.assignments if item.assignment_id == info.header.assignment_id),
            None,
        )
        if assignment is None or assignment.worktree_id != info.worktree_id:
            raise TeamStoreError("Team worktree assignment binding is not current")

    def send_team_message(self, team_id: str, member_id: str, body: str):
        """Persist one owner-to-member message without changing the Session."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_messaging.send_owner(team_id, member_id, body)

    def list_team_messages(
        self, team_id: str, *, limit: int = 100, member_id: str | None = None, status=None
    ):
        """List durable Team messages without consuming them."""
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            return self._team_messaging.list(
                team_id, limit=limit, member_id=member_id, status=status
            )

    def inspect_team_message(self, team_id: str, message_id: str):
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            return self._team_messaging.show(team_id, message_id)

    def read_team_message(self, team_id: str, message_id: str):
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_messaging.read(team_id, message_id)

    def show_team_message_for_model(
        self, team_id: str, message_id: str, *, context_id: str, tool_use_id: str
    ):
        """Deliver exactly one verified reply body after persisting its parent receipt."""
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            message = self._team_messaging.show(team_id, message_id)
            if message.sender_member_id is None or message.source_assignment_id is None:
                raise TeamStoreError("Team control may show only a member reply")
            if (
                message.source_child_session_id is None
                or message.source_turn_record_sequence is None
            ):
                raise TeamStoreError("Team reply lacks Child provenance")
            if message.source_handoff_sha256 is None:
                raise TeamStoreError("Team reply lacks handoff provenance")
            team = self._team_store.inspect(team_id)
            assignment = next(
                (
                    item
                    for item in team.assignments
                    if item.assignment_id == message.source_assignment_id
                ),
                None,
            )
            if assignment is None or assignment.reply_message_id != message.message_id:
                raise TeamStoreError("Team reply is not bound to its assignment")
            if (
                assignment.phase is not TeamAssignmentPhase.TERMINAL_OBSERVED
                or assignment.child_outcome != "completed"
            ):
                raise TeamStoreError("Team reply requires a completed observed Child")
            if assignment.handoff_sha256 != message.source_handoff_sha256:
                raise TeamStoreError("Team reply handoff provenance disagrees with assignment")
            existing = next(
                (
                    receipt
                    for receipt in self._writer.state.team_message_deliveries
                    if receipt.message_id == message.message_id
                ),
                None,
            )
            if existing is None:
                self._writer.team_message_delivered_to_parent(
                    context_id=context_id,
                    tool_use_id=tool_use_id,
                    team_id=team.team_id,
                    message_id=message.message_id,
                    body_sha256=message.body_sha256,
                    source_assignment_id=assignment.assignment_id,
                    source_child_session_id=message.source_child_session_id,
                    source_child_turn_sequence=message.source_turn_record_sequence,
                    source_handoff_sha256=message.source_handoff_sha256,
                )
            elif (
                existing.context_id != context_id
                or existing.tool_use_id != tool_use_id
                or existing.body_sha256 != message.body_sha256
            ):
                raise TeamStoreError("Team reply delivery receipt does not match this ToolUse")
            return message

    def read_team_message_for_model(self, team_id: str, message_id: str):
        """Mark a reply read only after an exact parent delivery receipt exists."""
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            if not any(
                receipt.message_id == message_id
                for receipt in self._writer.state.team_message_deliveries
            ):
                raise TeamStoreError("Team reply has not been delivered to the parent")
            return self._team_messaging.read(team_id, message_id)

    def cancel_team_message(self, team_id: str, message_id: str, reason: str):
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_messaging.cancel(team_id, message_id, reason)

    def create_team_work(
        self, team_id: str, title: str, objective: str, dependency_ids: tuple[str, ...] = ()
    ) -> TeamWorkItemState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_work.create(team_id, title, objective, dependency_ids)

    def list_team_work(self, team_id: str, *, limit: int = 100, status=None) -> TeamWorkList:
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            return self._team_work.list(team_id, limit=limit, status=status)

    def inspect_team_work(self, team_id: str, work_item_id: str) -> TeamWorkItemState:
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            return self._team_work.show(team_id, work_item_id)

    def cancel_team_work(self, team_id: str, work_item_id: str, reason: str) -> TeamWorkItemState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_work.cancel(team_id, work_item_id, reason)

    def assign_team_work(self, team_id: str, work_item_id: str, member_id: str):
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_work.assign(team_id, work_item_id, member_id)

    def release_team_work(self, team_id: str, work_item_id: str, reason: str) -> TeamWorkItemState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_work.release(team_id, work_item_id, reason)

    def complete_team_work(
        self, team_id: str, work_item_id: str, evidence: str
    ) -> TeamWorkItemState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_work.complete(team_id, work_item_id, evidence)

    def review_team_work_for_model(
        self,
        team_id: str,
        work_item_id: str,
        *,
        decision: str,
        note: str,
        message_id: str | None = None,
    ) -> TeamWorkItemState:
        """Apply explicit model review only with the required reply receipt evidence."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            item = self._team_work.show(team_id, work_item_id)
            if item.current_assignment_id is None:
                raise TeamStoreError("Team work item has no current assignment")
            if decision == "complete":
                if message_id is None:
                    raise TeamStoreError("Team work completion requires a delivered reply ID")
                assignment = self._team_service.inspect(
                    team_id, item.current_assignment_id
                ).assignment
                if assignment.reply_message_id != message_id:
                    raise TeamStoreError("Team work completion reply does not match assignment")
                if not any(
                    receipt.message_id == message_id
                    for receipt in self._writer.state.team_message_deliveries
                ):
                    raise TeamStoreError("Team work completion requires a parent delivery receipt")
                return self._team_work.complete(team_id, work_item_id, note)
            if decision == "release":
                if message_id is not None:
                    raise TeamStoreError("Team work release must not include a reply ID")
                return self._team_work.release(team_id, work_item_id, note)
            if decision == "cancel":
                if message_id is not None:
                    raise TeamStoreError("Team work cancellation must not include a reply ID")
                return self._team_work.cancel(team_id, work_item_id, note)
            raise TeamStoreError("Team work review decision is invalid")

    def run_team_schedule(
        self,
        team_id: str,
        *,
        max_assignments: int = 32,
        max_parallel: int = 4,
    ) -> TeamScheduleState:
        """Run one foreground schedule wave under the current owner Session."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            if self._team_schedule_supervisor is not None:
                raise TeamScheduleError("Team schedule supervisor is active")
            return self._team_schedule_service.run(
                team_id,
                self,
                source=TeamScheduleSource.HOST,
                max_assignments=max_assignments,
                max_parallel=max_parallel,
                parent_permission_mode=self._permission_mode.value,
            )

    def start_team_schedule(
        self,
        team_id: str,
        *,
        max_assignments: int = 32,
        max_parallel: int = 4,
    ) -> TeamScheduleState:
        """Start one background schedule wave and return its durable identity."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            team = self._ensure_team_owner(team_id)
            if self._team_schedule_supervisor is None:
                self._team_schedule_supervisor = TeamScheduleSupervisor(self.workspace, self)
            run = self._team_schedule_service.start(
                team.team_id,
                source=TeamScheduleSource.HOST,
                max_assignments=max_assignments,
                max_parallel=max_parallel,
                parent_permission_mode=self._permission_mode.value,
            )
            try:
                return self._team_schedule_supervisor.submit(run)
            except TeamScheduleSupervisorError:
                run.close()
                raise

    def team_schedule_status(
        self, team_id: str, schedule_run_id: str | None = None
    ) -> TeamScheduleState | None:
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            return self._team_schedule_service.status(team_id, schedule_run_id)

    def wait_team_schedule(
        self, team_id: str, schedule_run_id: str, timeout_seconds: float
    ) -> TeamScheduleNotification | None:
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            supervisor = self._team_schedule_supervisor
        if supervisor is None:
            return None
        return supervisor.wait(schedule_run_id, timeout_seconds)

    def cancel_team_schedule(
        self, team_id: str, schedule_run_id: str, reason: str
    ) -> TeamScheduleState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            state = self._team_schedule_service.cancel(
                team_id, schedule_run_id, reason, source=TeamScheduleSource.HOST
            )
            return state

    def recover_team_schedule(
        self, team_id: str, schedule_run_id: str | None = None
    ) -> TeamScheduleState:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            return self._team_schedule_service.recover(team_id, schedule_run_id)

    def _ensure_team_owner(self, team_id: str) -> TeamInfo:
        info = self._team_store.inspect(team_id)
        if info.owner_session_id != self._writer.session_id:
            raise TeamStoreError("Team belongs to another parent Session")
        return info

    def grant_team_memory_scope(self, team_id: str) -> MemoryAccessContext:
        """Explicitly grant this Host Session access to one Team scope."""
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            MemoryAccessContext.host(self._memory_scope_id, team_id=team_id)
            self._run_team_memory_scope_action("memory_team_scope_grant", team_id)
            return self._memory_access_context()

    def revoke_team_memory_scope(self, team_id: str) -> MemoryAccessContext:
        """Revoke the current explicit Team memory grant."""
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            self._run_team_memory_scope_action("memory_team_scope_revoke", team_id)
            return self._memory_access_context()

    def _run_team_memory_scope_action(self, tool_name: str, team_id: str) -> None:
        status = self._manager.status()
        binding = binding_from_status(status)
        context_id = self._loop.effective_context_snapshot().context_id
        lease = ActionLease(
            session_id=self._writer.session_id,
            lease_id=_uuid4_text(self._action_uuid_factory(), "action lease ID"),
            runtime_generation=status.generation,
            context_id=context_id,
        )
        request_id = _uuid4_text(self._action_uuid_factory(), "action request ID")
        identity = ActionIdentity(
            request_id=request_id,
            tool_use_id=f"host-{tool_name}-{request_id}",
            tool_name=tool_name,
            arguments=ToolArguments.from_mapping({"team_id": team_id}),
            action=PermissionAction.WORKSPACE_READ,
            workspace_fingerprint=self._session_store.workspace_fingerprint,
            lease=lease,
            precondition=ActionPrecondition.none(),
            **self._action_scope_fields(),
        )

        def revalidate(current: ActionIdentity) -> ActionIdentity:
            self._ensure_team_owner(team_id)
            return current

        def execute(_identity: ActionIdentity) -> ActionExecutionResult:
            if tool_name == "memory_team_scope_grant":
                self._memory_team_scope_id = team_id
                result_code = "memory_team_scope_granted"
            else:
                if self._memory_team_scope_id == team_id:
                    self._memory_team_scope_id = None
                result_code = "memory_team_scope_revoked"
            return ActionExecutionResult(
                ToolResult(identity.tool_use_id, result_code),
                ActionExecutionOutcome.SUCCEEDED,
                result_code,
                "Team memory scope control succeeded",
            )

        ActionCoordinator(
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
        )

    def create_team_assignment(
        self, team_id: str, member_id: str, objective: str
    ) -> TeamAssignmentInfo:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            assignment = self._team_service.create(
                team_id,
                member_id,
                objective,
                parent_permission_mode=self._permission_mode.value,
            )
            team = self._team_store.inspect(team_id)
            self._project_recursive_team(team, objective=objective)
            child = assignment.child
            if child is not None:
                team_node = self._recursive_store.node_for_team(team.team_id)
                self._project_recursive_child(
                    child, parent_node_id=team_node.node_id if team_node else None
                )
            return assignment

    def list_team_assignments(
        self, team_id: str, *, limit: int = 100
    ) -> tuple[TeamAssignmentInfo, ...]:
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            return self._team_service.list(team_id, limit=limit)

    def inspect_team_assignment(self, team_id: str, assignment_id: str) -> TeamAssignmentInfo:
        with self._lock:
            self._ensure_open()
            self._ensure_team_owner(team_id)
            return self._team_service.inspect(team_id, assignment_id)

    def recover_team_assignments(
        self, team_id: str, assignment_id: str | None = None, *, limit: int = 100
    ) -> TeamRecoveryResult:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_owner(team_id)
            result = self._team_service.recover(team_id, assignment_id, limit=limit)
            team = self._team_store.inspect(team_id)
            self._project_recursive_team(team)
            for item in self._team_service.list(team_id, limit=limit):
                if item.child is not None:
                    team_node = self._recursive_store.node_for_team(team.team_id)
                    self._project_recursive_child(
                        item.child,
                        parent_node_id=team_node.node_id if team_node is not None else None,
                    )
            return result

    def prepare_team_assignment(self, team_id: str, assignment_id: str) -> TeamAssignmentInfo:
        with self._lock:
            return self._prepare_team_assignment_unlocked(team_id, assignment_id)

    def _prepare_team_assignment_unlocked(
        self, team_id: str, assignment_id: str
    ) -> TeamAssignmentInfo:
        """Prepare one schedule assignment without reacquiring the parent Session lock."""
        self._ensure_open()
        self._ensure_not_compacting()
        info = self._ensure_team_assignment_owner(team_id, assignment_id)
        self._team_messaging.bind_assignment(team_id, assignment_id)
        info = self._ensure_team_assignment_owner(team_id, assignment_id)
        status = self._manager.status()
        child_info = self._child_run_store.inspect(info.assignment.child_run_id)
        child_session_id = child_info.child_session_id or str(uuid4())
        role_contract = info.assignment.member_role_contract
        if not role_allowed_by_parent(role_contract, self._permission_mode.value):
            raise TeamStoreError("parent permission ceiling cannot admit Team member role")
        role = child_role_descriptor(role_contract)
        action_names: tuple[str, ...] = ()
        execution_root_fingerprint = (
            info.assignment.worktree_id and child_info.execution_root_fingerprint
        )
        if role.execution_scope == "team-worktree":
            if (
                not info.assignment.worktree_id
                or not info.assignment.base_commit
                or not info.assignment.target_ref
            ):
                raise TeamStoreError("writable Team assignment has incomplete worktree identity")
            if execution_root_fingerprint is None:
                raise TeamStoreError("writable Child origin has no execution-root identity")
            from coquo.core.extensions import ToolExecutionKind
            from coquo.tools.catalog import select_tool_set
            from coquo.worktree_service import WorktreeService

            binding = WorktreeService(self.workspace).inspect_binding(info.assignment.worktree_id)
            if workspace_fingerprint(binding.worktree_root) != execution_root_fingerprint:
                raise TeamStoreError("writable Team worktree root fingerprint changed")
            tools = select_tool_set(role.tool_names)
            action_names = tuple(
                contract.name
                for contract in tools.contracts
                if contract.execution_kind is ToolExecutionKind.HOST_ACTION
            )
        spec = build_child_runtime_spec_from_binding(
            child_run_id=child_info.child_run_id,
            parent_session_id=self._writer.session_id,
            child_session_id=child_session_id,
            objective=child_info.objective,
            binding=binding_from_status(status),
            role_contract_version=(
                TEAM_CHILD_ROLE_CONTRACT_VERSION
                if role_contract == "read-only-investigator-v1"
                else 3
            ),
            role_contract=role_contract,
            execution_scope=role.execution_scope,
            execution_root_fingerprint=execution_root_fingerprint,
            worktree_id=info.assignment.worktree_id,
            base_commit=info.assignment.base_commit,
            target_ref=info.assignment.target_ref,
            child_action_names=action_names,
        )
        self._child_run_store.prepare(
            child_info.child_run_id,
            runtime_spec=spec,
            session_store=self._session_store,
            binding=binding_from_status(status),
        )
        return self._team_service.inspect(team_id, assignment_id)

    def run_team_assignment(self, team_id: str, assignment_id: str) -> TeamAssignmentInfo:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            info = self._ensure_team_assignment_owner(team_id, assignment_id)
            self.run_child_run(info.assignment.child_run_id)
            latest = self._team_service.inspect(team_id, assignment_id)
            if latest.child is not None and latest.child.status in {
                ChildRunStatus.COMPLETED,
                ChildRunStatus.FAILED,
                ChildRunStatus.CANCELLED,
                ChildRunStatus.INTERRUPTED,
            }:
                self._team_service.observe_terminal(team_id, assignment_id)
            result = self._team_service.inspect(team_id, assignment_id)
            if result.child is not None:
                team_node = self._recursive_store.node_for_team(result.team.team_id)
                self._project_recursive_child(
                    result.child,
                    parent_node_id=team_node.node_id if team_node is not None else None,
                )
            return result

    def start_team_assignment(self, team_id: str, assignment_id: str) -> TeamAssignmentInfo:
        with self._lock:
            return self._start_team_assignment_unlocked(team_id, assignment_id)

    def _start_team_assignment_unlocked(
        self, team_id: str, assignment_id: str
    ) -> TeamAssignmentInfo:
        """Submit one schedule assignment without reacquiring the parent Session lock."""
        self._ensure_open()
        self._ensure_not_compacting()
        info = self._ensure_team_assignment_owner(team_id, assignment_id)
        supervisor = self._child_supervisor
        if supervisor is None:
            from coquo.background_runtime import PersistentChildRunRuntime

            PersistentChildRunRuntime(
                self.workspace,
                parent_session_id=self._writer.session_id,
            ).start(info.assignment.child_run_id)
        else:
            supervisor.submit(info.assignment.child_run_id)
        result = self._team_service.inspect(team_id, assignment_id)
        if result.child is not None:
            team_node = self._recursive_store.node_for_team(result.team.team_id)
            self._project_recursive_child(
                result.child,
                parent_node_id=team_node.node_id if team_node is not None else None,
            )
        return result

    def wait_team_assignment(
        self, team_id: str, assignment_id: str, timeout_seconds: float
    ) -> TeamAssignmentInfo:
        with self._lock:
            return self._wait_team_assignment_unlocked(team_id, assignment_id, timeout_seconds)

    def _wait_team_assignment_unlocked(
        self, team_id: str, assignment_id: str, timeout_seconds: float
    ) -> TeamAssignmentInfo:
        """Observe one schedule assignment without reacquiring the parent Session lock."""
        self._ensure_open()
        self._ensure_not_compacting()
        info = self._ensure_team_assignment_owner(team_id, assignment_id)
        supervisor = self._child_supervisor
        if supervisor is None:
            from coquo.background_runtime import PersistentChildRunRuntime

            child_info = PersistentChildRunRuntime(
                self.workspace,
                parent_session_id=self._writer.session_id,
            ).wait(info.assignment.child_run_id, timeout_seconds)
        else:
            child_info = supervisor.wait(info.assignment.child_run_id, timeout_seconds)
        if child_info.parent_session_id != self._writer.session_id:
            raise ChildRunStoreError("Child Run belongs to another parent Session")
        latest = self._team_service.inspect(team_id, assignment_id)
        if latest.child is not None and latest.child.status in {
            ChildRunStatus.COMPLETED,
            ChildRunStatus.FAILED,
            ChildRunStatus.CANCELLED,
            ChildRunStatus.INTERRUPTED,
        }:
            self._team_service.observe_terminal(team_id, assignment_id)
        result = self._team_service.inspect(team_id, assignment_id)
        if result.child is not None:
            team_node = self._recursive_store.node_for_team(result.team.team_id)
            self._project_recursive_child(
                result.child,
                parent_node_id=team_node.node_id if team_node is not None else None,
            )
        return result

    def cancel_team_assignment(
        self, team_id: str, assignment_id: str, reason: str
    ) -> TeamAssignmentInfo:
        with self._lock:
            return self._cancel_team_assignment_unlocked(team_id, assignment_id, reason)

    def _cancel_team_assignment_unlocked(
        self, team_id: str, assignment_id: str, reason: str
    ) -> TeamAssignmentInfo:
        """Cancel one schedule assignment without reacquiring the parent Session lock."""
        self._ensure_open()
        self._ensure_not_compacting()
        info = self._ensure_team_assignment_owner(team_id, assignment_id)
        supervisor = self._child_supervisor
        if supervisor is not None:
            supervisor.cancel(info.assignment.child_run_id, reason)
        else:
            self._child_run_store.request_cancel(info.assignment.child_run_id, reason=reason)
        latest = self._team_service.inspect(team_id, assignment_id)
        if latest.child is not None and latest.child.status in {
            ChildRunStatus.COMPLETED,
            ChildRunStatus.FAILED,
            ChildRunStatus.CANCELLED,
            ChildRunStatus.INTERRUPTED,
        }:
            self._team_service.observe_terminal(team_id, assignment_id)
        result = self._team_service.inspect(team_id, assignment_id)
        if result.child is not None:
            team_node = self._recursive_store.node_for_team(result.team.team_id)
            self._project_recursive_child(
                result.child,
                parent_node_id=team_node.node_id if team_node is not None else None,
            )
        return result

    def publish_team_assignment_handoff(self, team_id: str, assignment_id: str):
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._ensure_team_assignment_owner(team_id, assignment_id)
            return self._team_service.observe_terminal(team_id, assignment_id)

    def _ensure_team_assignment_owner(self, team_id: str, assignment_id: str) -> TeamAssignmentInfo:
        info = self._team_service.inspect(team_id, assignment_id)
        if info.team.owner_session_id != self._writer.session_id:
            raise TeamStoreError("Team belongs to another parent Session")
        if info.child is None:
            raise TeamStoreError(info.child_error or "Team assignment Child Run is unavailable")
        if info.child.parent_session_id != self._writer.session_id:
            raise TeamStoreError("Team assignment Child Run belongs to another parent Session")
        return info

    def create_child_run(self, objective: str) -> ChildRunInfo:
        """Queue Child Run metadata under the current Session without invoking a Provider."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            info = self._child_run_store.create(objective, parent_session=self._writer.session_id)
            self._project_recursive_child(info)
            return info

    def prepare_child_run(self, child_run_id: str) -> ChildRunInfo:
        """Freeze a read-only Child envelope and bind its detached Session without Provider work."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            info = self._child_run_store.inspect(child_run_id)
            status = self._manager.status()
            child_session_id = info.child_session_id or str(uuid4())
            spec = build_child_runtime_spec(
                child_run_id=info.child_run_id,
                parent_session_id=self._writer.session_id,
                child_session_id=child_session_id,
                objective=info.objective,
                status=status,
                depth=info.delegated.depth if info.delegated is not None else 1,
                parent_child_run_id=(
                    info.delegated.parent_child_run_id if info.delegated is not None else None
                ),
                root_child_run_id=(
                    info.delegated.root_child_run_id if info.delegated is not None else None
                ),
                delegation_allowed=(
                    info.delegated is not None
                    and info.delegated.depth == 1
                    and info.delegated.capability == "read-only-explorer-v1"
                ),
            )
            result = self._child_run_store.prepare(
                info.child_run_id,
                runtime_spec=spec,
                session_store=self._session_store,
                binding=binding_from_status(status),
            )
            self._project_recursive_child(result)
            return result

    def run_child_run(self, child_run_id: str) -> ChildRunInfo:
        """Run one prepared Child in its detached Session without changing this parent."""
        from coquo.child_runtime import ChildRunExecutor

        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            info = ChildRunExecutor(self.workspace).run(child_run_id)
            self._project_recursive_child(info)
            return info

    def start_child_run(self, child_run_id: str) -> ChildRunInfo:
        """Durably submit one ready Child, reusing an injected local supervisor when present."""
        return self.start_child_run_observation(child_run_id).child

    def start_child_run_observation(self, child_run_id: str) -> ChildStartObservation:
        """Submit one Child and expose the exact Host-observed submission identity."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            if self._child_supervisor is not None:
                info = self._child_supervisor.submit(child_run_id)
                self._project_recursive_child(info)
                return ChildStartObservation(info, "process-local-supervisor")

            from coquo.background_runtime import PersistentChildRunRuntime

            submission = PersistentChildRunRuntime(
                self.workspace,
                parent_session_id=self._writer.session_id,
            ).start(child_run_id)
            info = self._child_run_store.inspect(child_run_id)
            if info.parent_session_id != self._writer.session_id:
                raise ChildRunStoreError("Child Run belongs to another parent Session")
            self._project_recursive_child(info)
            return ChildStartObservation(
                info,
                "durable-background-worker",
                submission_id=submission.item.submission_id,
                queue_state=submission.item.state,
                worker_started=submission.worker_started,
                worker_pid=submission.worker_pid,
                launch_error=submission.launch_error,
            )

    def child_notifications(self):
        with self._lock:
            self._ensure_open()
            if self._child_supervisor is None:
                return ()
            return self._child_supervisor.drain_notifications()

    def wait_child_run(self, child_run_id: str, timeout_seconds: float) -> ChildRunInfo:
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            supervisor = self._child_supervisor
            if supervisor is None:
                from coquo.background_runtime import PersistentChildRunRuntime

                info = PersistentChildRunRuntime(
                    self.workspace,
                    parent_session_id=self._writer.session_id,
                ).wait(child_run_id, timeout_seconds)
            else:
                info = supervisor.wait(child_run_id, timeout_seconds)
            if info.parent_session_id != self._writer.session_id:
                raise ChildRunStoreError("Child Run belongs to another parent Session")
            self._project_recursive_child(info)
            return info

    def recover_child_runs(self, child_run_id: str | None = None, limit: int = 100):
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            from coquo.child_recovery import ChildRunRecoveryService

            result = ChildRunRecoveryService(self.workspace).recover(
                parent_session_id=self._writer.session_id,
                child_run_id=child_run_id,
                limit=limit,
            )
            for info in self._child_run_store.list():
                if child_run_id is None or info.child_run_id == child_run_id:
                    self._project_recursive_child(info)
            return result

    def background_status(self):
        """Inspect the durable Child queue and restartable worker without invoking a Provider."""
        with self._lock:
            self._ensure_open()
            from coquo.background_runtime import PersistentChildRunRuntime

            return PersistentChildRunRuntime(
                self.workspace,
                parent_session_id=self._writer.session_id,
            ).status()

    def recover_child_background(self):
        """Reconcile durable Child worker orphans with fail-closed semantics."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            from coquo.background_runtime import PersistentChildRunRuntime

            return PersistentChildRunRuntime(
                self.workspace,
                parent_session_id=self._writer.session_id,
            ).recover_orphans()

    def list_child_runs(self, *, status=None) -> tuple[ChildRunInfo, ...]:
        """List Child Run control-plane metadata without changing Session state."""
        with self._lock:
            self._ensure_open()
            return self._child_run_store.list(status=status)

    def inspect_child_run(self, child_run_id: str) -> ChildRunInfo:
        """Inspect one Child Run without invoking a Provider."""
        with self._lock:
            self._ensure_open()
            return self._child_run_store.inspect(child_run_id)

    def publish_child_handoff(self, child_run_id: str):
        """Publish one exact terminal handoff owned by the current parent Session."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            info = self._child_run_store.inspect(child_run_id)
            if info.parent_session_id != self._writer.session_id:
                raise ChildRunStoreError("Child Run belongs to another parent Session")
            from coquo.child_handoff import publish_child_handoff

            return publish_child_handoff(self.workspace, child_run_id)

    def deliver_child_handoff(
        self,
        child_run_id: str,
        *,
        source: str = "host",
        tool_use_id: str | None = None,
    ):
        """Commit a content-free receipt before returning one exact Child handoff."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            from coquo.child_handoff import deliver_child_handoff

            return deliver_child_handoff(
                self.workspace,
                child_run_id,
                parent_writer=self._writer,
                source=source,
                tool_use_id=tool_use_id,
            )

    def cancel_child_run(self, child_run_id: str, reason: str) -> ChildRunInfo:
        """Durably request cancellation and signal a local Child worker when present."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            if self._child_supervisor is not None:
                info = self._child_supervisor.cancel(child_run_id, reason)
            else:
                info = self._child_run_store.request_cancel(child_run_id, reason=reason)
            self._project_recursive_child(info)
            return info

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
                self._project_recursive_task(existing)
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
            self._project_recursive_task(existing)
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
            self._retire_team_schedule_supervisor_for_identity_change()
            self._retire_child_supervisor_for_identity_change()
            candidate = self._session_store.create(binding_from_status(self._manager.status()))
            try:
                loop = self._new_loop(candidate)
            except BaseException:
                candidate.release()
                raise
            old = self._writer
            self._writer = candidate
            self._loop = loop
            self._runtime = AgentRuntime(
                loop,
                self._runtime_services(),
                self._runtime_callbacks(candidate),
            )
            old.release()
            return candidate.info

    def fork_session(self, selector: str | Path, through_turn: int) -> SessionInfo:
        """Create and select a provenance-linked Session from complete parent turns."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            self._retire_team_schedule_supervisor_for_identity_change()
            self._retire_child_supervisor_for_identity_change()
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
            self._runtime = AgentRuntime(
                loop,
                self._runtime_services(),
                self._runtime_callbacks(candidate),
            )
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
            self._retire_team_schedule_supervisor_for_identity_change()
            self._retire_child_supervisor_for_identity_change()
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
                    tool_registry_factory=lambda: registry_snapshot_with_browser(
                        self.workspace,
                        registry_snapshot_with_memory(
                            self.workspace, self._mcp_catalog_service.registry_snapshot()
                        ),
                        enabled=self._browser is not None,
                    ),
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
                self._runtime = AgentRuntime(
                    loop,
                    self._runtime_services(),
                    self._runtime_callbacks(committed.writer),
                )
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
            if _enabled_tool_names is None and self._memory_tools_enabled():
                enabled_tool_names = (*enabled_tool_names, *MEMORY_TOOL_NAMES)
            if _enabled_tool_names is None and self._browser is not None:
                enabled_tool_names = (*enabled_tool_names, BROWSER_ACTION_TOOL_NAME)
            if not any(source in {"brave", "tavily"} for source in self._search_source_order):
                enabled_tool_names = tuple(
                    name for name in enabled_tool_names if name != WEB_SEARCH_TOOL_NAME
                )
            observation_context = _turn_observation_context(self._writer.session_id)
            request = AgentTurnRequest(
                text=text,
                event_sink=self._observation_event_sink(event_sink, observation_context),
                include_tool_details=include_tool_details,
                cancellation=cancellation,
                allow_tools=_allow_tools,
                enabled_tool_names=(enabled_tool_names if _allow_tools else None),
                session_title_source_text=session_title_source_text,
                task_proposal_sink=(
                    _task_proposal_sink
                    if _task_proposal_sink is not None
                    else self._capture_task_admission_proposal
                ),
                failure_usage_sink=_failure_usage_sink,
                observation_context=observation_context,
            )
            return self._runtime.run_turn(request)

    @property
    def observation_stream(self) -> ObservationStream:
        """Read-only access to the bounded process-local live observation rail."""
        return self._observation_stream

    def _observation_event_sink(
        self, sink: PromptEventSink | None, context: ObservationContext
    ) -> PromptEventSink:
        self._observation_stream.set_context(context)

        def emit(event: PromptEvent) -> None:
            self._observation_stream.publish_prompt(event)
            self._emit_prompt_event(sink, event)

        return emit

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

    def set_reasoning_effort(
        self, reasoning_effort: ReasoningEffort | None
    ) -> ReasoningEffortUpdateResult:
        """Set or reset a process-local reasoning effort without changing history."""
        with self._lock:
            self._ensure_open()
            self._ensure_not_compacting()
            result = self._manager.set_reasoning_effort(reasoning_effort)
            try:
                self._writer.runtime_changed(
                    binding_from_status(result.status), reason="reasoning_effort"
                )
            except Exception as error:
                raise RuntimeSwitchAuditError(RuntimeSwitchResult(result.status, None)) from error
            return result

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
                ProviderReliabilityBudgetError,
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
            **self._action_scope_fields(),
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
            team_supervisor = self._team_schedule_supervisor
            self._team_schedule_supervisor = None
            if team_supervisor is not None:
                team_supervisor.close(join_timeout=1.0)
            self._closed = True
            supervisor = self._child_supervisor
            self._child_supervisor = None
            if supervisor is not None:
                supervisor.close(join_timeout=1.0)
            mcp_cleanup_complete = self._mcp_process_manager.close()
            browser_cleanup_complete = True
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    browser_cleanup_complete = False
            try:
                self._writer.close()
            finally:
                self._manager.close()
            if not mcp_cleanup_complete:
                raise RuntimeError("MCP process cleanup is incomplete")
            if not browser_cleanup_complete:
                raise RuntimeError("browser process cleanup is incomplete")

    def _retire_team_schedule_supervisor_for_identity_change(self) -> None:
        supervisor = self._team_schedule_supervisor
        if supervisor is None:
            return
        if supervisor.queued_count:
            raise SessionStoreError(
                "cannot change Session identity while Team schedules are queued or active; "
                "cancel or wait for them first"
            )
        self._team_schedule_supervisor = None
        supervisor.close(join_timeout=1.0)

    def _retire_child_supervisor_for_identity_change(self) -> None:
        supervisor = self._child_supervisor
        if supervisor is None:
            return
        if supervisor.pending_submission_count:
            raise SessionStoreError(
                "cannot change Session identity while Child Runs are queued or active; "
                "cancel or wait for them first"
            )
        self._child_supervisor = None
        supervisor.close(join_timeout=1.0)

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
        memory_recall_factory=None,
    ) -> AgentLoop:
        services = AgentRuntimeServices(
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
            project_instructions_factory,
            tool_registry_factory or (lambda: TOOL_REGISTRY_SNAPSHOT),
            skill_inventory_factory or (lambda: SkillInventorySnapshot((), ())),
            hook_set_factory or (lambda: HookSetSnapshot(())),
            skill_resource_reader or (lambda *_args, **_kwargs: ""),
            memory_recall_factory=memory_recall_factory,
        )
        return AgentRuntimeFactory.create(
            state,
            services,
            AgentRuntimeCallbacks(commit_turn=commit_turn),
        ).loop

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
            tool_registry_factory=lambda: registry_snapshot_with_browser(
                self.workspace,
                registry_snapshot_with_memory(
                    self.workspace, self._mcp_catalog_service.registry_snapshot()
                ),
                enabled=self._browser is not None,
            ),
            skill_inventory_factory=self._skill_inventory_loader.load,
            hook_set_factory=self._hook_store.snapshot,
            skill_resource_reader=self._skill_inventory_loader.read_resource,
            memory_recall_factory=(
                empty_memory_recall
                if self._memory_recall_service is None
                else self._memory_recall_service.recall
            ),
        )
        loop.install_action_dispatcher(self._dispatch_action)
        loop.install_task_control_dispatcher(
            _COMMIT_CONTROL_TOOL_NAMES, self._dispatch_task_control
        )
        loop.install_child_control_dispatcher(
            CHILD_CONTROL_TOOL_NAMES, self._dispatch_child_control
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
            current = registry_snapshot_with_browser(
                self.workspace,
                registry_snapshot_with_memory(
                    self.workspace, self._mcp_catalog_service.registry_snapshot()
                ),
                enabled=self._browser is not None,
            )
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

    def _dispatch_child_control(
        self, request: ToolUse, context_id: str
    ) -> ChildControlDispatchResult:
        """Execute one Host-owned Child control, including bounded recursion."""
        if self._child_mode and not self._child_delegation_allowed:
            raise RuntimeError("Child controls are unavailable inside this Child")
        parsed = parse_child_control(request)
        state = self._runtime.turn_state.child_control_state
        if state.depth not in {0, 1} or (state.depth == 1 and not state.delegation_allowed):
            raise RuntimeError("recursive Child delegation is unavailable")
        try:
            if parsed.name == CHILD_SPAWN_TOOL_NAME:
                assert parsed.objective is not None
                return self._dispatch_child_spawn(
                    request, context_id=context_id, objective=parsed.objective
                )
            assert parsed.child_run_id is not None
            info = self._child_run_store.inspect(parsed.child_run_id)
            if info.parent_session_id != self._writer.session_id:
                raise ChildRunStoreError("Child Run belongs to another parent Session")
            if parsed.name == CHILD_STATUS_TOOL_NAME:
                return _child_control_success(request, _child_state_payload(info), "child_observed")
            if parsed.name == CHILD_WAIT_TOOL_NAME:
                assert parsed.timeout_seconds is not None
                state.reserve_wait(parsed.timeout_seconds)
                info = self.wait_child_run(parsed.child_run_id, parsed.timeout_seconds)
                payload = _child_state_payload(info)
                if info.status in _CHILD_TERMINAL_STATUSES:
                    handoff = self.deliver_child_handoff(
                        info.child_run_id,
                        source="model",
                        tool_use_id=request.tool_use_id,
                    )
                    payload["handoff"] = {
                        "body": handoff.body,
                        "handoff_sha256": handoff.handoff_sha256,
                        "outcome": handoff.outcome,
                        "truncated": handoff.truncated,
                    }
                return _child_control_success(request, payload, "child_wait_observed")
            if parsed.name == CHILD_CANCEL_TOOL_NAME:
                assert parsed.reason is not None
                info = (
                    self._child_supervisor.cancel(
                        parsed.child_run_id, parsed.reason, source="model"
                    )
                    if self._child_supervisor is not None
                    else self._child_run_store.request_cancel(
                        parsed.child_run_id, reason=parsed.reason, source="model"
                    )
                )
                return _child_control_success(
                    request, _child_state_payload(info), "child_cancel_requested"
                )
            raise ValueError("unsupported Child control")
        except (ValueError, ChildRunStoreError) as error:
            from coquo.child_run_store import ChildRunAppendCommitError, ChildRunCreateCommitError
            from coquo.session_store import SessionAppendCommitError

            if isinstance(
                error,
                (ChildRunAppendCommitError, ChildRunCreateCommitError, SessionAppendCommitError),
            ):
                raise
            return _child_control_error(request, str(error), "child_control_rejected")

    def _dispatch_team_control(
        self, request: ToolUse, context_id: str
    ) -> TeamControlDispatchResult:
        """Dispatch one parent-only Team control through its dedicated audit boundary."""
        if self._child_mode:
            raise RuntimeError("Team controls are unavailable inside a Child")
        parsed = parse_team_control(request)
        state = self._runtime.turn_state.team_control_state
        mutation_names = {
            TEAM_CREATE_TOOL_NAME,
            TEAM_ADD_MEMBER_TOOL_NAME,
            TEAM_MESSAGE_SEND_TOOL_NAME,
            TEAM_MESSAGE_READ_TOOL_NAME,
            TEAM_WORK_CREATE_TOOL_NAME,
            TEAM_SCHEDULE_START_TOOL_NAME,
            TEAM_WORK_REVIEW_TOOL_NAME,
            TEAM_CLOSE_TOOL_NAME,
        }
        try:
            if parsed.name == TEAM_STATUS_TOOL_NAME:
                return _team_control_success(
                    request,
                    _team_status_payload(
                        self._ensure_team_owner(parsed.team_id), workspace=self.workspace
                    ),
                    "team_observed",
                )
            if parsed.name == TEAM_MESSAGE_SHOW_TOOL_NAME:
                state.reserve_message_show()
                assert parsed.team_id is not None and parsed.message_id is not None
                message = self.show_team_message_for_model(
                    parsed.team_id,
                    parsed.message_id,
                    context_id=context_id,
                    tool_use_id=request.tool_use_id,
                )
                return _team_control_success(
                    request,
                    {
                        "body": message.body,
                        "body_sha256": message.body_sha256,
                        "message_id": message.message_id,
                        "team_id": parsed.team_id,
                        "source_assignment_id": message.source_assignment_id,
                        "source_child_session_id": message.source_child_session_id,
                        "source_turn_record_sequence": message.source_turn_record_sequence,
                        "source_handoff_sha256": message.source_handoff_sha256,
                    },
                    "team_reply_delivered",
                    exact_body=True,
                )
            if parsed.name == TEAM_SCHEDULE_WAIT_TOOL_NAME:
                assert parsed.team_id is not None and parsed.schedule_run_id is not None
                assert parsed.timeout_seconds is not None
                state.reserve_wait(parsed.timeout_seconds)
                self._ensure_team_owner(parsed.team_id)
                notification = self.wait_team_schedule(
                    parsed.team_id, parsed.schedule_run_id, parsed.timeout_seconds
                )
                schedule = self._team_schedule_service.status(
                    parsed.team_id, parsed.schedule_run_id
                )
                return _team_control_success(
                    request,
                    _team_schedule_payload(parsed.team_id, schedule, notification),
                    "team_schedule_observed",
                )

            if parsed.name in mutation_names:
                if parsed.name == TEAM_CREATE_TOOL_NAME:
                    state.reserve_create()
                    target_team_id = _uuid4_text(self._action_uuid_factory(), "Team ID")
                elif parsed.name == TEAM_ADD_MEMBER_TOOL_NAME:
                    state.reserve_member_add()
                    assert parsed.team_id is not None
                    target_team_id = self._ensure_team_owner(parsed.team_id).team_id
                elif parsed.name == TEAM_SCHEDULE_START_TOOL_NAME:
                    state.reserve_schedule_start()
                    assert parsed.team_id is not None
                    target_team_id = self._ensure_team_owner(parsed.team_id).team_id
                else:
                    state.reserve_mutation()
                    assert parsed.team_id is not None
                    target_team_id = self._ensure_team_owner(parsed.team_id).team_id
                schedule_run_id = None
                route_fingerprint = None
                child_tool_set_id = None
                max_assignments = None
                max_parallel = None
                per_child_provider_invocations = None
                per_child_tool_requests = None
                per_child_output_tokens = None
                per_child_deadline_seconds = None
                if parsed.name == TEAM_SCHEDULE_START_TOOL_NAME:
                    schedule_run_id = _uuid4_text(
                        self._action_uuid_factory(), "Team schedule run ID"
                    )
                    status = self._manager.status()
                    route_fingerprint = (
                        status.route_fingerprint or binding_from_status(status).route_fingerprint
                    )
                    child_tools = child_tool_set()
                    child_tool_set_id = child_tools.snapshot_id
                    max_assignments = parsed.max_assignments
                    max_parallel = parsed.max_parallel
                    per_child_provider_invocations = CHILD_MAX_PROVIDER_INVOCATIONS
                    per_child_tool_requests = CHILD_MAX_TOOL_REQUESTS
                    per_child_output_tokens = CHILD_MAX_OUTPUT_TOKENS
                    per_child_deadline_seconds = CHILD_DEADLINE_SECONDS
                identity = TeamControlApprovalIdentity(
                    parent_session_id=self._writer.session_id,
                    context_id=context_id,
                    tool_use_id=request.tool_use_id,
                    control_name=parsed.name,
                    canonical_arguments_sha256=canonical_team_arguments_sha256(parsed),
                    target_or_preallocated_team_id=target_team_id,
                    approval_mode=self._approval_mode,
                    schedule_run_id=schedule_run_id,
                    route_fingerprint=route_fingerprint,
                    child_tool_set_id=child_tool_set_id,
                    max_assignments=max_assignments,
                    max_parallel=max_parallel,
                    per_child_provider_invocations=per_child_provider_invocations,
                    per_child_tool_requests=per_child_tool_requests,
                    per_child_output_tokens=per_child_output_tokens,
                    per_child_deadline_seconds=per_child_deadline_seconds,
                )
                preview = TeamControlApprovalPreview(
                    control_name=parsed.name,
                    team_id=target_team_id,
                    summary=_team_control_summary(parsed),
                    provider_id=(self._manager.status().provider_id if route_fingerprint else None),
                    model=(self._manager.status().wire_model if route_fingerprint else None),
                    child_tool_names=tuple(child_tool_set().names) if route_fingerprint else (),
                    max_assignments=max_assignments,
                    max_parallel=max_parallel,
                    per_child_provider_invocations=per_child_provider_invocations,
                    per_child_tool_requests=per_child_tool_requests,
                    per_child_output_tokens=per_child_output_tokens,
                    per_child_deadline_seconds=per_child_deadline_seconds,
                )
                resolution = ApprovalResolution.ACCEPT
                if self._approval_mode is ApprovalMode.ASK:
                    state.pending_approval_identity = identity
                    try:
                        resolution = self._approval_handler(
                            TeamControlApprovalRequest(identity, preview)  # type: ignore[arg-type]
                        )
                    finally:
                        state.pending_approval_identity = None
                    if type(resolution) is not ApprovalResolution:
                        raise ValueError("approval handler returned an invalid resolution")
                outcome = {
                    ApprovalResolution.ACCEPT: ApprovalAuditOutcome.ACCEPTED,
                    ApprovalResolution.REJECT: ApprovalAuditOutcome.REJECTED,
                    ApprovalResolution.CANCEL: ApprovalAuditOutcome.CANCELLED,
                }[resolution]
                decision = TeamControlDecided(
                    sequence=self._writer.state.next_sequence,
                    occurred_at=self._writer.now(),
                    parent_session_id=self._writer.session_id,
                    context_id=context_id,
                    tool_use_id=request.tool_use_id,
                    control_name=parsed.name,
                    target_team_id=target_team_id,
                    team_control_identity_sha256=identity.digest,
                    canonical_arguments_sha256=identity.canonical_arguments_sha256,
                    approval_mode=identity.approval_mode,
                    outcome=outcome,
                    decision_sha256=team_control_decision_sha256(identity, outcome.value),
                )
                self._writer.team_control_decided(decision)
                if resolution is ApprovalResolution.REJECT:
                    return _team_control_error(
                        request, "Team control approval rejected", "team_rejected"
                    )
                if resolution is ApprovalResolution.CANCEL:
                    return _team_control_error(
                        request, "Team control approval cancelled", "team_cancelled"
                    )
                return self._execute_accepted_team_control(
                    request, parsed, target_team_id=target_team_id, schedule_run_id=schedule_run_id
                )
            raise ValueError("Team control name is unsupported")
        except (
            SessionStoreError,
            TeamScheduleError,
            TeamScheduleSupervisorError,
            TeamStoreError,
            ValueError,
        ) as error:
            from coquo.session_store import SessionAppendCommitError

            if isinstance(error, SessionAppendCommitError):
                raise
            return _team_control_error(request, str(error), "team_control_rejected")

    def _execute_accepted_team_control(
        self,
        request: ToolUse,
        parsed,
        *,
        target_team_id: str,
        schedule_run_id: str | None,
    ) -> TeamControlDispatchResult:
        if parsed.name == TEAM_CREATE_TOOL_NAME:
            assert parsed.team_name is not None
            team = self.create_team_preallocated(target_team_id, parsed.team_name)
            return _team_control_success(
                request, {"team_id": team.team_id, "status": team.status.value}, "team_created"
            )
        if parsed.name == TEAM_ADD_MEMBER_TOOL_NAME:
            assert parsed.name_value is not None
            member = self.add_team_member(
                target_team_id, parsed.name_value, role_contract=parsed.role_contract
            )
            return _team_control_success(
                request,
                {
                    "team_id": target_team_id,
                    "member_id": member.member_id,
                    "status": member.status.value,
                    "role": member.role_contract,
                },
                "team_member_added",
            )
        if parsed.name == TEAM_MESSAGE_SEND_TOOL_NAME:
            assert parsed.member_id is not None and parsed.body is not None
            message = self.send_team_message(target_team_id, parsed.member_id, parsed.body)
            return _team_control_success(
                request,
                {
                    "team_id": target_team_id,
                    "message_id": message.message_id,
                    "status": message.status.value,
                },
                "team_message_sent",
            )
        if parsed.name == TEAM_MESSAGE_READ_TOOL_NAME:
            assert parsed.message_id is not None
            message = self.read_team_message_for_model(target_team_id, parsed.message_id)
            return _team_control_success(
                request,
                {
                    "team_id": target_team_id,
                    "message_id": message.message_id,
                    "status": message.status.value,
                },
                "team_message_read",
            )
        if parsed.name == TEAM_WORK_CREATE_TOOL_NAME:
            assert parsed.title is not None and parsed.objective is not None
            item = self.create_team_work(
                target_team_id, parsed.title, parsed.objective, parsed.dependency_ids
            )
            return _team_control_success(
                request,
                {
                    "team_id": target_team_id,
                    "work_item_id": item.work_item_id,
                    "status": item.status.value,
                },
                "team_work_created",
            )
        if parsed.name == TEAM_SCHEDULE_START_TOOL_NAME:
            assert (
                parsed.max_assignments is not None
                and parsed.max_parallel is not None
                and schedule_run_id is not None
            )
            team = self._ensure_team_owner(target_team_id)
            if self._team_schedule_supervisor is None:
                self._team_schedule_supervisor = TeamScheduleSupervisor(self.workspace, self)
            run = self._team_schedule_service.start(
                team.team_id,
                source=TeamScheduleSource.MODEL,
                max_assignments=parsed.max_assignments,
                max_parallel=parsed.max_parallel,
                schedule_run_id=schedule_run_id,
            )
            state = self._team_schedule_supervisor.submit(run)
            return _team_control_success(
                request, _team_schedule_payload(team.team_id, state, None), "team_schedule_started"
            )
        if parsed.name == TEAM_WORK_REVIEW_TOOL_NAME:
            assert parsed.decision is not None and parsed.note is not None
            item = self.review_team_work_for_model(
                target_team_id,
                parsed.work_item_id,
                decision=parsed.decision,
                note=parsed.note,
                message_id=parsed.message_id,
            )
            return _team_control_success(
                request,
                {
                    "team_id": target_team_id,
                    "work_item_id": item.work_item_id,
                    "status": item.status.value,
                },
                "team_work_reviewed",
            )
        if parsed.name == TEAM_CLOSE_TOOL_NAME:
            team = self.close_team(target_team_id)
            return _team_control_success(
                request, {"team_id": team.team_id, "status": team.status.value}, "team_closed"
            )
        raise ValueError("accepted Team control is unsupported")

    def _dispatch_child_spawn(
        self, request: ToolUse, *, context_id: str, objective: str
    ) -> ChildControlDispatchResult:
        state = self._runtime.turn_state.child_control_state
        spawn_number = state.reserve_spawn()
        status = self._manager.status()
        tool_set = child_tool_set()
        route_fingerprint = (
            status.route_fingerprint or binding_from_status(status).route_fingerprint
        )
        identity = DelegationApprovalIdentity(
            parent_session_id=self._writer.session_id,
            context_id=context_id,
            tool_use_id=request.tool_use_id,
            objective_sha256=hashlib.sha256(objective.encode("utf-8")).hexdigest(),
            route_fingerprint=route_fingerprint,
            child_tool_set_id=tool_set.snapshot_id,
            max_provider_invocations=CHILD_MAX_PROVIDER_INVOCATIONS,
            max_tool_requests=CHILD_MAX_TOOL_REQUESTS,
            max_output_tokens=CHILD_MAX_OUTPUT_TOKENS,
            deadline_seconds=CHILD_DEADLINE_SECONDS,
            depth=state.depth + 1,
            parent_child_run_id=(self._current_child_run_id if state.depth == 1 else None),
            root_child_run_id=(self._current_child_run_id if state.depth == 1 else None),
            capability="read-only-explorer-v1",
            approval_mode=self._approval_mode,
        )
        preview = DelegationApprovalPreview(
            objective=objective,
            provider_id=status.provider_id or "fake",
            profile_name=status.profile,
            model=status.wire_model or status.selected_model,
            tool_names=CHILD_TOOL_NAMES,
            max_provider_invocations=CHILD_MAX_PROVIDER_INVOCATIONS,
            max_tool_requests=CHILD_MAX_TOOL_REQUESTS,
            max_output_tokens=CHILD_MAX_OUTPUT_TOKENS,
            deadline_seconds=CHILD_DEADLINE_SECONDS,
            spawn_number=spawn_number,
        )
        resolution = ApprovalResolution.ACCEPT
        if self._approval_mode is ApprovalMode.ASK:
            state.pending_approval_identity = identity
            try:
                resolution = self._approval_handler(DelegationApprovalRequest(identity, preview))
            finally:
                state.pending_approval_identity = None
            if type(resolution) is not ApprovalResolution:
                raise ValueError("approval handler returned an invalid resolution")
        outcome = {
            ApprovalResolution.ACCEPT: ApprovalAuditOutcome.ACCEPTED,
            ApprovalResolution.REJECT: ApprovalAuditOutcome.REJECTED,
            ApprovalResolution.CANCEL: ApprovalAuditOutcome.CANCELLED,
        }[resolution]
        decision = ChildDelegationDecided(
            sequence=self._writer.state.next_sequence,
            occurred_at=self._writer.now(),
            parent_session_id=self._writer.session_id,
            context_id=context_id,
            tool_use_id=request.tool_use_id,
            delegation_identity_sha256=identity.digest,
            objective_sha256=identity.objective_sha256,
            route_fingerprint=identity.route_fingerprint,
            child_tool_set_id=identity.child_tool_set_id,
            depth=identity.depth,
            parent_child_run_id=identity.parent_child_run_id,
            root_child_run_id=identity.root_child_run_id,
            capability=identity.capability,
            approval_mode=identity.approval_mode,
            schema_version=2 if identity.depth == 2 else 1,
            outcome=outcome,
            decision_sha256=delegation_decision_sha256(identity, outcome.value),
        )
        decision = self._writer.child_delegation_decided(decision)
        if resolution is ApprovalResolution.REJECT:
            return _child_control_error(request, "Child delegation rejected", "child_rejected")
        if resolution is ApprovalResolution.CANCEL:
            return _child_control_error(request, "Child delegation cancelled", "child_cancelled")
        delegated = ChildRunDelegated(
            sequence=1,
            child_run_id=self._writer.session_id,
            parent_session_id=self._writer.session_id,
            parent_context_id=context_id,
            parent_tool_use_id=request.tool_use_id,
            decision_record_sequence=decision.sequence,
            decision_sha256=decision.decision_sha256,
            depth=identity.depth,
            source="model",
            delegated_at=self._writer.now(),
            parent_child_run_id=identity.parent_child_run_id,
            root_child_run_id=identity.root_child_run_id,
            capability=identity.capability,
            schema_version=2 if identity.depth == 2 else 1,
        )
        info = self._child_run_store.create(
            objective,
            parent_session=self._writer.session_id,
            delegation=delegated,
        )
        self._project_recursive_child(info)
        state.record_spawn(info.child_run_id)
        info = self.prepare_child_run(info.child_run_id)
        info = self.start_child_run(info.child_run_id)
        return _child_control_success(request, _child_state_payload(info), "child_spawned")

    # ------------------------------------------------------------------
    # Host-owned Task–Child–Team lineage projection
    # ------------------------------------------------------------------
    def _ensure_recursive_tree(self):
        """Load or lazily create the workspace's one bounded lineage tree."""
        try:
            if self._recursive_store.path.exists():
                return self._recursive_store.inspect()
            return self._recursive_store.create(
                permission_mode=self._permission_mode.value,
                objective=f"session:{self._writer.session_id}",
            )
        except RecursiveOrchestrationError as error:
            # Another Session/process may have created the root between the
            # existence check and create.  Re-read only on that benign race;
            # malformed or inaccessible ledgers remain hard errors.
            if self._recursive_store.path.exists():
                return self._recursive_store.inspect()
            raise error

    def _recursive_parent_node(self) -> RecursiveNode:
        tree = self._ensure_recursive_tree()
        if not self._child_mode:
            return tree.root
        if self._current_child_run_id is None:
            raise RecursiveOrchestrationError("Child lineage identity is unavailable")
        node = self._recursive_store.node_for_child_run(self._current_child_run_id)
        if node is None:
            raise RecursiveOrchestrationError("current Child is absent from recursive lineage")
        return node

    @staticmethod
    def _recursive_child_status(status: ChildRunStatus) -> RecursiveNodeStatus:
        if status in {ChildRunStatus.QUEUED, ChildRunStatus.ADMITTED, ChildRunStatus.READY}:
            return RecursiveNodeStatus.QUEUED
        if status in {ChildRunStatus.RUNNING, ChildRunStatus.CANCELLING}:
            return RecursiveNodeStatus.RUNNING
        if status is ChildRunStatus.COMPLETED:
            return RecursiveNodeStatus.COMPLETED
        if status is ChildRunStatus.CANCELLED:
            return RecursiveNodeStatus.CANCELLED
        if status is ChildRunStatus.INTERRUPTED:
            return RecursiveNodeStatus.INTERRUPTED
        return RecursiveNodeStatus.FAILED

    def _project_recursive_child(
        self, info: ChildRunInfo, *, parent_node_id: str | None = None
    ) -> RecursiveNode:
        """Create/reconcile one Child projection and return its current node."""
        node = self._recursive_store.node_for_child_run(info.child_run_id)
        if node is None:
            parent = (
                self._recursive_parent_node()
                if parent_node_id is None
                else next(
                    item
                    for item in self._ensure_recursive_tree().all_nodes
                    if item.node_id == parent_node_id
                )
            )
            depth = info.delegated.depth if info.delegated is not None else parent.depth + 1
            if depth != parent.depth + 1:
                raise RecursiveOrchestrationError("Child delegation depth disagrees with lineage")
            spawn = (
                self._recursive_store.project_child
                if parent_node_id is not None
                and parent.kind in {RecursiveNodeKind.TEAM, RecursiveNodeKind.TASK}
                else self._recursive_store.spawn_child
            )
            role_contract = info.role_contract or (
                info.team_assignment.role_contract if info.team_assignment is not None else None
            )
            node = spawn(
                parent.node_id,
                info.objective,
                permission_mode=(
                    child_role_descriptor(role_contract).permission_mode
                    if role_contract is not None
                    else PermissionMode.READ_ONLY.value
                ),
                capability=(
                    info.delegated.capability
                    if info.delegated is not None
                    else "read-only-explorer-v1"
                ),
                child_run_id=info.child_run_id,
            )
        desired = self._recursive_child_status(info.status)
        if node.status is not desired:
            node = self._recursive_store.transition(node.node_id, desired)
        return node

    def _project_recursive_team(
        self, team: TeamInfo, *, objective: str | None = None
    ) -> RecursiveNode:
        node = self._recursive_store.node_for_team(team.team_id)
        if node is None:
            parent = self._recursive_parent_node()
            node = self._recursive_store.spawn_team(
                parent.node_id,
                objective or team.name,
                permission_mode=self._permission_mode.value,
                team_id=team.team_id,
            )
        desired = (
            RecursiveNodeStatus.COMPLETED
            if team.status is TeamStatus.CLOSED
            else RecursiveNodeStatus.RUNNING
        )
        if node.status is not desired:
            node = self._recursive_store.transition(node.node_id, desired)
        return node

    def _project_recursive_task(self, task: TaskInfo) -> RecursiveNode:
        node = self._recursive_store.node_for_task(task.task_id)
        if node is None:
            parent = self._recursive_parent_node()
            node = self._recursive_store.spawn_task(
                parent.node_id,
                task.objective,
                permission_mode=self._permission_mode.value,
                task_id=task.task_id,
            )
        if task.terminal_outcome is not None:
            desired = (
                RecursiveNodeStatus.COMPLETED
                if task.terminal_outcome.outcome.value == "completed"
                else RecursiveNodeStatus.CANCELLED
            )
        elif task.status is TaskStatus.STAGE_IN_PROGRESS:
            desired = RecursiveNodeStatus.RUNNING
        else:
            desired = RecursiveNodeStatus.QUEUED
        if node.status is not desired:
            node = self._recursive_store.transition(node.node_id, desired)
        return node

    def inspect_recursive_orchestration(self):
        """Return the durable cross-runtime lineage tree for observation."""
        with self._lock:
            self._ensure_open()
            return self._ensure_recursive_tree()

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

    def _dispatch_restricted_child_action(
        self, request: ToolUse, lease: ActionLease
    ) -> ToolDispatchResult:
        """Apply the injected Child Action allowlist before shared Host dispatch."""
        if not self._child_mode or request.name not in self._child_action_names:
            raise RuntimeError("Child Action is outside its immutable allowlist")
        tool_set = self._active_tool_set_snapshot
        if tool_set is None or request.name not in tool_set.names:
            raise RuntimeError("Child Action ToolSet snapshot does not contain the request")
        contract = tool_set.contract(request.name)
        if contract.execution_kind is not ToolExecutionKind.HOST_ACTION:
            raise RuntimeError("Child Action must use a built-in Host action")
        return self._dispatch_action(request, lease)

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
        prepared_browser = None
        prepared_memory: PreparedMemoryAction | None = None
        prepared_mcp: PreparedMcpCall | None = None
        prepared_integration = None
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
        elif request.name == BROWSER_ACTION_TOOL_NAME:
            if self._browser is None:
                return _invalid_tool_request(
                    request, BrowserAutomationError("browser runtime is unavailable")
                )
            try:
                prepared_browser = parse_browser_action(request)
            except (ValueError, BrowserAutomationError) as error:
                return _invalid_tool_request(request, error)
            action = PermissionAction.NETWORK_READ
            precondition = ActionPrecondition.none()
        elif request.name in MEMORY_TOOL_NAMES:
            try:
                prepared_memory = self._memory_tool.prepare(
                    request,
                    self._memory_access_context(),
                    source_session_id=self._writer.session_id,
                    source_turn=len(self._writer.state.turns) + 1,
                )
            except (MemoryStoreError, ValueError) as error:
                return _invalid_tool_request(request, error)
            action = prepared_memory.action
            precondition = ActionPrecondition.none()
        elif request.name == TEAM_WORKTREE_INTEGRATE_TOOL_NAME:
            if self._child_mode:
                return _invalid_tool_request(
                    request, ValueError("team_worktree_integrate is parent-only")
                )
            try:
                parsed = parse_team_worktree_integrate(request)
                prepared_integration = self._worktree_integration.prepare(
                    parsed.team_id,
                    parsed.assignment_id,
                    parsed.expected_patch_sha256,
                )
            except (ValueError, WorktreeIntegrationError) as error:
                return _invalid_tool_request(request, error)
            action = PermissionAction.DANGEROUS
            precondition = ActionPrecondition.worktree_integration(
                prepared_integration.precondition_sha256
            )
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
            **self._action_scope_fields(),
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
        elif prepared_browser is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.BROWSER_ACTION,
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
        elif prepared_integration is not None:
            approval_preview = build_metadata_preview(
                action_digest=identity.digest,
                kind=ApprovalPreviewKind.TEAM_WORKTREE_INTEGRATION,
            )
        coordinator = ActionCoordinator(
            writer=self._writer,
            approval_handler=self._approval_handler,
            uuid_factory=self._action_uuid_factory,
        )
        command_observation: RunCommandExecutionObservation | None = None
        mcp_observation: McpRuntimeExecution | None = None

        def revalidate(current: ActionIdentity) -> ActionIdentity:
            nonlocal prepared_integration
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
            if prepared_browser is not None:
                return current
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
            if prepared_integration is not None:
                refreshed_request = parse_team_worktree_integrate(request)
                refreshed = self._worktree_integration.prepare(
                    refreshed_request.team_id,
                    refreshed_request.assignment_id,
                    refreshed_request.expected_patch_sha256,
                )
                prepared_integration = refreshed
                return replace(
                    current,
                    precondition=ActionPrecondition.worktree_integration(
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
            if prepared_integration is not None:
                try:
                    integrated = self._worktree_integration.integrate(
                        prepared_integration,
                        action_digest=current.digest,
                    )
                except WorktreeIntegrationError as error:
                    message = str(error)[:4096]
                    payload = json.dumps(
                        {
                            "status": "failed",
                            "result_code": "integration_failed",
                            "message": message,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    return ActionExecutionResult(
                        tool_result=ToolResult(request.tool_use_id, payload, is_error=True),
                        outcome=ActionExecutionOutcome.FAILED,
                        result_code="integration_failed",
                        audit_message=f"team worktree integration failed: {message}",
                    )
                payload = json.dumps(
                    {
                        "assignment_id": integrated.assignment_id,
                        "changed_paths": integrated.changed_paths,
                        "manifest_sha256": integrated.manifest_sha256,
                        "patch_sha256": integrated.patch_sha256,
                        "result_code": integrated.result_code,
                        "status": integrated.status,
                        "target_head": integrated.target_head,
                        "target_ref": integrated.target_ref,
                        "team_id": integrated.team_id,
                        "worktree_id": integrated.worktree_id,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                return ActionExecutionResult(
                    tool_result=ToolResult(request.tool_use_id, payload),
                    outcome=ActionExecutionOutcome.SUCCEEDED,
                    result_code=integrated.result_code,
                    audit_message=(
                        "sealed Team worktree patch applied; authority changes remain uncommitted"
                    ),
                )
            if prepared_memory is not None:
                result = self._memory_tool.execute(prepared_memory)
                return ActionExecutionResult(
                    tool_result=result,
                    outcome=(
                        ActionExecutionOutcome.FAILED
                        if result.is_error
                        else ActionExecutionOutcome.SUCCEEDED
                    ),
                    result_code="memory_error" if result.is_error else "memory_ok",
                    audit_message=(
                        "memory operation failed"
                        if result.is_error
                        else "memory operation succeeded"
                    ),
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
            elif request.name == BROWSER_ACTION_TOOL_NAME and prepared_browser is not None:
                if self._browser is None:
                    return ActionExecutionResult(
                        tool_result=ToolResult(
                            request.tool_use_id,
                            "browser runtime is unavailable",
                            is_error=True,
                        ),
                        outcome=ActionExecutionOutcome.FAILED,
                        result_code="browser_unavailable",
                        audit_message=(
                            "browser action failed because no Host browser runtime is configured"
                        ),
                    )
                try:
                    browser_result = self._execute_browser_action(prepared_browser)
                except BrowserAutomationError as error:
                    return ActionExecutionResult(
                        tool_result=ToolResult(
                            request.tool_use_id,
                            str(error)[:4096],
                            is_error=True,
                        ),
                        outcome=ActionExecutionOutcome.FAILED,
                        result_code="browser_policy_error",
                        audit_message="browser action was rejected by the Host browser policy",
                    )
                succeeded = browser_result.outcome == "completed"
                return ActionExecutionResult(
                    tool_result=ToolResult(
                        request.tool_use_id,
                        _browser_observation_payload(browser_result),
                        is_error=not succeeded,
                    ),
                    outcome=(
                        ActionExecutionOutcome.SUCCEEDED
                        if succeeded
                        else ActionExecutionOutcome.FAILED
                    ),
                    result_code="browser_ok" if succeeded else "browser_failed",
                    audit_message=(
                        "browser action completed" if succeeded else "browser backend action failed"
                    ),
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

    def _execute_browser_action(self, request):
        """Execute one parsed Browser action through the injected Host runtime."""
        browser = self._browser
        if browser is None:
            raise BrowserAutomationError("browser runtime is unavailable")
        if request.action is BrowserAction.NAVIGATE:
            assert request.url is not None
            return browser.navigate(request.url)
        if request.action is BrowserAction.CLICK:
            assert request.selector is not None
            return browser.click(request.selector)
        if request.action is BrowserAction.FILL:
            assert request.selector is not None and request.value is not None
            return browser.fill(request.selector, request.value)
        if request.action is BrowserAction.EXTRACT_TEXT:
            return browser.extract_text(request.selector)
        if request.action is BrowserAction.SCREENSHOT:
            return browser.screenshot()
        raise BrowserAutomationError("browser action is unsupported")

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
            memory_evidence=context.memory_evidence,
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
            # Reliability retries are physical attempts inside one logical
            # invocation and must not consume the AgentLoop invocation budget.
            used = attempts
            if used >= MAX_PROVIDER_INVOCATIONS_PER_TURN:
                break
            if self._active_cancellation is not None:
                self._active_cancellation.check()
            self._emit_prompt_event(
                self._active_event_sink,
                SessionTitleGenerationStarted(attempt, SESSION_TITLE_MAX_ATTEMPTS),
            )
            # The hook runs after the first AgentLoop response has already
            # consumed invocation 1; title attempts occupy the following
            # logical invocation slots before the loop continues.
            invocation_index = used + 2
            self._emit_prompt_event(
                self._active_event_sink,
                ProviderInvocationStarted(
                    invocation_index,
                    MAX_PROVIDER_INVOCATIONS_PER_TURN,
                    ProviderInvocationPurpose.SESSION_TITLE,
                ),
            )
            started = time.monotonic_ns()
            attempts += 1
            try:
                response = runtime.generate_session_title(
                    build_session_title_request(
                        title_source_text,
                        rejected_titles=tuple(rejected),
                    )
                )
                if self._active_cancellation is not None:
                    self._active_cancellation.check()
            except TurnCancelled:
                self._emit_prompt_event(
                    self._active_event_sink,
                    ProviderInvocationFinished(
                        invocation_index,
                        MAX_PROVIDER_INVOCATIONS_PER_TURN,
                        ProviderInvocationOutcome.CANCELLED,
                        elapsed_milliseconds=_provider_elapsed_milliseconds(started),
                        purpose=ProviderInvocationPurpose.SESSION_TITLE,
                    ),
                )
                raise
            except ProviderAdapterError as error:
                self._emit_prompt_event(
                    self._active_event_sink,
                    ProviderInvocationFinished(
                        invocation_index,
                        MAX_PROVIDER_INVOCATIONS_PER_TURN,
                        ProviderInvocationOutcome.FAILED,
                        elapsed_milliseconds=_provider_elapsed_milliseconds(started),
                        purpose=ProviderInvocationPurpose.SESSION_TITLE,
                    ),
                )
                fallback_reason = (
                    SessionTitleFallbackReason.PROVIDER_OUTPUT_LIMIT
                    if error.failure.kind == ProviderFailureKind.OUTPUT_LIMIT
                    else SessionTitleFallbackReason.PROVIDER_FAILURE
                )
                break
            except Exception:
                self._emit_prompt_event(
                    self._active_event_sink,
                    ProviderInvocationFinished(
                        invocation_index,
                        MAX_PROVIDER_INVOCATIONS_PER_TURN,
                        ProviderInvocationOutcome.FAILED,
                        elapsed_milliseconds=_provider_elapsed_milliseconds(started),
                        purpose=ProviderInvocationPurpose.SESSION_TITLE,
                    ),
                )
                fallback_reason = SessionTitleFallbackReason.PROVIDER_FAILURE
                break
            self._emit_prompt_event(
                self._active_event_sink,
                ProviderInvocationFinished(
                    invocation_index,
                    MAX_PROVIDER_INVOCATIONS_PER_TURN,
                    ProviderInvocationOutcome.FINAL_TEXT,
                    elapsed_milliseconds=_provider_elapsed_milliseconds(started),
                    purpose=ProviderInvocationPurpose.SESSION_TITLE,
                ),
            )
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
        self._emit_prompt_event(self._active_event_sink, TurnCommitCompleted())
        if self._memory_candidate_extractor is not None:
            self._run_memory_candidate_extraction(writer, turn)
        self._record_evolution_trace(writer, turn)
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

    def _run_memory_candidate_extraction(self, writer: SessionWriter, turn: CommittedTurn) -> None:
        extractor = self._memory_candidate_extractor
        if extractor is None:
            return
        prepared = extractor.prepare(turn)
        if prepared is None:
            return
        lease = self._active_action_lease
        binding = self._active_action_binding
        if lease is None or binding is None:
            self._memory_observations.record(
                "candidate_extraction", "failed", actor="host", reason="missing_turn_identity"
            )
            return

        source_turn = len(writer.state.turns)
        identity = ActionIdentity(
            request_id=_uuid4_text(self._action_uuid_factory(), "action request ID"),
            tool_use_id=f"host-memory-candidate-extract-{source_turn}",
            tool_name="memory_candidate_extract",
            arguments=ToolArguments.from_mapping({"source_turn": source_turn}),
            action=PermissionAction.WORKSPACE_CREATE,
            workspace_fingerprint=self._session_store.workspace_fingerprint,
            lease=lease,
            precondition=ActionPrecondition.none(),
            **self._action_scope_fields(),
        )

        def execute(_identity: ActionIdentity) -> ActionExecutionResult:
            result = extractor.after_commit(
                turn,
                session_id=writer.session_id,
                source_turn=source_turn,
                authorized=True,
                prepared=prepared,
            )
            if result.partial:
                outcome = ActionExecutionOutcome.PARTIAL
                code = "memory_candidate_partial"
                is_error = True
            elif result.memory_id is None:
                outcome = ActionExecutionOutcome.FAILED
                code = "memory_candidate_failed"
                is_error = True
            else:
                outcome = ActionExecutionOutcome.SUCCEEDED
                code = (
                    "memory_candidate_confirmed" if result.confirmed else "memory_candidate_created"
                )
                is_error = False
            return ActionExecutionResult(
                ToolResult(identity.tool_use_id, code, is_error=is_error),
                outcome,
                code,
                "automatic memory candidate extraction completed"
                if not is_error
                else "automatic memory candidate extraction did not complete",
            )

        coordinated = ActionCoordinator(
            writer=writer,
            approval_handler=self._approval_handler,
            uuid_factory=self._action_uuid_factory,
        ).run(
            identity=identity,
            binding=binding,
            permission_mode=self._permission_mode,
            approval_mode=self._approval_mode,
            revalidate=lambda current: current,
            execute=execute,
        )
        if not coordinated.executed:
            self._memory_observations.record(
                "candidate_extraction",
                "denied",
                actor="host",
                reason=(
                    "permission_denied"
                    if coordinated.permission_result.decision.value == "deny"
                    else "approval_not_granted"
                ),
            )

    def _record_evolution_trace(self, writer: SessionWriter, turn: CommittedTurn) -> None:
        """Record content-free committed-turn facts without affecting commit truth."""
        unsuccessful = {
            "error",
            "denied",
            "rejected",
            "cancelled",
            "failed",
            "partial",
            "outcome-unknown",
            "skipped-after-failure",
            "rejected-over-budget",
        }
        failures = sum(entry.outcome.value in unsuccessful for entry in turn.tool_ledger.entries)
        if not turn.tool_ledger.entries or failures == 0:
            outcome = EvolutionOutcome.SUCCESS
        elif failures == len(turn.tool_ledger.entries):
            outcome = EvolutionOutcome.FAILED
        else:
            outcome = EvolutionOutcome.PARTIAL
        summary = (
            f"committed turn with {turn.tool_ledger.requested} tool requests and "
            f"{failures} unsuccessful outcomes"
        )
        workflow = tuple(
            f"{entry.tool_name}:{entry.outcome.value}" for entry in turn.tool_ledger.entries
        )
        trace_inputs = {
            "source_session_id": writer.session_id,
            "source_turn": len(writer.state.turns),
            "metrics": {
                "success_rate": 0.0 if failures else 1.0,
                "tool_requests": turn.tool_ledger.requested,
                "tool_failures": failures,
            },
            "workflow": workflow,
        }
        # Each target receives an independent content-free trace.  Evolution
        # is diagnostic state: a single failed target must not suppress the
        # others or turn a durable Session commit into an ambiguous outcome.
        for target in (EvolutionTarget.WORKFLOW, EvolutionTarget.MEMORY, EvolutionTarget.PROMPT):
            try:
                trace = self._evolution.record_trace(target, outcome, summary, **trace_inputs)
                if target is EvolutionTarget.WORKFLOW:
                    if self._skill_evolution is not None:
                        self._skill_evolution.ingest_trace(trace)
                        self._skill_evolution.observe_turn(turn)
                    if self._strategy_evolution is not None:
                        self._strategy_evolution.ingest_trace(trace)
                elif target is EvolutionTarget.MEMORY and self._memory_evolution is not None:
                    self._memory_evolution.ingest_trace(trace)
                elif target is EvolutionTarget.PROMPT and self._strategy_evolution is not None:
                    self._strategy_evolution.ingest_trace(trace)
            except Exception:
                continue

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


def _turn_observation_context(session_id: str) -> ObservationContext:
    """Create process-local correlation for one ordinary Agent Turn."""
    turn_id = str(uuid4())
    return ObservationContext(trace_id=turn_id, session_id=session_id, turn_id=turn_id)


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
        reasoning_effort=status.reasoning_effort,
    )


_CHILD_TERMINAL_STATUSES = frozenset(
    {
        ChildRunStatus.CANCELLED,
        ChildRunStatus.COMPLETED,
        ChildRunStatus.FAILED,
        ChildRunStatus.INTERRUPTED,
    }
)


def _child_state_payload(info: ChildRunInfo) -> dict[str, object]:
    return {
        "child_run_id": info.child_run_id,
        "child_session_id": info.child_session_id,
        "status": info.status.value,
    }


def _child_control_success(
    request: ToolUse, payload: dict[str, object], result_code: str
) -> ChildControlDispatchResult:
    body = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    return ChildControlDispatchResult(
        ToolDispatchResult(
            ToolResult(request.tool_use_id, body),
            ToolEventStatus.SUCCEEDED,
            result_code,
        )
    )


def _child_control_error(
    request: ToolUse, message: str, result_code: str
) -> ChildControlDispatchResult:
    bounded = message.replace("\r", " ").replace("\n", " ")[:512] or "Child control failed"
    body = json.dumps(
        {"error": result_code, "message": bounded},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ChildControlDispatchResult(
        ToolDispatchResult(
            ToolResult(request.tool_use_id, body, is_error=True),
            ToolEventStatus.REJECTED,
            result_code,
        )
    )


def _team_control_success(
    request: ToolUse,
    payload: dict[str, object],
    result_code: str,
    *,
    exact_body: bool = False,
) -> TeamControlDispatchResult:
    body = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    if len(body.encode("utf-8")) > MAX_TEAM_RESULT_BYTES:
        raise ValueError("Team control result exceeds its 40 KiB bound")
    return TeamControlDispatchResult(
        ToolDispatchResult(
            ToolResult(request.tool_use_id, body), ToolEventStatus.SUCCEEDED, result_code
        )
    )


def _team_control_error(
    request: ToolUse, message: str, result_code: str
) -> TeamControlDispatchResult:
    bounded = message.replace("\r", " ").replace("\n", " ")[:512] or "Team control failed"
    body = json.dumps(
        {"error": result_code, "message": bounded},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return TeamControlDispatchResult(
        ToolDispatchResult(
            ToolResult(request.tool_use_id, body, is_error=True),
            ToolEventStatus.REJECTED,
            result_code,
        )
    )


def _team_status_payload(team: TeamInfo, *, workspace: Path | None = None) -> dict[str, object]:
    worktrees: list[dict[str, object]] = []
    if workspace is not None:
        service = WorktreeService(workspace)
        for assignment in team.assignments:
            if assignment.worktree_id is None:
                continue
            try:
                info = service.store.inspect(assignment.worktree_id)
                worktrees.append(
                    {
                        "worktree_id": info.worktree_id,
                        "assignment_id": info.header.assignment_id,
                        "member_id": info.header.member_id,
                        "role": info.header.role_contract,
                        "state": info.state,
                        "target_ref": info.header.target_ref,
                        "base_commit": info.header.base_commit,
                        "branch": info.header.branch,
                        "sealed_patch_sha256": (
                            None if info.sealed is None else info.sealed.patch_sha256
                        ),
                        "sealed_manifest_sha256": (
                            None if info.sealed is None else info.sealed.manifest_sha256
                        ),
                        "integration": (
                            None
                            if info.integration is None
                            else {
                                "target_ref": info.integration.target_ref,
                                "target_head": info.integration.target_head,
                                "result_code": info.integration.result_code,
                            }
                        ),
                    }
                )
            except (WorktreeStoreError, WorktreeServiceError):
                worktrees.append(
                    {
                        "worktree_id": assignment.worktree_id,
                        "assignment_id": assignment.assignment_id,
                        "member_id": assignment.member_id,
                        "state": "unavailable",
                    }
                )
    return {
        "team_id": team.team_id,
        "status": team.status.value,
        "members": tuple(
            {
                "member_id": member.member_id,
                "name": member.name,
                "status": member.status.value,
                "role": member.role_contract,
            }
            for member in team.members
        ),
        "worktrees": tuple(worktrees[:32]),
        "schedules": tuple(
            {
                "schedule_run_id": schedule.schedule_run_id,
                "status": schedule.status.value,
                "assignment_count": schedule.assignment_count,
                "result_code": schedule.result_code,
            }
            for schedule in team.schedules[-1:]
        ),
        "work": tuple(
            {
                "work_item_id": item.work_item_id,
                "status": item.status.value,
                "current_assignment_id": item.current_assignment_id,
            }
            for item in team.work_items
            if item.status.value not in {"completed", "cancelled"}
        )[:32],
        "unread_reply_ids": tuple(
            {"message_id": message.message_id, "body_sha256": message.body_sha256}
            for message in team.messages
            if message.sender_member_id is not None and message.status.value == "unread"
        ),
    }


def _team_schedule_payload(team_id: str, schedule, notification) -> dict[str, object]:
    state = (
        notification.state
        if notification is not None and notification.state is not None
        else schedule
    )
    payload: dict[str, object] = {"team_id": team_id}
    if state is None:
        payload["status"] = "unknown"
        return payload
    payload.update(
        {
            "schedule_run_id": state.schedule_run_id,
            "status": state.status.value,
            "assignment_count": state.assignment_count,
            "assignment_ids": state.assignment_ids,
            "result_code": state.result_code,
            "message": state.message,
        }
    )
    return payload


def _team_control_summary(parsed) -> str:
    values = [parsed.name]
    for value in (parsed.team_id, parsed.member_id, parsed.work_item_id, parsed.message_id):
        if value is not None:
            values.append(value)
    if parsed.name_value is not None:
        values.append(f"name={parsed.name_value}")
    if parsed.decision is not None:
        values.append(f"decision={parsed.decision}")
    return " ".join(values)


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


def _browser_observation_payload(observation: BrowserObservation) -> str:
    """Serialize bounded browser output as explicitly untrusted evidence."""
    payload = {
        "action": observation.action.value,
        "evidence": "untrusted",
        "outcome": observation.outcome,
        "step": observation.step,
        "value": observation.value,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _provider_elapsed_milliseconds(started: int) -> int:
    elapsed = max(0, (time.monotonic_ns() - started) // 1_000_000)
    return min(elapsed, 86_400_000)


def _safe_failure_message(error: BaseException) -> str:
    if isinstance(error, ContextPreflightError):
        return str(error)[:4096]
    if isinstance(error, ProviderAdapterError):
        return error.failure.message[:4096]
    if isinstance(error, SessionStoreError):
        return str(error)[:4096]
    return type(error).__name__
