"""Explicit authority versus tool execution-root identity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from coquo.session_records import canonical_session_id, workspace_fingerprint

_FINGERPRINT = re.compile(r"v1-[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ExecutionScope:
    """Immutable root identity used by tools and Action Audit."""

    authority_workspace: Path
    execution_root: Path
    kind: str = "authority-workspace"
    execution_root_fingerprint: str | None = None
    worktree_id: str | None = None

    def __post_init__(self) -> None:
        authority = Path(self.authority_workspace).resolve(strict=True)
        execution = Path(self.execution_root).resolve(strict=True)
        if not authority.is_dir() or not execution.is_dir():
            raise ValueError("execution scope roots must be directories")
        if self.kind not in {"authority-workspace", "team-worktree"}:
            raise ValueError("execution scope kind is invalid")
        expected = workspace_fingerprint(execution)
        if self.execution_root_fingerprint not in {None, expected}:
            raise ValueError("execution root fingerprint does not match root")
        if self.kind == "authority-workspace":
            if execution != authority or self.worktree_id is not None:
                raise ValueError("authority execution scope must use the authority root")
        else:
            if execution == authority or self.worktree_id is None:
                raise ValueError("Team worktree scope requires a distinct root and ID")
            canonical_session_id(self.worktree_id)
        object.__setattr__(self, "authority_workspace", authority)
        object.__setattr__(self, "execution_root", execution)
        object.__setattr__(self, "execution_root_fingerprint", expected)

    @classmethod
    def authority(cls, workspace: Path) -> ExecutionScope:
        resolved = Path(workspace).resolve(strict=True)
        return cls(resolved, resolved)

    @classmethod
    def team_worktree(cls, authority: Path, root: Path, worktree_id: str) -> ExecutionScope:
        return cls(authority, root, "team-worktree", worktree_fingerprint(root), worktree_id)


def worktree_fingerprint(root: Path) -> str:
    return workspace_fingerprint(Path(root).resolve(strict=True))
