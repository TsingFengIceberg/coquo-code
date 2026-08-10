"""Bounded argv-based command preparation and execution for one workspace."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import json
import os
from pathlib import Path, PureWindowsPath
import signal
import stat
import subprocess
from threading import Thread
import time

from coquo.core.actions import ActionPrecondition
from coquo.core.cancellation import TurnCancellation
from coquo.core.contracts import ToolArguments, ToolResult, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.permissions import PermissionAction
from coquo.tools.command_sandbox import (
    CommandSandbox,
    CommandSandboxDependencies,
    CommandSandboxUnavailable,
    LinuxBubblewrapCommandSandbox,
    SANDBOX_STATUS_MAX_BYTES,
    sandbox_activation_succeeded,
)

RUN_COMMAND_TOOL_NAME = "run_command"
MAX_COMMAND_ARGUMENTS = 64
MAX_COMMAND_ARGUMENT_CHARACTERS = 1024
MAX_COMMAND_ARGUMENT_BYTES = 1024
MAX_COMMAND_ARGV_BYTES = 8 * 1024
MAX_COMMAND_CWD_CHARACTERS = 4096
MAX_COMMAND_CWD_BYTES = 4096
MAX_COMMAND_CWD_COMPONENTS = 64
MIN_COMMAND_TIMEOUT_SECONDS = 1
MAX_COMMAND_TIMEOUT_SECONDS = 300
MAX_COMMAND_STDOUT_BYTES = 32 * 1024
MAX_COMMAND_STDERR_BYTES = 32 * 1024
COMMAND_TERMINATE_GRACE_SECONDS = 1.0
COMMAND_KILL_GRACE_SECONDS = 1.0
COMMAND_PIPE_DRAIN_GRACE_SECONDS = 1.0
COMMAND_SANDBOX_ACTIVATION_GRACE_SECONDS = 1.0
COMMAND_ENVIRONMENT_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_COLOR",
    "PATH",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "UV_CACHE_DIR",
    "VIRTUAL_ENV",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
)


@dataclass(frozen=True)
class PreparedRunCommand:
    """One exact command request prepared without starting a process."""

    request: ToolUse
    argv: tuple[str, ...]
    relative_cwd: str
    timeout_seconds: int
    action: PermissionAction
    precondition: ActionPrecondition


class RunCommandOutcome(StrEnum):
    """Known Host outcome after a command process may have started."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class RunCommandExecutionStatus(StrEnum):
    """Trusted Host observation of the command process lifecycle."""

    SPAWN_REJECTED = "spawn-rejected"
    SANDBOX_REJECTED = "sandbox-rejected"
    SPAWN_FAILED = "spawn-failed"
    EXITED = "exited"
    SIGNALED = "signaled"
    TIMED_OUT = "timed-out"
    CANCELLED = "cancelled"
    CLEANUP_INCOMPLETE = "cleanup-incomplete"


@dataclass(frozen=True)
class RunCommandStreamObservation:
    """Byte accounting for one bounded captured command stream."""

    bytes_captured: int
    bytes_total: int
    truncated: bool

    def __post_init__(self) -> None:
        if (
            type(self.bytes_captured) is not int
            or type(self.bytes_total) is not int
            or self.bytes_captured < 0
            or self.bytes_total < self.bytes_captured
        ):
            raise ValueError("command stream observation byte counts are invalid")
        if type(self.truncated) is not bool or self.truncated != (
            self.bytes_total > self.bytes_captured
        ):
            raise ValueError("command stream observation truncation is invalid")


@dataclass(frozen=True)
class RunCommandExecutionObservation:
    """Content-free process metadata produced directly by the command executor."""

    status: RunCommandExecutionStatus
    exit_code: int | None
    signal: int | None
    duration_ms: int | None
    stdout: RunCommandStreamObservation
    stderr: RunCommandStreamObservation
    cleanup_complete: bool

    def __post_init__(self) -> None:
        if type(self.status) is not RunCommandExecutionStatus:
            raise ValueError("command execution observation status is invalid")
        if self.exit_code is not None and (type(self.exit_code) is not int or self.exit_code < 0):
            raise ValueError("command execution observation exit code is invalid")
        if self.signal is not None and (type(self.signal) is not int or self.signal <= 0):
            raise ValueError("command execution observation signal is invalid")
        if self.exit_code is not None and self.signal is not None:
            raise ValueError("command execution observation cannot have exit code and signal")
        if self.duration_ms is not None and (
            type(self.duration_ms) is not int or self.duration_ms < 0
        ):
            raise ValueError("command execution observation duration is invalid")
        if type(self.stdout) is not RunCommandStreamObservation:
            raise ValueError("command stdout observation is invalid")
        if type(self.stderr) is not RunCommandStreamObservation:
            raise ValueError("command stderr observation is invalid")
        if type(self.cleanup_complete) is not bool:
            raise ValueError("command cleanup observation is invalid")


