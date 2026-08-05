from __future__ import annotations

import pytest

from leonervis_code.mcp.capabilities import McpCapabilityClient
from leonervis_code.mcp.client import McpClientError, McpNotificationSummary


class Session:
    capability_names = ("prompts", "resources")

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, params, **kwargs):
        self.requests.append((method, params))
        return self.responses.pop(0), McpNotificationSummary()


def test_resources_list_and_read_are_bounded_untrusted_data():
    session = Session(
        [
            {
                "resources": [
                    {
                        "uri": "file:///guide.txt",
                        "name": "guide",
                        "mimeType": "text/plain",
                        "size": 2,
                    }
                ]
            },
            {"contents": [{"uri": "file:///guide.txt", "mimeType": "text/plain", "text": "ok"}]},
        ]
    )
    client = McpCapabilityClient(session)
    assert client.list_resources()[0].name == "guide"
    result = client.read_resource("file:///guide.txt")
    assert result.content.endswith("\nok")
    assert result.blocks == 1


def test_prompt_get_labels_roles_and_rejects_non_text_content():
    session = Session(
        [
            {
                "description": "template",
                "messages": [{"role": "user", "content": {"type": "text", "text": "review"}}],
            },
            {"messages": [{"role": "user", "content": {"type": "image", "data": "AA=="}}]},
        ]
    )
    client = McpCapabilityClient(session)
    assert client.get_prompt("review", {}).content == "[user] review"
    with pytest.raises(McpClientError, match="text content only|content type is unsupported"):
        client.get_prompt("image", {})


def test_capability_must_be_advertised():
    session = Session([])
    session.capability_names = ()
    with pytest.raises(McpClientError, match="does not advertise resources"):
        McpCapabilityClient(session).list_resources()
