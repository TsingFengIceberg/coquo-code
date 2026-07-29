from __future__ import annotations

import io

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from leonervis_code.cli.prompt_editor import (
    MAX_PROMPT_BYTES,
    PromptInputError,
    PromptRead,
    PromptReadKind,
    SlashCommandCompleter,
    StreamPromptEditor,
    TerminalPromptEditor,
    create_prompt_editor,
    validate_prompt_text,
)


def test_prompt_read_is_a_closed_submit_cancel_exit_contract() -> None:
    assert PromptRead.submit("exact\ntext") == PromptRead(PromptReadKind.SUBMIT, "exact\ntext")
    assert PromptRead.cancel() == PromptRead(PromptReadKind.CANCEL)
    assert PromptRead.exit() == PromptRead(PromptReadKind.EXIT)
    with pytest.raises(ValueError, match="cannot carry"):
        PromptRead(PromptReadKind.CANCEL, "text")


def test_stream_editor_is_single_line_and_removes_only_one_line_ending() -> None:
    output = io.StringIO()
    editor = StreamPromptEditor(io.StringIO("first\r\nsecond\n"), output)

    assert editor.read("> ") == PromptRead.submit("first")
    assert editor.read("> ") == PromptRead.submit("second")
    assert editor.read("> ") == PromptRead.exit()
    assert output.getvalue() == "> > > "


def test_factory_uses_stream_editor_for_injected_streams() -> None:
    assert isinstance(create_prompt_editor(io.StringIO(), io.StringIO()), StreamPromptEditor)


def test_terminal_editor_inserts_newline_with_alt_enter() -> None:
    with create_pipe_input() as pipe:
        editor = TerminalPromptEditor(input=pipe, output=DummyOutput())
        pipe.send_text("first\x1b\rsecond\r")

        assert editor.read("> ") == PromptRead.submit("first\nsecond")


def test_terminal_editor_preserves_trailing_newline_on_submit() -> None:
    with create_pipe_input() as pipe:
        editor = TerminalPromptEditor(input=pipe, output=DummyOutput())
        pipe.send_text("  indented\x1b\r\r")

        assert editor.read("> ") == PromptRead.submit("  indented\n")


def test_terminal_editor_preserves_bracketed_multiline_paste_as_one_buffer() -> None:
    with create_pipe_input() as pipe:
        editor = TerminalPromptEditor(input=pipe, output=DummyOutput())
        pipe.send_text("\x1b[200~def greet():\n    return 'hello'\x1b[201~\r")

        assert editor.read("> ") == PromptRead.submit("def greet():\n    return 'hello'")


def test_terminal_editor_cancels_nonempty_draft_and_exits_on_empty_ctrl_c() -> None:
    with create_pipe_input() as pipe:
        editor = TerminalPromptEditor(input=pipe, output=DummyOutput())
        pipe.send_text("draft\x03")
        assert editor.read("> ") == PromptRead.cancel()

        pipe.send_text("\x03")
        assert editor.read("> ") == PromptRead.exit()


def test_terminal_editor_exits_on_empty_ctrl_d() -> None:
    with create_pipe_input() as pipe:
        editor = TerminalPromptEditor(input=pipe, output=DummyOutput())
        pipe.send_text("\x04")

        assert editor.read("> ") == PromptRead.exit()


def test_terminal_editor_reuses_multiline_history_as_one_entry() -> None:
    with create_pipe_input() as pipe:
        editor = TerminalPromptEditor(input=pipe, output=DummyOutput())
        pipe.send_text("first\x1b\rsecond\r")
        assert editor.read("> ") == PromptRead.submit("first\nsecond")

        pipe.send_text("\x1b[A\r")
        assert editor.read("> ") == PromptRead.submit("first\nsecond")


def test_terminal_editor_replaces_history_with_session_entries() -> None:
    with create_pipe_input() as pipe:
        editor = TerminalPromptEditor(input=pipe, output=DummyOutput())
        editor.set_history(("older", "latest\nmultiline"))
        pipe.send_text("\x1b[A\r")

        assert editor.read("> ") == PromptRead.submit("latest\nmultiline")

        editor.set_history(("other session",))
        pipe.send_text("\x1b[A\r")
        assert editor.read("> ") == PromptRead.submit("other session")


def test_terminal_editor_rebuild_removes_uncommitted_and_slash_submissions() -> None:
    with create_pipe_input() as pipe:
        editor = TerminalPromptEditor(input=pipe, output=DummyOutput())
        editor.set_history(("committed",))
        pipe.send_text("/help\r")
        assert editor.read("> ") == PromptRead.submit("/help")

        editor.set_history(("committed",))
        pipe.send_text("\x1b[A\r")
        assert editor.read("> ") == PromptRead.submit("committed")


def test_slash_completion_is_single_line_and_end_of_buffer_only() -> None:
    completer = SlashCommandCompleter()
    event = CompleteEvent(completion_requested=True)

    top_level = list(completer.get_completions(Document("/h", 2), event))
    assert [completion.text for completion in top_level] == ["/help", "/history"]
    assert [fragment_list_to_text(completion.display_meta) for completion in top_level] == [
        "Show Host commands",
        "Show recent Session turns",
    ]
    assert [
        completion.text for completion in completer.get_completions(Document("/session "), event)
    ] == [
        "/session show",
        "/session list",
        "/session new",
        "/session rename",
        "/session archive",
        "/session unarchive",
    ]
    assert [
        completion.text for completion in completer.get_completions(Document("/provider c"), event)
    ] == ["/provider current"]
    assert [
        completion.text
        for completion in completer.get_completions(Document("/tool-details "), event)
    ] == ["/tool-details compact", "/tool-details full"]
    assert list(completer.get_completions(Document("/h\ntext"), event)) == []
    assert list(completer.get_completions(Document("ordinary"), event)) == []


def test_prompt_bounds_reject_bytes_without_rewriting_valid_text() -> None:
    exact = "  code\n    indented\n"
    validate_prompt_text(exact)

    with pytest.raises(PromptInputError, match="NUL"):
        validate_prompt_text("before\x00after")
    with pytest.raises(PromptInputError, match="UTF-8 bytes"):
        validate_prompt_text("界" * (MAX_PROMPT_BYTES // 3 + 1))
