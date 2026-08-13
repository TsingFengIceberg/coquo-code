"""Shared assembly boundary for one Coquo Agent runtime.

The runtime deliberately wraps the existing :class:`AgentLoop` instead of
creating a second loop implementation.  Host-owned callbacks remain explicit
so a future child run can reuse the same causal machinery without inheriting
the parent Session facade.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from coquo.agent.loop import (
    ActionDispatcher,
    AgentEventSink,
    AgentLoop,
    PreparedAgentTurn,
    TaskControlDispatcher,
    ToolSetTransitionDispatcher,
)
from coquo.core.cancellation import TurnCancellation
from coquo.core.contracts import ConversationProvider, CommittedTurn, ToolAttemptUsage
from coquo.core.compaction import decide_auto_compaction
from coquo.providers.request_context import ContextFitDecision, raise_for_context_fit
from coquo.providers.usage import ProviderInvocationKind


@dataclass(frozen=True)
class AgentRuntimeServices:
    """Immutable service bindings used to assemble one existing AgentLoop."""

    read_file: Any
    glob: Any
    grep: Any
    list_directory: Any
    read_file_lines: Any
    stat_path: Any
    list_tree: Any
    grep_regex: Any
    git_status: Any
    git_diff: Any
    git_log: Any
    git_show: Any
    compare_files: Any
    git_blame: Any
    git_refs: Any
    json_query: Any
    checksum_file: Any
    archive_list: Any
    project_instructions_factory: Callable[[], Any]
    tool_registry_factory: Callable[[], Any]
    skill_inventory_factory: Callable[[], Any]
    hook_set_factory: Callable[[], Any]
    skill_resource_reader: Callable[..., Any]
    provider_manager: Any = None


@dataclass(frozen=True)
class AgentRuntimeCallbacks:
    """Host-owned seams installed on a runtime's loop exactly once."""

    commit_turn: Callable[[CommittedTurn], None]
    action_dispatcher: ActionDispatcher | None = None
    task_control_names: tuple[str, ...] = ()
    task_control_dispatcher: TaskControlDispatcher | None = None
    tool_set_transition_dispatcher: ToolSetTransitionDispatcher | None = None
    activate_turn: Callable[["AgentRuntimeTurnState", bool], None] | None = None
    bind_provider: Callable[["AgentRuntimeTurnState"], None] | None = None
    issue_action_lease: Callable[[PreparedAgentTurn, Any, Any], PreparedAgentTurn] | None = None
    auto_compact_turn: Callable[..., PreparedAgentTurn] | None = None
    emit_usage: Callable[[Any, AgentEventSink | None], None] | None = None
    record_failure: Callable[[Any, BaseException, tuple[Any, ...]], None] | None = None
    prepare_first_response_hook: Callable[[Any, int, str], Callable[[], int] | None] | None = None
    binding_for_provider: Callable[[Any], Any] | None = None


@dataclass(frozen=True)
class AgentTurnRequest:
    """Provider-independent inputs for one prepared Agent turn."""

    text: str
    event_sink: AgentEventSink | None = None
    include_tool_details: bool = False
    cancellation: TurnCancellation | None = None
    allow_tools: bool = True
    enabled_tool_names: tuple[str, ...] | None = None
    session_title_source_text: str | None = None
    task_proposal_sink: Callable[..., Any] | None = None
    failure_usage_sink: Callable[[tuple[Any, ...], ToolAttemptUsage], None] | None = None


@dataclass
class AgentRuntimeTurnState:
    """Volatile state reserved for one active run; never persisted or shared."""

    prepared: PreparedAgentTurn | None = None
    action_lease: Any | None = None
    provider_runtime: Any | None = None
    binding: Any | None = None
    usage_cursor: int | None = None
    cancellation: TurnCancellation | None = None
    event_sink: AgentEventSink | None = None
    tool_attempt_usage: ToolAttemptUsage = field(default_factory=ToolAttemptUsage)
    hook_set_snapshot: Any | None = None
    session_title_source_text: str | None = None

    @property
    def active(self) -> bool:
        return self.prepared is not None

    def clear(self) -> None:
        """Clear all volatile references after every turn outcome."""
        self.prepared = None
        self.action_lease = None
        self.provider_runtime = None
        self.binding = None
        self.usage_cursor = None
        self.cancellation = None
        self.event_sink = None
        self.tool_attempt_usage = ToolAttemptUsage()
        self.hook_set_snapshot = None
        self.session_title_source_text = None


