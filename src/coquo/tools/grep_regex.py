"""Process-isolated bounded regular-expression search for workspace text files."""

from __future__ import annotations

import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import re
import stat

from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.tools.file_selector import (
    FileSelectionFailure,
    SelectorFailureKind,
    select_files,
)

GREP_REGEX_TOOL_NAME = "grep_regex"
MAX_GREP_REGEX_PATTERN_CHARACTERS = 4096
MAX_GREP_REGEX_PATTERN_BYTES = 4096
MAX_GREP_REGEX_CANDIDATE_FILES = 1_000
MAX_GREP_REGEX_FILE_BYTES = 1024 * 1024
MAX_GREP_REGEX_AGGREGATE_BYTES = 16 * 1024 * 1024
MAX_GREP_REGEX_MATCHES = 200
MAX_GREP_REGEX_OUTPUT_BYTES = 32 * 1024
GREP_REGEX_TIMEOUT_SECONDS = 1.0
GREP_REGEX_CLEANUP_SECONDS = 1.0
GREP_REGEX_TRUNCATION_SENTINEL = '{"truncated":true}\n'


class _GrepRegexFailure(RuntimeError):
    pass


def grep_regex_model_definition() -> dict[str, object]:
    return {
        "name": GREP_REGEX_TOOL_NAME,
        "description": (
            "Search regular UTF-8 workspace files with one case-sensitive Python regular "
            "expression applied independently to each logical line. Use this read-only tool "
            "only when literal grep is insufficient. Matching runs in a bounded worker process "
            "and returns deterministic JSON Lines containing path, line, and text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_GREP_REGEX_PATTERN_CHARACTERS,
                    "description": "One non-empty single-line Python regular expression.",
                },
                "include": {
                    "type": "string",
                    "description": (
                        "Portable workspace-relative '/'-separated regular-file glob pattern."
                    ),
                },
            },
            "required": ["pattern", "include"],
            "additionalProperties": False,
        },
    }


def grep_regex_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(grep_regex_model_definition())


