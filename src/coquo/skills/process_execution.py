"""Explicit, fail-closed process runner for executable Skills.

Only a Host-supplied launcher may execute code.  argv is passed directly to
``Popen`` with no shell, bounded output is captured, and a missing launcher is
an error rather than an implicit unsandboxed fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Mapping


class SkillProcessExecutionError(RuntimeError):
    """Raised when the isolated Skill process cannot be started safely."""


@dataclass(frozen=True)
class SkillProcessPolicy:
    launcher: Path
    timeout_seconds: float = 30.0
    max_output_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        launcher = Path(self.launcher)
        if launcher.is_symlink() or not launcher.is_file() or not os.access(launcher, os.X_OK):
            raise ValueError("skill sandbox launcher is unavailable")
        if isinstance(self.timeout_seconds, bool) or not 0 < self.timeout_seconds <= 300:
            raise ValueError("skill process timeout is invalid")
        if type(self.max_output_bytes) is not int or not 1 <= self.max_output_bytes <= 1024 * 1024:
            raise ValueError("skill process output limit is invalid")


@dataclass(frozen=True)
class SkillProcessResult:
    outcome: str
    returncode: int | None
    stdout: str
    stderr: str
    truncated: bool = False


class SkillProcessRunner:
    def __init__(self, policy: SkillProcessPolicy) -> None:
        self.policy = policy

    def run(
        self,
        argv: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> SkillProcessResult:
        if (
            not isinstance(argv, (tuple, list))
            or not argv
            or any(not isinstance(item, str) or not item or "\x00" in item for item in argv)
        ):
            raise SkillProcessExecutionError("Skill argv is invalid")
        root = Path(cwd).resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise SkillProcessExecutionError("Skill cwd is invalid")
        command = [str(self.policy.launcher), *argv]
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=None if env is None else dict(env),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=float(self.policy.timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return SkillProcessResult(
                "timeout",
                None,
                _bounded(error.stdout, self.policy.max_output_bytes),
                _bounded(error.stderr, self.policy.max_output_bytes),
                True,
            )
        except (OSError, ValueError) as error:
            raise SkillProcessExecutionError("Skill sandbox process failed to start") from error
        stdout = _bounded(completed.stdout, self.policy.max_output_bytes)
        stderr = _bounded(completed.stderr, self.policy.max_output_bytes)
        truncated = (
            len(_bytes(completed.stdout)) > self.policy.max_output_bytes
            or len(_bytes(completed.stderr)) > self.policy.max_output_bytes
        )
        return SkillProcessResult(
            "completed" if completed.returncode == 0 else "failed",
            completed.returncode,
            stdout,
            stderr,
            truncated,
        )


def _bytes(value: object) -> bytes:
    if value is None:
        return b""
    return value if isinstance(value, bytes) else str(value).encode("utf-8", errors="replace")


def _bounded(value: object, limit: int) -> str:
    return _bytes(value)[:limit].decode("utf-8", errors="replace")


__all__ = [
    "SkillProcessExecutionError",
    "SkillProcessPolicy",
    "SkillProcessResult",
    "SkillProcessRunner",
]
