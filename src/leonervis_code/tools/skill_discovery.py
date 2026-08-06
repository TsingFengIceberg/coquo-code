"""Fixed discovery tools for bounded declarative Skills."""

from leonervis_code.core.effective_context import CanonicalToolDefinition


SKILL_SEARCH_TOOL_NAME = "skill_search"
SKILL_LOAD_TOOL_NAME = "skill_load"
SKILL_READ_RESOURCE_TOOL_NAME = "skill_read_resource"
MAX_SKILL_SEARCH_RESULTS = 8


def skill_search_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": SKILL_SEARCH_TOOL_NAME,
            "description": (
                "Search only the immutable Skill inventory frozen for this Turn. Returns bounded "
                "metadata, not Skill instructions. The call must be isolated in its response."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 256},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SKILL_SEARCH_RESULTS,
                    },
                },
                "required": ["query", "max_results"],
            },
        }
    )


def skill_load_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": SKILL_LOAD_TOOL_NAME,
            "description": (
                "Load the complete bounded instructions for one exact Skill name and fingerprint "
                "returned by skill_search. The call must be isolated in its response."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "fingerprint": {
                        "type": "string",
                        "pattern": "^skill-v1-[0-9a-f]{64}$",
                    },
                },
                "required": ["name", "fingerprint"],
            },
        }
    )


def skill_read_resource_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": SKILL_READ_RESOURCE_TOOL_NAME,
            "description": (
                "Read one complete bounded UTF-8 resource from an active Skill using the exact "
                "Skill and resource fingerprints returned by skill_load. The call must be "
                "isolated in its response."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "skill_fingerprint": {
                        "type": "string",
                        "pattern": "^skill-v1-[0-9a-f]{64}$",
                    },
                    "path": {"type": "string", "minLength": 1, "maxLength": 256},
                    "resource_fingerprint": {
                        "type": "string",
                        "pattern": "^resource-v1-[0-9a-f]{64}$",
                    },
                },
                "required": [
                    "name",
                    "skill_fingerprint",
                    "path",
                    "resource_fingerprint",
                ],
            },
        }
    )


def skill_discovery_snapshots() -> tuple[CanonicalToolDefinition, ...]:
    return (skill_search_snapshot(), skill_load_snapshot(), skill_read_resource_snapshot())
