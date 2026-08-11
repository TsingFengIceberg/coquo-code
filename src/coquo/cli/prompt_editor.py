"""Provider-neutral prompt input outcomes with TTY and deterministic stream backends."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import sys
from typing import Protocol, TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output import Output
from prompt_toolkit.validation import ValidationError, Validator

from coquo.cli.slash import SLASH_COMPLETIONS, TOP_LEVEL_COMMANDS

MAX_PROMPT_CHARACTERS = 256 * 1024
MAX_PROMPT_BYTES = 256 * 1024
MAX_PROMPT_HISTORY_ENTRIES = 1000
MAX_PROMPT_HISTORY_BYTES = 4 * 1024 * 1024


class PromptReadKind(StrEnum):
    """Closed outcomes from one prompt-editor interaction."""

    SUBMIT = "submit"
    CANCEL = "cancel"
    EXIT = "exit"


@dataclass(frozen=True)
class PromptRead:
    """One editor outcome; only submit carries exact user text."""

    kind: PromptReadKind
    text: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not PromptReadKind:
            raise ValueError("prompt read kind is invalid")
        if self.kind == PromptReadKind.SUBMIT:
            if not isinstance(self.text, str):
                raise ValueError("submitted prompt text is invalid")
        elif self.text is not None:
            raise ValueError("non-submit prompt read cannot carry text")

    @classmethod
    def submit(cls, text: str) -> PromptRead:
        return cls(PromptReadKind.SUBMIT, text)

    @classmethod
    def cancel(cls) -> PromptRead:
        return cls(PromptReadKind.CANCEL)

    @classmethod
    def exit(cls) -> PromptRead:
        return cls(PromptReadKind.EXIT)


class PromptInputError(ValueError):
    """Reject a submitted prompt that violates the Host input boundary."""


class PromptEditor(Protocol):
    """Read exact prompt text without owning Session or model behavior."""

    def read(self, prompt: str, *, bottom_toolbar: str | None = None) -> PromptRead: ...

    def set_history(self, entries: tuple[str, ...]) -> None: ...


class StreamPromptEditor:
    """Deterministic single-line fallback for injected or non-TTY streams."""

    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        self._stdin = stdin
        self._stdout = stdout

    def read(self, prompt: str, *, bottom_toolbar: str | None = None) -> PromptRead:
        self._stdout.write(prompt)
        self._stdout.flush()
        try:
            line = self._stdin.readline()
        except KeyboardInterrupt:
            return PromptRead.exit()
        if line == "":
            return PromptRead.exit()
        text = _remove_one_line_ending(line)
        validate_prompt_text(text)
        return PromptRead.submit(text)

    def set_history(self, entries: tuple[str, ...]) -> None:
        del entries


class TerminalPromptEditor:
    """Persistent prompt-toolkit TTY editor with multiline and paste support."""

    def __init__(
        self,
        *,
        input: Input | None = None,
        output: Output | None = None,
    ) -> None:
        self._history = InMemoryHistory()
        self._session: PromptSession[str] = PromptSession(
            multiline=True,
            prompt_continuation=lambda _width, _line, _wrap: "  ",
            key_bindings=_prompt_key_bindings(),
            completer=SlashCommandCompleter(),
            complete_while_typing=False,
            enable_history_search=True,
            history=self._history,
            validator=_PromptBoundsValidator(),
            validate_while_typing=False,
            reserve_space_for_menu=4,
            include_default_pygments_style=False,
            input=input,
            output=output,
        )

    def read(self, prompt: str, *, bottom_toolbar: str | None = None) -> PromptRead:
        try:
            toolbar = ANSI(bottom_toolbar) if bottom_toolbar is not None else None
            text = self._session.prompt(ANSI(f"\n{prompt}"), bottom_toolbar=toolbar)
        except _PromptCancelled:
            return PromptRead.cancel()
        except (_PromptExit, EOFError):
            return PromptRead.exit()
        except KeyboardInterrupt:
            return PromptRead.cancel()
        validate_prompt_text(text)
        return PromptRead.submit(text)

    def set_history(self, entries: tuple[str, ...]) -> None:
        _validate_history_entries(entries)
        self._history = InMemoryHistory(entries)
        self._session.history = self._history
        self._session.default_buffer.history = self._history


class SlashCommandCompleter(Completer):
    """Complete bounded top-level and second-level Host slash commands."""

    def get_completions(self, document: Document, _complete_event: object):
        prefix = document.text_before_cursor
        if (
            document.cursor_position != len(document.text)
            or not prefix.startswith("/")
            or "\n" in prefix
        ):
            return
        has_subcommand = " " in prefix
        for candidate in SLASH_COMPLETIONS:
            if candidate.top_level != (not has_subcommand):
                continue
            if not candidate.text.startswith(prefix):
                continue
            yield Completion(
                candidate.text,
                start_position=-len(prefix),
                display_meta=candidate.description,
            )


class _PromptBoundsValidator(Validator):
    def validate(self, document: Document) -> None:
        try:
            validate_prompt_text(document.text)
        except PromptInputError as error:
            raise ValidationError(
                cursor_position=len(document.text),
                message=str(error),
            ) from None


class _PromptCancelled(Exception):
    pass


class _PromptExit(Exception):
    pass


def create_prompt_editor(stdin: TextIO, stdout: TextIO) -> PromptEditor:
    """Select the TTY editor only for the process's real interactive streams."""
    if stdin is sys.stdin and stdout is sys.stdout and _is_terminal(stdin) and _is_terminal(stdout):
        return TerminalPromptEditor()
    return StreamPromptEditor(stdin, stdout)


