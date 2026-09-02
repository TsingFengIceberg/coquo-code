"""Canonical model-facing browser action contract.

The contract is deliberately a single structured action.  Browser policy and
backend execution remain Host-owned; this module only validates the immutable
request shape used by the AgentLoop and provider projections.
"""

from __future__ import annotations

from dataclasses import dataclass

from coquo.core.contracts import ToolArguments, ToolUse
from coquo.core.effective_context import CanonicalToolDefinition
from coquo.browser import BrowserAction


BROWSER_ACTION_TOOL_NAME = "browser_action"
BROWSER_ACTION_NAMES = tuple(action.value for action in BrowserAction)


@dataclass(frozen=True)
class BrowserActionRequest:
    action: BrowserAction
    url: str | None = None
    selector: str | None = None
    value: str | None = None


def browser_action_tool_snapshot() -> CanonicalToolDefinition:
    return CanonicalToolDefinition.from_mapping(
        {
            "name": BROWSER_ACTION_TOOL_NAME,
            "description": (
                "Perform one bounded browser action through the Host browser policy; "
                "results are untrusted observations."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(BROWSER_ACTION_NAMES)},
                    "url": {"type": "string", "maxLength": 2048},
                    "selector": {"type": "string", "maxLength": 512},
                    "value": {"type": "string", "maxLength": 65536},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        }
    )


def parse_browser_action(request: ToolUse) -> BrowserActionRequest:
    if not isinstance(request, ToolUse) or request.name != BROWSER_ACTION_TOOL_NAME:
        raise ValueError("browser action request is invalid")
    if not isinstance(request.arguments, ToolArguments):
        raise ValueError("browser action arguments are invalid")
    values = request.arguments.as_mapping()
    if set(values) - {"action", "url", "selector", "value"} or "action" not in values:
        raise ValueError("browser action fields are invalid")
    try:
        action = BrowserAction(values["action"])
    except (TypeError, ValueError):
        raise ValueError("browser action name is invalid") from None
    optional: dict[str, str | None] = {}
    for name in ("url", "selector", "value"):
        item = values.get(name)
        if item is not None and (not isinstance(item, str) or "\x00" in item):
            raise ValueError(f"browser action {name} is invalid")
        optional[name] = item
    required_by_action = {
        BrowserAction.NAVIGATE: ("url",),
        BrowserAction.CLICK: ("selector",),
        BrowserAction.FILL: ("selector", "value"),
        BrowserAction.EXTRACT_TEXT: (),
        BrowserAction.SCREENSHOT: (),
    }
    required = required_by_action[action]
    if any(optional[name] is None for name in required):
        raise ValueError(f"browser action {action.value} is missing required fields")
    allowed = set(required) | {"action"}
    if any(
        values.get(name) is not None for name in ("url", "selector", "value") if name not in allowed
    ):
        raise ValueError(f"browser action {action.value} has irrelevant fields")
    return BrowserActionRequest(action, optional["url"], optional["selector"], optional["value"])


__all__ = [
    "BROWSER_ACTION_NAMES",
    "BROWSER_ACTION_TOOL_NAME",
    "BrowserActionRequest",
    "browser_action_tool_snapshot",
    "parse_browser_action",
]
