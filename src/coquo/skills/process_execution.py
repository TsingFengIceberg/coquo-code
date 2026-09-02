"""Explicit, fail-closed process runner for executable Skills.

Only a Host-supplied launcher may execute code.  argv is passed directly to
``Popen`` with no shell, bounded output is captured, and a missing launcher is
an error rather than an implicit unsandboxed fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Mapping
from uuid import uuid4

from coquo.core.cancellation import TurnCancellation
from coquo.core.contracts import ToolArguments, ToolResult, ToolUse
from coquo.core.extension_actions import CoordinatedExtensionActionInvoker
from coquo.core.permissions import PermissionAction
from coquo.tools.run_command import RunCommandExecutionResult, RunCommandTool


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
    def __init__(
        self,
        policy: SkillProcessPolicy,
        *,
        command_tool: RunCommandTool | None = None,
        action_invoker: CoordinatedExtensionActionInvoker | None = None,
    ) -> None:
        if command_tool is not None and not isinstance(command_tool, RunCommandTool):
            raise ValueError("skill command tool is invalid")
        if action_invoker is not None and not isinstance(
            action_invoker, CoordinatedExtensionActionInvoker
        ):
            raise ValueError("skill action invoker is invalid")
        if action_invoker is not None and command_tool is None:
            raise ValueError("skill action invoker requires a Host command tool")
        self.policy = policy
        self.command_tool = command_tool
        self.action_invoker = action_invoker

    def run(
        self,
        argv: tuple[str, ...] | list[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        cancellation: TurnCancellation | None = None,
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
        if cancellation is not None and not isinstance(cancellation, TurnCancellation):
            raise SkillProcessExecutionError("Skill cancellation token is invalid")
        if cancellation is not None:
            cancellation.check()
        if self.command_tool is not None:
            if env is not None:
                raise SkillProcessExecutionError(
                    "Skill environment overrides are not accepted by the Host command boundary"
                )
            return self._run_host_command(argv, root, env=env, cancellation=cancellation)
        command = [str(self.policy.launcher), *argv]
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                env=None if env is None else dict(env),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            raise SkillProcessExecutionError("Skill sandbox process failed to start") from error
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancellation is not None and cancellation.requested:
                    _terminate_process(process)
                    stdout, stderr = process.communicate()
                    stdout_bytes = _bytes(stdout)
                    stderr_bytes = _bytes(stderr)
                    return SkillProcessResult(
                        "cancelled",
                        None,
                        _bounded(stdout, self.policy.max_output_bytes),
                        _bounded(stderr, self.policy.max_output_bytes),
                        len(stdout_bytes) > self.policy.max_output_bytes
                        or len(stderr_bytes) > self.policy.max_output_bytes,
                    )
                if time.monotonic() - started >= self.policy.timeout_seconds:
                    _terminate_process(process)
                    stdout, stderr = process.communicate()
                    stdout_bytes = _bytes(stdout)
                    stderr_bytes = _bytes(stderr)
                    return SkillProcessResult(
                        "timeout",
                        None,
                        _bounded(stdout, self.policy.max_output_bytes),
                        _bounded(stderr, self.policy.max_output_bytes),
                        len(stdout_bytes) > self.policy.max_output_bytes
                        or len(stderr_bytes) > self.policy.max_output_bytes,
                    )
                time.sleep(0.01)
            stdout, stderr = process.communicate()
        except Exception as error:
            try:
                _terminate_process(process)
            except OSError:
                pass
            raise SkillProcessExecutionError("Skill process cleanup failed") from error
        stdout_bytes = _bytes(stdout)
        stderr_bytes = _bytes(stderr)
        truncated = (
            len(stdout_bytes) > self.policy.max_output_bytes
            or len(stderr_bytes) > self.policy.max_output_bytes
        )
        stdout = _bounded(stdout_bytes, self.policy.max_output_bytes)
        stderr = _bounded(stderr_bytes, self.policy.max_output_bytes)
        return SkillProcessResult(
            "completed" if process.returncode == 0 else "failed",
            process.returncode,
            stdout,
            stderr,
            truncated,
        )

    def _run_host_command(
        self,
        argv: tuple[str, ...] | list[str],
        cwd: Path,
        *,
        env: Mapping[str, str] | None,
        cancellation: TurnCancellation | None,
    ) -> SkillProcessResult:
        """Route executable Skill work through the existing command ToolSet."""
        assert self.command_tool is not None
        try:
            relative = cwd.relative_to(self.command_tool.workspace).as_posix() or "."
        except ValueError as error:
            raise SkillProcessExecutionError("Skill cwd escapes the Host workspace") from error
        request_id = str(uuid4())
        request = ToolUse(
            f"skill-process-{request_id}",
            "run_command",
            ToolArguments.from_mapping(
                {
                    "argv": [str(self.policy.launcher), *argv],
                    "cwd": relative,
                    "timeout_seconds": int(self.policy.timeout_seconds),
                }
            ),
        )
        prepared = self.command_tool.prepare(request)

        def execute() -> RunCommandExecutionResult:
            return self.command_tool.execute_detailed(prepared, cancellation=cancellation)

        if self.action_invoker is None:
            execution = execute()
        else:
            coordinated = self.action_invoker.invoke(
                tool_name="skill_process",
                arguments={"argv": list(argv), "cwd": relative},
                action=PermissionAction.DANGEROUS,
                approval_required=True,
                execute=lambda identity: _as_action_result(identity, execute()),
            )
            if coordinated.tool_result.is_error:
                return SkillProcessResult("denied", None, coordinated.tool_result.content, "", True)
            return _from_command_result(coordinated.tool_result, coordinated.result_code)
        return _from_command_result(execution.tool_result, execution.result_code)


def _bytes(value: object) -> bytes:
    if value is None:
        return b""
    return value if isinstance(value, bytes) else str(value).encode("utf-8", errors="replace")


def _bounded(value: object, limit: int) -> str:
    return _bytes(value)[:limit].decode("utf-8", errors="replace")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate the launcher and its descendants when possible."""
    try:
        os.killpg(process.pid, 9)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _as_action_result(identity, result: RunCommandExecutionResult):
    from coquo.core.action_coordinator import ActionExecutionResult
    from coquo.session_records import ActionExecutionOutcome

    tool_result = result.tool_result
    if tool_result.tool_use_id != identity.tool_use_id:
        tool_result = ToolResult(
            identity.tool_use_id,
            tool_result.content,
            is_error=tool_result.is_error,
            truncated=tool_result.truncated,
        )
    return ActionExecutionResult(
        tool_result,
        ActionExecutionOutcome.SUCCEEDED
        if result.outcome.value == "succeeded"
        else ActionExecutionOutcome.FAILED,
        result.result_code,
        result.audit_message,
    )


def _from_command_result(result: ToolResult, result_code: str) -> SkillProcessResult:
    try:
        payload = json.loads(result.content)
    except (TypeError, json.JSONDecodeError):
        return SkillProcessResult(
            "failed" if result.is_error else "completed",
            None,
            result.content,
            "",
            result.truncated,
        )
    returncode = payload.get("exit_code")
    stdout = str(payload.get("stdout", ""))
    stderr = str(payload.get("stderr", ""))
    if not result.is_error and result_code == "command_succeeded":
        outcome = "completed"
    elif result_code in {"command_timed_out", "command_timeout_cleanup_incomplete"}:
        outcome = "timeout"
    elif result_code in {"command_cancelled", "command_cancel_cleanup_incomplete"}:
        outcome = "cancelled"
    else:
        outcome = "failed"
    return SkillProcessResult(
        outcome,
        returncode if isinstance(returncode, int) else None,
        stdout,
        stderr,
        bool(
            result.truncated or payload.get("stdout_truncated") or payload.get("stderr_truncated")
        ),
    )


__all__ = [
    "SkillProcessExecutionError",
    "SkillProcessPolicy",
    "SkillProcessResult",
    "SkillProcessRunner",
]
