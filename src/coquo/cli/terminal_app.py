"""Persistent inline prompt-toolkit application for the interactive TTY REPL."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import sys
import time
from pathlib import Path
from typing import TextIO

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.filters import Condition, is_searching
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.emacs import load_emacs_search_bindings
from prompt_toolkit.key_binding.key_bindings import KeyBindingsBase, merge_key_bindings
from prompt_toolkit.layout import BufferControl, FormattedTextControl, HSplit, Layout, Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.output import Output
from prompt_toolkit.widgets import SearchToolbar

from coquo.cli.approval import TerminalApprovalBroker, render_approval_request
from coquo.cli.event_sink import TerminalEventSink
from coquo.cli.frontend import (
    ApprovalPending,
    ApprovalResolved,
    CancellationRequested,
    FrontendEvent,
    FrontendEventQueue,
    PromptActivity,
    TerminalPhase,
    TerminalViewState,
    TurnCompleting,
    TurnFailed,
    TurnFinished,
    TurnSubmitted,
    reduce_terminal_state,
)
from coquo.cli.markdown_renderer import (
    DEFAULT_TERMINAL_WIDTH,
    escape_terminal_controls,
    render_markdown_document,
    render_plain_document,
    terminal_content_width,
)
from coquo.cli.presentation import (
    CLEAR_SCREEN,
    MessageKind,
    ToolDetailMode,
    render_activity_line,
    render_assistant_prefix,
    render_child_supervisor_notification,
    render_host_message,
    render_message_separator,
    render_prompt,
    render_prompt_event,
    render_prompt_toolbar,
    render_turn_trace,
)
from coquo.cli.prompt_editor import (
    MAX_PROMPT_BYTES,
    MAX_PROMPT_CHARACTERS,
    MAX_PROMPT_HISTORY_BYTES,
    MAX_PROMPT_HISTORY_ENTRIES,
    SlashCommandCompleter,
    validate_prompt_text,
)
from coquo.cli.slash import SessionSwitchCatalog, ToolDetailSettings, dispatch_slash
from coquo.cli.turn_runner import TaskTurnRequest, TurnRunner
from coquo.agent.tool_events import ProviderInvocationFinished, ProviderInvocationStarted
from coquo.core.action_coordinator import ApprovalResolution
from coquo.session import (
    SessionTitleGenerationStarted,
    SessionTitlePrepared,
    TurnCommitStarted,
)

PROVIDER_WAIT_HEARTBEAT_SECONDS = 5.0


def supports_terminal_application(stdin: TextIO, stdout: TextIO) -> bool:
    """Use the persistent frontend only for the process's real interactive streams."""
    return stdin is sys.stdin and stdout is sys.stdout and stdin.isatty() and stdout.isatty()


