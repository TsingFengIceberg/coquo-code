"""Interactive terminal orchestration for a persistent project session."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from leonervis_code.cli.brand import render_banner
from leonervis_code.cli.event_sink import TerminalEventSink
from leonervis_code.cli.prompt_editor import (
    MAX_PROMPT_BYTES,
    MAX_PROMPT_CHARACTERS,
    MAX_PROMPT_HISTORY_BYTES,
    MAX_PROMPT_HISTORY_ENTRIES,
    PromptEditor,
    PromptInputError,
    PromptReadKind,
    TerminalPromptEditor,
    create_prompt_editor,
)
from leonervis_code.cli.failure_guidance import render_turn_failure
from leonervis_code.cli.presentation import (
    CLEAR_SCREEN,
    ToolDetailMode,
    render_message,
    render_prompt,
    render_prompt_toolbar,
    render_runtime_status,
    render_session_info,
)
from leonervis_code.cli.slash import SessionSwitchCatalog, ToolDetailSettings, dispatch_slash
from leonervis_code.cli.approval import TerminalApprovalBroker
from leonervis_code.cli.frontend import FrontendEventQueue
from leonervis_code.cli.terminal_app import TerminalApplication, supports_terminal_application


def parse_history_count(command: str) -> int | None:
    """Return a positive count from ``/history <count>``, if valid."""
    parts = command.split()
    if (
        len(parts) != 2
        or parts[0] != "/history"
        or not parts[1].isascii()
        or not parts[1].isdigit()
    ):
        return None
    count = int(parts[1])
    return count if count > 0 else None


def run_repl(
    session: object,
    *,
    stdin: TextIO,
    stdout: TextIO,
    version: str,
    cwd: Path,
    color: bool,
    render_markdown: bool = False,
    prompt_editor: PromptEditor | None = None,
    frontend_queue: FrontendEventQueue | None = None,
    approval_broker: TerminalApprovalBroker | None = None,
) -> int:
    """Read input, dispatch local commands, and route ordinary text to the model."""
    editor = prompt_editor or create_prompt_editor(stdin, stdout)
    terminal_ui = isinstance(editor, TerminalPromptEditor)
    stdout.write(f"\n{render_banner(version=version, cwd=cwd, color=color)}\n")
    status = _snapshot(session, "status")
    if status is not None:
        stdout.write(f"\n{render_runtime_status(status)}\n")
    session_info = _snapshot(session, "session_info")
    if session_info is not None:
        stdout.write(f"\n{render_session_info(session_info)}\nAuto-save: enabled\n")
    stdout.write("\n")
    stdout.flush()
    if (
        prompt_editor is None
        and frontend_queue is not None
        and approval_broker is not None
        and supports_terminal_application(stdin, stdout)
    ):
        return TerminalApplication(
            session,
            stdout=stdout,
            cwd=cwd,
            color=color,
            render_markdown=render_markdown,
            queue=frontend_queue,
            approval_broker=approval_broker,
        ).run()
    tool_details = ToolDetailSettings()
    session_switch = SessionSwitchCatalog()

    while True:
        status = _snapshot(session, "status")
        session_info = _snapshot(session, "session_info")
        prompt_text = render_prompt(
            status,
            session_info,
            color=color,
            readline=False,
        )
        usage = _snapshot(session, "usage")
        prompt_toolbar = render_prompt_toolbar(
            status,
            cwd,
            color=color,
            usage=usage,
            session=session_info,
        )
        try:
            editor.set_history(_session_prompt_history(session))
            prompt_read = editor.read(prompt_text, bottom_toolbar=prompt_toolbar)
        except PromptInputError as error:
            stdout.write(f"{render_message(f'Input error: {error}', 'error', color=color)}\n")
            stdout.flush()
            continue
        if prompt_read.kind == PromptReadKind.CANCEL:
            stdout.write("\n")
            stdout.flush()
            continue
        if prompt_read.kind == PromptReadKind.EXIT:
            stdout.write("\n")
            stdout.flush()
            return 0
        assert prompt_read.text is not None
        prompt = prompt_read.text

        if not prompt.strip():
            continue

        try:
            result = dispatch_slash(
                prompt,
                session,
                tool_details=tool_details,
                session_switch=session_switch,
            )
        except KeyboardInterrupt:
            stdout.write(
                f"{render_message('Operation cancelled; no uncommitted state was installed.', 'warning', color=color)}\n"
            )
            stdout.flush()
            continue
        if result.handled:
            if result.clear_screen:
                stdout.write(CLEAR_SCREEN)
                stdout.flush()
            if result.message is not None:
                stdout.write(f"{render_message(result.message, result.kind, color=color)}\n")
                stdout.flush()
            if result.exit:
                return 0
            continue

        try:
            session_switch.clear()
            prompt_method = getattr(session, "prompt", None)
            event_sink = TerminalEventSink(
                stdout,
                color=color,
                render_markdown=render_markdown,
                show_role_markers=terminal_ui,
                show_waiting=terminal_ui,
                tool_detail_mode=tool_details.mode,
            )
            event_sink.start_waiting()
            if callable(prompt_method):
                if tool_details.mode == ToolDetailMode.FULL:
                    response = prompt_method(
                        prompt,
                        event_sink=event_sink,
                        include_tool_details=True,
                    )
                else:
                    response = prompt_method(prompt, event_sink=event_sink)
            else:
                response = getattr(session, "run")(prompt)
            if not event_sink.final_text_was_streamed:
                event_sink.write_final_text(response)
        except KeyboardInterrupt:
            if event_sink.abort_stream():
                stdout.write(
                    f"{render_message('Generation cancelled; partial assistant text was not committed.', 'warning', color=color)}\n"
                )
            else:
                stdout.write(
                    f"{render_message('Generation cancelled; no turn was committed.', 'warning', color=color)}\n"
                )
            stdout.flush()
            continue
        except Exception as error:
            _report_aborted_stream(event_sink, stdout, color=color)
            stdout.write(f"{render_message(render_turn_failure(error), 'error', color=color)}\n")
        stdout.flush()


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
    selected: list[str] = []
    total_bytes = 0
    for turn in reversed(turns):
        user = getattr(turn, "user", None)
        text = getattr(user, "text", None)
        if not isinstance(text, str):
            continue
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            continue
        if "\x00" in text or len(text) > MAX_PROMPT_CHARACTERS or len(encoded) > MAX_PROMPT_BYTES:
            continue
        if len(selected) == MAX_PROMPT_HISTORY_ENTRIES:
            break
        if total_bytes + len(encoded) > MAX_PROMPT_HISTORY_BYTES:
            break
        selected.append(text)
        total_bytes += len(encoded)
    return tuple(reversed(selected))


def _report_aborted_stream(
    event_sink: TerminalEventSink,
    stdout: TextIO,
    *,
    color: bool,
) -> None:
    if event_sink.abort_stream():
        stdout.write(
            f"{render_message('Partial assistant text was not committed.', 'warning', color=color)}\n"
        )
