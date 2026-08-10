"""Bounded local branch, tag, and current-HEAD observation."""

from __future__ import annotations

import json
from pathlib import Path

from coquo.core.contracts import ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.tools.git_repository import (
    GIT_OBJECT_ID_PATTERN,
    GitObservationError,
    GitRepository,
)

GIT_REFS_TOOL_NAME = "git_refs"
MAX_GIT_REFS = 200
MAX_GIT_REFS_RAW_OUTPUT_BYTES = 1024 * 1024
MAX_GIT_REFS_OUTPUT_BYTES = 32 * 1024
GIT_REFS_TRUNCATION_SENTINEL = '{"truncated":true}\n'


def git_refs_model_definition() -> dict[str, object]:
    return {
        "name": GIT_REFS_TOOL_NAME,
        "description": (
            "List bounded local branches, tags, and current HEAD for the workspace Git "
            "repository. This read-only tool accepts no ref expression or arbitrary Git arguments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }


def git_refs_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(git_refs_model_definition())


class GitRefsTool:
    def __init__(self, workspace: Path, repository: GitRepository | None = None) -> None:
        self._repository = repository or GitRepository(workspace)

    def execute(self, request: ToolUse) -> ToolResult:
        try:
            if request.name != GIT_REFS_TOOL_NAME or request.arguments.as_mapping():
                raise ValueError
            head_id = _single_ascii(
                self._repository.history(("rev-parse", "--verify", "HEAD"), stdout_limit=256),
                "HEAD object ID",
            )
            if GIT_OBJECT_ID_PATTERN.fullmatch(head_id) is None:
                raise GitObservationError("git refs returned an invalid HEAD object ID")
            branch = _single_utf8(
                self._repository.history(("rev-parse", "--abbrev-ref", "HEAD"), stdout_limit=4096),
                "HEAD name",
            )
            branch = None if branch == "HEAD" else branch
            raw = self._repository.history(
                (
                    "for-each-ref",
                    "--sort=refname",
                    "--format=%(refname)%00%(objectname)%00%(objecttype)%00%(*objectname)",
                    "refs/heads",
                    "refs/tags",
                ),
                stdout_limit=MAX_GIT_REFS_RAW_OUTPUT_BYTES,
            )
            if raw.truncated:
                raise GitObservationError("git refs exceeded the raw observation limit")
            entries = _parse_refs(raw.stdout, branch)
            content, truncated = _format_refs(head_id, branch, entries)
        except (AttributeError, ValueError):
            return ToolResult(request.tool_use_id, "git_refs input is malformed", is_error=True)
        except GitObservationError as error:
            return ToolResult(request.tool_use_id, str(error), is_error=True)
        return ToolResult(request.tool_use_id, content, truncated=truncated)


def _single_ascii(result, label: str) -> str:  # noqa: ANN001
    if result.truncated:
        raise GitObservationError(f"git refs returned an oversized {label}")
    try:
        value = result.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        raise GitObservationError(f"git refs returned a non-ASCII {label}") from None
    if not value:
        raise GitObservationError(f"git refs returned an empty {label}")
    return value


def _single_utf8(result, label: str) -> str:  # noqa: ANN001
    if result.truncated:
        raise GitObservationError(f"git refs returned an oversized {label}")
    try:
        value = result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise GitObservationError(f"git refs returned a non-UTF-8 {label}") from None
    if not value or "\x00" in value or "\n" in value:
        raise GitObservationError(f"git refs returned an invalid {label}")
    return value


def _parse_refs(data: bytes, current_branch: str | None) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for line in data.splitlines():
        fields = line.split(b"\0")
        if len(fields) != 4:
            raise GitObservationError("git refs returned malformed data")
        try:
            refname, object_id, object_type, peeled = (field.decode("utf-8") for field in fields)
        except UnicodeDecodeError:
            raise GitObservationError("git refs returned non-UTF-8 data") from None
        if refname.startswith("refs/heads/"):
            kind = "branch"
            name = refname.removeprefix("refs/heads/")
        elif refname.startswith("refs/tags/"):
            kind = "tag"
            name = refname.removeprefix("refs/tags/")
        else:
            raise GitObservationError("git refs returned an unsupported ref")
        if not name or GIT_OBJECT_ID_PATTERN.fullmatch(object_id) is None:
            raise GitObservationError("git refs returned an invalid ref")
        if peeled and GIT_OBJECT_ID_PATTERN.fullmatch(peeled) is None:
            raise GitObservationError("git refs returned an invalid peeled object ID")
        entry: dict[str, object] = {
            "current": kind == "branch" and name == current_branch,
            "kind": kind,
            "name": name,
            "object_id": object_id,
            "object_type": object_type,
        }
        if peeled:
            entry["peeled_object_id"] = peeled
        entries.append(entry)
        if len(entries) > MAX_GIT_REFS:
            raise GitObservationError(f"git refs repository exceeds {MAX_GIT_REFS} refs")
    return entries


def _format_refs(
    head_id: str,
    branch: str | None,
    entries: list[dict[str, object]],
) -> tuple[str, bool]:
    values: list[dict[str, object]] = [
        {"branch": branch, "kind": "head", "object_id": head_id},
        *entries,
    ]
    output: list[str] = []
    size = 0
    sentinel_size = len(GIT_REFS_TRUNCATION_SENTINEL.encode("ascii"))
    for value in values:
        record = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        record_size = len(record.encode("utf-8"))
        if record_size + sentinel_size > MAX_GIT_REFS_OUTPUT_BYTES:
            raise GitObservationError("one git ref exceeds the output limit")
        if size + record_size + sentinel_size > MAX_GIT_REFS_OUTPUT_BYTES:
            output.append(GIT_REFS_TRUNCATION_SENTINEL)
            return "".join(output), True
        output.append(record)
        size += record_size
    return "".join(output), False