class _QueuedPromptRenderer:
    """Reuse stable event presentation against memory, never a worker-owned TTY."""

    def __init__(
        self,
        *,
        color: bool,
        render_markdown: bool,
        immediate_streaming: bool = False,
        width: int = DEFAULT_TERMINAL_WIDTH,
    ) -> None:
        self._stream = io.StringIO()
        self._cursor = 0
        self._color = color
        self._render_markdown = render_markdown
        self._immediate_streaming = immediate_streaming
        self._width = width
        self._sink = self._new_sink()

    @property
    def final_text_was_streamed(self) -> bool:
        return self._sink.final_text_was_streamed

    def render(self, event: object) -> str:
        try:
            self._sink(event)
        except ValueError:
            text = getattr(event, "text", None)
            self._sink.abort_stream()
            if not isinstance(text, str):
                raise
            first_prefix = f"\n{render_assistant_prefix(color=self._color)}"
            self._stream.write(
                render_markdown_document(
                    text,
                    color=self._color,
                    width=self._width,
                    first_prefix=first_prefix,
                    continuation_prefix="  ",
                    prefix_width=2,
                )
                if self._render_markdown
                else render_plain_document(
                    text,
                    width=self._width,
                    first_prefix=first_prefix,
                    continuation_prefix="  ",
                    prefix_width=2,
                )
            )
        return self._take_output()

    def resize(self, width: int) -> None:
        """Update wrapping width while preserving the current response stream."""
        self._width = width
        self._sink.resize(width)

    def abort(self) -> tuple[bool, str]:
        partial = self._sink.abort_stream()
        return partial, self._take_output()

    def reset(self) -> None:
        self._stream = io.StringIO()
        self._cursor = 0
        self._sink = self._new_sink()

    def render_final(self, response: str) -> str:
        self._sink.write_final_text(response)
        return self._take_output()

    def configure(self, mode: ToolDetailMode, *, width: int) -> None:
        if type(mode) is not ToolDetailMode:
            raise ValueError("tool detail mode is invalid")
        self._tool_detail_mode = mode
        self._width = width
        self.reset()

    def _new_sink(self) -> TerminalEventSink:
        return TerminalEventSink(
            self._stream,
            color=self._color,
            render_markdown=self._render_markdown,
            show_role_markers=True,
            immediate_streaming=self._immediate_streaming,
            tool_detail_mode=getattr(self, "_tool_detail_mode", ToolDetailMode.COMPACT),
            markdown_width=self._width,
        )

    def _take_output(self) -> str:
        value = self._stream.getvalue()
        result = value[self._cursor :]
        self._cursor = len(value)
        return result


