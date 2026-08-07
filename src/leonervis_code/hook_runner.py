"""Pinned foreground execution for local Hook handlers through the command sandbox."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat

from leonervis_code.core.actions import ActionPrecondition
from leonervis_code.core.cancellation import TurnCancellation
from leonervis_code.core.contracts import ToolArguments, ToolResult, ToolUse
from leonervis_code.core.hook_contracts import (
    HookActionOutcome,
    HookEffect,
    HookEvent,
    HookHandlerResult,
)
from leonervis_code.hooks import HookEntry
from leonervis_code.session_records import ActionExecutionOutcome
from leonervis_code.tools.run_command import (
    RUN_COMMAND_TOOL_NAME,
    PreparedRunCommand,
    RunCommandOutcome,
    RunCommandTool,
)


HOOK_HANDLER_ACTION_NAME = "hook_handler"
HOOK_HANDLER_PROTOCOL_VERSION = 1
MAX_HOOK_HANDLER_EXECUTABLE_BYTES = 16 * 1024 * 1024
MAX_HOOK_HANDLER_EVENT_BYTES = 1024
MAX_HOOK_HANDLER_STDOUT_BYTES = 4096
_HANDLER_PRECONDITION_DOMAIN = b"leonervis-code-hook-handler-precondition-v1\0"


class HookHandlerPreparationError(ValueError):
    """One configured handler cannot be prepared at its pinned identity."""


@dataclass(frozen=True)
class HookHandlerEvent:
    """Content-free event envelope supplied as one Host-owned argv suffix."""

    event: HookEvent
    hook_set_id: str
    subject_id: str
    tool_name: str | None = None
    permission_action: str | None = None
    source: str | None = None
    action_outcome: HookActionOutcome | None = None

    def __post_init__(self) -> None:
        if type(self.event) is not HookEvent:
            raise ValueError("Hook handler event is invalid")
        for value, label in (
            (self.hook_set_id, "Hook set ID"),
            (self.subject_id, "Hook handler subject"),
        ):
            _safe_text(value, label)
        if self.event.is_action_event:
            _safe_text(self.tool_name, "Hook handler tool name")
            _safe_text(self.permission_action, "Hook handler permission action")
            if self.source not in {"builtin", "mcp"}:
                raise ValueError("Hook handler source is invalid")
            if self.event is HookEvent.AFTER_ACTION:
                if type(self.action_outcome) is not HookActionOutcome:
                    raise ValueError("after-action Hook handler event requires an outcome")
            elif self.action_outcome is not None:
                raise ValueError("preauthorization Hook handler event cannot contain an outcome")
        elif any(
            value is not None
            for value in (
                self.tool_name,
                self.permission_action,
                self.source,
                self.action_outcome,
            )
        ):
            raise ValueError("lifecycle Hook handler event cannot contain action metadata")

    def canonical_json(self, hook_id: str) -> str:
        _safe_text(hook_id, "Hook ID")
        payload = json.dumps(
            {
                "action_outcome": (
                    self.action_outcome.value if self.action_outcome is not None else None
                ),
                "event": self.event.value,
                "hook_id": hook_id,
                "hook_set_id": self.hook_set_id,
                "permission_action": self.permission_action,
                "source": self.source,
                "subject_id": self.subject_id,
                "tool_name": self.tool_name,
                "version": HOOK_HANDLER_PROTOCOL_VERSION,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(payload.encode("utf-8")) > MAX_HOOK_HANDLER_EVENT_BYTES:
            raise ValueError("Hook handler event exceeds its byte bound")
        return payload


@dataclass(frozen=True)
class PreparedHookHandler:
    """One exact handler command plus its trusted revalidation identity."""

    entry: HookEntry
    event: HookHandlerEvent
    resolved_executable: str
    executable_sha256: str
    command: PreparedRunCommand
    identity_arguments: ToolArguments
    precondition: ActionPrecondition


@dataclass(frozen=True)
class HookHandlerExecution:
    """One bounded handler execution with a safe Action Audit result."""

    tool_result: ToolResult
    outcome: ActionExecutionOutcome
    result_code: str
    audit_message: str
    handler_result: HookHandlerResult | None


@dataclass(frozen=True)
class HookHandlerInspection:
    """Content-free readiness facts for one configured handler."""

    hook_id: str
    ready: bool
    code: str
    executable_sha256: str | None


class HookRunner:
    """Prepare and run fixed local handlers without shell or recursive Hook dispatch."""

    def __init__(self, workspace: Path, run_command: RunCommandTool) -> None:
        self._workspace = Path(workspace).resolve()
        if not self._workspace.is_dir():
            raise ValueError("Hook runner workspace must be an existing directory")
        if not isinstance(run_command, RunCommandTool):
            raise ValueError("Hook runner requires the controlled command tool")
        self._run_command = run_command

    def executable_sha256(self, executable: str) -> str:
        """Return the bounded digest used for explicit handler pinning."""
        _, digest = self._inspect_executable(executable)
        return digest

    def inspect(self, entry: HookEntry) -> HookHandlerInspection:
        if not isinstance(entry, HookEntry) or entry.rule.handler is None:
            raise ValueError("Hook handler inspection requires an executable Hook")
        try:
            _, digest = self._inspect_executable(entry.rule.handler.executable)
        except HookHandlerPreparationError as error:
            return HookHandlerInspection(entry.rule.hook_id, False, str(error), None)
        ready = digest == entry.rule.handler.executable_sha256
        return HookHandlerInspection(
            entry.rule.hook_id,
            ready,
            "ready" if ready else "handler_fingerprint_mismatch",
            digest,
        )

    def prepare(
        self,
        entry: HookEntry,
        event: HookHandlerEvent,
        *,
        tool_use_id: str,
    ) -> PreparedHookHandler:
        if not isinstance(entry, HookEntry) or entry.rule.handler is None:
            raise HookHandlerPreparationError("Hook does not configure a local handler")
        if not isinstance(event, HookHandlerEvent):
            raise HookHandlerPreparationError("Hook handler event is invalid")
        spec = entry.rule.handler
        resolved, digest = self._inspect_executable(spec.executable)
        if digest != spec.executable_sha256:
            raise HookHandlerPreparationError("handler_fingerprint_mismatch")
        envelope = event.canonical_json(entry.rule.hook_id)
        argv = [
            resolved,
            *spec.arguments,
            "--leonervis-hook-event-v1",
            envelope,
        ]
        request = ToolUse(
            tool_use_id,
            RUN_COMMAND_TOOL_NAME,
            ToolArguments.from_mapping(
                {
                    "argv": argv,
                    "cwd": ".",
                    "timeout_seconds": spec.timeout_seconds,
                }
            ),
        )
        try:
            command = self._run_command.prepare(request)
        except ValueError as error:
            raise HookHandlerPreparationError(str(error)) from None
        identity_arguments = ToolArguments.from_mapping(
            {
                "event": event.event.value,
                "executable": resolved,
                "executable_sha256": digest,
                "hook_id": entry.rule.hook_id,
                "hook_set_id": event.hook_set_id,
                "subject_id": event.subject_id,
                "timeout_seconds": spec.timeout_seconds,
            }
        )
        precondition = ActionPrecondition.expected_configuration(
            self._precondition_sha256(entry, event, resolved, digest)
        )
        return PreparedHookHandler(
            entry,
            event,
            resolved,
            digest,
            command,
            identity_arguments,
            precondition,
        )

    def revalidate(self, prepared: PreparedHookHandler) -> ActionPrecondition:
        if not isinstance(prepared, PreparedHookHandler):
            raise ValueError("prepared Hook handler is invalid")
        resolved, digest = self._inspect_executable(prepared.entry.rule.handler.executable)
        self._run_command.revalidate(prepared.command)
        return ActionPrecondition.expected_configuration(
            self._precondition_sha256(prepared.entry, prepared.event, resolved, digest)
        )

    def execute(
        self,
        prepared: PreparedHookHandler,
        *,
        cancellation: TurnCancellation | None = None,
    ) -> HookHandlerExecution:
        if not isinstance(prepared, PreparedHookHandler):
            raise ValueError("prepared Hook handler is invalid")
        try:
            current = self.revalidate(prepared)
        except (HookHandlerPreparationError, ValueError):
            current = None
        if current != prepared.precondition:
            return HookHandlerExecution(
                ToolResult(
                    prepared.command.request.tool_use_id,
                    "Hook handler changed after preparation",
                    is_error=True,
                ),
                ActionExecutionOutcome.FAILED,
                "hook_handler_stale",
                "Hook handler identity changed after preparation",
                None,
            )
        command = self._run_command.execute_detailed(
            prepared.command,
            cancellation=cancellation,
        )
        if command.outcome is not RunCommandOutcome.SUCCEEDED:
            outcome = {
                RunCommandOutcome.FAILED: ActionExecutionOutcome.FAILED,
                RunCommandOutcome.PARTIAL: ActionExecutionOutcome.PARTIAL,
            }[command.outcome]
            return HookHandlerExecution(
                ToolResult(
                    prepared.command.request.tool_use_id,
                    "Hook handler command did not complete successfully",
                    is_error=True,
                ),
                outcome,
                f"hook_handler_{command.result_code}",
                "Hook handler command did not complete successfully",
                None,
            )
        try:
            result = _parse_handler_result(command.tool_result.content, prepared.event.event)
        except ValueError:
            return HookHandlerExecution(
                ToolResult(
                    prepared.command.request.tool_use_id,
                    "Hook handler returned an invalid structured result",
                    is_error=True,
                ),
                ActionExecutionOutcome.FAILED,
                "hook_handler_protocol_invalid",
                "Hook handler returned an invalid structured result",
                None,
            )
        return HookHandlerExecution(
            ToolResult(
                prepared.command.request.tool_use_id,
                f"Hook handler returned {result.effect.value}",
            ),
            ActionExecutionOutcome.SUCCEEDED,
            f"hook_handler_{result.effect.value}",
            f"Hook handler returned {result.effect.value}",
            result,
        )

    def _precondition_sha256(
        self,
        entry: HookEntry,
        event: HookHandlerEvent,
        resolved: str,
        executable_sha256: str,
    ) -> str:
        handler = entry.rule.handler
        assert handler is not None
        payload = json.dumps(
            {
                "event": json.loads(event.canonical_json(entry.rule.hook_id)),
                "handler": handler.as_mapping(),
                "resolved_executable": resolved,
                "resolved_sha256": executable_sha256,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(_HANDLER_PRECONDITION_DOMAIN + payload).hexdigest()

    def _inspect_executable(self, executable: str) -> tuple[str, str]:
        try:
            raw = Path(executable)
        except (TypeError, ValueError):
            raise HookHandlerPreparationError("handler_executable_invalid") from None
        if raw.is_absolute():
            try:
                resolved = raw.resolve(strict=True)
            except OSError:
                raise HookHandlerPreparationError("handler_executable_unavailable") from None
        else:
            _validate_relative_executable(executable)
            current = self._workspace
            for part in PurePosixPath(executable).parts:
                current = current / part
                try:
                    info = current.lstat()
                except OSError:
                    raise HookHandlerPreparationError("handler_executable_unavailable") from None
                if stat.S_ISLNK(info.st_mode):
                    raise HookHandlerPreparationError("handler_executable_symlink_rejected")
            resolved = current
        try:
            info = resolved.stat()
        except OSError:
            raise HookHandlerPreparationError("handler_executable_unavailable") from None
        if not stat.S_ISREG(info.st_mode):
            raise HookHandlerPreparationError("handler_executable_not_regular")
        if info.st_size > MAX_HOOK_HANDLER_EXECUTABLE_BYTES:
            raise HookHandlerPreparationError("handler_executable_too_large")
        if not os.access(resolved, os.X_OK):
            raise HookHandlerPreparationError("handler_executable_not_executable")
        digest = hashlib.sha256()
        try:
            with resolved.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    digest.update(chunk)
        except OSError:
            raise HookHandlerPreparationError("handler_executable_unreadable") from None
        return str(resolved), digest.hexdigest()


def _parse_handler_result(payload: str, event: HookEvent) -> HookHandlerResult:
    try:
        outer = json.loads(payload)
        if not isinstance(outer, dict):
            raise ValueError
        stdout = outer["stdout"]
        if (
            not isinstance(stdout, dict)
            or stdout.get("encoding") != "utf-8"
            or stdout.get("truncated") is not False
            or type(stdout.get("bytes_total")) is not int
            or stdout["bytes_total"] > MAX_HOOK_HANDLER_STDOUT_BYTES
            or stdout.get("bytes_captured") != stdout["bytes_total"]
            or not isinstance(stdout.get("text"), str)
        ):
            raise ValueError
        value = json.loads(stdout["text"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("Hook handler result is invalid") from None
    if not isinstance(value, dict) or set(value) != {"effect", "message", "version"}:
        raise ValueError("Hook handler result must be a closed object")
    if value["version"] != HOOK_HANDLER_PROTOCOL_VERSION:
        raise ValueError("Hook handler result version is unsupported")
    try:
        result = HookHandlerResult(HookEffect(value["effect"]), value["message"])
    except (TypeError, ValueError):
        raise ValueError("Hook handler result fields are invalid") from None
    if event is not HookEvent.BEFORE_ACTION_AUTHORIZATION and result.effect in {
        HookEffect.DENY,
        HookEffect.REQUIRE_ASK,
    }:
        raise ValueError("observational Hook handler cannot return an authorization effect")
    return result


def _validate_relative_executable(value: str) -> None:
    try:
        encoded = value.encode("utf-8")
    except (AttributeError, UnicodeEncodeError):
        raise HookHandlerPreparationError("handler_executable_invalid") from None
    path = PurePosixPath(value)
    if (
        not value
        or not value.strip()
        or len(encoded) > 4096
        or "\x00" in value
        or "\\" in value
        or PureWindowsPath(value).drive
        or path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
        or str(path) != value
    ):
        raise HookHandlerPreparationError("handler_executable_invalid")


def _safe_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{label} is invalid") from None
    if (
        not value
        or len(value) > 256
        or len(encoded) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} is invalid")
    return value
