"""Fixed ordered model-tool contract for the current bounded workspace surface."""

from __future__ import annotations

from leonervis_code.core.contracts import ToolArguments, ToolUse
from leonervis_code.core.effective_context import CanonicalToolDefinition
from leonervis_code.tools.copy_file import COPY_FILE_TOOL_NAME, copy_file_tool_snapshot
from leonervis_code.tools.delete_directory import (
    DELETE_DIRECTORY_TOOL_NAME,
    delete_directory_tool_snapshot,
)
from leonervis_code.tools.delete_file import DELETE_FILE_TOOL_NAME, delete_file_tool_snapshot
from leonervis_code.tools.edit_file import EDIT_FILE_TOOL_NAME, edit_file_tool_snapshot
from leonervis_code.tools.glob import GLOB_TOOL_NAME, glob_tool_snapshot
from leonervis_code.tools.grep import GREP_TOOL_NAME, grep_tool_snapshot
from leonervis_code.tools.grep_regex import GREP_REGEX_TOOL_NAME, grep_regex_tool_snapshot
from leonervis_code.tools.git_diff import (
    GIT_DIFF_TOOL_NAME,
    GitDiffScope,
    git_diff_tool_snapshot,
)
from leonervis_code.tools.git_status import GIT_STATUS_TOOL_NAME, git_status_tool_snapshot
from leonervis_code.tools.git_log import (
    GIT_LOG_TOOL_NAME,
    MAX_GIT_LOG_LIMIT,
    git_log_tool_snapshot,
)
from leonervis_code.tools.git_repository import GIT_OBJECT_ID_PATTERN
from leonervis_code.tools.git_show import GIT_SHOW_TOOL_NAME, git_show_tool_snapshot
from leonervis_code.tools.list_directory import (
    LIST_DIRECTORY_TOOL_NAME,
    list_directory_tool_snapshot,
)
from leonervis_code.tools.list_tree import (
    LIST_TREE_TOOL_NAME,
    MAX_LIST_TREE_DEPTH,
    list_tree_tool_snapshot,
)
from leonervis_code.tools.mkdir import MKDIR_TOOL_NAME, mkdir_tool_snapshot
from leonervis_code.tools.move_file import MOVE_FILE_TOOL_NAME, move_file_tool_snapshot
from leonervis_code.tools.patch_file import (
    MAX_PATCH_FILE_EDITS,
    MAX_PATCH_FILE_TEXT_BYTES,
    MAX_PATCH_FILE_TEXT_CHARACTERS,
    PATCH_FILE_TOOL_NAME,
    patch_file_tool_snapshot,
)
from leonervis_code.tools.read_file import READ_FILE_TOOL_NAME, read_file_tool_snapshot
from leonervis_code.tools.read_file_lines import (
    MAX_READ_FILE_LINES_COUNT,
    MAX_READ_FILE_LINES_START,
    READ_FILE_LINES_TOOL_NAME,
    read_file_lines_tool_snapshot,
)
from leonervis_code.tools.run_command import (
    MAX_COMMAND_ARGUMENTS,
    MAX_COMMAND_ARGUMENT_BYTES,
    MAX_COMMAND_ARGUMENT_CHARACTERS,
    MAX_COMMAND_ARGV_BYTES,
    MAX_COMMAND_CWD_BYTES,
    MAX_COMMAND_CWD_CHARACTERS,
    MAX_COMMAND_TIMEOUT_SECONDS,
    MIN_COMMAND_TIMEOUT_SECONDS,
    RUN_COMMAND_TOOL_NAME,
    run_command_tool_snapshot,
)
from leonervis_code.tools.write_file import WRITE_FILE_TOOL_NAME, write_file_tool_snapshot
from leonervis_code.tools.stat_path import STAT_PATH_TOOL_NAME, stat_path_tool_snapshot

MAX_TOOL_CALLS_PER_RESPONSE = 8
MAX_TOOL_REQUESTS_PER_TURN = 32
MAX_PROVIDER_INVOCATIONS_PER_TURN = 24
# Compatibility name retained for terminal event consumers and older callers.
MAX_TOOL_EXECUTIONS_PER_TURN = MAX_TOOL_REQUESTS_PER_TURN
MAX_TOOL_INPUT_STRING_CHARACTERS = 4096
MAX_TOOL_INPUT_STRING_BYTES = 4096

