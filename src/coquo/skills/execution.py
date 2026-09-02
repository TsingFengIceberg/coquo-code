"""Host-gated execution for declarative Skill step plans.

An executable Skill is a bounded data file (``EXECUTION.json``) beside a
normal ``SKILL.md``.  It contains only existing ToolSet names and JSON
arguments.  The Host supplies dispatch and approval callbacks, so a Skill
cannot import code, start a shell, grant permissions, or bypass Action Audit.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from coquo.core.contracts import ToolArguments
from coquo.skills.catalog import SkillCandidate
from coquo.tools.catalog import ORDINARY_TOOL_NAMES


EXECUTION_PLAN_SCHEMA_VERSION = 1
MAX_EXECUTION_STEPS = 16
MAX_EXECUTION_PLAN_BYTES = 64 * 1024
MAX_EXECUTION_ARGUMENT_BYTES = 16 * 1024
_NAME = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_FORBIDDEN_DEFAULT_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "mkdir",
        "move_file",
        "delete_file",
        "delete_directory",
        "copy_file",
        "patch_file",
        "move_directory",
        "run_command",
        "web_fetch",
        "web_search",
        "download_file",
        "child_spawn",
        "child_status",
        "child_wait",
        "child_cancel",
        "team_create",
        "team_add_member",
        "team_status",
        "team_message_send",
        "team_message_show",
        "team_message_read",
        "team_work_create",
        "team_schedule_start",
        "team_schedule_wait",
        "team_work_review",
        "team_close",
        "team_worktree_integrate",
        "task_propose_start",
        "task_propose_plan",
        "task_report_reflection",
        "task_report_blocker",
        "task_propose_completion",
        "task_accept_admission",
        "task_accept_plan",
        "task_confirm_completion",
    }
)


class SkillExecutionError(RuntimeError):
    """Raised when a declarative Skill plan is malformed or not admissible."""


@dataclass(frozen=True)
class SkillExecutionStep:
    tool_name: str
    arguments: Mapping[str, Any]
    approval_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.tool_name, str) or _NAME.fullmatch(self.tool_name) is None:
            raise ValueError("Skill execution tool name is invalid")
        if self.tool_name not in ORDINARY_TOOL_NAMES:
            raise ValueError("Skill execution tool is not in the ordinary ToolSet")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("Skill execution arguments are invalid")
        if type(self.approval_required) is not bool:
            raise ValueError("Skill execution approval flag is invalid")
        encoded = _canonical_json(self.arguments)
        if len(encoded) > MAX_EXECUTION_ARGUMENT_BYTES:
            raise ValueError("Skill execution arguments exceed the byte limit")


@dataclass(frozen=True)
class SkillExecutionPlan:
    steps: tuple[SkillExecutionStep, ...]
    allow_dangerous: bool = False
    version: int = EXECUTION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.version != EXECUTION_PLAN_SCHEMA_VERSION:
            raise ValueError("unsupported Skill execution plan version")
        if not isinstance(self.steps, tuple) or not 1 <= len(self.steps) <= MAX_EXECUTION_STEPS:
            raise ValueError("Skill execution step count is invalid")
        if any(not isinstance(step, SkillExecutionStep) for step in self.steps):
            raise ValueError("Skill execution plan contains an invalid step")
        if type(self.allow_dangerous) is not bool:
            raise ValueError("Skill execution danger flag is invalid")

    @classmethod
    def from_mapping(cls, value: object) -> "SkillExecutionPlan":
        if not isinstance(value, dict):
            raise SkillExecutionError("Skill execution plan must be an object")
        if value.get("schema_version") != EXECUTION_PLAN_SCHEMA_VERSION:
            raise SkillExecutionError("Skill execution plan schema is unsupported")
        raw_steps = value.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_EXECUTION_STEPS:
            raise SkillExecutionError("Skill execution plan steps are invalid")
        steps: list[SkillExecutionStep] = []
        for raw in raw_steps:
            if not isinstance(raw, dict) or set(raw) - {"tool", "arguments", "approval_required"}:
                raise SkillExecutionError("Skill execution step shape is invalid")
            try:
                steps.append(
                    SkillExecutionStep(
                        tool_name=raw["tool"],
                        arguments=raw.get("arguments", {}),
                        approval_required=raw.get("approval_required", False),
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise SkillExecutionError("Skill execution step is invalid") from error
        try:
            return cls(tuple(steps), allow_dangerous=value.get("allow_dangerous", False))
        except ValueError as error:
            raise SkillExecutionError(str(error)) from error

    @classmethod
    def load(cls, path: Path) -> "SkillExecutionPlan":
        candidate = Path(path)
        if candidate.is_symlink() or not candidate.is_file():
            raise SkillExecutionError("Skill execution plan is not a regular file")
        try:
            raw = candidate.read_bytes()
        except OSError as error:
            raise SkillExecutionError("Skill execution plan could not be read") from error
        if len(raw) > MAX_EXECUTION_PLAN_BYTES:
            raise SkillExecutionError("Skill execution plan exceeds the byte limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SkillExecutionError("Skill execution plan is not valid UTF-8 JSON") from error
        return cls.from_mapping(value)

    def as_mapping(self) -> dict[str, object]:
        return {
            "allow_dangerous": self.allow_dangerous,
            "schema_version": self.version,
            "steps": [
                {
                    "approval_required": step.approval_required,
                    "arguments": dict(step.arguments),
                    "tool": step.tool_name,
                }
                for step in self.steps
            ],
        }


@dataclass(frozen=True)
class SkillExecutionResult:
    skill_name: str
    executed_steps: int
    stopped_at: int | None
    result_codes: tuple[str, ...]
    denied: bool = False


class ExecutableSkillRunner:
    """Validate and dispatch one Skill plan through Host-provided callbacks."""

    def __init__(
        self,
        *,
        dispatch: Callable[[str, ToolArguments], object],
        approve: Callable[[str, Mapping[str, Any]], bool] | None = None,
        max_steps: int = MAX_EXECUTION_STEPS,
    ) -> None:
        if not callable(dispatch):
            raise ValueError("Skill dispatch callback is required")
        if approve is not None and not callable(approve):
            raise ValueError("Skill approval callback is invalid")
        if type(max_steps) is not int or not 1 <= max_steps <= MAX_EXECUTION_STEPS:
            raise ValueError("Skill execution step limit is invalid")
        self.dispatch = dispatch
        self.approve = approve
        self.max_steps = max_steps

    def execute(
        self,
        candidate: SkillCandidate,
        plan: SkillExecutionPlan,
        *,
        inputs: Mapping[str, Any] | None = None,
    ) -> SkillExecutionResult:
        if not isinstance(candidate, SkillCandidate) or not candidate.active:
            raise SkillExecutionError("Skill candidate is not active")
        if not isinstance(plan, SkillExecutionPlan):
            raise SkillExecutionError("Skill execution plan is invalid")
        if len(plan.steps) > self.max_steps:
            raise SkillExecutionError("Skill execution plan exceeds the runner limit")
        variables = {} if inputs is None else dict(inputs)
        allowed = (
            set(candidate.manifest.allowed_tools)
            if candidate.manifest.allowed_tools is not None
            else set(ORDINARY_TOOL_NAMES)
        )
        results: list[str] = []
        for index, step in enumerate(plan.steps):
            if step.tool_name not in allowed:
                raise SkillExecutionError(
                    f"Skill step tool is outside the manifest allowlist: {step.tool_name}"
                )
            if step.tool_name in _FORBIDDEN_DEFAULT_TOOLS and not plan.allow_dangerous:
                raise SkillExecutionError(
                    f"Skill step requires explicit dangerous execution: {step.tool_name}"
                )
            arguments = _resolve_inputs(step.arguments, variables)
            if step.approval_required or step.tool_name in _FORBIDDEN_DEFAULT_TOOLS:
                if self.approve is None or not self.approve(step.tool_name, arguments):
                    return SkillExecutionResult(
                        candidate.manifest.name, index, index, tuple(results), True
                    )
            try:
                result = self.dispatch(step.tool_name, ToolArguments.from_mapping(arguments))
            except Exception as error:
                code = f"dispatch_error:{type(error).__name__}"
                results.append(code)
                return SkillExecutionResult(
                    candidate.manifest.name, index + 1, index, tuple(results)
                )
            code = getattr(result, "code", None)
            results.append(str(code) if code else "ok")
            if getattr(result, "is_error", False):
                return SkillExecutionResult(
                    candidate.manifest.name, index + 1, index, tuple(results)
                )
        return SkillExecutionResult(candidate.manifest.name, len(plan.steps), None, tuple(results))


def load_skill_execution_plan(package_root: Path, candidate: SkillCandidate) -> SkillExecutionPlan:
    """Load an exact sidecar plan after verifying package identity and path shape."""
    if (
        not isinstance(candidate, SkillCandidate)
        or candidate.relative_path != f"{candidate.manifest.name}/SKILL.md"
    ):
        raise SkillExecutionError("Skill candidate identity is invalid")
    root = Path(package_root).resolve(strict=True)
    package = root / candidate.manifest.name
    if package.is_symlink() or not package.is_dir():
        raise SkillExecutionError("Skill package directory is invalid")
    return SkillExecutionPlan.load(package / "EXECUTION.json")


def _resolve_inputs(value: object, inputs: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SkillExecutionError("Skill step arguments must be an object")
    resolved: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 128:
            raise SkillExecutionError("Skill argument key is invalid")
        if isinstance(item, str) and item.startswith("$input."):
            name = item[7:]
            if not name or name not in inputs:
                raise SkillExecutionError("Skill input variable is missing")
            item = inputs[name]
        elif isinstance(item, Mapping):
            item = _resolve_inputs(item, inputs)
        elif isinstance(item, list):
            item = [
                _resolve_inputs(entry, inputs) if isinstance(entry, Mapping) else entry
                for entry in item
            ]
        resolved[key] = item
    _canonical_json(resolved)
    return resolved


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SkillExecutionError("Skill execution arguments must be JSON values") from error


__all__ = [
    "EXECUTION_PLAN_SCHEMA_VERSION",
    "ExecutableSkillRunner",
    "SkillExecutionError",
    "SkillExecutionPlan",
    "SkillExecutionResult",
    "SkillExecutionStep",
    "load_skill_execution_plan",
]
