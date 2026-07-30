"""The bounded orchestration loop for the current sequential tool surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from leonervis_code.agent.tool_events import (
    AgentPromptEvent,
    AssistantFinalTextStreamCommitted,
    AssistantResponseTextDeltaReceived,
    AssistantToolTextStreamCompleted,
    AssistantToolTextReceived,
    ProviderInvocationPreflighted,
    ProviderInvocationUsageReceived,
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
from leonervis_code.core.actions import ActionLease
from leonervis_code.core.compaction import EffectiveContextSummary
from leonervis_code.core.cancellation import TurnCancellation
from leonervis_code.core.contracts import (
    AssistantToolBatch,
    AssistantText,
    CommittedTurn,
    ConversationItem,
    ConversationProvider,
    ConversationRequest,
    ConversationTurn,
    SystemPromptSnapshot,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolResult,
    ToolTurnLedger,
    ToolUse,
    TurnCommitter,
    UserMessage,
)
from leonervis_code.core.effective_context import (
    COMPACTED_EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_REPRESENTATION_VERSION,
    EFFECTIVE_CONTEXT_SOURCE_COMPACT_CHECKPOINT,
    EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY,
    EffectiveContextSnapshot,
    validate_complete_history,
)
from leonervis_code.core.project_instructions import ProjectInstructionsSnapshot
from leonervis_code.providers.streaming import ProviderResponseOutcome, respond_with_streaming
from leonervis_code.providers.request_context import ContextFitReport
from leonervis_code.system_prompt import build_system_prompt
from leonervis_code.tools.catalog import (
    MAX_PROVIDER_INVOCATIONS_PER_TURN,
    MAX_TOOL_CALLS_PER_RESPONSE,
    MAX_TOOL_REQUESTS_PER_TURN,
    TOOL_CATALOG,
)
from leonervis_code.tools.glob import GLOB_TOOL_NAME, GlobTool
from leonervis_code.tools.grep import GREP_TOOL_NAME, GrepTool
from leonervis_code.tools.grep_regex import GREP_REGEX_TOOL_NAME, GrepRegexTool
from leonervis_code.tools.git_diff import GIT_DIFF_TOOL_NAME, GitDiffTool
from leonervis_code.tools.git_log import GIT_LOG_TOOL_NAME, GitLogTool
from leonervis_code.tools.git_show import GIT_SHOW_TOOL_NAME, GitShowTool
from leonervis_code.tools.git_status import GIT_STATUS_TOOL_NAME, GitStatusTool
from leonervis_code.tools.list_directory import LIST_DIRECTORY_TOOL_NAME, ListDirectoryTool
from leonervis_code.tools.list_tree import LIST_TREE_TOOL_NAME, ListTreeTool
from leonervis_code.tools.read_file import READ_FILE_TOOL_NAME, ReadFileTool
from leonervis_code.tools.read_file_lines import READ_FILE_LINES_TOOL_NAME, ReadFileLinesTool
from leonervis_code.tools.stat_path import STAT_PATH_TOOL_NAME, StatPathTool

SystemPromptFactory = Callable[[], SystemPromptSnapshot]
ProjectInstructionsFactory = Callable[[], ProjectInstructionsSnapshot | None]
ActionDispatcher = Callable[[ToolUse, ActionLease], ToolResult | ToolDispatchResult]
AgentEventSink = Callable[[AgentPromptEvent], None]


def _no_project_instructions() -> None:
    return None


class ToolLoopLimitError(RuntimeError):
    """Raised when a provider does not finish after its tool-call budget is exhausted."""


@dataclass(frozen=True)
class PreparedAgentTurn:
    """One pending user item pinned to one committed Effective Context."""

    user: UserMessage
    context: EffectiveContextSnapshot
    pending_items: tuple[ConversationItem, ...]
    action_lease: ActionLease | None = None

    def __post_init__(self) -> None:
        if self.pending_items != (self.user,):
            raise ValueError("prepared turn must contain exactly its pending user message")

    @property
    def initial_request(self) -> ConversationRequest:
        return self.context.to_conversation_request(pending_items=self.pending_items)

    def rebase(self, context: EffectiveContextSnapshot) -> PreparedAgentTurn:
        if self.action_lease is not None:
            raise ValueError("a leased prepared turn cannot be rebased")
        return replace(self, context=context)

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
        initial_history: tuple[ConversationItem, ...] = (),
        initial_effective_history: tuple[ConversationItem, ...] | None = None,
        initial_effective_summary: EffectiveContextSummary | None = None,
        initial_effective_source: str = EFFECTIVE_CONTEXT_SOURCE_FULL_COMMITTED_HISTORY,
        commit_turn: TurnCommitter | None = None,
        system_prompt_factory: SystemPromptFactory = build_system_prompt,
        project_instructions_factory: ProjectInstructionsFactory = _no_project_instructions,
        action_dispatcher: ActionDispatcher | None = None,
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
        self._action_dispatcher = action_dispatcher

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
        return self._effective_context_snapshot(
            self._project_instructions_factory(),
        )

    def effective_context_snapshot_with_project_instructions(
        self,
        project_instructions: ProjectInstructionsSnapshot | None,
    ) -> EffectiveContextSnapshot:
        """Rebuild committed identity while retaining one already pinned instruction snapshot."""
        if project_instructions is not None and not isinstance(
            project_instructions, ProjectInstructionsSnapshot
        ):
            raise ValueError("project instructions snapshot is invalid")
        return self._effective_context_snapshot(project_instructions)

    def _effective_context_snapshot(
        self,
        project_instructions: ProjectInstructionsSnapshot | None,
    ) -> EffectiveContextSnapshot:
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
            tool_definitions=TOOL_CATALOG,
            full_history=self._full_history,
            effective_history=self._effective_history,
            effective_summary=self._effective_summary,
        )

    def committed_context_request(self) -> ConversationRequest:
        """Retain the committed-count compatibility seam through effective context."""
        return self.effective_context_snapshot().to_conversation_request()

    def prepare_turn(self, prompt: str) -> PreparedAgentTurn:
        """Freeze one pending user message without mutating conversation state."""
        user = UserMessage(text=prompt)
        return PreparedAgentTurn(
            user=user,
            context=self.effective_context_snapshot(),
            pending_items=(user,),
        )

    def run(
        self,
        prompt: str,
        *,
        provider: ConversationProvider | None = None,
        event_sink: AgentEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> str:
        """Prepare then run one bounded tool loop for compatibility callers."""
        return self.run_prepared(
            self.prepare_turn(prompt),
            provider=provider,
            event_sink=event_sink,
            include_tool_details=include_tool_details,
            cancellation=cancellation,
        )

    def run_prepared(
        self,
        prepared: PreparedAgentTurn,
        *,
        provider: ConversationProvider | None = None,
        event_sink: AgentEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
    ) -> str:
        """Run one prebuilt pending turn against its pinned committed context."""
        if type(include_tool_details) is not bool:
            raise ValueError("tool detail event option is invalid")
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
        ledger_summary_attached = False
        seen_tool_ids = set(validate_complete_history(context.full_history).tool_use_ids)

        while True:
            if cancellation is not None:
                cancellation.check()
            if provider_invocations >= MAX_PROVIDER_INVOCATIONS_PER_TURN:
                raise ToolLoopLimitError("provider invocation limit reached")
            allow_tools = (
                not force_final and provider_invocations < MAX_PROVIDER_INVOCATIONS_PER_TURN - 1
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
                ),
                event_sink,
                provider_invocations + 1,
                cancellation,
            )
            provider_invocations += 1
            response = outcome.response
            if isinstance(response, AssistantText):
                if cancellation is not None:
                    cancellation.check()
                ledger = ToolTurnLedger(tuple(ledger_entries))
                self._commit(pending + (response,), user, response, ledger)
                if outcome.text_was_streamed:
                    self._emit_prompt_event(
                        event_sink,
                        AssistantFinalTextStreamCommitted(response.text),
                    )
                self._emit_invocation_usage(event_sink, provider_invocations, outcome)
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

            if response.assistant_text is not None:
                companion_event = (
                    AssistantToolTextStreamCompleted(response.assistant_text)
                    if outcome.text_was_streamed
                    else AssistantToolTextReceived(response.assistant_text)
                )
                self._emit_prompt_event(event_sink, companion_event)
            self._emit_invocation_usage(event_sink, provider_invocations, outcome)
            pending += (response,)
            if tool_requests + len(requests) > MAX_TOOL_REQUESTS_PER_TURN:
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
                    dispatch = self._execute(request, prepared.action_lease)
                except Exception:
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
                if cancellation is not None:
                    cancellation.check()
                pending += (dispatch.tool_result,)
                ledger_entries.append(
                    ToolOutcomeEntry(
                        request.tool_use_id,
                        request.name,
                        call_index,
                        ToolRequestOutcome(dispatch.status.value),
                        safe_result_code(dispatch.result_code),
                    )
                )
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

        outcome = respond_with_streaming(
            provider,
            request,
            event_sink=lambda delta: self._emit_prompt_event(
                event_sink,
                AssistantResponseTextDeltaReceived(delta.text),
            ),
            prefer_stream=event_sink is not None,
            preflight_sink=receive_preflight,
            cancellation=cancellation,
        )
        return outcome

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
    ) -> None:
        """Persist one complete turn before exposing it through in-memory state."""
        turn = CommittedTurn(
            items=items,
            user=user,
            assistant=assistant,
            tool_ledger=tool_ledger,
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
        else:
            result = ToolResult(
                tool_use_id=request.tool_use_id,
                content=f"unknown tool: {request.name}",
                is_error=True,
            )
        return infer_tool_dispatch_result(result)

    @staticmethod
    def _emit_prompt_event(sink: AgentEventSink | None, event: AgentPromptEvent) -> None:
        if sink is None:
            return
        try:
            sink(event)
        except Exception:
            pass


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
