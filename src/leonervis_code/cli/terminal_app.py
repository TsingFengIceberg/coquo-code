"""Persistent inline prompt-toolkit application for the interactive TTY REPL."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import sys
from pathlib import Path
from typing import TextIO

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import BufferControl, FormattedTextControl, HSplit, Layout, Window
from prompt_toolkit.output import Output

from leonervis_code.cli.approval import TerminalApprovalBroker, render_approval_request
from leonervis_code.cli.event_sink import TerminalEventSink
from leonervis_code.cli.frontend import (
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
from leonervis_code.cli.markdown_renderer import (
    DEFAULT_TERMINAL_WIDTH,
    MAX_TERMINAL_WIDTH,
    MIN_TERMINAL_WIDTH,
    escape_terminal_controls,
    render_markdown_document,
    render_plain_document,
)
from leonervis_code.cli.presentation import (
    CLEAR_SCREEN,
    ToolDetailMode,
    render_host_message,
    render_message_separator,
    render_prompt,
    render_prompt_toolbar,
    render_turn_trace,
)
from leonervis_code.cli.prompt_editor import SlashCommandCompleter, validate_prompt_text
from leonervis_code.cli.slash import ToolDetailSettings, dispatch_slash
from leonervis_code.cli.turn_runner import TurnRunner
from leonervis_code.core.action_coordinator import ApprovalResolution
from leonervis_code.session import TurnCommitStarted


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
        width: int = DEFAULT_TERMINAL_WIDTH,
    ) -> None:
        self._stream = io.StringIO()
        self._cursor = 0
        self._color = color
        self._render_markdown = render_markdown
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
            self._stream.write(
                render_markdown_document(text, color=self._color)
                if self._render_markdown
                else (
                    escape_terminal_controls(text)
                    if text.endswith("\n")
                    else f"{escape_terminal_controls(text)}\n"
                )
            )
        return self._take_output()

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
        self._renderer = _QueuedPromptRenderer(color=color, render_markdown=render_markdown)
        self._status = _snapshot(session, "status")
        self._session_info = _snapshot(session, "session_info")
        self._usage = _snapshot(session, "usage")
        self._approval_draft: tuple[str, int] | None = None
        self._turn_starting = False
        self._cancel_pending_start = False
        self._turn_output_started = False
        self._history = InMemoryHistory(_session_prompt_history(session))
        self._buffer = Buffer(
            multiline=True,
            accept_handler=self._accept,
            completer=completer or SlashCommandCompleter(),
            complete_while_typing=False,
            enable_history_search=True,
            history=self._history,
            tempfile_suffix=".txt",
        )
        control = BufferControl(
            buffer=self._buffer,
            include_default_input_processors=True,
        )
        body = HSplit(
            [
                Window(control, wrap_lines=True, get_line_prefix=self._line_prefix),
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
        self._application.invalidate()

    async def _handle_event(self, event: FrontendEvent) -> None:
        previous = self._state
        self._state = reduce_terminal_state(self._state, event)
        if isinstance(event, ApprovalPending):
            if self._state.phase != TerminalPhase.APPROVAL:
                self._approval_broker.resolve(ApprovalResolution.CANCEL)
                return
            self._approval_draft = (self._buffer.text, self._buffer.cursor_position)
            self._buffer.set_document(self._buffer.document.__class__("", 0), bypass_readonly=True)
            await self._write_turn_output(
                render_turn_trace(
                    render_approval_request(event.request, color=self._color),
                    "plain",
                    color=self._color,
                )
                + "\n"
            )
        elif isinstance(event, PromptActivity):
            if isinstance(event.event, TurnCommitStarted):
                return
            rendered = self._renderer.render(event.event)
            if rendered:
                await self._write_turn_output(rendered)
        elif isinstance(event, TurnFailed):
            _, trailing = self._renderer.abort()
            if trailing:
                await self._write_turn_output(trailing)
            await self._write_turn_output(
                render_turn_trace(
                    escape_terminal_controls(event.message),
                    "warning" if event.cancelled else "error",
                    color=self._color,
                )
                + "\n"
            )
        elif isinstance(event, TurnFinished):
            if event.response and not self._renderer.final_text_was_streamed:
                await self._write_turn_output(self._renderer.render_final(event.response))
            await self._write(
                f"\n{render_message_separator(self._current_width(), color=self._color)}\n"
            )
            should_exit = previous.exit_after_turn
            self._renderer.reset()
            self._turn_output_started = False
            self._refresh_snapshots()
            self._replace_history()
            if should_exit:
                self._application.exit(result=None)
        elif isinstance(event, (TurnSubmitted, TurnCompleting)):
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
        if text.startswith("/") and "\n" not in text:
            self._dispatch_slash(text)
            buffer.reset(append_to_history=False)
            return False
        include_details = self._tool_details.mode == ToolDetailMode.FULL
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
        try:
            result = dispatch_slash(text, self._session, tool_details=self._tool_details)
            if result.clear_screen:
                self._schedule_write(CLEAR_SCREEN)
            if result.message is not None:
                self._schedule_write(
                    render_host_message(result.message, result.kind, color=self._color) + "\n"
                )
            if result.exit:
                self._application.exit(result=None)
            self._refresh_snapshots()
            self._replace_history()
        except BaseException as error:
            self._schedule_write(
                render_host_message(
                    f"Operation failed: {type(error).__name__}: {error}", "error", color=self._color
                )
                + "\n"
            )

    def _bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("enter", eager=True)
        def submit(event) -> None:
            if self._state.phase == TerminalPhase.APPROVAL:
                self._submit_approval()
                return
            if self._state.busy or self._turn_starting:
                self._state = replace(self._state, status="Busy; draft retained")
                event.app.invalidate()
                return
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter", eager=True)
        def newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-c", eager=True)
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

        @bindings.add("c-d", eager=True)
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

        return bindings

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
        )
        return ANSI(f"{self._state.status} · {base.strip()}")

    def _current_width(self) -> int:
        try:
            width = self._application.output.get_size().columns
        except Exception:
            width = DEFAULT_TERMINAL_WIDTH
        return max(MIN_TERMINAL_WIDTH, min(width, MAX_TERMINAL_WIDTH))

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

    def _replace_history(self) -> None:
        self._history = InMemoryHistory(_session_prompt_history(self._session))
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
    entries = []
    for turn in turns[-1000:]:
        text = getattr(getattr(turn, "user", None), "text", None)
        if isinstance(text, str):
            entries.append(text)
    return tuple(entries)