TOOL_CATALOG: tuple[CanonicalToolDefinition, ...] = (
    read_file_tool_snapshot(),
    glob_tool_snapshot(),
    grep_tool_snapshot(),
    write_file_tool_snapshot(),
    edit_file_tool_snapshot(),
    run_command_tool_snapshot(),
    mkdir_tool_snapshot(),
    move_file_tool_snapshot(),
    delete_file_tool_snapshot(),
    delete_directory_tool_snapshot(),
    list_directory_tool_snapshot(),
    copy_file_tool_snapshot(),
    read_file_lines_tool_snapshot(),
    stat_path_tool_snapshot(),
    list_tree_tool_snapshot(),
    grep_regex_tool_snapshot(),
    patch_file_tool_snapshot(),
    git_status_tool_snapshot(),
    git_diff_tool_snapshot(),
    git_log_tool_snapshot(),
    git_show_tool_snapshot(),
)


def model_tool_definitions() -> tuple[dict[str, object], ...]:
    """Return fresh definitions in the canonical model-visible order."""
    return tuple(definition.as_mapping() for definition in TOOL_CATALOG)


def tool_use_from_input(
    tool_use_id: str,
    name: str,
    tool_input: dict[str, object],
    *,
    assistant_text: str | None = None,
) -> ToolUse:
    """Validate one exact known-tool input and freeze its neutral arguments."""
    expected = _expected_keys(name)
    if not isinstance(tool_input, dict) or set(tool_input) != expected:
        raise ValueError(f"{name} input is malformed")
    _validate_known_input(name, tool_input, expected)
    return ToolUse(
        tool_use_id=tool_use_id,
        name=name,
        arguments=ToolArguments.from_mapping(tool_input),
        assistant_text=assistant_text,
    )


def tool_input_from_use(request: ToolUse) -> dict[str, object]:
    """Project and revalidate immutable arguments for one known tool."""
    if not isinstance(request.arguments, ToolArguments):
        raise ValueError("tool arguments are invalid")
    tool_input = request.arguments.as_mapping()
    expected = _expected_keys(request.name)
    if set(tool_input) != expected:
        raise ValueError(f"{request.name} input is malformed")
    _validate_known_input(request.name, tool_input, expected)
    return tool_input


def _expected_keys(name: str) -> set[str]:
    if name == READ_FILE_TOOL_NAME:
        return {"path"}
    if name == GLOB_TOOL_NAME:
        return {"pattern"}
    if name == GREP_TOOL_NAME:
        return {"query", "include"}
    if name == WRITE_FILE_TOOL_NAME:
        return {"path", "content"}
    if name == EDIT_FILE_TOOL_NAME:
        return {"path", "old_text", "new_text"}
    if name == RUN_COMMAND_TOOL_NAME:
        return {"argv", "cwd", "timeout_seconds"}
    if name == MKDIR_TOOL_NAME:
        return {"path"}
    if name == MOVE_FILE_TOOL_NAME:
        return {"source", "destination"}
    if name == DELETE_FILE_TOOL_NAME:
        return {"path"}
    if name == DELETE_DIRECTORY_TOOL_NAME:
        return {"path"}
    if name == LIST_DIRECTORY_TOOL_NAME:
        return {"path"}
    if name == COPY_FILE_TOOL_NAME:
        return {"source", "destination"}
    if name == READ_FILE_LINES_TOOL_NAME:
        return {"path", "start_line", "line_count"}
    if name == STAT_PATH_TOOL_NAME:
        return {"path"}
    if name == LIST_TREE_TOOL_NAME:
        return {"path", "max_depth"}
    if name == GREP_REGEX_TOOL_NAME:
        return {"pattern", "include"}
    if name == PATCH_FILE_TOOL_NAME:
        return {"path", "edits"}
    if name == GIT_STATUS_TOOL_NAME:
        return set()
    if name == GIT_DIFF_TOOL_NAME:
        return {"scope", "path"}
    if name == GIT_LOG_TOOL_NAME:
        return {"limit", "path"}
    if name == GIT_SHOW_TOOL_NAME:
        return {"commit_id", "path"}
    raise ValueError(f"unsupported tool: {name}")


