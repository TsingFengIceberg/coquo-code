"""Parent-only contract for applying one sealed Team worktree patch."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition

TEAM_WORKTREE_INTEGRATE_TOOL_NAME = "team_worktree_integrate"
TEAM_WORKTREE_INTEGRATE_MAX_RESULT_BYTES = 16 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class TeamWorktreeIntegrateRequest:
    team_id: str
    assignment_id: str
    expected_patch_sha256: str


def team_worktree_integrate_tool_snapshot() -> CanonicalToolDefinition:
    uuid = {"type": "string", "minLength": 36, "maxLength": 36}
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TEAM_WORKTREE_INTEGRATE_TOOL_NAME,
            "description": (
                "Apply one exact sealed Team worktree patch to the clean parent workspace; "
                "leave changes uncommitted for explicit review."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "team_id": uuid,
                    "assignment_id": uuid,
                    "expected_patch_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                },
                "required": ["team_id", "assignment_id", "expected_patch_sha256"],
                "additionalProperties": False,
            },
        }
    )


def parse_team_worktree_integrate(request: ToolUse) -> TeamWorktreeIntegrateRequest:
    if not isinstance(request, ToolUse) or request.name != TEAM_WORKTREE_INTEGRATE_TOOL_NAME:
        raise ValueError("team_worktree_integrate request is invalid")
    if not isinstance(request.arguments, ToolArguments):
        raise ValueError("team_worktree_integrate arguments are invalid")
    values = request.arguments.as_mapping()
    expected = {"team_id", "assignment_id", "expected_patch_sha256"}
    if set(values) != expected:
        raise ValueError("team_worktree_integrate input is malformed")
    try:
        team_id = _canonical_id(values["team_id"], "team ID")
        assignment_id = _canonical_id(values["assignment_id"], "assignment ID")
    except Exception:
        raise ValueError("team_worktree_integrate IDs are invalid") from None
    patch_sha256 = values["expected_patch_sha256"]
    if not isinstance(patch_sha256, str) or _SHA256.fullmatch(patch_sha256) is None:
        raise ValueError("team_worktree_integrate patch digest is invalid")
    return TeamWorktreeIntegrateRequest(team_id, assignment_id, patch_sha256)


def _canonical_id(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} is invalid") from None
    if parsed.version != 4 or value != str(parsed):
        raise ValueError(f"{label} is invalid")
    return value
