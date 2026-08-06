"""Deterministic replay and budgets for Skills retained in Effective Context."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from leonervis_code.core.contracts import ConversationItem, ToolResult, ToolUse
from leonervis_code.tools.skill_discovery import SKILL_LOAD_TOOL_NAME


MAX_ACTIVE_SKILLS = 4
MAX_SKILL_LOADS_PER_TURN = 4
MAX_ACTIVE_SKILL_INSTRUCTION_BYTES = 64 * 1024
_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_FINGERPRINT = re.compile(r"skill-v1-[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ActiveSkill:
    """One exact successful Skill load still retained in causal history."""

    name: str
    fingerprint: str
    source: str
    instruction_bytes: int
    resource_count: int
    allowed_tools: tuple[str, ...] | None

    def __post_init__(self) -> None:
        if _NAME.fullmatch(self.name) is None or _FINGERPRINT.fullmatch(self.fingerprint) is None:
            raise ValueError("active Skill identity is invalid")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("active Skill source is invalid")
        if type(self.instruction_bytes) is not int or self.instruction_bytes < 1:
            raise ValueError("active Skill instruction size is invalid")
        if type(self.resource_count) is not int or self.resource_count < 0:
            raise ValueError("active Skill resource count is invalid")
        if self.allowed_tools is not None and (
            not isinstance(self.allowed_tools, tuple)
            or len(set(self.allowed_tools)) != len(self.allowed_tools)
            or any(not isinstance(name, str) or not name for name in self.allowed_tools)
        ):
            raise ValueError("active Skill allowed tools are invalid")


@dataclass(frozen=True)
class SkillActivationInspection:
    """Read-only projection of current Skill activation and action restrictions."""

    inventory_id: str
    active: tuple[ActiveSkill, ...]
    action_tools: tuple[str, ...]
    max_active: int = MAX_ACTIVE_SKILLS
    max_loads_per_turn: int = MAX_SKILL_LOADS_PER_TURN
    max_instruction_bytes: int = MAX_ACTIVE_SKILL_INSTRUCTION_BYTES

    @property
    def instruction_bytes(self) -> int:
        return sum(skill.instruction_bytes for skill in self.active)


def active_skills_from_history(history: tuple[ConversationItem, ...]) -> tuple[ActiveSkill, ...]:
    """Replay complete successful Host Skill loads in causal order, deduplicated exactly."""
    active: list[ActiveSkill] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(history[:-1]):
        if not isinstance(item, ToolUse) or item.name != SKILL_LOAD_TOOL_NAME:
            continue
        result = history[index + 1]
        if (
            not isinstance(result, ToolResult)
            or result.tool_use_id != item.tool_use_id
            or result.is_error
        ):
            continue
        parsed = _parse_loaded_skill(result.content)
        if parsed is None:
            continue
        identity = (parsed.name, parsed.fingerprint)
        if identity not in seen:
            active.append(parsed)
            seen.add(identity)
    return tuple(active)


def _parse_loaded_skill(content: str) -> ActiveSkill | None:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != "skill_loaded":
        return None
    name = payload.get("name")
    fingerprint = payload.get("fingerprint")
    source = payload.get("source")
    instructions = payload.get("instructions")
    allowed = payload.get("allowed_tools")
    resources = payload.get("resources", [])
    if (
        not isinstance(name, str)
        or not isinstance(fingerprint, str)
        or not isinstance(source, str)
        or not isinstance(instructions, str)
        or not instructions
        or not isinstance(resources, list)
    ):
        return None
    if allowed is not None and (
        not isinstance(allowed, list) or not all(isinstance(value, str) for value in allowed)
    ):
        return None
    try:
        return ActiveSkill(
            name=name,
            fingerprint=fingerprint,
            source=source,
            instruction_bytes=len(instructions.encode("utf-8")),
            resource_count=len(resources),
            allowed_tools=None if allowed is None else tuple(allowed),
        )
    except ValueError:
        return None
