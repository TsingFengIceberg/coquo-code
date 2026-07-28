"""Bounded fixed-command Git observation for one workspace repository."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
from threading import Thread
import time

GIT_OBSERVATION_TIMEOUT_SECONDS = 5.0
GIT_OBSERVATION_STDERR_BYTES = 8 * 1024
GIT_STATUS_RAW_OUTPUT_BYTES = 1024 * 1024
GIT_TERMINATE_GRACE_SECONDS = 0.5
GIT_KILL_GRACE_SECONDS = 0.5
GIT_PIPE_DRAIN_GRACE_SECONDS = 0.5
MAX_GIT_CONFIG_BYTES = 1024 * 1024
_INCLUDE_SECTION = re.compile(r"^\s*\[\s*include(?:if)?(?:\s|\])", re.IGNORECASE)
_FILTER_SECTION = re.compile(r"^\s*\[\s*filter(?:\s|\])", re.IGNORECASE)


class GitObservationError(RuntimeError):
    """One safe failure from fixed, read-only repository observation."""


@dataclass(frozen=True)
class GitCommandOutput:
    """Bounded bytes captured from one successful fixed Git command."""

    stdout: bytes
    stdout_total: int

    @property
    def truncated(self) -> bool:
        return self.stdout_total > len(self.stdout)


@dataclass
class _Capture:
    limit: int
    data: bytearray
    total: int = 0
    failed: bool = False

    def consume(self, chunk: bytes) -> None:
        self.total += len(chunk)
        remaining = self.limit - len(self.data)
        if remaining > 0:
            self.data.extend(chunk[:remaining])


class GitRepository:
    """Run only reviewed Git inspection commands at the workspace root."""

    def __init__(
        self,
        workspace: Path,
        environment: Mapping[str, str] | None = None,
        *,
        executable: str | None = None,
    ) -> None:
        self._workspace = workspace.resolve()
        self._environment = dict(os.environ if environment is None else environment)
        selected = executable or shutil.which("git", path=self._environment.get("PATH"))
        if not selected:
            raise ValueError("git executable is unavailable")
        self._executable = selected
        try:
            root = self._workspace.lstat()
        except OSError:
            raise ValueError("workspace directory could not be inspected") from None
        if stat.S_ISLNK(root.st_mode) or not stat.S_ISDIR(root.st_mode):
            raise ValueError("workspace root must be a non-symlink directory")

    def status_porcelain(self) -> bytes:
        """Return complete porcelain-v2 status bytes or fail without partial parsing."""
        self._validate_repository()
        result = self._run(
            (
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=normal",
                "--ignore-submodules=all",
            ),
            stdout_limit=GIT_STATUS_RAW_OUTPUT_BYTES,
        )
        if result.truncated:
            raise GitObservationError("git status exceeded the raw observation limit")
        return result.stdout

    def diff(self, arguments: Sequence[str], *, stdout_limit: int) -> GitCommandOutput:
        """Return one bounded patch from caller-selected reviewed diff arguments."""
        self._validate_repository()
        return self._run(arguments, stdout_limit=stdout_limit)

    def _validate_repository(self) -> None:
        try:
            root = self._workspace.lstat()
            marker = (self._workspace / ".git").lstat()
        except FileNotFoundError:
            raise GitObservationError("workspace is not a supported Git worktree root") from None
        except OSError:
            raise GitObservationError("workspace Git metadata could not be inspected") from None
        if stat.S_ISLNK(root.st_mode) or not stat.S_ISDIR(root.st_mode):
            raise GitObservationError("workspace root is not a non-symlink directory")
        if stat.S_ISLNK(marker.st_mode) or not stat.S_ISDIR(marker.st_mode):
            raise GitObservationError("workspace must use an in-root non-symlink .git directory")
        self._validate_repository_metadata()

        result = self._run(("rev-parse", "--show-toplevel"), stdout_limit=4096)
        if result.truncated:
            raise GitObservationError("workspace Git top-level path is invalid")
        try:
            top_level_text = result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise GitObservationError("workspace Git top-level path is not valid UTF-8") from None
        if not top_level_text or Path(top_level_text).resolve() != self._workspace:
            raise GitObservationError("workspace must be the Git worktree top level")

    def _run(self, arguments: Sequence[str], *, stdout_limit: int) -> GitCommandOutput:
        if stdout_limit <= 0:
            raise ValueError("Git stdout limit must be positive")
        command = (
            self._executable,
            "--no-optional-locks",
            "--git-dir=.git",
            "--work-tree=.",
            "-c",
            "color.ui=false",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "core.excludesFile=/dev/null",
            "-c",
            "diff.external=",
            "-c",
            "pager.status=false",
            "-c",
            "pager.diff=false",
            "-c",
            "submodule.recurse=false",
            *arguments,
        )
        environment = {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_EXTERNAL_DIFF": "",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
            "PATH": self._environment.get("PATH", os.defpath),
            "PWD": str(self._workspace),
        }
        stdout = _Capture(stdout_limit, bytearray())
        stderr = _Capture(GIT_OBSERVATION_STDERR_BYTES, bytearray())
        try:
            process = subprocess.Popen(
                command,
                cwd=self._workspace,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        except (OSError, ValueError):
            raise GitObservationError("git observation could not start") from None

        assert process.stdout is not None and process.stderr is not None
        readers = (
            Thread(target=_drain_pipe, args=(process.stdout, stdout), daemon=True),
            Thread(target=_drain_pipe, args=(process.stderr, stderr), daemon=True),
        )
        for reader in readers:
            reader.start()

        try:
            process.wait(timeout=GIT_OBSERVATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            cleaned = _terminate_process_group(process)
            _finish_readers(process, readers)
            if not cleaned:
                raise GitObservationError("git observation timed out and cleanup is incomplete")
            raise GitObservationError("git observation timed out") from None
        except KeyboardInterrupt:
            _terminate_process_group(process)
            _finish_readers(process, readers)
            raise

        readers_complete = _finish_readers(process, readers)
        if not readers_complete or stdout.failed or stderr.failed:
            raise GitObservationError("git observation output could not be collected")
        if process.returncode != 0:
            raise GitObservationError("workspace Git observation failed")
        return GitCommandOutput(bytes(stdout.data), stdout.total)

    def _validate_repository_metadata(self) -> None:
        git_dir = self._workspace / ".git"
        for relative, expected_directory in (("objects", True), ("config", False)):
            path = git_dir / relative
            try:
                info = path.lstat()
            except OSError:
                raise GitObservationError("workspace Git metadata is incomplete") from None
            expected = (
                stat.S_ISDIR(info.st_mode) if expected_directory else stat.S_ISREG(info.st_mode)
            )
            if stat.S_ISLNK(info.st_mode) or not expected:
                raise GitObservationError("workspace Git metadata contains an unsafe entry")

        for relative in ("commondir", "objects/info/alternates"):
            try:
                (git_dir / relative).lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise GitObservationError("workspace Git metadata could not be inspected") from None
            raise GitObservationError("external Git metadata references are not supported")

        config_path = git_dir / "config"
        try:
            with config_path.open("rb") as config_file:
                config_data = config_file.read(MAX_GIT_CONFIG_BYTES + 1)
        except OSError:
            raise GitObservationError("workspace Git config could not be read") from None
        if len(config_data) > MAX_GIT_CONFIG_BYTES:
            raise GitObservationError("workspace Git config exceeds the supported size")
        try:
            config_text = config_data.decode("utf-8")
        except UnicodeDecodeError:
            raise GitObservationError("workspace Git config is not valid UTF-8") from None
        lines = config_text.splitlines()
        if "\x00" in config_text or any(_INCLUDE_SECTION.match(line) for line in lines):
            raise GitObservationError("external Git config includes are not supported")
        if any(_FILTER_SECTION.match(line) for line in lines):
            raise GitObservationError("external Git filters are not supported")


def _drain_pipe(pipe, capture: _Capture) -> None:
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                return
            capture.consume(chunk)
    except (OSError, ValueError):
        capture.failed = True


def _finish_readers(process, readers: tuple[Thread, Thread]) -> bool:
    deadline = time.monotonic() + GIT_PIPE_DRAIN_GRACE_SECONDS
    for reader in readers:
        reader.join(max(0.0, deadline - time.monotonic()))
    if all(not reader.is_alive() for reader in readers):
        return True
    _terminate_process_group(process)
    for pipe in (process.stdout, process.stderr):
        try:
            pipe.close()
        except OSError:
            pass
    deadline = time.monotonic() + GIT_PIPE_DRAIN_GRACE_SECONDS
    for reader in readers:
        reader.join(max(0.0, deadline - time.monotonic()))
    return all(not reader.is_alive() for reader in readers)


def _terminate_process_group(process: subprocess.Popen[bytes]) -> bool:
    if not _signal_process_group(process, signal.SIGTERM):
        return False
    if _wait_for_group_exit(process, GIT_TERMINATE_GRACE_SECONDS):
        return True
    if not _signal_process_group(process, signal.SIGKILL):
        return False
    return _wait_for_group_exit(process, GIT_KILL_GRACE_SECONDS)


def _signal_process_group(process: subprocess.Popen[bytes], requested: signal.Signals) -> bool:
    try:
        os.killpg(process.pid, requested)
        return True
    except ProcessLookupError:
        return True
    except OSError:
        if process.poll() is not None:
            return False
        try:
            process.send_signal(requested)
        except OSError:
            return False
        return True


def _wait_for_group_exit(process: subprocess.Popen[bytes], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return process.poll() is not None
        except OSError:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.01, remaining))
