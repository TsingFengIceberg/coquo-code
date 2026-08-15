"""Fixed, fail-closed Git worktree operations for B6.

This module does not accept arbitrary Git arguments. It validates the authority
repository and only creates/observes generated linked worktrees.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Mapping

from coquo.session_records import workspace_fingerprint

GIT_WORKTREE_TIMEOUT_SECONDS = 10.0
GIT_WORKTREE_OUTPUT_BYTES = 64 * 1024
_BRANCH = re.compile(r"coquo/team/[0-9a-f-]{36}/[0-9a-f-]{36}\Z")
_REF = re.compile(r"refs/heads/[A-Za-z0-9._/-]{1,200}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class GitWorktreeError(RuntimeError):
    """One bounded failure from Git worktree identity or execution."""


@dataclass(frozen=True)
class AuthorityRepository:
    root: Path
    git_dir: Path
    workspace_fingerprint: str
    target_ref: str
    head: str


@dataclass(frozen=True)
class LinkedWorktreeBinding:
    authority: AuthorityRepository
    worktree_root: Path
    worktree_id: str
    branch: str
    base_commit: str
    relative_path: str


@dataclass(frozen=True)
class GitWorktreeResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def inspect_authority_repository(
    workspace: Path, *, environment: Mapping[str, str] | None = None
) -> AuthorityRepository:
    root = Path(workspace)
    if root.is_symlink() or not root.is_dir():
        raise GitWorktreeError("authority workspace must be a real directory")
    root = root.resolve(strict=True)
    marker = root / ".git"
    try:
        marker_info = marker.lstat()
    except OSError:
        raise GitWorktreeError("authority workspace is not a Git top level") from None
    if stat.S_ISLNK(marker_info.st_mode) or not stat.S_ISDIR(marker_info.st_mode):
        raise GitWorktreeError("authority workspace must use an internal .git directory")
    for relative, directory in (("objects", True), ("refs", True), ("config", False)):
        try:
            info = (marker / relative).lstat()
        except OSError:
            raise GitWorktreeError("authority Git metadata is incomplete") from None
        if stat.S_ISLNK(info.st_mode) or (stat.S_ISDIR(info.st_mode) is not directory):
            raise GitWorktreeError("authority Git metadata contains an unsafe entry")
    for relative in ("commondir", "objects/info/alternates"):
        if (marker / relative).exists() or (marker / relative).is_symlink():
            raise GitWorktreeError("authority Git metadata references external state")
    result = _run_git(root, ("rev-parse", "--show-toplevel"), environment=environment)
    if result.returncode != 0 or Path(_line(result.stdout)).resolve() != root:
        raise GitWorktreeError("authority workspace is not the Git top level")
    branch = _run_git(
        root,
        ("symbolic-ref", "--quiet", "--short", "HEAD"),
        environment=environment,
        accepted=(0, 1),
    )
    if branch.returncode != 0:
        raise GitWorktreeError("authority repository must have an attached branch")
    target_ref = "refs/heads/" + _line(branch.stdout)
    if _REF.fullmatch(target_ref) is None:
        raise GitWorktreeError("authority target branch is invalid")
    head = _line(_run_git(root, ("rev-parse", "--verify", "HEAD"), environment=environment).stdout)
    if _SHA.fullmatch(head) is None:
        raise GitWorktreeError("authority HEAD is not a supported commit")
    return AuthorityRepository(root, marker, workspace_fingerprint(root), target_ref, head)


def validate_generated_identity(branch: str, relative_path: str) -> None:
    if _BRANCH.fullmatch(branch) is None:
        raise GitWorktreeError("generated worktree branch is invalid")
    parts = relative_path.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts) or not relative_path.isascii():
        raise GitWorktreeError("generated worktree path is invalid")


def inspect_linked_worktree(binding: LinkedWorktreeBinding) -> LinkedWorktreeBinding:
    validate_generated_identity(binding.branch, binding.relative_path)
    root = binding.worktree_root.resolve(strict=True)
    if root != binding.authority.root / Path(binding.relative_path):
        raise GitWorktreeError("worktree path does not match generated identity")
    marker = root / ".git"
    try:
        info = marker.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise GitWorktreeError("linked worktree .git pointer is unsafe")
        content = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise GitWorktreeError("linked worktree .git pointer is unreadable") from None
    if not content.startswith("gitdir:"):
        raise GitWorktreeError("worktree .git pointer is malformed")
    gitdir = Path(content.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (root / gitdir).resolve()
    else:
        gitdir = gitdir.resolve()
    expected_parent = (binding.authority.git_dir / "worktrees").resolve()
    if expected_parent not in gitdir.parents or gitdir == expected_parent:
        raise GitWorktreeError("linked worktree admin directory is outside authority Git metadata")
    commondir = gitdir / "commondir"
    if commondir.exists() or commondir.is_symlink():
        try:
            common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
        except (OSError, UnicodeDecodeError):
            raise GitWorktreeError("linked worktree commondir is invalid") from None
        if common != binding.authority.git_dir.resolve():
            raise GitWorktreeError("linked worktree commondir is not the authority repository")
    head_file = gitdir / "HEAD"
    try:
        head = head_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise GitWorktreeError("linked worktree HEAD is unavailable") from None
    if head not in {f"ref: refs/heads/{binding.branch}", binding.branch}:
        raise GitWorktreeError("linked worktree branch identity does not match")
    return LinkedWorktreeBinding(
        binding.authority,
        root,
        binding.worktree_id,
        binding.branch,
        binding.base_commit,
        binding.relative_path,
    )


def add_linked_worktree(
    authority: AuthorityRepository, binding: LinkedWorktreeBinding
) -> GitWorktreeResult:
    validate_generated_identity(binding.branch, binding.relative_path)
    if binding.authority != authority or not _SHA.fullmatch(binding.base_commit):
        raise GitWorktreeError("worktree binding authority or base is invalid")
    target = authority.root / Path(binding.relative_path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return _run_git(
        authority.root,
        ("worktree", "add", "-b", binding.branch, str(target), binding.base_commit),
        accepted=(0,),
    )


def authority_status(authority: AuthorityRepository) -> bytes:
    return _run_git(
        authority.root,
        ("status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=all"),
    ).stdout


def worktree_status(binding: LinkedWorktreeBinding) -> bytes:
    checked = inspect_linked_worktree(binding)
    return _run_git(
        checked.worktree_root,
        ("status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=all"),
    ).stdout


def worktree_diff(binding: LinkedWorktreeBinding, *, base: str | None = None) -> bytes:
    checked = inspect_linked_worktree(binding)
    selected = base or checked.base_commit
    if _SHA.fullmatch(selected) is None:
        raise GitWorktreeError("diff base is invalid")
    return _run_git(
        checked.worktree_root, ("diff", "--binary", "--no-ext-diff", selected, "--")
    ).stdout


def worktree_untracked_paths(binding: LinkedWorktreeBinding) -> tuple[str, ...]:
    checked = inspect_linked_worktree(binding)
    raw = _run_git(
        checked.worktree_root,
        ("status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignore-submodules=all"),
    ).stdout
    paths: list[str] = []
    for entry in raw.split(b"\0"):
        if not entry or not entry.startswith(b"?? "):
            continue
        try:
            path = entry[3:].decode("utf-8")
        except UnicodeDecodeError:
            raise GitWorktreeError("Git status path is not valid UTF-8") from None
        if not path or path.startswith("/") or ".." in Path(path).parts:
            raise GitWorktreeError("Git status path is unsafe")
        paths.append(path)
    return tuple(sorted(set(paths)))


def untracked_diff(binding: LinkedWorktreeBinding, relative_path: str) -> bytes:
    checked = inspect_linked_worktree(binding)
    if not relative_path or relative_path.startswith("/") or ".." in Path(relative_path).parts:
        raise GitWorktreeError("untracked path is unsafe")
    return _run_git(
        checked.worktree_root,
        ("diff", "--no-index", "--no-ext-diff", "--binary", "/dev/null", "--", relative_path),
        accepted=(0, 1),
    ).stdout


def _run_git(
    root: Path,
    arguments: tuple[str, ...],
    *,
    environment: Mapping[str, str] | None = None,
    accepted: tuple[int, ...] = (0,),
) -> GitWorktreeResult:
    executable = shutil.which("git", path=(environment or os.environ).get("PATH"))
    if not executable:
        raise GitWorktreeError("git executable is unavailable")
    env = {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": (environment or os.environ).get("PATH", os.defpath),
        "PWD": str(root),
    }
    command = (
        executable,
        "--no-pager",
        "-c",
        "color.ui=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "diff.external=",
        *arguments,
    )
    try:
        process = subprocess.run(
            command,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_WORKTREE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise GitWorktreeError("Git worktree operation timed out") from None
    stdout = process.stdout[:GIT_WORKTREE_OUTPUT_BYTES]
    stderr = process.stderr[:GIT_WORKTREE_OUTPUT_BYTES]
    if (
        len(process.stdout) > GIT_WORKTREE_OUTPUT_BYTES
        or len(process.stderr) > GIT_WORKTREE_OUTPUT_BYTES
    ):
        raise GitWorktreeError("Git worktree output exceeded its bound")
    if process.returncode not in accepted:
        raise GitWorktreeError(
            f"Git worktree operation failed: {stderr.decode('utf-8', 'replace')[:1024]}"
        )
    return GitWorktreeResult(process.returncode, stdout, stderr)


def _line(payload: bytes) -> str:
    try:
        value = payload.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise GitWorktreeError("Git output is not valid UTF-8") from None
    if not value or "\x00" in value or "\n" in value:
        raise GitWorktreeError("Git output is not one bounded line")
    return value
