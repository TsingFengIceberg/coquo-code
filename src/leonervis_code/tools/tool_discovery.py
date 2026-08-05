"""Fixed model-visible surface for bounded deferred-tool discovery."""

from __future__ import annotations

from leonervis_code.core.effective_context import CanonicalToolDefinition


TOOL_SEARCH_TOOL_NAME = "tool_search"
TOOL_PROMOTE_TOOL_NAME = "tool_promote"
TOOL_DISCOVERY_TOOL_NAMES = (TOOL_SEARCH_TOOL_NAME, TOOL_PROMOTE_TOOL_NAME)
MIN_TOOL_SEARCH_RESULTS = 1
MAX_TOOL_SEARCH_RESULTS = 8
MAX_TOOL_SEARCH_QUERY_CHARACTERS = 256
MAX_TOOL_PROMOTIONS = 8


def tool_search_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TOOL_SEARCH_TOOL_NAME,
            "description": (
                "Search the current Turn's frozen deferred-tool catalog by literal terms. "
                "This only returns bounded candidates and does not expose or execute them."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_TOOL_SEARCH_QUERY_CHARACTERS,
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": MIN_TOOL_SEARCH_RESULTS,
                        "maximum": MAX_TOOL_SEARCH_RESULTS,
                    },
                },
                "required": ["query", "max_results"],
                "additionalProperties": False,
            },
        }
    )


def tool_promote_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": TOOL_PROMOTE_TOOL_NAME,
            "description": (
                "Explicitly promote exact names returned by tool_search into the next immutable "
                "ToolSet epoch. Promotion does not execute the selected tools."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 64},
                        "minItems": 1,
                        "maxItems": MAX_TOOL_PROMOTIONS,
                    }
                },
                "required": ["names"],
                "additionalProperties": False,
            },
        }
    )


def tool_discovery_snapshots() -> tuple[CanonicalToolDefinition, ...]:
    return (tool_search_snapshot(), tool_promote_snapshot())