class AgentRuntime:
    """One configured existing AgentLoop plus per-run volatile state."""

    def __init__(
        self,
        loop: AgentLoop,
        services: AgentRuntimeServices | None = None,
        callbacks: AgentRuntimeCallbacks | None = None,
    ) -> None:
        if not isinstance(loop, AgentLoop):
            raise TypeError("Agent runtime requires an AgentLoop")
        self._loop = loop
        self._services = services
        self._callbacks = callbacks
        self._turn_state = AgentRuntimeTurnState()

    @property
    def loop(self) -> AgentLoop:
        return self._loop

    @property
    def turn_state(self) -> AgentRuntimeTurnState:
        return self._turn_state

    @property
    def history(self):
        return self._loop.history

    @property
    def effective_history(self):
        return self._loop.effective_history

    @property
    def effective_summary(self):
        return self._loop.effective_summary

    @property
    def effective_source(self):
        return self._loop.effective_source

    @property
    def turns(self):
        return self._loop.turns

    def effective_context_snapshot(self):
        return self._loop.effective_context_snapshot()

    def effective_context_snapshot_with_project_instructions(self, *args: Any, **kwargs: Any):
        return self._loop.effective_context_snapshot_with_project_instructions(*args, **kwargs)

    def prepare_turn(self, request: AgentTurnRequest) -> PreparedAgentTurn:
        if not isinstance(request, AgentTurnRequest):
            raise TypeError("Agent turn request is invalid")
        if self._turn_state.active:
            raise RuntimeError("Agent runtime already has an active turn")
        prepared = self._loop.prepare_turn(
            request.text,
            allow_tools=request.allow_tools,
            enabled_tool_names=request.enabled_tool_names,
        )
        self._turn_state.prepared = prepared
        self._turn_state.cancellation = request.cancellation
        self._turn_state.event_sink = request.event_sink
        return prepared

    def run_prepared(
        self,
        prepared: PreparedAgentTurn,
        *,
        provider: ConversationProvider,
        event_sink: AgentEventSink | None = None,
        include_tool_details: bool = False,
        cancellation: TurnCancellation | None = None,
        tool_usage_sink: Callable[[ToolAttemptUsage], None] | None = None,
        task_proposal_sink: Callable[..., Any] | None = None,
        first_provider_response_hook: Callable[[], int] | None = None,
    ) -> str:
        """Run the existing causal loop and clear volatile state on every exit."""
        if self._turn_state.active and self._turn_state.prepared is not prepared:
            raise RuntimeError("prepared turn does not belong to this runtime")
        try:
            return self._loop.run_prepared(
                prepared,
                provider=provider,
                event_sink=event_sink,
                include_tool_details=include_tool_details,
                cancellation=cancellation,
                tool_usage_sink=tool_usage_sink,
                task_proposal_sink=task_proposal_sink,
                first_provider_response_hook=first_provider_response_hook,
            )
        finally:
            self._turn_state.clear()

    def run_turn(self, request: AgentTurnRequest) -> str:
        """Run one Host-prepared turn with the existing causal loop.

        Host effects remain callbacks, but provider preflight, compaction, lease
        ordering, usage, failure recording, and volatile cleanup are centralized
        here so a future Child runtime cannot copy the parent turn sequence.
        """
        if not isinstance(request, AgentTurnRequest):
            raise TypeError("Agent turn request is invalid")
        services = self._services
        callbacks = self._callbacks
        if services is None or callbacks is None:
            raise RuntimeError("Agent runtime services and callbacks are unavailable")
        manager = services.provider_manager
        if manager is None:
            raise RuntimeError("Agent runtime provider manager is unavailable")
        prepared = self.prepare_turn(request)
        usage_cursor = manager.begin_turn_usage()
        self._turn_state.usage_cursor = usage_cursor
        self._turn_state.event_sink = request.event_sink
        self._turn_state.session_title_source_text = request.session_title_source_text
        tool_attempt_usage = ToolAttemptUsage()

        def observe_tool_usage(usage: ToolAttemptUsage) -> None:
            nonlocal tool_attempt_usage
            tool_attempt_usage = usage

        try:
            if callbacks.activate_turn is not None:
                callbacks.activate_turn(self._turn_state, False)
            self._turn_state.hook_set_snapshot = prepared.hook_set_snapshot
            if request.cancellation is not None:
                request.cancellation.check()
            with manager.provider_for_turn() as provider:
                self._turn_state.provider_runtime = provider
                if callbacks.binding_for_provider is None:
                    raise RuntimeError("Agent runtime binding callback is unavailable")
                self._turn_state.binding = callbacks.binding_for_provider(provider.status)
                if callbacks.bind_provider is not None:
                    callbacks.bind_provider(self._turn_state)
                assessment = provider.assess_context(prepared.initial_request)
                if request.cancellation is not None:
                    request.cancellation.check()
                report = assessment.fit_report
                if report is not None and report.decision == ContextFitDecision.MODEL_OUTPUT_EXCEEDED:
                    raise_for_context_fit(report)
                decision = decide_auto_compaction(report)
                if decision.trigger is not None:
                    if callbacks.auto_compact_turn is None:
                        raise RuntimeError("Agent runtime compaction callback is unavailable")
                    prepared = callbacks.auto_compact_turn(
                        prepared,
                        loop=self._loop,
                        runtime=provider,
                        trigger=decision.trigger,
                        mandatory=decision.mandatory,
                        source_report=report,
                        event_sink=request.event_sink,
                        cancellation=request.cancellation,
                    )
                    self._turn_state.prepared = prepared
                if callbacks.issue_action_lease is None:
                    raise RuntimeError("Agent runtime action lease callback is unavailable")
                prepared = callbacks.issue_action_lease(
                    prepared,
                    provider,
                    self._turn_state.binding,
                )
                self._turn_state.prepared = prepared
                self._turn_state.action_lease = prepared.action_lease
                self._turn_state.hook_set_snapshot = prepared.hook_set_snapshot
                if callbacks.bind_provider is not None:
                    callbacks.bind_provider(self._turn_state)
                first_hook = None
                if callbacks.prepare_first_response_hook is not None:
                    first_hook = callbacks.prepare_first_response_hook(
                        provider,
                        usage_cursor,
                        request.session_title_source_text or request.text,
                    )
                response = self._loop.run_prepared(
                    prepared,
                    provider=provider,
                    event_sink=request.event_sink,
                    include_tool_details=request.include_tool_details,
                    cancellation=request.cancellation,
                    tool_usage_sink=(
                        observe_tool_usage if request.failure_usage_sink is not None else None
                    ),
                    task_proposal_sink=request.task_proposal_sink,
                    first_provider_response_hook=first_hook,
                )
            usage = manager.finish_turn_usage(usage_cursor)
            if callbacks.emit_usage is not None:
                callbacks.emit_usage(usage, request.event_sink)
            return response
        except BaseException as error:
            manager.finish_turn_usage(usage_cursor)
            provider_usage = manager.usage_since(usage_cursor, kind=ProviderInvocationKind.TURN)
            # RuntimeProviderManager accepts the enum at runtime; keep this
            # module independent of its provider implementation types.
            if request.failure_usage_sink is not None:
                try:
                    request.failure_usage_sink(provider_usage, tool_attempt_usage)
                except Exception:
                    pass
            if callbacks.record_failure is not None:
                callbacks.record_failure(
                    self._turn_state.binding,
                    error,
                    provider_usage,
                )
            raise
        finally:
            if callbacks.activate_turn is not None:
                callbacks.activate_turn(self._turn_state, True)
            self._turn_state.clear()


