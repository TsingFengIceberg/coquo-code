from __future__ import annotations

import pytest

from leonervis_code.mcp.client import McpClientError
from leonervis_code.mcp.config import McpServerConfiguration, McpServerEntry
from leonervis_code.mcp.reverse import (
    McpElicitationResponse,
    McpReverseRequestCoordinator,
    McpSamplingResponse,
)


ENTRY = McpServerEntry(
    "project",
    McpServerConfiguration(name="local", command="/bin/true", enabled=True),
)


def test_sampling_requires_explicit_authorization_and_callback():
    params = {
        "messages": [{"role": "user", "content": {"type": "text", "text": "summarize"}}],
        "maxTokens": 32,
    }
    with pytest.raises(McpClientError, match="not explicitly authorized"):
        McpReverseRequestCoordinator().handle(ENTRY, "sampling/createMessage", params)

    coordinator = McpReverseRequestCoordinator(
        authorize_sampling=lambda request: request.max_tokens == 32,
        sample=lambda request: McpSamplingResponse("done", "review-model"),
    )
    result = coordinator.handle(ENTRY, "sampling/createMessage", params)
    assert result["content"]["text"] == "done"
    assert result["model"] == "review-model"


def test_elicitation_validates_user_response_against_closed_schema():
    coordinator = McpReverseRequestCoordinator(
        elicit=lambda request: McpElicitationResponse("accept", {"confirm": True})
    )
    result = coordinator.handle(
        ENTRY,
        "elicitation/create",
        {
            "message": "Continue?",
            "requestedSchema": {
                "type": "object",
                "properties": {"confirm": {"type": "boolean"}},
                "required": ["confirm"],
                "additionalProperties": False,
            },
        },
    )
    assert result == {"action": "accept", "content": {"confirm": True}}


def test_elicitation_without_ui_handler_is_denied():
    with pytest.raises(McpClientError, match="no explicit user interaction handler"):
        McpReverseRequestCoordinator().handle(
            ENTRY,
            "elicitation/create",
            {
                "message": "Continue?",
                "requestedSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        )
