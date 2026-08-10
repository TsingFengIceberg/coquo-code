"""Model-visible schemas for explicit, commit-coupled Skill authoring."""

from __future__ import annotations

from coquo.core.effective_context import CanonicalToolDefinition
from coquo.core.skill_authoring import (
    SKILL_ACCEPT_CREATE_TOOL_NAME,
    SKILL_PROPOSE_CREATE_TOOL_NAME,
)


def skill_propose_create_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": SKILL_PROPOSE_CREATE_TOOL_NAME,
            "description": (
                "Propose one new declarative Skill only when the current user explicitly asks "
                "to preserve a reusable workflow. This creates an inactive candidate after the "
                "Turn commits; it does not install, activate, authorize, or execute the Skill."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,63}$"},
                    "description": {"type": "string", "minLength": 1, "maxLength": 512},
                    "scope": {"type": "string", "enum": ["workspace", "project"]},
                    "allowed_tools": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "pattern": "^[a-z][a-z0-9_]{0,127}$",
                                },
                                "maxItems": 64,
                                "uniqueItems": True,
                            },
                        ]
                    },
                    "instructions": {"type": "string", "minLength": 1, "maxLength": 32768},
                },
                "required": [
                    "name",
                    "description",
                    "scope",
                    "allowed_tools",
                    "instructions",
                ],
                "additionalProperties": False,
            },
        }
    )


def skill_accept_create_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": SKILL_ACCEPT_CREATE_TOOL_NAME,
            "description": (
                "Accept and install one exact pending generated Skill candidate only when the "
                "current user explicitly approves that candidate. Installation occurs only after "
                "this Turn commits and remains subject to stale, scope, conflict, and canonical "
                "package checks."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "candidate_id": {
                        "type": "string",
                        "pattern": "^skc-v1-[0-9a-f]{64}$",
                    }
                },
                "required": ["candidate_id"],
                "additionalProperties": False,
            },
        }
    )


def skill_authoring_tool_snapshots() -> tuple[CanonicalToolDefinition, ...]:
    return (skill_propose_create_tool_snapshot(), skill_accept_create_tool_snapshot())