class GrepRegexTool:
    """Run potentially expensive stdlib regex matching outside the Host process."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"include", "pattern"}:
                raise _GrepRegexFailure("grep_regex input is malformed")
            pattern = arguments["pattern"]
            include = arguments["include"]
            if not isinstance(pattern, str) or not isinstance(include, str):
                raise _GrepRegexFailure("grep_regex input is malformed")
            _validate_pattern(pattern)
            result = self._run_worker(pattern, include)
        except (AttributeError, ValueError):
            return self._error(request, "grep_regex input is malformed")
        except _GrepRegexFailure as error:
            return self._error(request, str(error))
        status, content, truncated = result
        if status == "error":
            return self._error(request, content)
        return ToolResult(request.tool_use_id, content, truncated=truncated)

    def _run_worker(self, pattern: str, include: str) -> tuple[str, str, bool]:
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(
            target=_worker_main,
            args=(send, str(self._workspace), pattern, include),
            daemon=True,
        )
        started = False
        try:
            process.start()
            started = True
            send.close()
            process.join(GREP_REGEX_TIMEOUT_SECONDS)
            if process.is_alive():
                process.terminate()
                process.join(GREP_REGEX_CLEANUP_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(GREP_REGEX_CLEANUP_SECONDS)
                raise _GrepRegexFailure(
                    "grep_regex timed out; use a simpler pattern or narrower include"
                )
            if process.exitcode != 0 or not receive.poll():
                raise _GrepRegexFailure("grep_regex worker failed")
            payload = receive.recv()
            if (
                not isinstance(payload, tuple)
                or len(payload) != 3
                or payload[0] not in {"ok", "error"}
                or not isinstance(payload[1], str)
                or type(payload[2]) is not bool
            ):
                raise _GrepRegexFailure("grep_regex worker returned an invalid result")
            return payload
        except _GrepRegexFailure:
            raise
        except (EOFError, OSError, RuntimeError):
            raise _GrepRegexFailure("grep_regex worker failed") from None
        finally:
            send.close()
            receive.close()
            if started and process.is_alive():
                process.terminate()
                process.join(GREP_REGEX_CLEANUP_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(GREP_REGEX_CLEANUP_SECONDS)
            if started:
                process.close()

    @staticmethod
    def _error(request: ToolUse, content: str) -> ToolResult:
        return ToolResult(request.tool_use_id, content, is_error=True)


def _worker_main(send: Connection, workspace: str, pattern: str, include: str) -> None:
    try:
        try:
            expression = re.compile(pattern)
        except re.error:
            raise _GrepRegexFailure("grep_regex pattern is invalid") from None
        try:
            candidates = select_files(
                Path(workspace),
                include,
                max_files=MAX_GREP_REGEX_CANDIDATE_FILES,
            )
        except FileSelectionFailure as error:
            raise _GrepRegexFailure(_selector_message(error.kind)) from None
        content, truncated = _search(expression, candidates)
        send.send(("ok", content, truncated))
    except _GrepRegexFailure as error:
        send.send(("error", str(error), False))
    except BaseException:
        send.send(("error", "grep_regex worker failed", False))
    finally:
        send.close()


def _validate_pattern(pattern: str) -> None:
    if not pattern:
        raise _GrepRegexFailure("grep_regex pattern must not be empty")
    if any(character in pattern for character in ("\x00", "\r", "\n")):
        raise _GrepRegexFailure("grep_regex pattern must be one line without NUL")
    try:
        encoded = pattern.encode("utf-8")
    except UnicodeEncodeError:
        raise _GrepRegexFailure("grep_regex pattern must be valid UTF-8") from None
    if (
        len(pattern) > MAX_GREP_REGEX_PATTERN_CHARACTERS
        or len(encoded) > MAX_GREP_REGEX_PATTERN_BYTES
    ):
        raise _GrepRegexFailure("grep_regex pattern exceeds the supported size")


def _search(expression: re.Pattern[str], candidates) -> tuple[str, bool]:
    output: list[str] = []
    output_bytes = 0
    aggregate_bytes = 0
    match_count = 0
    sentinel_bytes = len(GREP_REGEX_TRUNCATION_SENTINEL.encode("utf-8"))

    for candidate in candidates:
        data = _read_candidate(candidate.path)
        aggregate_bytes += len(data)
        if aggregate_bytes > MAX_GREP_REGEX_AGGREGATE_BYTES:
            raise _GrepRegexFailure(
                "grep_regex aggregate read limit reached; use a narrower include"
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise _GrepRegexFailure(
                "grep_regex encountered a file that is not valid UTF-8"
            ) from None
        if "\x00" in text:
            raise _GrepRegexFailure("grep_regex encountered a file containing NUL")

        for line_number, line in enumerate(_logical_lines(text), start=1):
            if expression.search(line) is None:
                continue
            record = _match_record(candidate.relative_path, line_number, line)
            record_bytes = len(record.encode("utf-8"))
            if record_bytes + sentinel_bytes > MAX_GREP_REGEX_OUTPUT_BYTES:
                raise _GrepRegexFailure(
                    "grep_regex matching line exceeds the output limit; use read_file_lines"
                )
            match_count += 1
            if (
                match_count > MAX_GREP_REGEX_MATCHES
                or output_bytes + record_bytes + sentinel_bytes > MAX_GREP_REGEX_OUTPUT_BYTES
            ):
                output.append(GREP_REGEX_TRUNCATION_SENTINEL)
                return "".join(output), True
            output.append(record)
            output_bytes += record_bytes
    return "".join(output), False


def _read_candidate(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        descriptor = os.open(path, flags)
    except (FileNotFoundError, PermissionError):
        raise _GrepRegexFailure("grep_regex encountered an unreadable file") from None
    except OSError:
        raise _GrepRegexFailure("grep_regex could not read a selected file") from None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise _GrepRegexFailure("grep_regex selected file changed before it could be read")
        if opened.st_size > MAX_GREP_REGEX_FILE_BYTES:
            raise _GrepRegexFailure("grep_regex selected file exceeds the per-file limit")
        chunks: list[bytes] = []
        remaining = MAX_GREP_REGEX_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_GREP_REGEX_FILE_BYTES:
            raise _GrepRegexFailure("grep_regex selected file exceeds the per-file limit")
        after = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise _GrepRegexFailure("grep_regex selected file changed while being read")
        return data
    except _GrepRegexFailure:
        raise
    except OSError:
        raise _GrepRegexFailure("grep_regex could not read a selected file") from None
    finally:
        os.close(descriptor)


def _logical_lines(text: str) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        if text[index] not in {"\r", "\n"}:
            index += 1
            continue
        lines.append(text[start:index])
        if text[index] == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            index += 1
        index += 1
        start = index
    if start < len(text):
        lines.append(text[start:])
    return lines


def _match_record(path: str, line: int, text: str) -> str:
    return (
        json.dumps(
            {"path": path, "line": line, "text": text},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    )


def _selector_message(kind: SelectorFailureKind) -> str:
    messages = {
        SelectorFailureKind.BLANK_PATTERN: "grep_regex include must not be blank",
        SelectorFailureKind.INVALID_COMPONENT: (
            "grep_regex include contains an unsupported path component"
        ),
        SelectorFailureKind.OVERSIZED_PATTERN: ("grep_regex include exceeds the supported size"),
        SelectorFailureKind.NONPORTABLE_PATTERN: (
            "grep_regex include must be workspace-relative and use '/' separators"
        ),
        SelectorFailureKind.DIRECTORY_LIMIT: (
            "grep_regex directory limit reached; use a narrower include"
        ),
        SelectorFailureKind.UNREADABLE_DIRECTORY: (
            "grep_regex encountered an unreadable directory"
        ),
        SelectorFailureKind.SCAN_FAILED: "grep_regex could not scan the workspace",
        SelectorFailureKind.INVALID_UTF8_PATH: (
            "grep_regex encountered a path that is not valid UTF-8"
        ),
        SelectorFailureKind.ENTRY_LIMIT: (
            "grep_regex traversal entry limit reached; use a narrower include"
        ),
        SelectorFailureKind.DEPTH_LIMIT: ("grep_regex depth limit reached; use a narrower include"),
        SelectorFailureKind.FILE_LIMIT: (
            "grep_regex candidate file limit reached; use a narrower include"
        ),
    }
    return messages[kind]