class TerminalApplication:
    """Own TTY input, redraw, inline scrollback, approval focus, and one turn worker."""

    def __init__(
        self,
        session: object,
        *,
        stdout: TextIO,
        cwd: Path,
        color: bool,
        render_markdown: bool,
        immediate_streaming: bool = False,
        queue: FrontendEventQueue,
        approval_broker: TerminalApprovalBroker,
        input: Input | None = None,
        output: Output | None = None,
        completer: Completer | None = None,
    ) -> None:
        self._session = session
        self._stdout = stdout
        self._cwd = cwd
        self._color = color
        self._queue = queue
        self._approval_broker = approval_broker
        self._runner = TurnRunner(session, queue, approval_broker)
        self._state = TerminalViewState()
        self._tool_details = ToolDetailSettings()
        self._session_switch = SessionSwitchCatalog()
        self._renderer = _QueuedPromptRenderer(
            color=color,
            render_markdown=render_markdown,
            immediate_streaming=immediate_streaming,
        )
        self._status = _snapshot(session, "status")
        self._session_info = _snapshot(session, "session_info")
        self._session_info_before_prepared_title: object | None = None
        self._usage = _snapshot(session, "usage")
        self._approval_draft: tuple[str, int] | None = None
        self._turn_starting = False
        self._cancel_pending_start = False
        self._turn_output_started = False
        self._provider_invocation_started_at: float | None = None
        self._provider_invocation_active: ProviderInvocationStarted | None = None
        self._provider_wait_last_heartbeat: float | None = None
        self._history_entries = _session_prompt_history(session)
        self._history = InMemoryHistory(self._history_entries)
        self._buffer = Buffer(
            multiline=True,
            accept_handler=self._accept,
            completer=completer or SlashCommandCompleter(),
            complete_while_typing=False,
            enable_history_search=True,
            history=self._history,
            tempfile_suffix=".txt",
        )
        self._search_toolbar = SearchToolbar(
            backward_search_prompt="History search: ",
            forward_search_prompt="History search forward: ",
            ignore_case=True,
        )
        control = BufferControl(
            buffer=self._buffer,
            include_default_input_processors=True,
            search_buffer_control=self._search_toolbar.control,
        )
        body = HSplit(
            [
                ConditionalContainer(
                    Window(
                        FormattedTextControl(self._activity_line),
                        height=1,
                        style="class:activity",
                    ),
                    filter=Condition(self._activity_visible),
                ),
                Window(control, wrap_lines=True, get_line_prefix=self._line_prefix),
                self._search_toolbar,
                Window(
                    FormattedTextControl(self._toolbar),
                    height=1,
                    style="class:bottom-toolbar",
                ),
            ]
        )
        self._application: Application[None] = Application(
            layout=Layout(body, focused_element=control),
            key_bindings=self._bindings(),
            full_screen=False,
            mouse_support=False,
            input=input,
            output=output,
            refresh_interval=0.1,
        )

    @property
    def state(self) -> TerminalViewState:
        return self._state

    @property
    def draft(self) -> str:
        return self._buffer.text

    def run(self) -> int:
        try:
            self._application.run(pre_run=self._start_event_pump)
        finally:
            self._queue.close()
            if self._runner.busy:
                self._runner.cancel()
                self._approval_broker.resolve(ApprovalResolution.CANCEL)
                self._runner.join()
        return 0

    def _start_event_pump(self) -> None:
        self._application.create_background_task(self._event_pump())

    async def _event_pump(self) -> None:
        while not self._application.is_done:
            await self._drain_events()
            await asyncio.sleep(0.025)

    async def _drain_events(self) -> None:
        for event in self._queue.drain():
            try:
                await self._handle_event(event)
            except Exception:
                self._renderer.reset()
        await self._emit_provider_wait_heartbeat()
        self._drain_child_notifications()
        self._application.invalidate()

    async def _emit_provider_wait_heartbeat(self) -> None:
        active = self._provider_invocation_active
        started = self._provider_invocation_started_at
        if active is None or started is None:
            return
        now = time.monotonic()
        elapsed = max(0.0, now - started)
        last = self._provider_wait_last_heartbeat
        if last is not None and now - last < PROVIDER_WAIT_HEARTBEAT_SECONDS:
            return
        if elapsed < PROVIDER_WAIT_HEARTBEAT_SECONDS:
            return
        self._provider_wait_last_heartbeat = now
        purpose = (
            "session title"
            if getattr(active.purpose, "value", "turn") == "session-title"
            else "Provider"
        )
        await self._write_turn_output(
            render_turn_trace(
                f"{purpose} still waiting: model round "
                f"[{active.invocation_index}/{active.invocation_limit}] "
                f"({elapsed:.1f}s)",
                "info",
                color=self._color,
                width=self._current_width(),
            )
            + "\n"
        )

    def _drain_child_notifications(self) -> None:
        drain = getattr(self._session, "child_notifications", None)
        if not callable(drain):
            return
        try:
            notifications = drain()
        except Exception:
            return
        for notification in notifications:
            self._schedule_write(
                self._render_slash_block(
                    "/child",
                    render_child_supervisor_notification(notification),
                    "info",
                )
            )

    async def _handle_event(self, event: FrontendEvent) -> None:
        previous = self._state
        self._state = reduce_terminal_state(self._state, event)
        if isinstance(event, TurnSubmitted):
            await self._write_turn_output(
                render_turn_trace(
                    f"Turn {event.turn_id}: started",
                    "info",
                    color=self._color,
                    width=self._current_width(),
                )
                + "\n"
            )
        elif isinstance(event, ApprovalPending):
            if self._state.phase != TerminalPhase.APPROVAL:
                self._approval_broker.resolve(ApprovalResolution.CANCEL)
                return
            self._approval_draft = (self._buffer.text, self._buffer.cursor_position)
            self._buffer.set_document(self._buffer.document.__class__("", 0), bypass_readonly=True)
            await self._write_turn_output(
                render_turn_trace(
                    render_approval_request(event.request, color=False),
                    "warning",
                    color=self._color,
                    width=self._current_width(),
                )
                + "\n"
            )
        elif isinstance(event, PromptActivity):
            if isinstance(event.event, ProviderInvocationStarted):
                self._provider_invocation_started_at = time.monotonic()
                self._provider_invocation_active = event.event
                self._provider_wait_last_heartbeat = self._provider_invocation_started_at
            elif isinstance(event.event, ProviderInvocationFinished):
                self._provider_invocation_started_at = None
                self._provider_invocation_active = None
                self._provider_wait_last_heartbeat = None
            if isinstance(event.event, SessionTitlePrepared):
                if self._session_info_before_prepared_title is None:
                    self._session_info_before_prepared_title = self._session_info
                self._session_info = replace(
                    self._session_info,
                    name=event.event.name,
                    name_source=event.event.source,
                    title_fallback_reason=None,
                )
                return
            if isinstance(event.event, (SessionTitleGenerationStarted, TurnCommitStarted)):
                return
            if isinstance(event.event, (ProviderInvocationStarted, ProviderInvocationFinished)):
                message, kind = render_prompt_event(event.event)
                await self._write_turn_output(
                    render_turn_trace(
                        message,
                        kind,
                        color=self._color,
                        width=self._current_width(),
                    )
                    + "\n"
                )
                return
            self._renderer.resize(self._current_width())
            rendered = self._renderer.render(event.event)
            if rendered:
                await self._write_turn_output(rendered)
        elif isinstance(event, TurnFailed):
            self._provider_invocation_started_at = None
            self._provider_invocation_active = None
            self._provider_wait_last_heartbeat = None
            if self._session_info_before_prepared_title is not None:
                self._session_info = self._session_info_before_prepared_title
                self._session_info_before_prepared_title = None
            _, trailing = self._renderer.abort()
            if trailing:
                await self._write_turn_output(trailing)
            await self._write_turn_output(
                render_turn_trace(
                    escape_terminal_controls(event.message),
                    "warning" if event.cancelled else "error",
                    color=self._color,
                    width=self._current_width(),
                )
                + "\n"
            )
        elif isinstance(event, TurnFinished):
            self._provider_invocation_started_at = None
            self._provider_invocation_active = None
            self._provider_wait_last_heartbeat = None
            if event.response and not self._renderer.final_text_was_streamed:
                self._renderer.resize(self._current_width())
                await self._write_turn_output(self._renderer.render_final(event.response))
            if previous.phase == TerminalPhase.COMPLETING:
                await self._write_turn_output(
                    "\n"
                    + render_turn_trace(
                        f"Turn {event.turn_id}: committed",
                        "success",
                        color=self._color,
                        width=self._current_width(),
                    )
                    + "\n"
                )
            await self._write(
                f"\n{render_message_separator(self._current_width(), color=self._color)}\n"
            )
            should_exit = previous.exit_after_turn
            self._renderer.reset()
            self._turn_output_started = False
            self._refresh_snapshots()
            self._session_info_before_prepared_title = None
            if should_exit:
                self._application.exit(result=None)
            elif event.task_handoff is not None:
                for _ in range(100):
                    if not self._runner.busy:
                        break
                    await asyncio.sleep(0.001)
                else:
                    await self._write_turn_output(
                        render_turn_trace(
                            "Automatic Task continuation could not start because the prior worker did not finish.",
                            "error",
                            color=self._color,
                            width=self._current_width(),
                        )
                        + "\n"
                    )
                    return
                request = TaskTurnRequest(
                    "drive",
                    event.task_handoff.task_id,
                    max_stages=event.task_handoff.max_stages,
                )
                turn_id = self._runner.start_task(
                    request,
                    include_tool_details=self._tool_details.mode == ToolDetailMode.FULL,
                )
                if turn_id is None:
                    await self._write_turn_output(
                        render_turn_trace(
                            "Automatic Task continuation could not start because the worker is busy.",
                            "error",
                            color=self._color,
                            width=self._current_width(),
                        )
                        + "\n"
                    )
        elif isinstance(event, TurnCompleting):
            return

    def _accept(self, buffer: Buffer) -> bool:
        text = buffer.text
        if self._state.phase == TerminalPhase.APPROVAL:
            resolution = _parse_approval(text)
            if resolution is None:
                self._state = replace(self._state, status="Answer y, n, or c")
                return True
            turn_id = self._state.active_turn
            assert turn_id is not None
            if self._approval_broker.resolve(resolution):
                self._state = reduce_terminal_state(self._state, ApprovalResolved(turn_id))
                self._restore_approval_draft()
                return True
        if self._state.busy or self._turn_starting:
            self._state = replace(self._state, status="Busy; draft retained")
            return True
        if not text.strip():
            buffer.reset(append_to_history=False)
            return False
        validate_prompt_text(text)
        self._remember_input(text)
        if text.startswith("/") and "\n" not in text:
            self._dispatch_slash(text)
            buffer.reset(append_to_history=False)
            return False
        include_details = self._tool_details.mode == ToolDetailMode.FULL
        self._session_switch.clear()
        self._renderer.configure(self._tool_details.mode, width=self._current_width())
        self._turn_output_started = False
        self._turn_starting = True
        self._cancel_pending_start = False
        self._state = replace(self._state, status="Preparing turn")
        self._application.create_background_task(
            self._start_turn_after_echo(text, include_tool_details=include_details)
        )
        buffer.reset(append_to_history=False)
        return False

    async def _start_turn_after_echo(self, text: str, *, include_tool_details: bool) -> None:
        width = self._current_width()
        rendered = render_plain_document(
            text,
            width=width,
            first_prefix=render_prompt(
                self._status,
                self._session_info,
                color=self._color,
                readline=False,
            ),
            continuation_prefix="  ",
            prefix_width=2,
        )
        await self._write(f"\n{rendered}")
        if self._cancel_pending_start:
            self._turn_starting = False
            self._cancel_pending_start = False
            self._state = replace(self._state, status="Ready")
            self._application.invalidate()
            return
        turn_id = self._runner.start(text, include_tool_details=include_tool_details)
        self._turn_starting = False
        if turn_id is None:
            self._state = replace(self._state, status="Busy; draft retained")
        self._application.invalidate()

    def _dispatch_slash(self, text: str) -> None:
        previous_session_id = _session_identity(self._session_info)
        try:
            result = dispatch_slash(
                text,
                self._session,
                tool_details=self._tool_details,
                session_switch=self._session_switch,
            )
            if result.task_request is not None:
                self._start_task_request(text, result.task_request)
            elif result.clear_screen:
                self._schedule_write(CLEAR_SCREEN)
            elif result.message is not None:
                self._schedule_write(self._render_slash_block(text, result.message, result.kind))
            if result.exit:
                self._application.exit(result=None)
            self._refresh_snapshots()
            if _session_identity(self._session_info) != previous_session_id:
                self._replace_history((text,))
        except BaseException as error:
            self._schedule_write(
                self._render_slash_block(
                    text,
                    f"Operation failed: {type(error).__name__}: {error}",
                    "error",
                )
            )

    def _start_task_request(self, text: str, request: TaskTurnRequest) -> None:
        include_details = self._tool_details.mode == ToolDetailMode.FULL
        self._renderer.configure(self._tool_details.mode, width=self._current_width())
        self._turn_output_started = False
        self._turn_starting = True
        self._cancel_pending_start = False
        self._state = replace(self._state, status="Preparing Task Stage")
        self._application.create_background_task(
            self._start_task_after_echo(
                text,
                request,
                include_tool_details=include_details,
            )
        )

    async def _start_task_after_echo(
        self,
        text: str,
        request: TaskTurnRequest,
        *,
        include_tool_details: bool,
    ) -> None:
        width = self._current_width()
        rendered = render_plain_document(
            text,
            width=width,
            first_prefix=render_prompt(
                self._status,
                self._session_info,
                color=self._color,
                readline=False,
            ),
            continuation_prefix="  ",
            prefix_width=2,
        )
        await self._write(f"\n{rendered}")
        if self._cancel_pending_start:
            self._turn_starting = False
            self._cancel_pending_start = False
            self._state = replace(self._state, status="Ready")
            self._application.invalidate()
            return
        turn_id = self._runner.start_task(
            request,
            include_tool_details=include_tool_details,
        )
        self._turn_starting = False
        if turn_id is None:
            self._state = replace(self._state, status="Busy; draft retained")
        self._application.invalidate()

    def _render_slash_block(self, text: str, message: str, kind: MessageKind) -> str:
        width = self._current_width()
        prompt = render_plain_document(
            text,
            width=width,
            first_prefix=render_prompt(
                self._status,
                self._session_info,
                color=self._color,
                readline=False,
            ),
            continuation_prefix="  ",
            prefix_width=2,
        )
        result = render_host_message(message, kind, color=self._color, width=width)
        separator = render_message_separator(width, color=self._color)
        return f"\n{prompt}\n\n{result}\n\n{separator}\n"

    def _bindings(self) -> KeyBindingsBase:
        bindings = KeyBindings()

        @bindings.add("enter", filter=~is_searching, eager=True)
        def submit(event) -> None:
            if self._state.phase == TerminalPhase.APPROVAL:
                self._submit_approval()
                return
            if self._state.busy or self._turn_starting:
                self._state = replace(self._state, status="Busy; draft retained")
                event.app.invalidate()
                return
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter", filter=~is_searching, eager=True)
        def newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-c", filter=~is_searching, eager=True)
        def cancel(event) -> None:
            if self._turn_starting:
                self._cancel_pending_start = True
                self._state = replace(self._state, status="Cancelling")
            elif self._state.busy:
                turn_id = self._state.active_turn
                assert turn_id is not None
                self._runner.cancel()
                self._approval_broker.resolve(ApprovalResolution.CANCEL)
                if self._state.phase == TerminalPhase.APPROVAL:
                    self._restore_approval_draft()
                self._state = reduce_terminal_state(self._state, CancellationRequested(turn_id))
            elif event.current_buffer.text:
                event.current_buffer.reset()
            else:
                event.app.exit(result=None)

        @bindings.add("c-d", filter=~is_searching, eager=True)
        def exit_or_delete(event) -> None:
            buffer = event.current_buffer
            if self._turn_starting:
                self._cancel_pending_start = True
                event.app.exit(result=None)
            elif self._state.busy:
                turn_id = self._state.active_turn
                assert turn_id is not None
                self._runner.cancel()
                self._approval_broker.resolve(ApprovalResolution.CANCEL)
                if self._state.phase == TerminalPhase.APPROVAL:
                    self._restore_approval_draft()
                self._state = reduce_terminal_state(
                    self._state, CancellationRequested(turn_id, exit_after_turn=True)
                )
            elif not buffer.text:
                event.app.exit(result=None)
            elif buffer.cursor_position < len(buffer.text):
                buffer.delete(1)

        return merge_key_bindings((bindings, load_emacs_search_bindings()))

    def _submit_approval(self) -> None:
        resolution = _parse_approval(self._buffer.text)
        if resolution is None:
            self._state = replace(self._state, status="Answer y, n, or c")
            self._application.invalidate()
            return
        turn_id = self._state.active_turn
        assert turn_id is not None
        if self._approval_broker.resolve(resolution):
            self._state = reduce_terminal_state(self._state, ApprovalResolved(turn_id))
            self._restore_approval_draft()
            self._application.invalidate()

    def _line_prefix(self, line_number: int, wrap_count: int):
        if wrap_count:
            return ANSI("  ")
        if line_number:
            return ANSI("  ")
        marker = (
            "Approve › "
            if self._state.phase == TerminalPhase.APPROVAL
            else render_prompt(self._status, self._session_info, color=self._color, readline=False)
        )
        return ANSI(marker)

    def _toolbar(self):
        base = render_prompt_toolbar(
            self._status,
            self._cwd,
            color=self._color,
            usage=self._usage,
            session=self._session_info,
        )
        return ANSI(f"{self._state.status} · {base.strip()}")

    def _activity_visible(self) -> bool:
        return self._turn_starting or self._state.busy

    def _activity_line(self):
        status = self._state.status
        if self._provider_invocation_started_at is not None:
            elapsed = max(0.0, time.monotonic() - self._provider_invocation_started_at)
            spinner = "|/-\\"[int(elapsed * 8) % 4]
            status = f"{spinner} {status} · {elapsed:.1f}s"
        return ANSI(render_activity_line(status, color=self._color))

    def _current_width(self) -> int:
        try:
            columns = self._application.output.get_size().columns
        except Exception:
            columns = DEFAULT_TERMINAL_WIDTH + 1
        return terminal_content_width(columns)

    async def _write(self, text: str) -> None:
        if not text:
            return
        try:
            await run_in_terminal(lambda: self._safe_write(text), in_executor=False)
        except Exception:
            return

    async def _write_turn_output(self, text: str) -> None:
        """Open one visual turn boundary before its first visible output."""
        if not text:
            return
        if not self._turn_output_started:
            self._turn_output_started = True
            if not text.startswith("\n"):
                text = f"\n{text}"
        await self._write(text)

    def _schedule_write(self, text: str) -> None:
        self._application.create_background_task(self._write(text))

    def _safe_write(self, text: str) -> None:
        try:
            self._stdout.write(text)
            self._stdout.flush()
        except Exception:
            pass

    def _restore_approval_draft(self) -> None:
        draft = self._approval_draft
        self._approval_draft = None
        if draft is None:
            self._buffer.reset()
            return
        from prompt_toolkit.document import Document

        self._buffer.set_document(Document(draft[0], draft[1]), bypass_readonly=True)

    def _refresh_snapshots(self) -> None:
        self._status = _snapshot(self._session, "status")
        self._session_info = _snapshot(self._session, "session_info")
        self._usage = _snapshot(self._session, "usage")

    def _remember_input(self, text: str) -> None:
        self._history_entries = _bounded_prompt_history((*self._history_entries, text))
        self._history = InMemoryHistory(self._history_entries)
        self._buffer.history = self._history

    def _replace_history(self, additional_entries: tuple[str, ...] = ()) -> None:
        self._history_entries = _bounded_prompt_history(
            (*_session_prompt_history(self._session), *additional_entries)
        )
        self._history = InMemoryHistory(self._history_entries)
        self._buffer.history = self._history


