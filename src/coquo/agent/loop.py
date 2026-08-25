"""The bounded orchestration loop for the current sequential tool surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import json
import time

from coquo.agent.tool_events import (
    AgentPromptEvent,
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    AssistantToolTextStreamCompleted,
    AssistantToolTextReceived,
    HookLifecycleObserved,
    ProviderInvocationFinished,
    ProviderInvocationOutcome,
    ProviderInvocationPreflighted,
    ProviderInvocationStarted,
    ProviderInvocationUsageReceived,
    ProviderSearchActivityReceived,
    ProviderSearchSummaryReceived,
    ToolDispatchResult,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestSkipped,
    ToolRequestStarted,
    ToolTurnSummaryCommitted,
    infer_tool_dispatch_result,
    safe_result_code,
    safe_tool_request_details,
    safe_tool_request_summary,
)
from coquo.agent.task_control import (
    TaskControlDispatcher,
    TaskControlDispatchResult,
    TaskProposal,
    TaskProposalSink,
)
from coquo.agent.child_control import ChildControlDispatcher, ChildControlDispatchResult
from coquo.agent.team_control import TeamControlDispatcher, TeamControlDispatchResult
from coquo.core.actions import ActionLease
from coquo.core.compaction import EffectiveContextSummary
from coquo.core.cancellation import TurnCancellation, TurnCancelled
from coquo.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    CommittedTurn,
    ConversationItem,
    ConversationProvider,
    ConversationRequest,
    ConversationTurn,
    MemoryEvidence,
    ProviderResponseEnvelope,
    SystemPromptSnapshot,
    ToolOutcomeEntry,
    ToolAttemptUsage,
    ToolRequestOutcome,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    TurnCommitter,
    UserMessage,
)
from coquo.core.effective_context import (
    COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
    EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY,
    EffectiveContextSnapshot,
    validate_complete_history,
)
from coquo.core.project_instructions import ProjectInstructionsSnapshot
from coquo.core.extensions import (
    ExtensionSourceKind,
    ToolExecutionKind,
    ToolExposure,
    ToolRegistrySnapshot,
    ToolSetSnapshot,
)
from coquo.skills import (
    MAX_ACTIVE_SKILLS,
    MAX_ACTIVE_SKILL_INSTRUCTION_BYTES,
    MAX_SKILL_LOADS_PER_TURN,
    ActiveSkill,
    SkillInventorySnapshot,
    active_skills_from_history,
)
from coquo.hooks import HookSetSnapshot, evaluate_lifecycle_event
from coquo.core.hook_contracts import (
    HookAuditLedger,
    HookEvent,
    aggregate_hook_effect,
)
from coquo.tools.skill_discovery import (
    SKILL_LOAD_TOOL_NAME,
    SKILL_READ_RESOURCE_TOOL_NAME,
    SKILL_SEARCH_TOOL_NAME,
)
from coquo.providers.streaming import (
    ProviderResponseOutcome,
    ProviderSearchActivity,
    ProviderTextDelta,
    respond_with_streaming,
)
from coquo.providers.request_context import ContextFitReport
from coquo.system_prompt import build_system_prompt
from coquo.tools.catalog import (
    MAX_PROVIDER_INVOCATIONS_PER_TURN,
    MAX_TOOL_CALLS_PER_RESPONSE,
    MAX_TOOL_REQUESTS_PER_TURN,
    TOOL_REGISTRY_SNAPSHOT,
    tool_input_from_use,
)
from coquo.tools.archive_list import ARCHIVE_LIST_TOOL_NAME, ArchiveListTool
from coquo.tools.checksum_file import CHECKSUM_FILE_TOOL_NAME, ChecksumFileTool
from coquo.tools.compare_files import COMPARE_FILES_TOOL_NAME, CompareFilesTool
from coquo.tools.glob import GLOB_TOOL_NAME, GlobTool
from coquo.tools.grep import GREP_TOOL_NAME, GrepTool
from coquo.tools.grep_regex import GREP_REGEX_TOOL_NAME, GrepRegexTool
from coquo.tools.git_diff import GIT_DIFF_TOOL_NAME, GitDiffTool
from coquo.tools.git_log import GIT_LOG_TOOL_NAME, GitLogTool
from coquo.tools.git_show import GIT_SHOW_TOOL_NAME, GitShowTool
from coquo.tools.git_status import GIT_STATUS_TOOL_NAME, GitStatusTool
from coquo.tools.git_blame import GIT_BLAME_TOOL_NAME, GitBlameTool
from coquo.tools.git_refs import GIT_REFS_TOOL_NAME, GitRefsTool
from coquo.tools.json_query import JSON_QUERY_TOOL_NAME, JsonQueryTool
from coquo.tools.list_directory import LIST_DIRECTORY_TOOL_NAME, ListDirectoryTool
from coquo.tools.list_tree import LIST_TREE_TOOL_NAME, ListTreeTool
from coquo.tools.read_file import READ_FILE_TOOL_NAME, ReadFileTool
from coquo.tools.read_file_lines import READ_FILE_LINES_TOOL_NAME, ReadFileLinesTool
from coquo.tools.stat_path import STAT_PATH_TOOL_NAME, StatPathTool
from coquo.tools.tool_discovery import TOOL_PROMOTE_TOOL_NAME, TOOL_SEARCH_TOOL_NAME

SystemPromptFactory = Callable[[], SystemPromptSnapshot]
ProjectInstructionsFactory = Callable[[], ProjectInstructionsSnapshot | None]
ToolRegistryFactory = Callable[[], ToolRegistrySnapshot]
SkillInventoryFactory = Callable[[], SkillInventorySnapshot]
SkillResourceReader = Callable[..., str]
ActionDispatcher = Callable[[ToolUse, ActionLease], ToolResult | ToolDispatchResult]
AgentEventSink = Callable[[AgentPromptEvent], None]
ToolUsageSink = Callable[[ToolAttemptUsage], None]
FirstProviderResponseHook = Callable[[], int]
MemoryRecallFactory = Callable[[str], tuple[MemoryEvidence, ...]]


def _no_project_instructions() -> None:
    return None


def _builtin_tool_registry() -> ToolRegistrySnapshot:
    return TOOL_REGISTRY_SNAPSHOT


def _empty_skill_inventory() -> SkillInventorySnapshot:
    return SkillInventorySnapshot((), ())


def _empty_hook_set() -> HookSetSnapshot:
    return HookSetSnapshot(())


class ToolLoopLimitError(RuntimeError):
    """Raised when a provider does not finish after its tool-call budget is exhausted."""


class TaskControlProtocolError(RuntimeError):
    """Raised when a provider violates the isolated coordination-call contract."""


class ToolDiscoveryProtocolError(RuntimeError):
    """Raised when a provider violates the isolated discovery-call contract."""


@dataclass(frozen=True)
class ToolDiscoveryDispatchResult:
    dispatch: ToolDispatchResult
    discovered_names: tuple[str, ...] = ()
    discovered_skills: tuple[tuple[str, str], ...] = ()
    promoted_snapshot: ToolSetSnapshot | None = None
    activated_skill: ActiveSkill | None = None


ToolSetTransitionDispatcher = Callable[["PreparedAgentTurn", ToolSetSnapshot], "PreparedAgentTurn"]


@dataclass(frozen=True)
class PreparedAgentTurn:
    """One pending user item pinned to one committed Effective Context."""

    user: UserMessage
    context: EffectiveContextSnapshot
    pending_items: tuple[ConversationItem, ...]
    registry_snapshot: ToolRegistrySnapshot
    tool_set_snapshot: ToolSetSnapshot
    skill_inventory_snapshot: SkillInventorySnapshot
    hook_set_snapshot: HookSetSnapshot
    allow_tools: bool = True
    enabled_tool_names: tuple[str, ...] | None = None
    action_lease: ActionLease | None = None

    def __post_init__(self) -> None:
        if self.pending_items != (self.user,):
            raise ValueError("prepared turn must contain exactly its pending user message")
        if type(self.allow_tools) is not bool:
            raise ValueError("prepared turn tool exposure flag is invalid")
        if not isinstance(self.tool_set_snapshot, ToolSetSnapshot):
            raise ValueError("prepared turn tool set snapshot is invalid")
        if not isinstance(self.registry_snapshot, ToolRegistrySnapshot):
            raise ValueError("prepared turn registry snapshot is invalid")
        if not isinstance(self.skill_inventory_snapshot, SkillInventorySnapshot):
            raise ValueError("prepared turn Skill inventory snapshot is invalid")
        if not isinstance(self.hook_set_snapshot, HookSetSnapshot):
            raise ValueError("prepared turn Hook set snapshot is invalid")
        if (
            self.registry_snapshot.snapshot_id != self.tool_set_snapshot.registry_id
            or self.registry_snapshot.generation != self.tool_set_snapshot.registry_generation
        ):
            raise ValueError("prepared turn registry does not match its tool set snapshot")
        if (
            self.context.tool_definitions != self.tool_set_snapshot.definitions
            or self.context.tool_set_id != self.tool_set_snapshot.snapshot_id
            or self.context.skill_inventory_id != self.skill_inventory_snapshot.snapshot_id
            or self.context.hook_set_id != self.hook_set_snapshot.snapshot_id
        ):
            raise ValueError("prepared turn context does not match its tool set snapshot")
        if self.enabled_tool_names is not None:
            if not self.allow_tools:
                raise ValueError("disabled prepared turn cannot select enabled tools")
            available = tuple(definition.name for definition in self.context.tool_definitions)
            if (
                not isinstance(self.enabled_tool_names, tuple)
                or not self.enabled_tool_names
                or len(set(self.enabled_tool_names)) != len(self.enabled_tool_names)
                or any(name not in available for name in self.enabled_tool_names)
            ):
                raise ValueError("prepared turn enabled tools are invalid")
            if self.enabled_tool_names != self.tool_set_snapshot.names:
                raise ValueError("prepared turn enabled tools do not match its tool set snapshot")

    @property
    def initial_request(self) -> ConversationRequest:
        return self.context.to_conversation_request(
            pending_items=self.pending_items,
            allow_tools=self.allow_tools,
            enabled_tool_names=self.enabled_tool_names,
        )

    def rebase(self, context: EffectiveContextSnapshot) -> PreparedAgentTurn:
        if self.action_lease is not None:
            raise ValueError("a leased prepared turn cannot be rebased")
        rebased = replace(
            context,
            tool_definitions=self.tool_set_snapshot.definitions,
            tool_set_id=self.tool_set_snapshot.snapshot_id,
        )
        return replace(self, context=rebased)

    def advance_tool_set(self, snapshot: ToolSetSnapshot) -> PreparedAgentTurn:
        """Install one explicit later epoch before an action lease is issued."""
        if self.action_lease is not None:
            raise ValueError("a leased prepared turn cannot advance its tool set")
        if not isinstance(snapshot, ToolSetSnapshot):
            raise ValueError("advanced tool set snapshot is invalid")
        if (
            snapshot.registry_id != self.tool_set_snapshot.registry_id
            or snapshot.registry_generation != self.tool_set_snapshot.registry_generation
            or snapshot.epoch <= self.tool_set_snapshot.epoch
        ):
            raise ValueError("advanced tool set is not a later compatible epoch")
        old_names = set(self.tool_set_snapshot.names)
        new_names = set(snapshot.names)
        if not (old_names.issubset(new_names) or new_names.issubset(old_names)):
            raise ValueError("advanced tool set must be a pure promotion or restriction")
        context = replace(
            self.context,
            tool_definitions=snapshot.definitions,
            tool_set_id=snapshot.snapshot_id,
        )
        return replace(
            self,
            context=context,
            tool_set_snapshot=snapshot,
            enabled_tool_names=snapshot.names,
        )

    def retire_action_lease(self) -> PreparedAgentTurn:
        """Return an unleased copy for one Host-controlled ToolSet transition."""
        if self.action_lease is None:
            raise ValueError("prepared turn has no action lease to retire")
        return replace(self, action_lease=None)

    def with_action_lease(self, lease: ActionLease) -> PreparedAgentTurn:
        """Bind one non-recreatable lease after automatic compaction is complete."""
        if self.action_lease is not None:
            raise ValueError("prepared turn already has an action lease")
        if lease.context_id != self.context.context_id:
            raise ValueError("action lease context does not match prepared turn")
        return replace(self, action_lease=lease)


class AgentLoop:
    """Maintain atomic in-memory turns across a bounded provider/tool loop."""

    def __init__(
        self,
        provider: ConversationProvider | None,
        read_file: ReadFileTool,
        glob: GlobTool,
        grep: GrepTool,
        list_directory: ListDirectoryTool,
        read_file_lines: ReadFileLinesTool | None = None,
        stat_path: StatPathTool | None = None,
        list_tree: ListTreeTool | None = None,
        grep_regex: GrepRegexTool | None = None,
        *,
        git_status: GitStatusTool | None = None,
        git_diff: GitDiffTool | None = None,
        git_log: GitLogTool | None = None,
        git_show: GitShowTool | None = None,
        compare_files: CompareFilesTool | None = None,
        git_blame: GitBlameTool | None = None,
        git_refs: GitRefsTool | None = None,
        json_query: JsonQueryTool | None = None,
        checksum_file: ChecksumFileTool | None = None,
        archive_list: ArchiveListTool | None = None,
        initial_history: tuple[ConversationItem, ...] = (),
        initial_effective_history: tuple[ConversationItem, ...] | None = None,
        initial_effective_summary: EffectiveContextSummary | None = None,
        initial_effective_source: str = EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY,
        commit_turn: TurnCommitter | None = None,
        system_prompt_factory: SystemPromptFactory = build_system_prompt,
        project_instructions_factory: ProjectInstructionsFactory = _no_project_instructions,
        tool_registry_factory: ToolRegistryFactory = _builtin_tool_registry,
        skill_inventory_factory: SkillInventoryFactory = _empty_skill_inventory,
        hook_set_factory: Callable[[], HookSetSnapshot] = _empty_hook_set,
        skill_resource_reader: SkillResourceReader | None = None,
        action_dispatcher: ActionDispatcher | None = None,
        memory_recall_factory: MemoryRecallFactory | None = None,
    ) -> None:
        """Store a provider, confined tool, validated history, and durable commit hook."""
        self._provider = provider
        self._read_file = read_file
        self._glob = glob
        self._grep = grep
        self._list_directory = list_directory
        self._read_file_lines = read_file_lines
        self._stat_path = stat_path
        self._list_tree = list_tree
        self._grep_regex = grep_regex
        self._git_status = git_status
        self._git_diff = git_diff
        self._git_log = git_log
        self._git_show = git_show
        self._compare_files = compare_files
        self._git_blame = git_blame
        self._git_refs = git_refs
        self._json_query = json_query
        self._checksum_file = checksum_file
        self._archive_list = archive_list
        restored = validate_complete_history(initial_history)
        effective_items = (
            restored.history if initial_effective_history is None else initial_effective_history
        )
        validate_complete_history(effective_items)
        if initial_effective_source == EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY:
            if initial_effective_summary is not None or effective_items != restored.history:
                raise ValueError("full-history effective context must equal full history")
        elif initial_effective_source == EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT:
            if initial_effective_summary is None:
                raise ValueError("compacted effective context requires a summary")
            effective_turns = validate_complete_history(effective_items).complete_turns
            full_turns = restored.complete_turns
            if len(effective_turns) > len(full_turns) or (
                effective_turns and full_turns[-len(effective_turns) :] != effective_turns
            ):
                raise ValueError("compacted effective history must be a full-history turn suffix")
        else:
            raise ValueError("unsupported effective-context source")
        self._full_history = restored.history
        self._effective_history = effective_items
        self._effective_summary = initial_effective_summary
        self._effective_source = initial_effective_source
        self._turns = restored.display_turns
        self._commit_turn = commit_turn
        self._system_prompt_factory = system_prompt_factory
        self._project_instructions_factory = project_instructions_factory
        self._tool_registry_factory = tool_registry_factory
        self._skill_inventory_factory = skill_inventory_factory
        self._hook_set_factory = hook_set_factory
        self._skill_resource_reader = skill_resource_reader
        self._action_dispatcher = action_dispatcher
        self._memory_recall_factory = memory_recall_factory or (lambda _prompt: ())
        self._task_control_names: frozenset[str] = frozenset()
        self._task_control_dispatcher: TaskControlDispatcher | None = None
        self._child_control_names: frozenset[str] = frozenset()
        self._child_control_dispatcher: ChildControlDispatcher | None = None
        self._team_control_names: frozenset[str] = frozenset()
        self._team_control_dispatcher: TeamControlDispatcher | None = None
        self._tool_set_transition_dispatcher: ToolSetTransitionDispatcher | None = None

    @property
    def history(self) -> tuple[ConversationItem, ...]:
        """Return the complete ordered causal context of completed turns."""
        return self._full_history

    @property
    def effective_history(self) -> tuple[ConversationItem, ...]:
        """Return the committed causal context currently visible to providers."""
        return self._effective_history

    @property
    def effective_summary(self) -> EffectiveContextSummary | None:
        """Return the Host-produced prefix currently visible to providers."""
        return self._effective_summary

    @property
    def effective_source(self) -> str:
        """Return the durable source kind for current effective context."""
        return self._effective_source

    @property
    def turns(self) -> tuple[ConversationTurn, ...]:
        """Return completed user/final-assistant pairs for user-facing history display."""
        return self._turns

    def effective_context_snapshot(self) -> EffectiveContextSnapshot:
        """Freeze the full and provider-visible committed context without mutation."""
        registry = self._tool_registry_factory()
        if not isinstance(registry, ToolRegistrySnapshot):
            raise ValueError("tool registry factory returned an invalid snapshot")
        inventory = self._skill_inventory_factory()
        hooks = self._hook_set_factory()
        return self._effective_context_snapshot(
            self._project_instructions_factory(),
            self._apply_skill_restrictions(self._effective_history, registry.select()),
            inventory,
            hooks,
        )

    def effective_context_snapshot_with_project_instructions(
        self,
        project_instructions: ProjectInstructionsSnapshot | None,
        *,
        tool_set_snapshot: ToolSetSnapshot | None = None,
        skill_inventory_snapshot: SkillInventorySnapshot | None = None,
        hook_set_snapshot: HookSetSnapshot | None = None,
        memory_evidence: tuple[MemoryEvidence, ...] = (),
    ) -> EffectiveContextSnapshot:
        """Rebuild committed identity while retaining one already pinned instruction snapshot."""
        if project_instructions is not None and not isinstance(
            project_instructions, ProjectInstructionsSnapshot
        ):
            raise ValueError("project instructions snapshot is invalid")
        if tool_set_snapshot is None:
            registry = self._tool_registry_factory()
            if not isinstance(registry, ToolRegistrySnapshot):
                raise ValueError("tool registry factory returned an invalid snapshot")
            tool_set_snapshot = self._apply_skill_restrictions(
                self._effective_history, registry.select()
            )
        if skill_inventory_snapshot is None:
            skill_inventory_snapshot = self._skill_inventory_factory()
        if hook_set_snapshot is None:
            hook_set_snapshot = self._hook_set_factory()
        return self._effective_context_snapshot(
            project_instructions,
            tool_set_snapshot,
            skill_inventory_snapshot,
            hook_set_snapshot,
            memory_evidence=memory_evidence,
        )

    def _effective_context_snapshot(
        self,
        project_instructions: ProjectInstructionsSnapshot | None,
        tool_set: ToolSetSnapshot,
        skill_inventory: SkillInventorySnapshot,
        hook_set: HookSetSnapshot,
        *,
        memory_evidence: tuple[MemoryEvidence, ...] = (),
    ) -> EffectiveContextSnapshot:
        if not isinstance(tool_set, ToolSetSnapshot):
            raise ValueError("effective context tool set snapshot is invalid")
        if not isinstance(skill_inventory, SkillInventorySnapshot):
            raise ValueError("effective context Skill inventory snapshot is invalid")
        if not isinstance(hook_set, HookSetSnapshot):
            raise ValueError("effective context Hook set snapshot is invalid")
        representation_version = (
            EFFECTIVE_CONTEXT_REPRESENTATION_VERSION
            if self._effective_summary is None
            else COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION
        )
        return EffectiveContextSnapshot(
            representation_version=representation_version,
            source=self._effective_source,
            system_prompt=self._system_prompt_factory(),
            project_instructions=project_instructions,
            tool_definitions=tool_set.definitions,
            tool_set_id=tool_set.snapshot_id,
            skill_inventory_id=skill_inventory.snapshot_id,
            hook_set_id=hook_set.snapshot_id,
            full_history=self._full_history,
            effective_history=self._effective_history,
            effective_summary=self._effective_summary,
            memory_evidence=memory_evidence,
        )

    def committed_context_request(self) -> ConversationRequest:
        """Retain the committed-count compatibility seam through effective context."""
        return self.effective_context_snapshot().to_conversation_request()

    def prepare_turn(
        self,
        prompt: str,
        *,
        allow_tools: bool = True,
        enabled_tool_names: tuple[str, ...] | None = None,
    ) -> PreparedAgentTurn:
        """Freeze one pending user message without mutating conversation state."""
        user = UserMessage(text=prompt)
        registry = self._tool_registry_factory()
        if not isinstance(registry, ToolRegistrySnapshot):
            raise ValueError("tool registry factory returned an invalid snapshot")
        inventory = self._skill_inventory_factory()
        if not isinstance(inventory, SkillInventorySnapshot):
            raise ValueError("Skill inventory factory returned an invalid snapshot")
        hooks = self._hook_set_factory()
        if not isinstance(hooks, HookSetSnapshot):
            raise ValueError("Hook set factory returned an invalid snapshot")
        tool_set = self._apply_skill_restrictions(
            self._effective_history, registry.select(enabled_tool_names)
        )
        memory_evidence = self._memory_recall_factory(prompt)
        if not isinstance(memory_evidence, tuple) or not all(
            isinstance(item, MemoryEvidence) for item in memory_evidence
        ):
            raise ValueError("memory recall factory returned invalid evidence")
        context = self._effective_context_snapshot(
            self._project_instructions_factory(),
            tool_set,
            inventory,
            hooks,
            memory_evidence=memory_evidence,
        )
        return PreparedAgentTurn(
            user=user,
            context=context,
            pending_items=(user,),
            registry_snapshot=registry,
            tool_set_snapshot=tool_set,
            skill_inventory_snapshot=inventory,
            hook_set_snapshot=hooks,
            allow_tools=allow_tools,
            enabled_tool_names=(tool_set.names if enabled_tool_names is not None else None),
        )

    def tool_set_snapshot_for_effective_history(
        self, history: tuple[ConversationItem, ...]
    ) -> ToolSetSnapshot:
        """Derive the direct ToolSet for one validated hypothetical Effective Context."""
        validate_complete_history(history)
        registry = self._tool_registry_factory()
        if not isinstance(registry, ToolRegistrySnapshot):
            raise ValueError("tool registry factory returned an invalid snapshot")
        return self._apply_skill_restrictions(history, registry.select())

    @staticmethod
    def _apply_skill_restrictions(
        history: tuple[ConversationItem, ...], tool_set: ToolSetSnapshot
    ) -> ToolSetSnapshot:
        """Replay only complete Host-produced skill_load results in the supplied history."""
        current = tool_set
        for skill in active_skills_from_history(history):
            if skill.allowed_tools is None:
                continue
            current = current.restrict_actions(skill.allowed_tools)
        return current

    def run(
        self,
        prompt: str,
        *,
        provider: ConversationProvider | None = None,
        event_sink: AgentEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
        tool_usage_sink: ToolUsageSink | None = None,
        task_proposal_sink: TaskProposalSink | None = None,
        first_provider_response_hook: FirstProviderResponseHook | None = None,
    ) -> str:
        """Prepare then run one bounded tool loop for compatibility callers."""
        return self.run_prepared(
            self.prepare_turn(prompt),
            provider=provider,
            event_sink=event_sink,
            include_tool_details=include_tool_details,
            cancellation=cancellation,
            tool_usage_sink=tool_usage_sink,
            task_proposal_sink=task_proposal_sink,
            first_provider_response_hook=first_provider_response_hook,
        )

    def run_prepared(
        self,
        prepared: PreparedAgentTurn,
        *,
        provider: ConversationProvider | None = None,
        event_sink: AgentEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
        tool_usage_sink: ToolUsageSink | None = None,
        task_proposal_sink: TaskProposalSink | None = None,
        first_provider_response_hook: FirstProviderResponseHook | None = None,
    ) -> str:
        """Run one prebuilt pending turn against its pinned committed context."""
        if type(include_tool_details) is not bool:
            raise ValueError("tool detail event option is invalid")
        if task_proposal_sink is not None and not callable(task_proposal_sink):
            raise ValueError("coordination proposal sink is invalid")
        if first_provider_response_hook is not None and not callable(first_provider_response_hook):
            raise ValueError("first provider response hook is invalid")
        turn_provider = provider or self._provider
        if turn_provider is None:
            raise RuntimeError("conversation provider is required for this turn")
        user = prepared.user
        context = prepared.context
        pending = prepared.pending_items
        tool_requests = 0
        provider_invocations = 0
        force_final = False
        ledger_entries: list[ToolOutcomeEntry] = []
        hook_audit_entries = []
        ledger_summary_attached = False
        seen_tool_ids = set(validate_complete_history(context.full_history).tool_use_ids)
        attempt_usage = ToolAttemptUsage()
        pending_task_proposal: TaskProposal | None = None
        discovered_names: set[str] = set()
        discovered_skills: set[tuple[str, str]] = set()
        active_skills = list(active_skills_from_history(context.effective_history))
        skill_load_attempts = 0
        self._emit_tool_usage(tool_usage_sink, attempt_usage)

        while True:
            if cancellation is not None:
                cancellation.check()
            if provider_invocations >= MAX_PROVIDER_INVOCATIONS_PER_TURN:
                raise ToolLoopLimitError("provider invocation limit reached")
            allow_tools = (
                prepared.allow_tools
                and not force_final
                and provider_invocations < MAX_PROVIDER_INVOCATIONS_PER_TURN - 1
            )
            if not allow_tools and ledger_entries and not ledger_summary_attached:
                ledger = ToolTurnLedger(tuple(ledger_entries))
                pending = _attach_tool_ledger_summary(pending, ledger)
                ledger_summary_attached = True
            outcome = self._respond(
                turn_provider,
                context.to_conversation_request(
                    pending_items=pending,
                    allow_tools=allow_tools,
                    enabled_tool_names=(prepared.enabled_tool_names if allow_tools else None),
                ),
                event_sink,
                provider_invocations + 1,
                cancellation,
            )
            provider_invocations += 1
            response_invocation_index = provider_invocations
            if response_invocation_index == 1 and first_provider_response_hook is not None:
                additional_invocations = first_provider_response_hook()
                if (
                    type(additional_invocations) is not int
                    or additional_invocations < 0
                    or provider_invocations + additional_invocations
                    > MAX_PROVIDER_INVOCATIONS_PER_TURN
                ):
                    raise ValueError(
                        "first provider response hook returned an invalid invocation count"
                    )
                provider_invocations += additional_invocations
            response = outcome.response
            if isinstance(response, ProviderResponseEnvelope):
                pending += response.provider_items
                response = response.response
            if isinstance(response, AssistantText):
                if cancellation is not None:
                    cancellation.check()
                ledger = ToolTurnLedger(tuple(ledger_entries))
                turn_hook_evaluation = evaluate_lifecycle_event(
                    prepared.hook_set_snapshot,
                    event=HookEvent.TURN_COMMITTED,
                )
                hook_audit = HookAuditLedger(
                    (
                        *hook_audit_entries,
                        turn_hook_evaluation.audit_entry(
                            event=HookEvent.TURN_COMMITTED,
                            hook_set_id=prepared.hook_set_snapshot.snapshot_id,
                            subject_id=context.context_id,
                        ),
                    )
                )
                self._commit(pending + (response,), user, response, ledger, hook_audit)
                static_turn_matches = tuple(
                    match
                    for match in turn_hook_evaluation.matches
                    if prepared.hook_set_snapshot.get(match.hook_id).rule.handler is None
                )
                if static_turn_matches:
                    self._emit_prompt_event(
                        event_sink,
                        HookLifecycleObserved(
                            event=HookEvent.TURN_COMMITTED,
                            hook_set_id=prepared.hook_set_snapshot.snapshot_id,
                            result=aggregate_hook_effect(static_turn_matches),
                            matched_hook_ids=tuple(match.hook_id for match in static_turn_matches),
                            advisory=turn_hook_evaluation.advisory_text,
                        ),
                    )
                if pending_task_proposal is not None:
                    assert task_proposal_sink is not None
                    task_proposal_sink(pending_task_proposal)
                if outcome.text_was_streamed:
                    self._emit_prompt_event(
                        event_sink,
                        AssistantFinalTextStreamCommitted(response.text),
                    )
                self._emit_search_observation(event_sink, outcome)
                self._emit_invocation_usage(event_sink, response_invocation_index, outcome)
                if ledger.entries:
                    self._emit_prompt_event(event_sink, ToolTurnSummaryCommitted(ledger))
                return response.text

            if not allow_tools:
                raise ToolLoopLimitError(
                    "provider requested tools during the final text-only invocation"
                )
            requests = (
                response.tool_uses if isinstance(response, AssistantToolBatch) else (response,)
            )
            if len(requests) > MAX_TOOL_CALLS_PER_RESPONSE:
                raise ToolLoopLimitError("provider tool batch exceeded the per-response limit")
            if any(request.tool_use_id in seen_tool_ids for request in requests):
                raise ValueError("provider reused a tool use ID")
            seen_tool_ids.update(request.tool_use_id for request in requests)
            visible_names = frozenset(prepared.tool_set_snapshot.names)
            if any(request.name not in visible_names for request in requests):
                raise ValueError("provider requested a tool outside the prepared tool set")
            control_requests = tuple(
                request for request in requests if request.name in self._task_control_names
            )
            if control_requests and len(requests) != 1:
                raise TaskControlProtocolError(
                    "coordination control tool must be the only call in its assistant response"
                )
            if control_requests and task_proposal_sink is None:
                raise TaskControlProtocolError("coordination control tool requires a proposal sink")
            child_control_requests = tuple(
                request for request in requests if request.name in self._child_control_names
            )
            if child_control_requests and len(requests) != 1:
                raise TaskControlProtocolError(
                    "Child control tool must be the only call in its assistant response"
                )
            team_control_requests = tuple(
                request for request in requests if request.name in self._team_control_names
            )
            if team_control_requests and len(requests) != 1:
                raise TaskControlProtocolError(
                    "Team control tool must be the only call in its assistant response"
                )
            discovery_requests = tuple(
                request
                for request in requests
                if prepared.tool_set_snapshot.contract(request.name).execution_kind
                is ToolExecutionKind.TOOL_DISCOVERY
            )
            if discovery_requests and len(requests) != 1:
                raise ToolDiscoveryProtocolError(
                    "tool discovery must be the only call in its assistant response"
                )

            if response.assistant_text is not None:
                companion_event = (
                    AssistantToolTextStreamCompleted(response.assistant_text)
                    if outcome.text_was_streamed
                    else AssistantToolTextReceived(response.assistant_text)
                )
                self._emit_prompt_event(event_sink, companion_event)
            self._emit_search_observation(event_sink, outcome)
            self._emit_invocation_usage(event_sink, response_invocation_index, outcome)
            pending += (response,)
            if tool_requests + len(requests) > MAX_TOOL_REQUESTS_PER_TURN:
                attempt_usage = ToolAttemptUsage(
                    requested=attempt_usage.requested + len(requests),
                    admitted=attempt_usage.admitted,
                    dispatched=attempt_usage.dispatched,
                    succeeded=attempt_usage.succeeded,
                    unsuccessful=attempt_usage.unsuccessful,
                )
                self._emit_tool_usage(tool_usage_sink, attempt_usage)
                for offset, request in enumerate(requests, start=1):
                    request_index = len(ledger_entries) + 1
                    self._emit_prompt_event(
                        event_sink,
                        ToolRequestSkipped(
                            request.name,
                            request_index,
                            MAX_TOOL_REQUESTS_PER_TURN,
                            "batch_exceeds_remaining_budget",
                        ),
                    )
                    pending += (
                        ToolResult(
                            request.tool_use_id,
                            "tool batch was not executed because it exceeds the remaining tool-request budget",
                            is_error=True,
                        ),
                    )
                    ledger_entries.append(
                        ToolOutcomeEntry(
                            request.tool_use_id,
                            request.name,
                            request_index,
                            ToolRequestOutcome.REJECTED_OVER_BUDGET,
                            "batch_exceeds_remaining_budget",
                        )
                    )
                force_final = True
                continue

            first_call_index = tool_requests + 1
            tool_requests += len(requests)
            attempt_usage = ToolAttemptUsage(
                requested=attempt_usage.requested + len(requests),
                admitted=attempt_usage.admitted + len(requests),
                dispatched=attempt_usage.dispatched,
                succeeded=attempt_usage.succeeded,
                unsuccessful=attempt_usage.unsuccessful,
            )
            self._emit_tool_usage(tool_usage_sink, attempt_usage)
            stop_remaining = False
            for offset, request in enumerate(requests):
                if cancellation is not None:
                    cancellation.check()
                call_index = first_call_index + offset
                if stop_remaining:
                    self._emit_prompt_event(
                        event_sink,
                        ToolRequestSkipped(
                            request.name,
                            call_index,
                            MAX_TOOL_REQUESTS_PER_TURN,
                            "prior_batch_action_not_succeeded",
                        ),
                    )
                    pending += (
                        ToolResult(
                            request.tool_use_id,
                            "tool was not executed because an earlier action in the same batch did not succeed",
                            is_error=True,
                        ),
                    )
                    ledger_entries.append(
                        ToolOutcomeEntry(
                            request.tool_use_id,
                            request.name,
                            call_index,
                            ToolRequestOutcome.SKIPPED_AFTER_FAILURE,
                            "prior_batch_action_not_succeeded",
                        )
                    )
                    continue
                self._emit_prompt_event(
                    event_sink,
                    ToolRequestStarted(
                        request.name,
                        call_index,
                        MAX_TOOL_REQUESTS_PER_TURN,
                        safe_tool_request_summary(request),
                        safe_tool_request_details(request) if include_tool_details else (),
                    ),
                )
                try:
                    contract = prepared.tool_set_snapshot.contract(request.name)
                    if request.name in self._task_control_names:
                        control = self._execute_task_control(request, context.context_id)
                        dispatch = control.dispatch
                        pending_task_proposal = control.proposal
                        force_final = True
                    elif request.name in self._child_control_names:
                        dispatch = self._execute_child_control(request, context.context_id).dispatch
                    elif request.name in self._team_control_names:
                        dispatch = self._execute_team_control(request, context.context_id).dispatch
                    elif contract.execution_kind is ToolExecutionKind.TOOL_DISCOVERY:
                        if request.name == SKILL_LOAD_TOOL_NAME:
                            skill_load_attempts += 1
                        discovery = self._execute_tool_discovery(
                            request,
                            prepared,
                            frozenset(discovered_names),
                            frozenset(discovered_skills),
                            tuple(active_skills),
                            skill_load_attempts,
                        )
                        dispatch = discovery.dispatch
                        discovered_names.update(discovery.discovered_names)
                        discovered_skills.update(discovery.discovered_skills)
                        if discovery.activated_skill is not None:
                            active_skills.append(discovery.activated_skill)
                        if discovery.promoted_snapshot is not None:
                            prepared = self._transition_tool_set(
                                prepared,
                                discovery.promoted_snapshot,
                            )
                            context = prepared.context
                    elif contract.execution_kind is ToolExecutionKind.MCP_REMOTE:
                        if self._action_dispatcher is None:
                            dispatch = ToolDispatchResult(
                                ToolResult(
                                    request.tool_use_id,
                                    "MCP tool execution requires a ProjectSession action boundary",
                                    is_error=True,
                                ),
                                ToolEventStatus.ERROR,
                                "mcp_execution_boundary_unavailable",
                            )
                        else:
                            dispatch = self._execute(request, prepared.action_lease)
                    else:
                        dispatch = self._execute(request, prepared.action_lease)
                except BaseException:
                    attempt_usage = ToolAttemptUsage(
                        requested=attempt_usage.requested,
                        admitted=attempt_usage.admitted,
                        dispatched=attempt_usage.dispatched + 1,
                        succeeded=attempt_usage.succeeded,
                        unsuccessful=attempt_usage.unsuccessful + 1,
                    )
                    self._emit_tool_usage(tool_usage_sink, attempt_usage)
                    self._emit_prompt_event(
                        event_sink,
                        ToolRequestFinished(
                            request.name,
                            call_index,
                            MAX_TOOL_REQUESTS_PER_TURN,
                            ToolEventStatus.OUTCOME_UNKNOWN,
                        ),
                    )
                    raise
                succeeded = dispatch.status == ToolEventStatus.SUCCEEDED
                hook_audit_entries.extend(dispatch.hook_audit.entries)
                attempt_usage = ToolAttemptUsage(
                    requested=attempt_usage.requested,
                    admitted=attempt_usage.admitted,
                    dispatched=attempt_usage.dispatched + 1,
                    succeeded=attempt_usage.succeeded + int(succeeded),
                    unsuccessful=attempt_usage.unsuccessful + int(not succeeded),
                )
                self._emit_tool_usage(tool_usage_sink, attempt_usage)
                self._emit_prompt_event(
                    event_sink,
                    ToolRequestFinished(
                        request.name,
                        call_index,
                        MAX_TOOL_REQUESTS_PER_TURN,
                        dispatch.status,
                        safe_result_code(dispatch.result_code),
                        dispatch.tool_result.truncated,
                        dispatch.result_details,
                    ),
                )
                ledger_entries.append(
                    ToolOutcomeEntry(
                        request.tool_use_id,
                        request.name,
                        call_index,
                        ToolRequestOutcome(dispatch.status.value),
                        safe_result_code(dispatch.result_code),
                    )
                )
                if cancellation is not None:
                    cancellation.check()
                pending += (dispatch.tool_result,)
                if len(requests) > 1 and dispatch.status != ToolEventStatus.SUCCEEDED:
                    stop_remaining = True

    def _respond(
        self,
        provider: ConversationProvider,
        request: ConversationRequest,
        event_sink: AgentEventSink | None,
        invocation_index: int,
        cancellation: TurnCancellation | None,
    ) -> ProviderResponseOutcome:
        def receive_preflight(report: ContextFitReport) -> None:
            self._emit_prompt_event(
                event_sink,
                ProviderInvocationPreflighted(
                    invocation_index,
                    MAX_PROVIDER_INVOCATIONS_PER_TURN,
                    report,
                ),
            )

        def receive_provider_event(event: ProviderTextDelta | ProviderSearchActivity) -> None:
            if isinstance(event, ProviderTextDelta):
                self._emit_prompt_event(
                    event_sink,
                    AssistantResponseTextDeltaReceived(event.text),
                )
            else:
                self._emit_prompt_event(
                    event_sink,
                    ProviderSearchActivityReceived(event.phase),
                )

        self._emit_prompt_event(
            event_sink,
            ProviderInvocationStarted(
                invocation_index,
                MAX_PROVIDER_INVOCATIONS_PER_TURN,
            ),
        )
        started = time.monotonic_ns()
        try:
            outcome = respond_with_streaming(
                provider,
                request,
                event_sink=receive_provider_event,
                prefer_stream=event_sink is not None,
                preflight_sink=receive_preflight,
                cancellation=cancellation,
            )
            visible_response = (
                outcome.response.response
                if isinstance(outcome.response, ProviderResponseEnvelope)
                else outcome.response
            )
            if isinstance(visible_response, AssistantText):
                invocation_outcome = ProviderInvocationOutcome.FINAL_TEXT
                tool_count = 0
            elif isinstance(visible_response, AssistantToolBatch):
                invocation_outcome = ProviderInvocationOutcome.TOOL_REQUEST
                tool_count = len(visible_response.tool_uses)
            elif isinstance(visible_response, ToolUse):
                invocation_outcome = ProviderInvocationOutcome.TOOL_REQUEST
                tool_count = 1
            else:
                raise ValueError("provider returned an invalid response")
        except TurnCancelled:
            self._emit_prompt_event(
                event_sink,
                ProviderInvocationFinished(
                    invocation_index,
                    MAX_PROVIDER_INVOCATIONS_PER_TURN,
                    ProviderInvocationOutcome.CANCELLED,
                    elapsed_milliseconds=_provider_elapsed_milliseconds(started),
                ),
            )
            raise
        except Exception:
            self._emit_prompt_event(
                event_sink,
                ProviderInvocationFinished(
                    invocation_index,
                    MAX_PROVIDER_INVOCATIONS_PER_TURN,
                    ProviderInvocationOutcome.FAILED,
                    elapsed_milliseconds=_provider_elapsed_milliseconds(started),
                ),
            )
            raise
        self._emit_prompt_event(
            event_sink,
            ProviderInvocationFinished(
                invocation_index,
                MAX_PROVIDER_INVOCATIONS_PER_TURN,
                invocation_outcome,
                tool_count,
                _provider_elapsed_milliseconds(started),
            ),
        )
        return outcome

    def _emit_search_observation(
        self,
        event_sink: AgentEventSink | None,
        outcome: ProviderResponseOutcome,
    ) -> None:
        if outcome.search_observation is not None:
            self._emit_prompt_event(
                event_sink,
                ProviderSearchSummaryReceived(outcome.search_observation),
            )

    def _emit_invocation_usage(
        self,
        event_sink: AgentEventSink | None,
        invocation_index: int,
        outcome: ProviderResponseOutcome,
    ) -> None:
        if outcome.context_report is None:
            return
        self._emit_prompt_event(
            event_sink,
            ProviderInvocationUsageReceived(
                invocation_index,
                MAX_PROVIDER_INVOCATIONS_PER_TURN,
                outcome.usage,
            ),
        )

    def install_action_dispatcher(self, dispatcher: ActionDispatcher) -> None:
        """Install the ProjectSession-owned permission/audit dispatch seam exactly once."""
        if self._action_dispatcher is not None:
            raise ValueError("action dispatcher is already installed")
        self._action_dispatcher = dispatcher

    def install_task_control_dispatcher(
        self,
        tool_names: tuple[str, ...],
        dispatcher: TaskControlDispatcher,
    ) -> None:
        """Install one commit-coupled coordination boundary exactly once."""
        if self._task_control_dispatcher is not None or self._task_control_names:
            raise ValueError("coordination control dispatcher is already installed")
        registry = self._tool_registry_factory()
        if not isinstance(registry, ToolRegistrySnapshot):
            raise ValueError("tool registry factory returned an invalid snapshot")
        available = set(registry.names)
        if (
            not isinstance(tool_names, tuple)
            or not tool_names
            or len(set(tool_names)) != len(tool_names)
            or any(name not in available for name in tool_names)
        ):
            raise ValueError("coordination control tool names are invalid")
        if not callable(dispatcher):
            raise ValueError("coordination control dispatcher is invalid")
        self._task_control_names = frozenset(tool_names)
        self._task_control_dispatcher = dispatcher

    def install_child_control_dispatcher(
        self,
        tool_names: tuple[str, ...],
        dispatcher: ChildControlDispatcher,
    ) -> None:
        """Install one non-Action Child-control boundary exactly once."""
        if self._child_control_dispatcher is not None or self._child_control_names:
            raise ValueError("Child control dispatcher is already installed")
        registry = self._tool_registry_factory()
        if not isinstance(registry, ToolRegistrySnapshot):
            raise ValueError("tool registry factory returned an invalid snapshot")
        available = set(registry.names)
        if (
            not isinstance(tool_names, tuple)
            or not tool_names
            or len(set(tool_names)) != len(tool_names)
            or any(name not in available for name in tool_names)
        ):
            raise ValueError("Child control tool names are invalid")
        if not callable(dispatcher):
            raise ValueError("Child control dispatcher is invalid")
        self._child_control_names = frozenset(tool_names)
        self._child_control_dispatcher = dispatcher

    def install_team_control_dispatcher(
        self,
        tool_names: tuple[str, ...],
        dispatcher: TeamControlDispatcher,
    ) -> None:
        """Install one non-Action Team-control boundary exactly once."""
        if self._team_control_dispatcher is not None or self._team_control_names:
            raise ValueError("Team control dispatcher is already installed")
        if (
            not isinstance(tool_names, tuple)
            or not tool_names
            or len(set(tool_names)) != len(tool_names)
            or any(not isinstance(name, str) or not name for name in tool_names)
        ):
            raise ValueError("Team control tool names are invalid")
        if not callable(dispatcher):
            raise ValueError("Team control dispatcher is invalid")
        self._team_control_names = frozenset(tool_names)
        self._team_control_dispatcher = dispatcher

    def install_tool_set_transition_dispatcher(
        self,
        dispatcher: ToolSetTransitionDispatcher,
    ) -> None:
        """Install the Session-owned lease replacement seam exactly once."""
        if self._tool_set_transition_dispatcher is not None:
            raise ValueError("tool set transition dispatcher is already installed")
        if not callable(dispatcher):
            raise ValueError("tool set transition dispatcher is invalid")
        self._tool_set_transition_dispatcher = dispatcher

    def install_compaction(
        self,
        *,
        summary: EffectiveContextSummary,
        retained_history: tuple[ConversationItem, ...],
    ) -> None:
        """Install a prevalidated durable checkpoint with non-fallible assignments."""
        self._effective_summary = summary
        self._effective_history = retained_history
        self._effective_source = EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT

    def _commit(
        self,
        items: tuple[ConversationItem, ...],
        user: UserMessage,
        assistant: AssistantText,
        tool_ledger: ToolTurnLedger,
        hook_audit: HookAuditLedger,
    ) -> None:
        """Persist one complete turn before exposing it through in-memory state."""
        turn = CommittedTurn(
            items=items,
            user=user,
            assistant=assistant,
            tool_ledger=tool_ledger,
            hook_audit=hook_audit,
        )
        full_validated = validate_complete_history(self._full_history)
        validate_complete_history(
            items,
            prior_tool_use_ids=full_validated.tool_use_ids,
        )
        if self._commit_turn is not None:
            self._commit_turn(turn)
        self._full_history += items
        self._effective_history += items
        self._turns += (ConversationTurn(user=user, assistant=assistant),)

    def _execute(self, request: ToolUse, lease: ActionLease | None) -> ToolDispatchResult:
        """Dispatch one current tool through the Host action boundary when installed."""
        if self._action_dispatcher is not None:
            if lease is None:
                raise RuntimeError("prepared action lease is required")
            result = self._action_dispatcher(request, lease)
            if isinstance(result, ToolDispatchResult):
                return result
            if type(result) is ToolResult:
                return infer_tool_dispatch_result(result)
            raise ValueError("action dispatcher returned an invalid result")
        if request.name == READ_FILE_TOOL_NAME:
            result = self._read_file.execute(request)
        elif request.name == GLOB_TOOL_NAME:
            result = self._glob.execute(request)
        elif request.name == GREP_TOOL_NAME:
            result = self._grep.execute(request)
        elif request.name == LIST_DIRECTORY_TOOL_NAME:
            result = self._list_directory.execute(request)
        elif request.name == READ_FILE_LINES_TOOL_NAME and self._read_file_lines is not None:
            result = self._read_file_lines.execute(request)
        elif request.name == STAT_PATH_TOOL_NAME and self._stat_path is not None:
            result = self._stat_path.execute(request)
        elif request.name == LIST_TREE_TOOL_NAME and self._list_tree is not None:
            result = self._list_tree.execute(request)
        elif request.name == GREP_REGEX_TOOL_NAME and self._grep_regex is not None:
            result = self._grep_regex.execute(request)
        elif request.name == GIT_STATUS_TOOL_NAME and self._git_status is not None:
            result = self._git_status.execute(request)
        elif request.name == GIT_DIFF_TOOL_NAME and self._git_diff is not None:
            result = self._git_diff.execute(request)
        elif request.name == GIT_LOG_TOOL_NAME and self._git_log is not None:
            result = self._git_log.execute(request)
        elif request.name == GIT_SHOW_TOOL_NAME and self._git_show is not None:
            result = self._git_show.execute(request)
        elif request.name == COMPARE_FILES_TOOL_NAME and self._compare_files is not None:
            result = self._compare_files.execute(request)
        elif request.name == GIT_BLAME_TOOL_NAME and self._git_blame is not None:
            result = self._git_blame.execute(request)
        elif request.name == GIT_REFS_TOOL_NAME and self._git_refs is not None:
            result = self._git_refs.execute(request)
        elif request.name == JSON_QUERY_TOOL_NAME and self._json_query is not None:
            result = self._json_query.execute(request)
        elif request.name == CHECKSUM_FILE_TOOL_NAME and self._checksum_file is not None:
            result = self._checksum_file.execute(request)
        elif request.name == ARCHIVE_LIST_TOOL_NAME and self._archive_list is not None:
            result = self._archive_list.execute(request)
        else:
            result = ToolResult(
                tool_use_id=request.tool_use_id,
                content=f"unknown tool: {request.name}",
                is_error=True,
            )
        return infer_tool_dispatch_result(result)

    def _execute_tool_discovery(
        self,
        request: ToolUse,
        prepared: PreparedAgentTurn,
        discovered_names: frozenset[str],
        discovered_skills: frozenset[tuple[str, str]],
        active_skills: tuple[ActiveSkill, ...],
        skill_load_attempts: int,
    ) -> ToolDiscoveryDispatchResult:
        arguments = tool_input_from_use(request)
        if request.name == SKILL_SEARCH_TOOL_NAME:
            query = arguments["query"]
            limit = arguments["max_results"]
            if not isinstance(query, str) or type(limit) is not int:
                raise ValueError("skill_search input is malformed")
            selected = prepared.skill_inventory_snapshot.search(
                query, limit=limit, active_only=True
            )
            content = "\n".join(
                json.dumps(
                    {
                        "description": match.candidate.manifest.description,
                        "fingerprint": match.candidate.manifest.fingerprint,
                        "name": match.candidate.manifest.name,
                        "source": match.candidate.source.value,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                for match in selected
            )
            if not content:
                content = json.dumps({"matches": 0}, separators=(",", ":"), sort_keys=True)
            return ToolDiscoveryDispatchResult(
                ToolDispatchResult(
                    ToolResult(request.tool_use_id, content),
                    ToolEventStatus.SUCCEEDED,
                    "skill_search_completed",
                ),
                discovered_skills=tuple(
                    (
                        match.candidate.manifest.name,
                        match.candidate.manifest.fingerprint,
                    )
                    for match in selected
                ),
            )
        if request.name == SKILL_LOAD_TOOL_NAME:
            name = arguments["name"]
            fingerprint = arguments["fingerprint"]
            if not isinstance(name, str) or not isinstance(fingerprint, str):
                raise ValueError("skill_load input is malformed")
            if (name, fingerprint) not in discovered_skills:
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id,
                            "skill_load requires an exact name and fingerprint returned by skill_search in this Turn",
                            is_error=True,
                        ),
                        ToolEventStatus.REJECTED,
                        "skill_candidate_not_discovered",
                    )
                )
            if skill_load_attempts > MAX_SKILL_LOADS_PER_TURN:
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id,
                            f"Skill load limit reached: at most {MAX_SKILL_LOADS_PER_TURN} per Turn",
                            is_error=True,
                        ),
                        ToolEventStatus.REJECTED,
                        "skill_load_limit",
                    )
                )
            current_inventory = self._skill_inventory_factory()
            if current_inventory.snapshot_id != prepared.skill_inventory_snapshot.snapshot_id:
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id,
                            "Skill inventory changed after this Turn was prepared",
                            is_error=True,
                        ),
                        ToolEventStatus.REJECTED,
                        "skill_inventory_stale",
                    )
                )
            candidate = prepared.skill_inventory_snapshot.get(name)
            manifest = candidate.manifest
            if manifest.fingerprint != fingerprint:
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id, "Skill fingerprint is stale", is_error=True
                        ),
                        ToolEventStatus.REJECTED,
                        "skill_fingerprint_stale",
                    )
                )
            if any(skill.name == name for skill in active_skills):
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id,
                            "Skill is already active in Effective Context",
                            is_error=True,
                        ),
                        ToolEventStatus.REJECTED,
                        "skill_already_active",
                    )
                )
            if len(active_skills) >= MAX_ACTIVE_SKILLS:
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id,
                            f"Active Skill limit reached: at most {MAX_ACTIVE_SKILLS}",
                            is_error=True,
                        ),
                        ToolEventStatus.REJECTED,
                        "active_skill_limit",
                    )
                )
            instruction_bytes = len(manifest.instructions.encode("utf-8"))
            active_instruction_bytes = sum(skill.instruction_bytes for skill in active_skills)
            if active_instruction_bytes + instruction_bytes > MAX_ACTIVE_SKILL_INSTRUCTION_BYTES:
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id,
                            "Active Skill instruction byte limit would be exceeded",
                            is_error=True,
                        ),
                        ToolEventStatus.REJECTED,
                        "active_skill_instruction_limit",
                    )
                )
            restricted = (
                prepared.tool_set_snapshot
                if manifest.allowed_tools is None
                else prepared.tool_set_snapshot.restrict_actions(manifest.allowed_tools)
            )
            action_tools = tuple(
                contract.name
                for contract in restricted.contracts
                if contract.execution_kind
                in {ToolExecutionKind.HOST_ACTION, ToolExecutionKind.MCP_REMOTE}
            )
            payload = {
                "allowed_tools": (
                    None if manifest.allowed_tools is None else list(manifest.allowed_tools)
                ),
                "fingerprint": manifest.fingerprint,
                "instructions": manifest.instructions,
                "instruction_bytes": instruction_bytes,
                "kind": "skill_loaded",
                "name": manifest.name,
                "resources": [
                    {
                        "bytes": resource.byte_count,
                        "fingerprint": resource.fingerprint,
                        "path": resource.path,
                        "text_readable": resource.text_readable,
                    }
                    for resource in candidate.resources
                ],
                "source": candidate.source.value,
                "activation": {
                    "active_count": len(active_skills) + 1,
                    "instruction_bytes": active_instruction_bytes + instruction_bytes,
                    "max_active": MAX_ACTIVE_SKILLS,
                    "max_instruction_bytes": MAX_ACTIVE_SKILL_INSTRUCTION_BYTES,
                    "max_loads_per_turn": MAX_SKILL_LOADS_PER_TURN,
                    "remaining_action_tools": list(action_tools),
                },
            }
            activated = ActiveSkill(
                name=manifest.name,
                fingerprint=manifest.fingerprint,
                source=candidate.source.value,
                instruction_bytes=instruction_bytes,
                resource_count=len(candidate.resources),
                allowed_tools=manifest.allowed_tools,
            )
            return ToolDiscoveryDispatchResult(
                ToolDispatchResult(
                    ToolResult(
                        request.tool_use_id,
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ),
                    ToolEventStatus.SUCCEEDED,
                    "skill_loaded",
                ),
                promoted_snapshot=(
                    None if restricted is prepared.tool_set_snapshot else restricted
                ),
                activated_skill=activated,
            )
        if request.name == SKILL_READ_RESOURCE_TOOL_NAME:
            name = arguments["name"]
            skill_fingerprint = arguments["skill_fingerprint"]
            path = arguments["path"]
            resource_fingerprint = arguments["resource_fingerprint"]
            if not all(
                isinstance(value, str)
                for value in (name, skill_fingerprint, path, resource_fingerprint)
            ):
                raise ValueError("skill_read_resource input is malformed")
            if not any(
                skill.name == name and skill.fingerprint == skill_fingerprint
                for skill in active_skills
            ):
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id,
                            "Skill resource reading requires the exact active Skill fingerprint",
                            is_error=True,
                        ),
                        ToolEventStatus.REJECTED,
                        "skill_not_active",
                    )
                )
            if self._skill_resource_reader is None:
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id,
                            "Skill resource reader is unavailable",
                            is_error=True,
                        ),
                        ToolEventStatus.ERROR,
                        "skill_resource_boundary_unavailable",
                    )
                )
            try:
                content = self._skill_resource_reader(
                    inventory_id=prepared.skill_inventory_snapshot.snapshot_id,
                    name=name,
                    skill_fingerprint=skill_fingerprint,
                    path=path,
                    resource_fingerprint=resource_fingerprint,
                )
            except ValueError as error:
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(request.tool_use_id, str(error), is_error=True),
                        ToolEventStatus.REJECTED,
                        getattr(error, "code", "skill_resource_rejected"),
                    )
                )
            payload = {
                "content": content,
                "kind": "skill_resource",
                "name": name,
                "path": path,
                "resource_fingerprint": resource_fingerprint,
                "skill_fingerprint": skill_fingerprint,
            }
            return ToolDiscoveryDispatchResult(
                ToolDispatchResult(
                    ToolResult(
                        request.tool_use_id,
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ),
                    ToolEventStatus.SUCCEEDED,
                    "skill_resource_read",
                )
            )
        if request.name == TOOL_SEARCH_TOOL_NAME:
            query = arguments["query"]
            limit = arguments["max_results"]
            if not isinstance(query, str) or type(limit) is not int:
                raise ValueError("tool_search input is malformed")
            terms = tuple(part for part in query.casefold().split() if part)
            matches: list[tuple[int, str, str, str]] = []
            for contract in prepared.registry_snapshot.contracts:
                if (
                    contract.exposure is not ToolExposure.DEFERRED
                    or contract.source.kind is not ExtensionSourceKind.MCP
                ):
                    continue
                mapping = contract.definition.as_mapping()
                description = mapping.get("description", "")
                haystack = f"{contract.name} {description}".casefold()
                if terms and not all(term in haystack for term in terms):
                    continue
                score = sum(haystack.count(term) for term in terms)
                matches.append((score, contract.name, contract.source.name, str(description)))
            selected = sorted(matches, key=lambda item: (-item[0], item[1]))[:limit]
            content = "\n".join(
                json.dumps(
                    {
                        "description": description[:512],
                        "name": name,
                        "source": source,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                for _, name, source, description in selected
            )
            if not content:
                content = json.dumps({"matches": 0}, separators=(",", ":"), sort_keys=True)
            names = tuple(item[1] for item in selected)
            return ToolDiscoveryDispatchResult(
                ToolDispatchResult(
                    ToolResult(request.tool_use_id, content),
                    ToolEventStatus.SUCCEEDED,
                    "tool_search_completed",
                ),
                discovered_names=names,
            )
        if request.name == TOOL_PROMOTE_TOOL_NAME:
            raw_names = arguments["names"]
            if not isinstance(raw_names, list) or not all(
                isinstance(name, str) for name in raw_names
            ):
                raise ValueError("tool_promote input is malformed")
            names = tuple(raw_names)
            if not set(names).issubset(discovered_names):
                return ToolDiscoveryDispatchResult(
                    ToolDispatchResult(
                        ToolResult(
                            request.tool_use_id,
                            "only exact names returned by tool_search in this Turn can be promoted",
                            is_error=True,
                        ),
                        ToolEventStatus.REJECTED,
                        "tool_candidate_not_discovered",
                    )
                )
            promoted = prepared.tool_set_snapshot.promote(prepared.registry_snapshot, names)
            content = json.dumps(
                {
                    "epoch": promoted.epoch,
                    "promoted": list(names),
                    "tool_set_id": promoted.snapshot_id,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            return ToolDiscoveryDispatchResult(
                ToolDispatchResult(
                    ToolResult(request.tool_use_id, content),
                    ToolEventStatus.SUCCEEDED,
                    "tool_set_promoted",
                ),
                promoted_snapshot=(None if promoted is prepared.tool_set_snapshot else promoted),
            )
        raise ValueError("unsupported tool discovery request")

    def _transition_tool_set(
        self,
        prepared: PreparedAgentTurn,
        snapshot: ToolSetSnapshot,
    ) -> PreparedAgentTurn:
        if self._tool_set_transition_dispatcher is not None:
            transitioned = self._tool_set_transition_dispatcher(prepared, snapshot)
            if not isinstance(transitioned, PreparedAgentTurn):
                raise ValueError("tool set transition dispatcher returned an invalid turn")
            return transitioned
        if prepared.action_lease is not None:
            raise RuntimeError("leased ToolSet transition requires a Session dispatcher")
        return prepared.advance_tool_set(snapshot)

    def _execute_task_control(
        self,
        request: ToolUse,
        context_id: str,
    ) -> TaskControlDispatchResult:
        """Dispatch commit-coupled coordination without entering the Action boundary."""
        dispatcher = self._task_control_dispatcher
        if dispatcher is None:
            raise RuntimeError("coordination control dispatcher is not installed")
        result = dispatcher(request, context_id)
        if type(result) is not TaskControlDispatchResult:
            raise ValueError("coordination control dispatcher returned an invalid result")
        if result.proposal is not None and result.proposal.context_id != context_id:
            raise ValueError("coordination proposal context does not match prepared turn")
        return result

    def _execute_child_control(
        self, request: ToolUse, context_id: str
    ) -> ChildControlDispatchResult:
        dispatcher = self._child_control_dispatcher
        if dispatcher is None:
            raise RuntimeError("Child control dispatcher is not installed")
        result = dispatcher(request, context_id)
        if type(result) is not ChildControlDispatchResult:
            raise ValueError("Child control dispatcher returned an invalid result")
        return result

    def _execute_team_control(self, request: ToolUse, context_id: str) -> TeamControlDispatchResult:
        dispatcher = self._team_control_dispatcher
        if dispatcher is None:
            raise RuntimeError("Team control dispatcher is not installed")
        result = dispatcher(request, context_id)
        if type(result) is not TeamControlDispatchResult:
            raise ValueError("Team control dispatcher returned an invalid result")
        return result

    @staticmethod
    def _emit_prompt_event(sink: AgentEventSink | None, event: AgentPromptEvent) -> None:
        if sink is None:
            return
        try:
            sink(event)
        except Exception:
            pass

    @staticmethod
    def _emit_tool_usage(sink: ToolUsageSink | None, usage: ToolAttemptUsage) -> None:
        if sink is None:
            return
        try:
            sink(usage)
        except Exception:
            pass


def _provider_elapsed_milliseconds(started: int) -> int:
    elapsed = max(0, (time.monotonic_ns() - started) // 1_000_000)
    return min(elapsed, 86_400_000)


def render_tool_ledger_for_model(ledger: ToolTurnLedger) -> str:
    """Return one bounded canonical Host accounting line for finalization."""
    unused_admission_slots = max(0, MAX_TOOL_REQUESTS_PER_TURN - ledger.admitted)
    counts = " ".join(
        (
            f"requested={ledger.requested}",
            f"admitted={ledger.admitted}",
            f"dispatched={ledger.dispatched}",
            f"succeeded={ledger.count(ToolRequestOutcome.SUCCEEDED)}",
            f"error={ledger.count(ToolRequestOutcome.ERROR)}",
            f"denied={ledger.count(ToolRequestOutcome.DENIED)}",
            f"rejected={ledger.count(ToolRequestOutcome.REJECTED)}",
            f"cancelled={ledger.count(ToolRequestOutcome.CANCELLED)}",
            f"failed={ledger.count(ToolRequestOutcome.FAILED)}",
            f"partial={ledger.count(ToolRequestOutcome.PARTIAL)}",
            f"outcome_unknown={ledger.count(ToolRequestOutcome.OUTCOME_UNKNOWN)}",
            f"skipped_after_failure={ledger.count(ToolRequestOutcome.SKIPPED_AFTER_FAILURE)}",
            f"rejected_over_budget={ledger.count(ToolRequestOutcome.REJECTED_OVER_BUDGET)}",
            f"unused_admission_slots={unused_admission_slots}",
            "tool_requests_closed=true",
        )
    )
    return f"Host tool ledger: {counts}. Treat these counts as authoritative."


def _attach_tool_ledger_summary(
    items: tuple[ConversationItem, ...], ledger: ToolTurnLedger
) -> tuple[ConversationItem, ...]:
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if isinstance(item, ToolResult):
            annotated = replace(
                item,
                content=f"{item.content}\n\n{render_tool_ledger_for_model(ledger)}",
            )
            return items[:index] + (annotated,) + items[index + 1 :]
    raise RuntimeError("tool ledger finalization requires a preceding tool result")


def restore_history(
    history: tuple[ConversationItem, ...],
) -> tuple[tuple[ConversationItem, ...], tuple[ConversationTurn, ...]]:
    """Retain the public restoration seam through the canonical validator."""
    validated = validate_complete_history(history)
    return validated.history, validated.display_turns
