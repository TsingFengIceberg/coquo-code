from __future__ import annotations

import json

import pytest

from coquo.core.contracts import AssistantText, ToolArguments, ToolUse
from coquo.core.session_title import (
    SESSION_TITLE_MAX_OUTPUT_TOKENS,
    SESSION_TITLE_SOURCE_MAX_BYTES,
    SessionTitleCandidateError,
    build_session_title_request,
    fallback_session_title,
    numbered_session_title,
    parse_session_title_response,
)


def test_title_request_is_bounded_versioned_and_exposes_no_tools() -> None:
    request = build_session_title_request("修复 provider adapter", rejected_titles=("旧标题",))
    conversation = request.conversation_request
    payload = json.loads(conversation.history[0].text)

    assert request.max_output_tokens == SESSION_TITLE_MAX_OUTPUT_TOKENS == 512
    assert conversation.allow_tools is False
    assert conversation.effective_summary is None
    assert payload == {
        "first_user_message": "修复 provider adapter",
        "rejected_titles": ["旧标题"],
    }
    assert "untrusted JSON payload" in conversation.system_prompt.text


def test_title_request_truncates_source_on_a_utf8_boundary() -> None:
    request = build_session_title_request("界" * 2000)

    assert len(request.source_text.encode("utf-8")) <= SESSION_TITLE_SOURCE_MAX_BYTES
    assert request.source_text


@pytest.mark.parametrize(
    "response",
    [
        AssistantText(""),
        AssistantText("line one\nline two"),
        AssistantText("# Markdown title"),
        AssistantText('"Quoted title"'),
        AssistantText("Title: Adapter review"),
        AssistantText("Terminal punctuation."),
        ToolUse("tool-1", "read_file", ToolArguments.from_mapping({"path": "README.md"})),
    ],
)
def test_title_response_rejects_non_plain_candidates(response) -> None:
    with pytest.raises(SessionTitleCandidateError):
        parse_session_title_response(response)


def test_fallback_and_collision_suffix_remain_inside_generated_bounds() -> None:
    fallback = fallback_session_title("界" * 100)
    numbered = numbered_session_title(fallback, 123)

    assert fallback.endswith("...")
    assert numbered.endswith(" (123)")
    assert len(numbered) <= 48
    assert len(numbered.encode("utf-8")) <= 160