def _parse_approval(text: str) -> ApprovalResolution | None:
    answer = text.strip().lower()
    if answer in {"y", "yes"}:
        return ApprovalResolution.ACCEPT
    if answer in {"", "n", "no"}:
        return ApprovalResolution.REJECT
    if answer in {"c", "cancel"}:
        return ApprovalResolution.CANCEL
    return None


def _snapshot(session: object, method_name: str):
    method = getattr(session, method_name, None)
    if not callable(method):
        return None
    try:
        return method()
    except Exception:
        return None


def _session_prompt_history(session: object) -> tuple[str, ...]:
    turns = getattr(session, "turns", ())
    if not isinstance(turns, tuple):
        return ()
    entries: list[str] = []
    for turn in turns[-1000:]:
        text = getattr(getattr(turn, "user", None), "text", None)
        if isinstance(text, str):
            entries.append(text)
    return _bounded_prompt_history(tuple(entries))


def _bounded_prompt_history(entries: tuple[str, ...]) -> tuple[str, ...]:
    selected: list[str] = []
    total_bytes = 0
    for text in reversed(entries):
        if len(selected) == MAX_PROMPT_HISTORY_ENTRIES:
            break
        if not isinstance(text, str) or "\x00" in text or len(text) > MAX_PROMPT_CHARACTERS:
            continue
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            continue
        if len(encoded) > MAX_PROMPT_BYTES or total_bytes + len(encoded) > MAX_PROMPT_HISTORY_BYTES:
            continue
        selected.append(text)
        total_bytes += len(encoded)
    return tuple(reversed(selected))


def _session_identity(info: object) -> str | None:
    session_id = getattr(info, "session_id", None)
    return session_id if isinstance(session_id, str) else None
