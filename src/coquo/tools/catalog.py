"""Fixed ordered model-tool contract for the current bounded workspace surface."""

from __future__ import annotations

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.extensions import (
    ExtensionSource,
    ExtensionSourceKind,
    ExtensionToolContract,
    ToolExecutionKind,
    ToolExposure,
    ToolRegistrySnapshot,
    ToolSetSnapshot,
)
from coquo.core.permissions import PermissionAction
from coquo.core.task_admission import TASK_PROPOSE_START_TOOL_NAME
from coquo.core.skill_authoring import (
    SKILL_ACCEPT_CREATE_TOOL_NAME,
    SKILL_AUTHORING_CONTROL_TOOL_NAMES,
    SKILL_PROPOSE_CREATE_TOOL_NAME,
)
from coquo.tools.archive_list import ARCHIVE_LIST_TOOL_NAME, archive_list_tool_snapshot
from coquo.tools.checksum_file import CHECKSUM_FILE_TOOL_NAME, checksum_file_tool_snapshot
from coquo.tools.compare_files import COMPARE_FILES_TOOL_NAME, compare_files_tool_snapshot
from coquo.tools.copy_file import COPY_FILE_TOOL_NAME, copy_file_tool_snapshot
from coquo.tools.delete_directory import (
    DELETE_DIRECTORY_TOOL_NAME,
    delete_directory_tool_snapshot,
)
from coquo.tools.delete_file import DELETE_FILE_TOOL_NAME, delete_file_tool_snapshot
from coquo.tools.edit_file import EDIT_FILE_TOOL_NAME, edit_file_tool_snapshot
from coquo.tools.download_file import DOWNLOAD_FILE_TOOL_NAME, download_file_tool_snapshot
from coquo.tools.glob import GLOB_TOOL_NAME, glob_tool_snapshot
from coquo.tools.grep import GREP_TOOL_NAME, grep_tool_snapshot
from coquo.tools.grep_regex import GREP_REGEX_TOOL_NAME, grep_regex_tool_snapshot
from coquo.tools.git_diff import (
    GIT_DIFF_TOOL_NAME,
    GitDiffScope,
    git_diff_tool_snapshot,
)
from coquo.tools.git_blame import (
    GIT_BLAME_TOOL_NAME,
    MAX_GIT_BLAME_LINE_COUNT,
    MAX_GIT_BLAME_START_LINE,
    git_blame_tool_snapshot,
)
from coquo.tools.git_refs import GIT_REFS_TOOL_NAME, git_refs_tool_snapshot
from coquo.tools.git_status import GIT_STATUS_TOOL_NAME, git_status_tool_snapshot
from coquo.tools.git_log import (
    GIT_LOG_TOOL_NAME,
    MAX_GIT_LOG_LIMIT,
    git_log_tool_snapshot,
)
from coquo.tools.git_repository import GIT_OBJECT_ID_PATTERN
from coquo.tools.git_show import GIT_SHOW_TOOL_NAME, git_show_tool_snapshot
from coquo.tools.list_directory import (
    LIST_DIRECTORY_TOOL_NAME,
    list_directory_tool_snapshot,
)
from coquo.tools.list_tree import (
    LIST_TREE_TOOL_NAME,
    MAX_LIST_TREE_DEPTH,
    list_tree_tool_snapshot,
)
from coquo.tools.mkdir import MKDIR_TOOL_NAME, mkdir_tool_snapshot
from coquo.tools.move_directory import (
    MOVE_DIRECTORY_TOOL_NAME,
    move_directory_tool_snapshot,
)
from coquo.tools.move_file import MOVE_FILE_TOOL_NAME, move_file_tool_snapshot
from coquo.tools.patch_file import (
    MAX_PATCH_FILE_EDITS,
    MAX_PATCH_FILE_TEXT_BYTES,
    MAX_PATCH_FILE_TEXT_CHARACTERS,
    PATCH_FILE_TOOL_NAME,
    patch_file_tool_snapshot,
)
from coquo.tools.read_file import READ_FILE_TOOL_NAME, read_file_tool_snapshot
from coquo.tools.read_file_lines import (
    MAX_READ_FILE_LINES_COUNT,
    MAX_READ_FILE_LINES_START,
    READ_FILE_LINES_TOOL_NAME,
    read_file_lines_tool_snapshot,
)
from coquo.tools.run_command import (
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
from coquo.tools.write_file import WRITE_FILE_TOOL_NAME, write_file_tool_snapshot
from coquo.tools.stat_path import STAT_PATH_TOOL_NAME, stat_path_tool_snapshot
from coquo.tools.json_query import JSON_QUERY_TOOL_NAME, json_query_tool_snapshot
from coquo.tools.web_fetch import (
    WEB_FETCH_TOOL_NAME,
    WebFetchFormat,
    web_fetch_tool_snapshot,
)
from coquo.tools.web_search import (
    MAX_WEB_SEARCH_QUERY_BYTES,
    MAX_WEB_SEARCH_QUERY_CHARACTERS,
    MAX_WEB_SEARCH_RESULTS,
    MIN_WEB_SEARCH_RESULTS,
    WEB_SEARCH_TOOL_NAME,
    web_search_tool_snapshot,
)
from coquo.tools.task_coordination import (
    TASK_ACCEPT_ADMISSION_TOOL_NAME,
    TASK_ACCEPT_PLAN_TOOL_NAME,
    TASK_CONFIRM_COMPLETION_TOOL_NAME,
    TASK_PROPOSE_COMPLETION_TOOL_NAME,
    TASK_PROPOSE_PLAN_TOOL_NAME,
    TASK_REPORT_BLOCKER_TOOL_NAME,
    TASK_REPORT_REFLECTION_TOOL_NAME,
    task_control_tool_snapshots,
)
from coquo.tools.tool_discovery import (
    MAX_TOOL_PROMOTIONS,
    MAX_TOOL_SEARCH_QUERY_CHARACTERS,
    MAX_TOOL_SEARCH_RESULTS,
    MIN_TOOL_SEARCH_RESULTS,
    TOOL_DISCOVERY_TOOL_NAMES,
    TOOL_PROMOTE_TOOL_NAME,
    TOOL_SEARCH_TOOL_NAME,
    tool_discovery_snapshots,
)
from coquo.tools.skill_discovery import (
    MAX_SKILL_SEARCH_RESULTS,
    SKILL_LOAD_TOOL_NAME,
    SKILL_READ_RESOURCE_TOOL_NAME,
    SKILL_SEARCH_TOOL_NAME,
    skill_discovery_snapshots,
)
from coquo.tools.skill_authoring import skill_authoring_tool_snapshots
from coquo.tools.child_control import (
    CHILD_CONTROL_TOOL_NAMES,
    child_control_tool_snapshots,
    parse_child_control,
)
from coquo.tools.team_control import TEAM_CONTROL_TOOL_NAMES, team_control_tool_snapshots

MAX_TOOL_CALLS_PER_RESPONSE = 8
MAX_TOOL_REQUESTS_PER_TURN = 32
MAX_PROVIDER_INVOCATIONS_PER_TURN = 24
# Compatibility name retained for terminal event consumers and older callers.
MAX_TOOL_EXECUTIONS_PER_TURN = MAX_TOOL_REQUESTS_PER_TURN
MAX_TOOL_INPUT_STRING_CHARACTERS = 4096
MAX_TOOL_INPUT_STRING_BYTES = 4096

CHILD_CONTROL_TOOL_CATALOG = child_control_tool_snapshots()
TEAM_CONTROL_TOOL_CATALOG = team_control_tool_snapshots()

ORDINARY_TOOL_CATALOG: tuple[CanonicalToolDefinition, ...] = (
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
    web_search_tool_snapshot(),
    web_fetch_tool_snapshot(),
    compare_files_tool_snapshot(),
    git_blame_tool_snapshot(),
    git_refs_tool_snapshot(),
    json_query_tool_snapshot(),
    checksum_file_tool_snapshot(),
    archive_list_tool_snapshot(),
    move_directory_tool_snapshot(),
    download_file_tool_snapshot(),
    *tool_discovery_snapshots(),
    *skill_discovery_snapshots(),
)
ORDINARY_TOOL_NAMES = tuple(definition.name for definition in ORDINARY_TOOL_CATALOG)
ORDINARY_PROMPT_TOOL_NAMES = (
    *ORDINARY_TOOL_NAMES,
    TASK_PROPOSE_START_TOOL_NAME,
    TASK_ACCEPT_ADMISSION_TOOL_NAME,
    TASK_ACCEPT_PLAN_TOOL_NAME,
    TASK_CONFIRM_COMPLETION_TOOL_NAME,
    *SKILL_AUTHORING_CONTROL_TOOL_NAMES,
    *(definition.name for definition in CHILD_CONTROL_TOOL_CATALOG),
    *(definition.name for definition in TEAM_CONTROL_TOOL_CATALOG),
)
TOOL_CATALOG: tuple[CanonicalToolDefinition, ...] = (
    *ORDINARY_TOOL_CATALOG,
    *task_control_tool_snapshots(),
    *skill_authoring_tool_snapshots(),
    *CHILD_CONTROL_TOOL_CATALOG,
    *TEAM_CONTROL_TOOL_CATALOG,
)

BUILTIN_TOOL_SOURCE_GENERATION = 8
TOOL_REGISTRY_GENERATION = 8
_BUILTIN_SOURCE = ExtensionSource(
    ExtensionSourceKind.BUILTIN,
    "coquo",
    BUILTIN_TOOL_SOURCE_GENERATION,
)
CHILD_CONTROL_TOOL_CONTRACTS = tuple(
    ExtensionToolContract(
        definition=definition,
        source=_BUILTIN_SOURCE,
        execution_kind=ToolExecutionKind.CHILD_CONTROL,
        exposure=ToolExposure.DIRECT,
        permission_actions=(),
    )
    for definition in CHILD_CONTROL_TOOL_CATALOG
)
TEAM_CONTROL_TOOL_CONTRACTS = tuple(
    ExtensionToolContract(
        definition=definition,
        source=_BUILTIN_SOURCE,
        execution_kind=ToolExecutionKind.TEAM_CONTROL,
        exposure=ToolExposure.DIRECT,
        permission_actions=(),
    )
    for definition in TEAM_CONTROL_TOOL_CATALOG
)
_WORKSPACE_READ_TOOLS = frozenset(
    {
        READ_FILE_TOOL_NAME,
        GLOB_TOOL_NAME,
        GREP_TOOL_NAME,
        LIST_DIRECTORY_TOOL_NAME,
        READ_FILE_LINES_TOOL_NAME,
        STAT_PATH_TOOL_NAME,
        LIST_TREE_TOOL_NAME,
        GREP_REGEX_TOOL_NAME,
        GIT_STATUS_TOOL_NAME,
        GIT_DIFF_TOOL_NAME,
        GIT_LOG_TOOL_NAME,
        GIT_SHOW_TOOL_NAME,
        COMPARE_FILES_TOOL_NAME,
        GIT_BLAME_TOOL_NAME,
        GIT_REFS_TOOL_NAME,
        JSON_QUERY_TOOL_NAME,
        CHECKSUM_FILE_TOOL_NAME,
        ARCHIVE_LIST_TOOL_NAME,
    }
)
_HOST_PERMISSION_ACTIONS: dict[str, tuple[PermissionAction, ...]] = {
    **{name: (PermissionAction.WORKSPACE_READ,) for name in _WORKSPACE_READ_TOOLS},
    WRITE_FILE_TOOL_NAME: (
        PermissionAction.WORKSPACE_CREATE,
        PermissionAction.WORKSPACE_OVERWRITE,
    ),
    EDIT_FILE_TOOL_NAME: (PermissionAction.WORKSPACE_OVERWRITE,),
    RUN_COMMAND_TOOL_NAME: (PermissionAction.DANGEROUS,),
    MKDIR_TOOL_NAME: (PermissionAction.WORKSPACE_CREATE,),
    MOVE_FILE_TOOL_NAME: (PermissionAction.WORKSPACE_MOVE,),
    DELETE_FILE_TOOL_NAME: (PermissionAction.WORKSPACE_DELETE,),
    DELETE_DIRECTORY_TOOL_NAME: (PermissionAction.WORKSPACE_DELETE,),
    COPY_FILE_TOOL_NAME: (PermissionAction.WORKSPACE_CREATE,),
    PATCH_FILE_TOOL_NAME: (PermissionAction.WORKSPACE_OVERWRITE,),
    WEB_SEARCH_TOOL_NAME: (PermissionAction.NETWORK_READ,),
    WEB_FETCH_TOOL_NAME: (PermissionAction.NETWORK_READ,),
    MOVE_DIRECTORY_TOOL_NAME: (PermissionAction.WORKSPACE_MOVE,),
    DOWNLOAD_FILE_TOOL_NAME: (PermissionAction.NETWORK_WRITE,),
}
_TASK_STAGE_CONTROL_NAMES = frozenset(
    {
        TASK_PROPOSE_PLAN_TOOL_NAME,
        TASK_REPORT_REFLECTION_TOOL_NAME,
        TASK_REPORT_BLOCKER_TOOL_NAME,
        TASK_PROPOSE_COMPLETION_TOOL_NAME,
    }
)
_TASK_LIFECYCLE_NAMES = frozenset(
    {
        TASK_ACCEPT_ADMISSION_TOOL_NAME,
        TASK_ACCEPT_PLAN_TOOL_NAME,
        TASK_CONFIRM_COMPLETION_TOOL_NAME,
    }
)


def _builtin_contract(definition: CanonicalToolDefinition) -> ExtensionToolContract:
    name = definition.name
    if name in _HOST_PERMISSION_ACTIONS:
        execution_kind = ToolExecutionKind.HOST_ACTION
        permission_actions = _HOST_PERMISSION_ACTIONS[name]
    elif name in _TASK_STAGE_CONTROL_NAMES:
        execution_kind = ToolExecutionKind.TASK_STAGE_CONTROL
        permission_actions = ()
    elif name == TASK_PROPOSE_START_TOOL_NAME:
        execution_kind = ToolExecutionKind.TASK_ADMISSION
        permission_actions = ()
    elif name in TOOL_DISCOVERY_TOOL_NAMES or name in {
        SKILL_SEARCH_TOOL_NAME,
        SKILL_LOAD_TOOL_NAME,
        SKILL_READ_RESOURCE_TOOL_NAME,
    }:
        execution_kind = ToolExecutionKind.TOOL_DISCOVERY
        permission_actions = ()
    elif name in _TASK_LIFECYCLE_NAMES:
        execution_kind = ToolExecutionKind.TASK_LIFECYCLE
        permission_actions = ()
    elif name == SKILL_PROPOSE_CREATE_TOOL_NAME:
        execution_kind = ToolExecutionKind.SKILL_AUTHORING
        permission_actions = ()
    elif name == SKILL_ACCEPT_CREATE_TOOL_NAME:
        execution_kind = ToolExecutionKind.SKILL_LIFECYCLE
        permission_actions = ()
    elif name in {definition.name for definition in CHILD_CONTROL_TOOL_CATALOG}:
        execution_kind = ToolExecutionKind.CHILD_CONTROL
        permission_actions = ()
    elif name in TEAM_CONTROL_TOOL_NAMES:
        execution_kind = ToolExecutionKind.TEAM_CONTROL
        permission_actions = ()
    else:
        raise RuntimeError(f"canonical tool lacks an extension contract: {name}")
    return ExtensionToolContract(
        definition=definition,
        source=_BUILTIN_SOURCE,
        execution_kind=execution_kind,
        exposure=ToolExposure.DIRECT,
        permission_actions=permission_actions,
    )


TOOL_REGISTRY_SNAPSHOT = ToolRegistrySnapshot(
    generation=TOOL_REGISTRY_GENERATION,
    contracts=tuple(_builtin_contract(definition) for definition in TOOL_CATALOG),
)


def model_tool_definitions(
    enabled_tool_names: tuple[str, ...] | None = None,
    *,
    definitions: tuple[CanonicalToolDefinition, ...] | None = None,
) -> tuple[dict[str, object], ...]:
    """Return fresh definitions for one exact canonical model-visible tool set."""
    if definitions is not None:
        if not isinstance(definitions, tuple) or not definitions:
            raise ValueError("model tool definitions must be a non-empty tuple")
        selected = definitions
        if (
            enabled_tool_names is not None
            and tuple(definition.name for definition in definitions) != enabled_tool_names
        ):
            raise ValueError("model tool definitions do not match enabled tool names")
    else:
        selected = select_tool_definitions(enabled_tool_names)
    return tuple(definition.as_mapping() for definition in selected)


def select_tool_definitions(
    enabled_tool_names: tuple[str, ...] | None,
) -> tuple[CanonicalToolDefinition, ...]:
    """Select a validated subset while preserving global canonical order."""
    return TOOL_REGISTRY_SNAPSHOT.select(enabled_tool_names).definitions


def select_tool_set(
    enabled_tool_names: tuple[str, ...] | None,
) -> ToolSetSnapshot:
    """Return the current registry's immutable epoch-zero tool set."""
    return TOOL_REGISTRY_SNAPSHOT.select(enabled_tool_names)


def tool_use_from_input(
    tool_use_id: str,
    name: str,
    tool_input: dict[str, object],
    *,
    assistant_text: str | None = None,
) -> ToolUse:
    """Validate one exact known-tool input and freeze its neutral arguments."""
    if name.startswith("mcp_"):
        if not isinstance(tool_input, dict):
            raise ValueError(f"{name} input is malformed")
        return ToolUse(
            tool_use_id=tool_use_id,
            name=name,
            arguments=ToolArguments.from_mapping(tool_input),
            assistant_text=assistant_text,
        )
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


def tool_use_from_provider_input(
    tool_use_id: str,
    name: str,
    tool_input: dict[str, object],
) -> ToolUse:
    """Freeze provider input while deferring ordinary-tool validation to the Host."""
    if name not in ORDINARY_TOOL_NAMES:
        return tool_use_from_input(tool_use_id, name, tool_input)
    return ToolUse(
        tool_use_id=tool_use_id,
        name=name,
        arguments=ToolArguments.from_mapping(tool_input),
    )


def tool_input_from_use(request: ToolUse) -> dict[str, object]:
    """Project and revalidate immutable arguments for one known tool."""
    if not isinstance(request.arguments, ToolArguments):
        raise ValueError("tool arguments are invalid")
    tool_input = request.arguments.as_mapping()
    if request.name.startswith("mcp_"):
        return tool_input
    expected = _expected_keys(request.name)
    if set(tool_input) != expected:
        raise ValueError(f"{request.name} input is malformed")
    _validate_known_input(request.name, tool_input, expected)
    return tool_input


def tool_input_for_provider_history(request: ToolUse) -> dict[str, object]:
    """Replay frozen ordinary-tool input exactly after Host-side validation."""
    if request.name in ORDINARY_TOOL_NAMES:
        if not isinstance(request.arguments, ToolArguments):
            raise ValueError("tool arguments are invalid")
        return request.arguments.as_mapping()
    return tool_input_from_use(request)


def _expected_keys(name: str) -> set[str]:
    if name == CHILD_CONTROL_TOOL_NAMES[0]:
        return {"objective"}
    if name == CHILD_CONTROL_TOOL_NAMES[1]:
        return {"child_run_id"}
    if name == CHILD_CONTROL_TOOL_NAMES[2]:
        return {"child_run_id", "timeout_seconds"}
    if name == CHILD_CONTROL_TOOL_NAMES[3]:
        return {"child_run_id", "reason"}
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
    if name == WEB_SEARCH_TOOL_NAME:
        return {"query", "max_results"}
    if name == WEB_FETCH_TOOL_NAME:
        return {"url", "format"}
    if name == COMPARE_FILES_TOOL_NAME:
        return {"left", "right"}
    if name == GIT_BLAME_TOOL_NAME:
        return {"path", "start_line", "line_count"}
    if name == GIT_REFS_TOOL_NAME:
        return set()
    if name == JSON_QUERY_TOOL_NAME:
        return {"path", "pointer"}
    if name in {CHECKSUM_FILE_TOOL_NAME, ARCHIVE_LIST_TOOL_NAME}:
        return {"path"}
    if name == MOVE_DIRECTORY_TOOL_NAME:
        return {"source", "destination"}
    if name == DOWNLOAD_FILE_TOOL_NAME:
        return {"url", "path"}
    if name == TOOL_SEARCH_TOOL_NAME:
        return {"query", "max_results"}
    if name == TOOL_PROMOTE_TOOL_NAME:
        return {"names"}
    if name == SKILL_SEARCH_TOOL_NAME:
        return {"query", "max_results"}
    if name == SKILL_LOAD_TOOL_NAME:
        return {"name", "fingerprint"}
    if name == SKILL_READ_RESOURCE_TOOL_NAME:
        return {"name", "skill_fingerprint", "path", "resource_fingerprint"}
    if name == SKILL_PROPOSE_CREATE_TOOL_NAME:
        return {"name", "description", "scope", "allowed_tools", "instructions"}
    if name == SKILL_ACCEPT_CREATE_TOOL_NAME:
        return {"candidate_id"}
    if name == TASK_PROPOSE_PLAN_TOOL_NAME:
        return {"steps"}
    if name == TASK_REPORT_REFLECTION_TOOL_NAME:
        return {"recommendation", "summary", "next_objective"}
    if name == TASK_REPORT_BLOCKER_TOOL_NAME:
        return {"category", "summary"}
    if name == TASK_PROPOSE_COMPLETION_TOOL_NAME:
        return set()
    if name == TASK_PROPOSE_START_TOOL_NAME:
        return {"acceptance_criteria", "objective", "reason"}
    if name == TASK_ACCEPT_ADMISSION_TOOL_NAME:
        return {"admission_id"}
    if name in {TASK_ACCEPT_PLAN_TOOL_NAME, TASK_CONFIRM_COMPLETION_TOOL_NAME}:
        return {"task_id"}
    raise ValueError(f"unsupported tool: {name}")


def _validate_known_input(name: str, tool_input: dict[str, object], expected: set[str]) -> None:
    if name in CHILD_CONTROL_TOOL_NAMES:
        parse_child_control(ToolUse("validation", name, ToolArguments.from_mapping(tool_input)))
        return
    if name == SKILL_PROPOSE_CREATE_TOOL_NAME:
        from coquo.core.skill_authoring import SkillCreationProposal

        SkillCreationProposal.from_request(
            ToolUse("validation", name, ToolArguments.from_mapping(tool_input)),
            "ctx-v1-" + "0" * 64,
        )
        return
    if name == SKILL_ACCEPT_CREATE_TOOL_NAME:
        from coquo.core.skill_authoring import canonical_skill_candidate_id

        canonical_skill_candidate_id(tool_input["candidate_id"])
        return
    if name == SKILL_SEARCH_TOOL_NAME:
        query = tool_input["query"]
        max_results = tool_input["max_results"]
        _validate_input_string(
            query,
            label="skill_search query",
            max_characters=256,
            max_bytes=1024,
        )
        if type(max_results) is not int or not 1 <= max_results <= MAX_SKILL_SEARCH_RESULTS:
            raise ValueError(
                f"skill_search max_results must be an integer from 1 to {MAX_SKILL_SEARCH_RESULTS}"
            )
        return
    if name == SKILL_LOAD_TOOL_NAME:
        skill_name = tool_input["name"]
        fingerprint = tool_input["fingerprint"]
        _validate_input_string(
            skill_name,
            label="skill_load name",
            allow_whitespace=False,
            max_characters=64,
            max_bytes=64,
        )
        _validate_input_string(
            fingerprint,
            label="skill_load fingerprint",
            allow_whitespace=False,
            max_characters=73,
            max_bytes=73,
        )
        if (
            not fingerprint.startswith("skill-v1-")
            or len(fingerprint) != 73
            or any(character not in "0123456789abcdef" for character in fingerprint[9:])
        ):
            raise ValueError("skill_load fingerprint is invalid")
        return
    if name == SKILL_READ_RESOURCE_TOOL_NAME:
        skill_name = tool_input["name"]
        skill_fingerprint = tool_input["skill_fingerprint"]
        path = tool_input["path"]
        resource_fingerprint = tool_input["resource_fingerprint"]
        _validate_input_string(
            skill_name,
            label="skill_read_resource name",
            allow_whitespace=False,
            max_characters=64,
            max_bytes=64,
        )
        _validate_input_string(
            skill_fingerprint,
            label="skill_read_resource Skill fingerprint",
            allow_whitespace=False,
            max_characters=73,
            max_bytes=73,
        )
        _validate_input_string(
            path,
            label="skill_read_resource path",
            allow_whitespace=False,
            max_characters=256,
            max_bytes=1024,
        )
        _validate_input_string(
            resource_fingerprint,
            label="skill_read_resource resource fingerprint",
            allow_whitespace=False,
            max_characters=76,
            max_bytes=76,
        )
        if (
            not skill_fingerprint.startswith("skill-v1-")
            or len(skill_fingerprint) != 73
            or any(character not in "0123456789abcdef" for character in skill_fingerprint[9:])
        ):
            raise ValueError("skill_read_resource Skill fingerprint is invalid")
        if (
            not resource_fingerprint.startswith("resource-v1-")
            or len(resource_fingerprint) != 76
            or any(character not in "0123456789abcdef" for character in resource_fingerprint[12:])
        ):
            raise ValueError("skill_read_resource resource fingerprint is invalid")
        return
    if name == TOOL_SEARCH_TOOL_NAME:
        query = tool_input["query"]
        max_results = tool_input["max_results"]
        _validate_input_string(
            query,
            label="tool_search query",
            max_characters=MAX_TOOL_SEARCH_QUERY_CHARACTERS,
            max_bytes=MAX_TOOL_SEARCH_QUERY_CHARACTERS * 4,
        )
        if type(max_results) is not int or not (
            MIN_TOOL_SEARCH_RESULTS <= max_results <= MAX_TOOL_SEARCH_RESULTS
        ):
            raise ValueError(
                f"tool_search max_results must be an integer from "
                f"{MIN_TOOL_SEARCH_RESULTS} to {MAX_TOOL_SEARCH_RESULTS}"
            )
        return
    if name == TOOL_PROMOTE_TOOL_NAME:
        names = tool_input["names"]
        if (
            not isinstance(names, list)
            or not 1 <= len(names) <= MAX_TOOL_PROMOTIONS
            or len(names) != len(set(names))
        ):
            raise ValueError(f"tool_promote names must contain 1 to {MAX_TOOL_PROMOTIONS} names")
        for name_value in names:
            _validate_input_string(
                name_value,
                label="tool_promote name",
                allow_whitespace=False,
                max_characters=64,
                max_bytes=64,
            )
            if not name_value.startswith("mcp_"):
                raise ValueError("tool_promote accepts only MCP candidate names")
        return
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

    if name == WEB_SEARCH_TOOL_NAME:
        _validate_input_string(
            tool_input["query"],
            label="web_search query",
            allow_whitespace=True,
            max_characters=MAX_WEB_SEARCH_QUERY_CHARACTERS,
            max_bytes=MAX_WEB_SEARCH_QUERY_BYTES,
        )
        max_results = tool_input["max_results"]
        if type(max_results) is not int or not (
            MIN_WEB_SEARCH_RESULTS <= max_results <= MAX_WEB_SEARCH_RESULTS
        ):
            raise ValueError("web_search max_results is invalid")
        return

    if name == WEB_FETCH_TOOL_NAME:
        _validate_input_string(tool_input["url"], label="web_fetch url")
        if tool_input["format"] not in {item.value for item in WebFetchFormat}:
            raise ValueError("web_fetch format is invalid")
        return

    if name == GIT_BLAME_TOOL_NAME:
        _validate_input_string(tool_input["path"], label="git_blame path")
        start_line = tool_input["start_line"]
        line_count = tool_input["line_count"]
        if type(start_line) is not int or not 1 <= start_line <= MAX_GIT_BLAME_START_LINE:
            raise ValueError("git_blame start_line is invalid")
        if type(line_count) is not int or not 1 <= line_count <= MAX_GIT_BLAME_LINE_COUNT:
            raise ValueError("git_blame line_count is invalid")
        return

    if name == GIT_REFS_TOOL_NAME:
        return

    if name == JSON_QUERY_TOOL_NAME:
        _validate_input_string(tool_input["path"], label="json_query path")
        _validate_input_string(
            tool_input["pointer"],
            label="json_query pointer",
            allow_whitespace=True,
            allow_empty=True,
        )
        return

    if name == TASK_PROPOSE_PLAN_TOOL_NAME:
        steps = tool_input["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
            raise ValueError("task_propose_plan steps are invalid")
        for index, step in enumerate(steps):
            _validate_input_string(
                step,
                label=f"task_propose_plan steps[{index}]",
                max_characters=4096,
                max_bytes=16 * 1024,
            )
        return

    if name == TASK_REPORT_REFLECTION_TOOL_NAME:
        recommendation = tool_input["recommendation"]
        if recommendation not in {
            "continue",
            "correction",
            "revise-plan",
            "needs-human",
            "fail",
        }:
            raise ValueError("task_report_reflection recommendation is invalid")
        _validate_input_string(
            tool_input["summary"],
            label="task_report_reflection summary",
            allow_whitespace=True,
            max_characters=1024,
            max_bytes=4096,
        )
        next_objective = tool_input["next_objective"]
        if next_objective is not None:
            _validate_input_string(
                next_objective,
                label="task_report_reflection next_objective",
                max_characters=4096,
                max_bytes=16 * 1024,
            )
        actionable = recommendation in {"continue", "correction", "revise-plan"}
        if actionable != (next_objective is not None):
            raise ValueError("task_report_reflection next_objective does not match recommendation")
        return

    if name == TASK_REPORT_BLOCKER_TOOL_NAME:
        if tool_input["category"] not in {
            "information",
            "permission",
            "human-evidence",
            "external-condition",
            "other",
        }:
            raise ValueError("task_report_blocker category is invalid")
        _validate_input_string(
            tool_input["summary"],
            label="task_report_blocker summary",
            allow_whitespace=True,
            max_characters=1024,
            max_bytes=4096,
        )
        return

    if name == TASK_PROPOSE_COMPLETION_TOOL_NAME:
        return

    if name == TASK_PROPOSE_START_TOOL_NAME:
        _validate_input_string(
            tool_input["objective"],
            label="task_propose_start objective",
            max_characters=4096,
            max_bytes=16 * 1024,
        )
        _validate_input_string(
            tool_input["reason"],
            label="task_propose_start reason",
            max_characters=1024,
            max_bytes=4096,
        )
        criteria = tool_input["acceptance_criteria"]
        if not isinstance(criteria, list) or not 1 <= len(criteria) <= 16:
            raise ValueError("task_propose_start acceptance_criteria are invalid")
        for index, criterion in enumerate(criteria):
            _validate_input_string(
                criterion,
                label=f"task_propose_start acceptance_criteria[{index}]",
                max_characters=1024,
                max_bytes=4096,
            )
        return

    if name == TASK_ACCEPT_ADMISSION_TOOL_NAME:
        from coquo.core.task_admission import canonical_task_admission_id

        canonical_task_admission_id(tool_input["admission_id"])
        return

    if name in {TASK_ACCEPT_PLAN_TOOL_NAME, TASK_CONFIRM_COMPLETION_TOOL_NAME}:
        from coquo.task_records import canonical_task_id

        canonical_task_id(tool_input["task_id"])
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