class AgentRuntimeFactory:
    """Construct every parent/future-child runtime through one explicit seam."""

    @staticmethod
    def create(
        state: Any,
        services: AgentRuntimeServices,
        callbacks: AgentRuntimeCallbacks,
    ) -> AgentRuntime:
        if not isinstance(services, AgentRuntimeServices):
            raise TypeError("Agent runtime services are invalid")
        if not isinstance(callbacks, AgentRuntimeCallbacks):
            raise TypeError("Agent runtime callbacks are invalid")
        loop = AgentLoop(
            None,
            services.read_file,
            services.glob,
            services.grep,
            services.list_directory,
            services.read_file_lines,
            services.stat_path,
            services.list_tree,
            services.grep_regex,
            git_status=services.git_status,
            git_diff=services.git_diff,
            git_log=services.git_log,
            git_show=services.git_show,
            compare_files=services.compare_files,
            git_blame=services.git_blame,
            git_refs=services.git_refs,
            json_query=services.json_query,
            checksum_file=services.checksum_file,
            archive_list=services.archive_list,
            initial_history=state.history,
            initial_effective_history=state.effective_history,
            initial_effective_summary=state.effective_summary,
            initial_effective_source=state.effective_source,
            commit_turn=callbacks.commit_turn,
            project_instructions_factory=services.project_instructions_factory,
            tool_registry_factory=services.tool_registry_factory,
            skill_inventory_factory=services.skill_inventory_factory,
            hook_set_factory=services.hook_set_factory,
            skill_resource_reader=services.skill_resource_reader,
        )
        if callbacks.action_dispatcher is not None:
            loop.install_action_dispatcher(callbacks.action_dispatcher)
        if callbacks.task_control_dispatcher is not None:
            loop.install_task_control_dispatcher(
                callbacks.task_control_names,
                callbacks.task_control_dispatcher,
            )
        if callbacks.tool_set_transition_dispatcher is not None:
            loop.install_tool_set_transition_dispatcher(callbacks.tool_set_transition_dispatcher)
        return AgentRuntime(loop, services, callbacks)
