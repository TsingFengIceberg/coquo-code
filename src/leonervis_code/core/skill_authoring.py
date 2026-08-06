"""Commit-coupled model proposals for explicit Skill authoring."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import TypeAlias

from leonervis_code.core.contracts import ToolUse
from leonervis_code.skills.catalog import (
    MAX_SKILL_ALLOWED_TOOLS,
    MAX_SKILL_DESCRIPTION_CHARS,
    MAX_SKILL_FILE_BYTES,
    canonical_skill_name,
)


SKILL_PROPOSE_CREATE_TOOL_NAME = "skill_propose_create"
SKILL_ACCEPT_CREATE_TOOL_NAME = "skill_accept_create"
SKILL_AUTHORING_CONTROL_TOOL_NAMES = (
    SKILL_PROPOSE_CREATE_TOOL_NAME,
    SKILL_ACCEPT_CREATE_TOOL_NAME,
)
SKILL_CANDIDATE_ID_PATTERN = re.compile(r"skc-v1-[0-9a-f]{64}\Z")
_CONTEXT_ID = re.compile(r"ctx-v[1-9][0-9]*-[0-9a-f]{64}\Z")


class SkillAuthoringScope(StrEnum):
    """Scopes that a model may explicitly propose for the current workspace."""

    WORKSPACE = "workspace"
    PROJECT = "project"


@dataclass(frozen=True)
class SkillCreationProposal:
    """One immutable Skill draft bound to the exact proposing Turn context."""

    name: str
    description: str
    scope: SkillAuthoringScope
    allowed_tools: tuple[str, ...] | None
    instructions: str
    context_id: str
    tool_use_id: str

    def __post_init__(self) -> None:
        canonical_skill_name(self.name)
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or len(self.description) > MAX_SKILL_DESCRIPTION_CHARS
            or any(character in self.description for character in "\x00\r")
        ):
            raise ValueError("Skill proposal description is invalid")
        if type(self.scope) is not SkillAuthoringScope:
            raise ValueError("Skill proposal scope is invalid")
        if self.allowed_tools is not None and (
            not isinstance(self.allowed_tools, tuple)
            or len(self.allowed_tools) > MAX_SKILL_ALLOWED_TOOLS
            or len(set(self.allowed_tools)) != len(self.allowed_tools)
            or any(
                re.fullmatch(r"[a-z][a-z0-9_]{0,127}", name) is None for name in self.allowed_tools
            )
        ):
            raise ValueError("Skill proposal allowed tools are invalid")
        if (
            not isinstance(self.instructions, str)
            or not self.instructions.strip()
            or len(self.instructions.encode("utf-8")) > MAX_SKILL_FILE_BYTES
            or any(character in self.instructions for character in "\x00\r")
        ):
            raise ValueError("Skill proposal instructions are invalid")
        if not isinstance(self.context_id, str) or _CONTEXT_ID.fullmatch(self.context_id) is None:
            raise ValueError("Skill proposal context identity is invalid")
        if not isinstance(self.tool_use_id, str) or not self.tool_use_id:
            raise ValueError("Skill proposal tool-use ID is invalid")

    @classmethod
    def from_request(cls, request: ToolUse, context_id: str) -> SkillCreationProposal:
        if type(request) is not ToolUse or request.name != SKILL_PROPOSE_CREATE_TOOL_NAME:
            raise ValueError("Skill proposal request is invalid")
        values = request.arguments.as_mapping()
        if set(values) != {
            "allowed_tools",
            "description",
            "instructions",
            "name",
            "scope",
        }:
            raise ValueError("Skill proposal arguments are invalid")
        allowed = values["allowed_tools"]
        if allowed is not None and not isinstance(allowed, list):
            raise ValueError("Skill proposal allowed tools are invalid")
        try:
            scope = SkillAuthoringScope(values["scope"])
        except (TypeError, ValueError):
            raise ValueError("Skill proposal scope is invalid") from None
        return cls(
            name=values["name"],
            description=values["description"],
            scope=scope,
            allowed_tools=None if allowed is None else tuple(allowed),
            instructions=values["instructions"],
            context_id=context_id,
            tool_use_id=request.tool_use_id,
        )

    @property
    def candidate_id(self) -> str:
        instructions = self.instructions
        if not instructions.endswith("\n"):
            instructions += "\n"
        payload = json.dumps(
            {
                "allowed_tools": self.allowed_tools,
                "context_id": self.context_id,
                "description": self.description,
                "instructions": instructions,
                "name": self.name,
                "scope": self.scope.value,
                "tool_use_id": self.tool_use_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(b"leonervis-skill-candidate-v1\0" + payload).hexdigest()
        return f"skc-v1-{digest}"

    @property
    def skill_file(self) -> bytes:
        allowed = ""
        if self.allowed_tools is not None:
            allowed = "allowed-tools:\n" + "".join(f"  - {name}\n" for name in self.allowed_tools)
        body = self.instructions
        if not body.endswith("\n"):
            body += "\n"
        return (
            "---\n"
            "manifest-version: 1\n"
            f"name: {self.name}\n"
            f"description: {json.dumps(self.description, ensure_ascii=False)}\n"
            f"{allowed}"
            "---\n"
            f"{body}"
        ).encode("utf-8")


@dataclass(frozen=True)
class SkillInstallRequest:
    """One exact pending-candidate acceptance requested from direct user language."""

    candidate_id: str
    context_id: str
    tool_use_id: str
    expected_fingerprint: str

    def __post_init__(self) -> None:
        canonical_skill_candidate_id(self.candidate_id)
        if not isinstance(self.context_id, str) or _CONTEXT_ID.fullmatch(self.context_id) is None:
            raise ValueError("Skill install context identity is invalid")
        if not isinstance(self.tool_use_id, str) or not self.tool_use_id:
            raise ValueError("Skill install tool-use ID is invalid")
        if re.fullmatch(r"skill-v1-[0-9a-f]{64}", self.expected_fingerprint) is None:
            raise ValueError("Skill install expected fingerprint is invalid")

    @classmethod
    def from_request(
        cls,
        request: ToolUse,
        context_id: str,
        *,
        expected_fingerprint: str,
    ) -> SkillInstallRequest:
        if type(request) is not ToolUse or request.name != SKILL_ACCEPT_CREATE_TOOL_NAME:
            raise ValueError("Skill install request is invalid")
        values = request.arguments.as_mapping()
        if set(values) != {"candidate_id"}:
            raise ValueError("Skill install arguments are invalid")
        return cls(
            candidate_id=values["candidate_id"],
            context_id=context_id,
            tool_use_id=request.tool_use_id,
            expected_fingerprint=expected_fingerprint,
        )


SkillAuthoringControl: TypeAlias = SkillCreationProposal | SkillInstallRequest


def canonical_skill_candidate_id(value: str) -> str:
    if not isinstance(value, str) or SKILL_CANDIDATE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("Skill candidate ID is invalid")
    return value


def skill_proposal_receipt(proposal: SkillCreationProposal) -> str:
    return json.dumps(
        {
            "candidate_id": proposal.candidate_id,
            "name": proposal.name,
            "scope": proposal.scope.value,
            "status": "pending_turn_commit",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