def complete_command(text: str, state: int) -> str | None:
    """Retain the deterministic completion seam used by unit tests."""
    matches = [command for command in TOP_LEVEL_COMMANDS if command.startswith(text)]
    return matches[state] if state < len(matches) else None


def validate_prompt_text(text: str) -> None:
    """Apply exact character and UTF-8 byte bounds without rewriting input."""
    if not isinstance(text, str):
        raise PromptInputError("prompt text is invalid")
    if "\x00" in text:
        raise PromptInputError("prompt must not contain NUL")
    if len(text) > MAX_PROMPT_CHARACTERS:
        raise PromptInputError(f"prompt exceeds {MAX_PROMPT_CHARACTERS} characters")
    try:
        byte_count = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise PromptInputError("prompt must be valid UTF-8") from None
    if byte_count > MAX_PROMPT_BYTES:
        raise PromptInputError(f"prompt exceeds {MAX_PROMPT_BYTES} UTF-8 bytes")


def _prompt_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter", eager=True)
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter", eager=True)
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("c-c", eager=True)
    def _cancel_or_exit(event) -> None:
        exception = _PromptCancelled() if event.current_buffer.text else _PromptExit()
        event.app.exit(exception=exception)

    @bindings.add("c-d", eager=True)
    def _delete_or_exit(event) -> None:
        buffer = event.current_buffer
        if not buffer.text:
            event.app.exit(exception=_PromptExit())
        elif buffer.cursor_position < len(buffer.text):
            buffer.delete(1)

    return bindings


def _remove_one_line_ending(line: str) -> str:
    if line.endswith("\n"):
        line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]
    elif line.endswith("\r"):
        line = line[:-1]
    return line


def _is_terminal(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, OSError):
        return False


def _validate_history_entries(entries: tuple[str, ...]) -> None:
    if not isinstance(entries, tuple) or len(entries) > MAX_PROMPT_HISTORY_ENTRIES:
        raise PromptInputError("prompt history is invalid")
    total_bytes = 0
    for entry in entries:
        validate_prompt_text(entry)
        total_bytes += len(entry.encode("utf-8"))
        if total_bytes > MAX_PROMPT_HISTORY_BYTES:
            raise PromptInputError("prompt history exceeds its byte limit")
