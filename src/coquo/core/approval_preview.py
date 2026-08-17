"""Bounded, exact-action-bound previews for informed human approval."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from difflib import unified_diff
from enum import StrEnum
import re

APPROVAL_PREVIEW_VERSION = 6
MAX_APPROVAL_DIFF_LINES = 160
MAX_APPROVAL_DIFF_BYTES = 24 * 1024
MAX_APPROVAL_DIFF_LINE_BYTES = 4096

_ACTION_DIGEST = re.compile(r"act-v[12]-[0-9a-f]{64}")


class ApprovalPreviewKind(StrEnum):
    """Closed presentation classes derived from already-prepared Host actions."""

    FILE_CHANGE = "file-change"
    FILE_COPY = "file-copy"
    FILE_MOVE = "file-move"
    FILE_DELETE = "file-delete"
    DIRECTORY_CREATE = "directory-create"
    DIRECTORY_DELETE = "directory-delete"
    COMMAND = "command"
    WEB_SEARCH = "web-search"
    WEB_FETCH = "web-fetch"
    DIRECTORY_MOVE = "directory-move"
    FILE_DOWNLOAD = "file-download"
    MCP_TOOL = "mcp-tool"
    HOOK_HANDLER = "hook-handler"
    TEAM_WORKTREE_INTEGRATION = "team-worktree-integration"


@dataclass(frozen=True)
class ApprovalPreview:
    """One non-persistent preview bound to an exact ActionIdentity digest."""

    action_digest: str
    kind: ApprovalPreviewKind
    byte_count: int | None = None
    body: str | None = None
    backend: str | None = None
    transport: str | None = None
    truncated: bool = False
    version: int = APPROVAL_PREVIEW_VERSION

    def __post_init__(self) -> None:
        if self.version != APPROVAL_PREVIEW_VERSION:
            raise ValueError("approval preview version is invalid")
        if (
            not isinstance(self.action_digest, str)
            or _ACTION_DIGEST.fullmatch(self.action_digest) is None
        ):
            raise ValueError("approval preview action digest is invalid")
        if type(self.kind) is not ApprovalPreviewKind:
            raise ValueError("approval preview kind is invalid")
        if self.byte_count is not None and (
            type(self.byte_count) is not int or self.byte_count < 0
        ):
            raise ValueError("approval preview byte count is invalid")
        if type(self.truncated) is not bool:
            raise ValueError("approval preview truncated flag is invalid")
        if self.kind == ApprovalPreviewKind.FILE_CHANGE:
            if not isinstance(self.body, str) or not self.body or self.byte_count is None:
                raise ValueError("file-change approval preview is incomplete")
            if len(self.body.encode("utf-8")) > MAX_APPROVAL_DIFF_BYTES:
                raise ValueError("approval preview body exceeds its byte bound")
            if self.body.count("\n") > MAX_APPROVAL_DIFF_LINES:
                raise ValueError("approval preview body exceeds its line bound")
        else:
            if self.body is not None or self.truncated:
                raise ValueError("metadata approval preview cannot carry a diff body")
            sized_kinds = {
                ApprovalPreviewKind.FILE_COPY,
                ApprovalPreviewKind.FILE_MOVE,
                ApprovalPreviewKind.FILE_DELETE,
            }
            if (self.kind in sized_kinds) != (self.byte_count is not None):
                raise ValueError("approval preview byte count does not match its kind")
        if self.kind == ApprovalPreviewKind.WEB_SEARCH:
            if self.backend not in {"brave", "tavily"}:
                raise ValueError("web-search approval preview backend is invalid")
        elif self.backend is not None:
            raise ValueError("approval preview backend does not match its kind")
        if self.kind == ApprovalPreviewKind.MCP_TOOL:
            if self.transport not in {"stdio", "streamable-http"}:
                raise ValueError("MCP approval preview transport is invalid")
        elif self.transport is not None:
            raise ValueError("approval preview transport does not match its kind")


def build_file_change_preview(
    *,
    action_digest: str,
    path: str,
    before: bytes | None,
    after: bytes,
) -> ApprovalPreview:
    """Build a bounded unified diff from the exact prepared source and candidate."""
    if not isinstance(path, str) or not path:
        raise ValueError("approval preview path is invalid")
    if before is not None and not isinstance(before, bytes):
        raise ValueError("approval preview source is invalid")
    if not isinstance(after, bytes):
        raise ValueError("approval preview candidate is invalid")
    old_text = "" if before is None else before.decode("utf-8", errors="strict")
    new_text = after.decode("utf-8", errors="strict")

    from_file = "/dev/null" if before is None else f"a/{path}"
    to_file = f"b/{path}"
    if old_text == new_text:
        note = (
            "\\ Empty file will be created"
            if before is None
            else "\\ Content is unchanged; approval still authorizes the prepared overwrite"
        )
        body = f"--- {from_file}\n+++ {to_file}\n@@ -0,0 +0,0 @@\n{note}\n"
        truncated = False
    else:
        fragments = unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=from_file,
            tofile=to_file,
            lineterm="\n",
        )
        body, truncated = _bounded_diff(fragments)
        if not body:
            raise ValueError("approval preview diff is empty")
    return ApprovalPreview(
        action_digest=action_digest,
        kind=ApprovalPreviewKind.FILE_CHANGE,
        byte_count=len(after),
        body=body,
        truncated=truncated,
    )


def build_metadata_preview(
    *,
    action_digest: str,
    kind: ApprovalPreviewKind,
    byte_count: int | None = None,
    backend: str | None = None,
    transport: str | None = None,
) -> ApprovalPreview:
    """Build a content-free preview for an already-prepared non-edit action."""
    if kind == ApprovalPreviewKind.FILE_CHANGE:
        raise ValueError("file changes require a diff preview")
    return ApprovalPreview(
        action_digest=action_digest,
        kind=kind,
        byte_count=byte_count,
        backend=backend,
        transport=transport,
    )


def _bounded_diff(fragments: Iterable[str]) -> tuple[str, bool]:
    lines: list[str] = []
    total_bytes = 0
    truncated = False
    for fragment in fragments:
        if not isinstance(fragment, str):
            raise ValueError("approval preview diff fragment is invalid")
        terminated = fragment.endswith("\n")
        visible = fragment[:-1] if terminated else fragment
        visible, line_truncated = _truncate_line(visible)
        candidates = [f"{visible}\n"]
        if not terminated and fragment.startswith((" ", "+", "-")):
            candidates.append("\\ No newline at end of file\n")
        limit_reached = False
        for candidate in candidates:
            encoded_length = len(candidate.encode("utf-8"))
            if (
                len(lines) >= MAX_APPROVAL_DIFF_LINES
                or total_bytes + encoded_length > MAX_APPROVAL_DIFF_BYTES
            ):
                truncated = True
                limit_reached = True
                break
            lines.append(candidate)
            total_bytes += encoded_length
        if limit_reached:
            break
        truncated = truncated or line_truncated
    return "".join(lines), truncated


def _truncate_line(line: str) -> tuple[str, bool]:
    encoded = line.encode("utf-8")
    if len(encoded) <= MAX_APPROVAL_DIFF_LINE_BYTES:
        return line, False
    suffix = "... [line truncated]"
    budget = MAX_APPROVAL_DIFF_LINE_BYTES - len(suffix.encode("ascii"))
    prefix = encoded[:budget].decode("utf-8", errors="ignore")
    return f"{prefix}{suffix}", True