@dataclass(frozen=True)
class RunCommandExecutionResult:
    tool_result: ToolResult
    outcome: RunCommandOutcome
    result_code: str
    audit_message: str
    observation: RunCommandExecutionObservation


@dataclass(frozen=True)
class CommandSandboxInspection:
    """One read-only dependency check with optional activation verification."""

    dependencies: CommandSandboxDependencies
    activation_verified: bool | None
    result_code: str | None = None

    @property
    def available(self) -> bool:
        return self.dependencies.ready and self.activation_verified is not False


class RunCommandPreparationError(ValueError):
    """Reject malformed or unsafe-to-prepare command requests before permission policy."""


@dataclass
class _BoundedCapture:
    limit: int
    captured: bytearray
    total: int = 0
    error: bool = False

    def consume(self, chunk: bytes) -> None:
        self.total += len(chunk)
        remaining = self.limit - len(self.captured)
        if remaining > 0:
            self.captured.extend(chunk[:remaining])


class RunCommandTool:
    """Prepare and execute one direct bounded command without shell interpretation."""

    def __init__(
        self,
        workspace: Path,
        environment: Mapping[str, str] | None = None,
        *,
        command_sandbox: CommandSandbox | None = None,
    ) -> None:
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        self._environment = dict(os.environ if environment is None else environment)
        self._command_sandbox = command_sandbox or LinuxBubblewrapCommandSandbox()

    def inspect_sandbox(self, *, verify_activation: bool = False) -> CommandSandboxInspection:
        """Inspect dependencies and optionally run one fixed activation probe."""
        if type(verify_activation) is not bool:
            raise ValueError("sandbox activation verification flag is invalid")
        inspect_dependencies = getattr(self._command_sandbox, "inspect_dependencies", None)
        if not callable(inspect_dependencies):
            dependencies = CommandSandboxDependencies(
                platform="unknown",
                platform_supported=False,
                bubblewrap_path="<custom>",
                bubblewrap_available=False,
                seccomp_available=False,
            )
            return CommandSandboxInspection(dependencies, False, "sandbox_status_unavailable")
        dependencies = inspect_dependencies()
        if type(dependencies) is not CommandSandboxDependencies:
            raise ValueError("command sandbox dependency inspection is invalid")
        if not verify_activation or not dependencies.ready:
            return CommandSandboxInspection(dependencies, None)

        request = ToolUse(
            "sandbox-check",
            RUN_COMMAND_TOOL_NAME,
            ToolArguments.from_mapping(
                {
                    "argv": ["/usr/bin/true"],
                    "cwd": ".",
                    "timeout_seconds": 5,
                }
            ),
        )
        result = self.execute_detailed(self.prepare(request))
        verified = result.result_code == "command_succeeded"
        return CommandSandboxInspection(dependencies, verified, result.result_code)

    def prepare(self, request: ToolUse) -> PreparedRunCommand:
        """Validate and freeze one exact command request without starting a process."""
        try:
            if request.name != RUN_COMMAND_TOOL_NAME:
                raise ValueError
            arguments = request.arguments.as_mapping()
            if set(arguments) != {"argv", "cwd", "timeout_seconds"}:
                raise ValueError
            raw_argv = arguments["argv"]
            raw_cwd = arguments["cwd"]
            timeout_seconds = arguments["timeout_seconds"]
            if not isinstance(raw_argv, list) or not isinstance(raw_cwd, str):
                raise ValueError
        except (AttributeError, ValueError):
            raise RunCommandPreparationError("run_command input is malformed") from None

        argv = self._validate_argv(raw_argv)
        relative_cwd = self._validate_cwd(raw_cwd)
        if type(timeout_seconds) is not int or not (
            MIN_COMMAND_TIMEOUT_SECONDS <= timeout_seconds <= MAX_COMMAND_TIMEOUT_SECONDS
        ):
            raise RunCommandPreparationError(
                "run_command timeout_seconds must be an integer from "
                f"{MIN_COMMAND_TIMEOUT_SECONDS} to {MAX_COMMAND_TIMEOUT_SECONDS}"
            )

        return PreparedRunCommand(
            request=request,
            argv=argv,
            relative_cwd=relative_cwd,
            timeout_seconds=timeout_seconds,
            action=PermissionAction.DANGEROUS,
            precondition=ActionPrecondition.none(),
        )

    def revalidate(self, prepared: PreparedRunCommand) -> ActionPrecondition:
        """Recheck the workspace root and cwd immediately before execution starts."""
        if type(prepared) is not PreparedRunCommand:
            raise ValueError("prepared run_command is invalid")
        self._validate_cwd(prepared.relative_cwd)
        return ActionPrecondition.none()

    def execute(
        self,
        prepared: PreparedRunCommand,
        *,
        cancellation: TurnCancellation | None = None,
    ) -> ToolResult:
        """Execute one prepared command and return its structured model result."""
        return self.execute_detailed(prepared, cancellation=cancellation).tool_result

    def execute_detailed(
        self,
        prepared: PreparedRunCommand,
        *,
        cancellation: TurnCancellation | None = None,
    ) -> RunCommandExecutionResult:
        """Run argv in the required sandbox with bounded output and process cleanup."""
        if type(prepared) is not PreparedRunCommand:
            raise ValueError("prepared run_command is invalid")
        if cancellation is not None and type(cancellation) is not TurnCancellation:
            raise ValueError("run_command cancellation token is invalid")
        if cancellation is not None:
            cancellation.check()
        request = prepared.request
        try:
            self._validate_cwd(prepared.relative_cwd)
        except RunCommandPreparationError:
            empty_stdout = _BoundedCapture(MAX_COMMAND_STDOUT_BYTES, bytearray())
            empty_stderr = _BoundedCapture(MAX_COMMAND_STDERR_BYTES, bytearray())
            observation = _execution_observation(
                status=RunCommandExecutionStatus.SPAWN_REJECTED,
                returncode=None,
                duration_ms=None,
                stdout=empty_stdout,
                stderr=empty_stderr,
                cleanup_complete=True,
            )
            return RunCommandExecutionResult(
                ToolResult(
                    request.tool_use_id,
                    self._payload(
                        prepared,
                        observation=observation,
                        stdout=empty_stdout,
                        stderr=empty_stderr,
                    ),
                    is_error=True,
                ),
                RunCommandOutcome.FAILED,
                "command_cwd_invalid",
                "run_command cwd no longer satisfies the prepared boundary",
                observation,
            )
        cwd = (
            self._workspace
            if prepared.relative_cwd == "."
            else self._workspace / prepared.relative_cwd
        )
        environment = {
            key: value
            for key in COMMAND_ENVIRONMENT_ALLOWLIST
            if isinstance((value := self._environment.get(key)), str)
        }
        environment["PWD"] = str(cwd)
        stdout_capture = _BoundedCapture(MAX_COMMAND_STDOUT_BYTES, bytearray())
        stderr_capture = _BoundedCapture(MAX_COMMAND_STDERR_BYTES, bytearray())

        try:
            launch = self._command_sandbox.prepare_launch(
                workspace=self._workspace,
                cwd=cwd,
                argv=prepared.argv,
                environment=environment,
            )
        except CommandSandboxUnavailable:
            return self._sandbox_unavailable(prepared)

        started = time.monotonic()
        try:
            process = subprocess.Popen(
                launch.argv,
                cwd=launch.cwd,
                env=launch.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                pass_fds=launch.pass_fds,
            )
        except (OSError, ValueError):
            sandbox_expected = launch.activation_read_fd is not None
            launch.close_without_spawn()
            if sandbox_expected:
                return self._sandbox_unavailable(
                    prepared,
                    duration_ms=_elapsed_milliseconds(started),
                )
            observation = _execution_observation(
                status=RunCommandExecutionStatus.SPAWN_FAILED,
                returncode=None,
                duration_ms=_elapsed_milliseconds(started),
                stdout=stdout_capture,
                stderr=stderr_capture,
                cleanup_complete=True,
            )
            payload = self._payload(
                prepared,
                observation=observation,
                stdout=stdout_capture,
                stderr=stderr_capture,
            )
            return RunCommandExecutionResult(
                ToolResult(request.tool_use_id, payload, is_error=True),
                RunCommandOutcome.FAILED,
                "command_spawn_failed",
                "run_command could not start the requested executable",
                observation,
            )
        launch.close_after_spawn()

        assert process.stdout is not None and process.stderr is not None
        readers = [
            Thread(target=_drain_pipe, args=(process.stdout, stdout_capture), daemon=True),
            Thread(target=_drain_pipe, args=(process.stderr, stderr_capture), daemon=True),
        ]
        activation_capture = _BoundedCapture(SANDBOX_STATUS_MAX_BYTES, bytearray())
        activation_pipe = None
        started_readers: list[Thread] = []
        try:
            if launch.activation_read_fd is not None:
                activation_pipe = os.fdopen(launch.activation_read_fd, "rb", buffering=0)
                readers.append(
                    Thread(
                        target=_drain_pipe,
                        args=(activation_pipe, activation_capture),
                        daemon=True,
                    )
                )
            for reader in readers:
                reader.start()
                started_readers.append(reader)
        except (OSError, RuntimeError):
            if launch.activation_release_fd is not None:
                try:
                    os.close(launch.activation_release_fd)
                except OSError:
                    pass
            cleanup_complete = self._terminate_process_group(process)
            for pipe in (process.stdout, process.stderr, activation_pipe):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
            if activation_pipe is None and launch.activation_read_fd is not None:
                try:
                    os.close(launch.activation_read_fd)
                except OSError:
                    pass
            readers_complete = _join_readers(started_readers, COMMAND_PIPE_DRAIN_GRACE_SECONDS)
            if cleanup_complete and readers_complete:
                return self._sandbox_unavailable(
                    prepared,
                    duration_ms=_elapsed_milliseconds(started),
                )
            return self._sandbox_cleanup_incomplete(
                prepared,
                duration_ms=_elapsed_milliseconds(started),
            )

        if launch.activation_read_fd is not None:
            assert activation_pipe is not None
            activation_reader = readers[-1]
            activation_reader.join(COMMAND_SANDBOX_ACTIVATION_GRACE_SECONDS)
            sandbox_active = (
                launch.activation_release_fd is not None
                and not activation_reader.is_alive()
                and sandbox_activation_succeeded(
                    bytes(activation_capture.captured),
                    read_error=(
                        activation_capture.error
                        or activation_capture.total > activation_capture.limit
                    ),
                )
            )
            if sandbox_active:
                assert launch.activation_release_fd is not None
                try:
                    os.write(launch.activation_release_fd, b"1")
                except OSError:
                    sandbox_active = False
            if launch.activation_release_fd is not None:
                try:
                    os.close(launch.activation_release_fd)
                except OSError:
                    pass
            if not sandbox_active:
                cleanup_complete = self._terminate_process_group(process)
                for pipe in (process.stdout, process.stderr, activation_pipe):
                    try:
                        pipe.close()
                    except OSError:
                        pass
                readers_complete = _join_readers(readers, COMMAND_PIPE_DRAIN_GRACE_SECONDS)
                if cleanup_complete and readers_complete:
                    return self._sandbox_unavailable(
                        prepared,
                        duration_ms=_elapsed_milliseconds(started),
                    )
                return self._sandbox_cleanup_incomplete(
                    prepared,
                    duration_ms=_elapsed_milliseconds(started),
                )

        status = RunCommandExecutionStatus.EXITED
        cleanup_complete = True
        interrupted = False
        try:
            deadline = started + prepared.timeout_seconds
            while process.poll() is None:
                if cancellation is not None and cancellation.requested:
                    status = RunCommandExecutionStatus.CANCELLED
                    interrupted = True
                    cleanup_complete = self._terminate_process_group(process)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = RunCommandExecutionStatus.TIMED_OUT
                    cleanup_complete = self._terminate_process_group(process)
                    break
                try:
                    process.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    continue
        except KeyboardInterrupt:
            status = RunCommandExecutionStatus.CANCELLED
            interrupted = True
            cleanup_complete = self._terminate_process_group(process)

        readers_complete = _join_readers(readers, COMMAND_PIPE_DRAIN_GRACE_SECONDS)
        if not readers_complete:
            cleanup_complete = self._terminate_process_group(process) and cleanup_complete
            for pipe in (process.stdout, process.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass
            if activation_pipe is not None:
                try:
                    activation_pipe.close()
                except OSError:
                    pass
            readers_complete = _join_readers(readers, COMMAND_PIPE_DRAIN_GRACE_SECONDS)
        cleanup_complete = (
            cleanup_complete
            and readers_complete
            and not (stdout_capture.error or stderr_capture.error)
        )

        if activation_pipe is not None:
            try:
                activation_pipe.close()
            except OSError:
                pass

        returncode = process.poll()
        if returncode is None:
            cleanup_complete = False
        elif launch.encodes_signals_as_exit_status and 129 <= returncode <= 192:
            returncode = -(returncode - 128)
        if interrupted:
            result_code = (
                "command_cancelled" if cleanup_complete else "command_cancel_cleanup_incomplete"
            )
            outcome = RunCommandOutcome.PARTIAL
        elif status == RunCommandExecutionStatus.TIMED_OUT:
            result_code = (
                "command_timed_out" if cleanup_complete else "command_timeout_cleanup_incomplete"
            )
            outcome = RunCommandOutcome.PARTIAL
        elif not cleanup_complete:
            status = RunCommandExecutionStatus.CLEANUP_INCOMPLETE
            result_code = "command_cleanup_incomplete"
            outcome = RunCommandOutcome.PARTIAL
        elif returncode < 0:
            status = RunCommandExecutionStatus.SIGNALED
            result_code = "command_signaled"
            outcome = RunCommandOutcome.PARTIAL
        elif returncode == 0 and cleanup_complete:
            result_code = "command_succeeded"
            outcome = RunCommandOutcome.SUCCEEDED
        elif returncode == 0:
            status = RunCommandExecutionStatus.CLEANUP_INCOMPLETE
            result_code = "command_cleanup_incomplete"
            outcome = RunCommandOutcome.PARTIAL
        else:
            result_code = "command_exited_nonzero"
            outcome = RunCommandOutcome.FAILED

        observation = _execution_observation(
            status=status,
            returncode=returncode,
            duration_ms=_elapsed_milliseconds(started),
            stdout=stdout_capture,
            stderr=stderr_capture,
            cleanup_complete=cleanup_complete,
        )
        payload = self._payload(
            prepared,
            observation=observation,
            stdout=stdout_capture,
            stderr=stderr_capture,
        )
        is_error = outcome != RunCommandOutcome.SUCCEEDED
        audit_message = {
            "command_succeeded": "run_command exited successfully",
            "command_cwd_invalid": "run_command cwd no longer satisfies the prepared boundary",
            "command_exited_nonzero": "run_command exited with a nonzero status",
            "command_signaled": "run_command ended because of a signal",
            "command_timed_out": "run_command timed out and its process group was terminated",
            "command_timeout_cleanup_incomplete": "run_command timed out and cleanup is incomplete",
            "command_cancelled": "run_command was cancelled and its process group was terminated",
            "command_cancel_cleanup_incomplete": "run_command was cancelled and cleanup is incomplete",
            "command_cleanup_incomplete": "run_command process cleanup is incomplete",
            "command_sandbox_unavailable": "run_command sandbox was unavailable",
        }[result_code]
        return RunCommandExecutionResult(
            ToolResult(
                request.tool_use_id,
                payload,
                is_error=is_error,
                truncated=(
                    stdout_capture.total > stdout_capture.limit
                    or stderr_capture.total > stderr_capture.limit
                ),
            ),
            outcome,
            result_code,
            audit_message,
            observation,
        )

    def _sandbox_cleanup_incomplete(
        self,
        prepared: PreparedRunCommand,
        *,
        duration_ms: int,
    ) -> RunCommandExecutionResult:
        stdout = _BoundedCapture(MAX_COMMAND_STDOUT_BYTES, bytearray())
        stderr = _BoundedCapture(MAX_COMMAND_STDERR_BYTES, bytearray())
        observation = _execution_observation(
            status=RunCommandExecutionStatus.CLEANUP_INCOMPLETE,
            returncode=None,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            cleanup_complete=False,
        )
        return RunCommandExecutionResult(
            ToolResult(
                prepared.request.tool_use_id,
                self._payload(
                    prepared,
                    observation=observation,
                    stdout=stdout,
                    stderr=stderr,
                ),
                is_error=True,
            ),
            RunCommandOutcome.PARTIAL,
            "command_cleanup_incomplete",
            "run_command sandbox setup failed and process cleanup is incomplete",
            observation,
        )

    def _sandbox_unavailable(
        self,
        prepared: PreparedRunCommand,
        *,
        duration_ms: int | None = None,
    ) -> RunCommandExecutionResult:
        stdout = _BoundedCapture(MAX_COMMAND_STDOUT_BYTES, bytearray())
        stderr = _BoundedCapture(MAX_COMMAND_STDERR_BYTES, bytearray())
        observation = _execution_observation(
            status=RunCommandExecutionStatus.SANDBOX_REJECTED,
            returncode=None,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            cleanup_complete=True,
        )
        return RunCommandExecutionResult(
            ToolResult(
                prepared.request.tool_use_id,
                self._payload(
                    prepared,
                    observation=observation,
                    stdout=stdout,
                    stderr=stderr,
                ),
                is_error=True,
            ),
            RunCommandOutcome.FAILED,
            "command_sandbox_unavailable",
            "run_command sandbox is unavailable; the requested command was not started",
            observation,
        )

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[bytes]) -> bool:
        pgid = process.pid
        if not _signal_process_group(process, pgid, signal.SIGTERM):
            return False
        if _wait_for_process_group_exit(process, pgid, COMMAND_TERMINATE_GRACE_SECONDS):
            return True
        if not _signal_process_group(process, pgid, signal.SIGKILL):
            return False
        return _wait_for_process_group_exit(process, pgid, COMMAND_KILL_GRACE_SECONDS)

    @staticmethod
    def _payload(
        prepared: PreparedRunCommand,
        *,
        observation: RunCommandExecutionObservation,
        stdout: _BoundedCapture,
        stderr: _BoundedCapture,
    ) -> str:
        return (
            json.dumps(
                {
                    "cleanup_complete": observation.cleanup_complete,
                    "cwd": prepared.relative_cwd,
                    "exit_code": observation.exit_code,
                    "signal": observation.signal,
                    "status": observation.status,
                    "stderr": _capture_payload(stderr),
                    "stdout": _capture_payload(stdout),
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )

    @staticmethod
    def _validate_argv(raw_argv: list[object]) -> tuple[str, ...]:
        if not raw_argv or len(raw_argv) > MAX_COMMAND_ARGUMENTS:
            raise RunCommandPreparationError(
                f"run_command argv must contain 1 to {MAX_COMMAND_ARGUMENTS} arguments"
            )

        argv: list[str] = []
        total_bytes = 0
        for index, value in enumerate(raw_argv):
            if not isinstance(value, str) or "\x00" in value:
                raise RunCommandPreparationError(
                    f"run_command argv[{index}] must be valid text without NUL"
                )
            if index == 0 and (not value or not value.strip()):
                raise RunCommandPreparationError(
                    "run_command argv[0] must name a nonblank executable"
                )
            try:
                encoded = value.encode("utf-8")
            except UnicodeEncodeError:
                raise RunCommandPreparationError(
                    f"run_command argv[{index}] must be valid UTF-8"
                ) from None
            if (
                len(value) > MAX_COMMAND_ARGUMENT_CHARACTERS
                or len(encoded) > MAX_COMMAND_ARGUMENT_BYTES
            ):
                raise RunCommandPreparationError(
                    f"run_command argv[{index}] exceeds {MAX_COMMAND_ARGUMENT_BYTES} bytes"
                )
            total_bytes += len(encoded)
            argv.append(value)
        if total_bytes > MAX_COMMAND_ARGV_BYTES:
            raise RunCommandPreparationError(
                f"run_command argv exceeds {MAX_COMMAND_ARGV_BYTES} total bytes"
            )
        return tuple(argv)

    def _validate_cwd(self, value: str) -> str:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise RunCommandPreparationError("run_command cwd must be valid UTF-8") from None
        parts = value.split("/")
        invalid = (
            not value
            or not value.strip()
            or "\x00" in value
            or "\\" in value
            or value.startswith("/")
            or PureWindowsPath(value).drive
            or value != value.strip()
            or (value != "." and any(part in {"", ".", ".."} for part in parts))
            or value.endswith("/")
            or len(parts) > MAX_COMMAND_CWD_COMPONENTS
            or len(value) > MAX_COMMAND_CWD_CHARACTERS
            or len(encoded) > MAX_COMMAND_CWD_BYTES
        )
        if invalid:
            raise RunCommandPreparationError(
                "run_command cwd must be '.' or a portable workspace-relative directory"
            )

        self._inspect_directory(self._workspace, workspace_root=True)
        if value != ".":
            current = self._workspace
            for part in parts:
                current = current / part
                self._inspect_directory(current, workspace_root=False)
        return value

    @staticmethod
    def _inspect_directory(path: Path, *, workspace_root: bool) -> None:
        subject = "workspace root" if workspace_root else "cwd directory"
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise RunCommandPreparationError(f"run_command {subject} does not exist") from None
        except PermissionError:
            raise RunCommandPreparationError(f"run_command {subject} is not accessible") from None
        except OSError:
            raise RunCommandPreparationError(f"run_command could not inspect {subject}") from None
        if stat.S_ISLNK(info.st_mode):
            if workspace_root:
                raise RunCommandPreparationError(
                    "run_command workspace root must not be a symbolic link"
                )
            raise RunCommandPreparationError("run_command cwd must not contain a symbolic link")
        if not stat.S_ISDIR(info.st_mode):
            if workspace_root:
                raise RunCommandPreparationError(
                    "run_command workspace root must identify an existing directory"
                )
            raise RunCommandPreparationError("run_command cwd must identify an existing directory")


def _signal_process_group(
    process: subprocess.Popen[bytes], pgid: int, requested_signal: signal.Signals
) -> bool:
    try:
        os.killpg(pgid, requested_signal)
        return True
    except ProcessLookupError:
        return True
    except OSError:
        if process.poll() is not None:
            return False
        try:
            process.send_signal(requested_signal)
        except OSError:
            return False
        return True


def _wait_for_process_group_exit(
    process: subprocess.Popen[bytes], pgid: int, timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        process.poll()
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return process.poll() is not None
        except PermissionError:
            pass
        except OSError:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.01, remaining))


def _drain_pipe(pipe, capture: _BoundedCapture) -> None:
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                return
            capture.consume(chunk)
    except (OSError, ValueError):
        capture.error = True


def _join_readers(readers: list[Thread], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    for reader in readers:
        reader.join(max(0.0, deadline - time.monotonic()))
    return all(not reader.is_alive() for reader in readers)


def _elapsed_milliseconds(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _execution_observation(
    *,
    status: RunCommandExecutionStatus,
    returncode: int | None,
    duration_ms: int | None,
    stdout: _BoundedCapture,
    stderr: _BoundedCapture,
    cleanup_complete: bool,
) -> RunCommandExecutionObservation:
    return RunCommandExecutionObservation(
        status=status,
        exit_code=returncode if returncode is not None and returncode >= 0 else None,
        signal=-returncode if returncode is not None and returncode < 0 else None,
        duration_ms=duration_ms,
        stdout=_stream_observation(stdout),
        stderr=_stream_observation(stderr),
        cleanup_complete=cleanup_complete,
    )


def _stream_observation(capture: _BoundedCapture) -> RunCommandStreamObservation:
    captured = len(capture.captured)
    return RunCommandStreamObservation(
        bytes_captured=captured,
        bytes_total=capture.total,
        truncated=capture.total > captured,
    )


def _capture_payload(capture: _BoundedCapture) -> dict[str, object]:
    data = bytes(capture.captured)
    payload: dict[str, object] = {
        "bytes_captured": len(data),
        "bytes_total": capture.total,
        "truncated": capture.total > capture.limit,
    }
    try:
        payload["encoding"] = "utf-8"
        payload["text"] = data.decode("utf-8")
    except UnicodeDecodeError:
        payload["base64"] = base64.b64encode(data).decode("ascii")
        payload["encoding"] = "base64"
    return payload


def run_command_model_definition() -> dict[str, object]:
    """Return the exact provider-neutral controlled command definition."""
    return {
        "name": RUN_COMMAND_TOOL_NAME,
        "description": (
            "Run one bounded local command by direct argument vector in an existing workspace "
            "directory. This is dangerous full local process execution, not a shell or sandbox; "
            "the Host applies permission and approval policy, a fixed timeout, bounded output, "
            "and process-group cleanup."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "description": "Direct executable and arguments; shell syntax is literal.",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_COMMAND_ARGUMENTS,
                },
                "cwd": {
                    "type": "string",
                    "description": "'.' or an existing portable workspace-relative directory.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Host-enforced timeout in whole seconds.",
                    "minimum": MIN_COMMAND_TIMEOUT_SECONDS,
                    "maximum": MAX_COMMAND_TIMEOUT_SECONDS,
                },
            },
            "required": ["argv", "cwd", "timeout_seconds"],
            "additionalProperties": False,
        },
    }


def run_command_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(run_command_model_definition())