def _validate_known_input(name: str, tool_input: dict[str, object], expected: set[str]) -> None:
    if name == RUN_COMMAND_TOOL_NAME:
        argv = tool_input["argv"]
        cwd = tool_input["cwd"]
        timeout = tool_input["timeout_seconds"]
        if not isinstance(argv, list) or not (1 <= len(argv) <= MAX_COMMAND_ARGUMENTS):
            raise ValueError(
                f"run_command argv must contain 1 to {MAX_COMMAND_ARGUMENTS} arguments"
            )
        total_bytes = 0
        for index, argument in enumerate(argv):
            _validate_input_string(
                argument,
                label=f"run_command argv[{index}]",
                allow_whitespace=index != 0,
                allow_empty=index != 0,
                max_characters=MAX_COMMAND_ARGUMENT_CHARACTERS,
                max_bytes=MAX_COMMAND_ARGUMENT_BYTES,
            )
            total_bytes += len(argument.encode("utf-8"))
        if total_bytes > MAX_COMMAND_ARGV_BYTES:
            raise ValueError(f"run_command argv exceeds {MAX_COMMAND_ARGV_BYTES} total bytes")
        _validate_input_string(
            cwd,
            label="run_command cwd",
            max_characters=MAX_COMMAND_CWD_CHARACTERS,
            max_bytes=MAX_COMMAND_CWD_BYTES,
        )
        if type(timeout) is not int or not (
            MIN_COMMAND_TIMEOUT_SECONDS <= timeout <= MAX_COMMAND_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "run_command timeout_seconds must be an integer from "
                f"{MIN_COMMAND_TIMEOUT_SECONDS} to {MAX_COMMAND_TIMEOUT_SECONDS}"
            )
        return

    if name == READ_FILE_LINES_TOOL_NAME:
        _validate_input_string(tool_input["path"], label="read_file_lines path")
        start_line = tool_input["start_line"]
        line_count = tool_input["line_count"]
        if type(start_line) is not int or not 1 <= start_line <= MAX_READ_FILE_LINES_START:
            raise ValueError("read_file_lines start_line is invalid")
        if type(line_count) is not int or not 1 <= line_count <= MAX_READ_FILE_LINES_COUNT:
            raise ValueError("read_file_lines line_count is invalid")
        return

    if name == LIST_TREE_TOOL_NAME:
        _validate_input_string(tool_input["path"], label="list_tree path")
        max_depth = tool_input["max_depth"]
        if type(max_depth) is not int or not 1 <= max_depth <= MAX_LIST_TREE_DEPTH:
            raise ValueError("list_tree max_depth is invalid")
        return

    if name == PATCH_FILE_TOOL_NAME:
        _validate_input_string(tool_input["path"], label="patch_file path")
        edits = tool_input["edits"]
        if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_PATCH_FILE_EDITS:
            raise ValueError("patch_file edits are invalid")
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict) or set(edit) != {"old_text", "new_text"}:
                raise ValueError(f"patch_file edits[{index}] is malformed")
            _validate_input_string(
                edit["old_text"],
                label=f"patch_file edits[{index}].old_text",
                allow_whitespace=True,
                max_characters=MAX_PATCH_FILE_TEXT_CHARACTERS,
                max_bytes=MAX_PATCH_FILE_TEXT_BYTES,
            )
            _validate_input_string(
                edit["new_text"],
                label=f"patch_file edits[{index}].new_text",
                allow_whitespace=True,
                allow_empty=True,
                max_characters=MAX_PATCH_FILE_TEXT_CHARACTERS,
                max_bytes=MAX_PATCH_FILE_TEXT_BYTES,
            )
        return

    if name == GREP_REGEX_TOOL_NAME:
        _validate_input_string(
            tool_input["pattern"],
            label="grep_regex pattern",
            allow_whitespace=True,
        )
        _validate_input_string(tool_input["include"], label="grep_regex include")
        return

    if name == GIT_DIFF_TOOL_NAME:
        scope = tool_input["scope"]
        if not isinstance(scope, str) or scope not in {
            GitDiffScope.UNSTAGED.value,
            GitDiffScope.STAGED.value,
        }:
            raise ValueError("git_diff scope is invalid")
        _validate_input_string(tool_input["path"], label="git_diff path")
        return

    if name == GIT_LOG_TOOL_NAME:
        limit = tool_input["limit"]
        if type(limit) is not int or not 1 <= limit <= MAX_GIT_LOG_LIMIT:
            raise ValueError("git_log limit is invalid")
        _validate_input_string(tool_input["path"], label="git_log path")
        return

    if name == GIT_SHOW_TOOL_NAME:
        commit_id = tool_input["commit_id"]
        if not isinstance(commit_id, str) or not GIT_OBJECT_ID_PATTERN.fullmatch(commit_id):
            raise ValueError("git_show commit_id is invalid")
        _validate_input_string(tool_input["path"], label="git_show path")
        return

    for key in expected:
        _validate_input_string(
            tool_input[key],
            label=f"{name} {key}",
            allow_whitespace=key in {"query", "content", "old_text", "new_text"},
            allow_empty=key in {"content", "new_text"},
        )


def _validate_input_string(
    value: object,
    *,
    label: str,
    allow_whitespace: bool = False,
    allow_empty: bool = False,
    max_characters: int = MAX_TOOL_INPUT_STRING_CHARACTERS,
    max_bytes: int = MAX_TOOL_INPUT_STRING_BYTES,
) -> None:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or (not allow_empty and not allow_whitespace and not value.strip())
        or "\x00" in value
    ):
        raise ValueError(f"{label} must be nonblank text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{label} must be valid UTF-8") from None
    if len(value) > max_characters or len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the supported size")
