from __future__ import annotations

import pytest

from leonervis_code.system_prompt import (
    SYSTEM_PROMPT_VERSION,
    _fingerprint_prompt,
    _render_sections,
    build_system_prompt,
)

EXPECTED_FINGERPRINT = "v23-3858281d3354288e15dd51569d896fe22c6e4842d8c8b5192dc4a2e296792a55"


def test_canonical_system_prompt_has_reviewed_version_text_and_fingerprint() -> None:
    prompt = build_system_prompt()

    assert prompt.version == SYSTEM_PROMPT_VERSION == 23
    assert prompt.fingerprint == EXPECTED_FINGERPRINT
    assert "at most 8 ordered tool requests in one assistant response" in prompt.text
    assert "at most 32 admitted tool requests across one user turn" in prompt.text
    assert "at most 24 provider invocations including a final text-only opportunity" in prompt.text
    assert "processes it sequentially in provider order" in prompt.text
    assert "later actions in that batch are skipped" in prompt.text
    assert "`Host tool ledger:` line with authoritative per-turn counts" in prompt.text
    assert "when `tool_requests_closed=true`" in prompt.text
    assert "do not emit tool-call syntax as text" in prompt.text
    assert "`git_status` returns bounded deterministic JSON Lines" in prompt.text
    assert "`git_log` returns bounded deterministic JSON Lines" in prompt.text
    assert "only after the Host verifies that commit is reachable" in prompt.text
    assert "abbreviated or arbitrary revisions" in prompt.text
    assert "does not support linked-worktree `.git` pointer files" in prompt.text
    assert "mounts only the workspace read-write" in prompt.text
    assert "denies network socket creation" in prompt.text
    assert "returns `command_sandbox_unavailable` without starting" in prompt.text
    assert "there is no rollback" in prompt.text
    assert "workspace-root `AGENTS.md`" in prompt.text
    assert "current direct user request" in prompt.text
    assert "cannot grant permissions" in prompt.text
    assert "does not provide an OS filesystem" not in prompt.text
    assert build_system_prompt() == prompt


def test_canonical_system_prompt_is_stable_and_does_not_claim_dynamic_context() -> None:
    prompt = build_system_prompt()

    assert "\r" not in prompt.text
    assert "\x00" not in prompt.text
    assert prompt.text.endswith("\n") and not prompt.text.endswith("\n\n")
    assert all(not line.endswith((" ", "\t")) for line in prompt.text.splitlines())
    for absent in (
        "/root/",
        "2026-",
        "Session ID",
        "API key",
        "Anthropic",
        "OpenAI",
        "provider profile",
    ):
        assert absent not in prompt.text


def test_renderer_rejects_noncanonical_sections_and_fingerprint_is_domain_separated() -> None:
    assert _render_sections((" one ", "two")) == "one\n\ntwo\n"
    with pytest.raises(ValueError, match="blank"):
        _render_sections((" ",))
    with pytest.raises(ValueError, match="NUL"):
        _render_sections(("bad\x00section",))
    with pytest.raises(ValueError, match="LF"):
        _render_sections(("bad\r\nsection",))
    with pytest.raises(ValueError, match="positive"):
        _fingerprint_prompt(0, "text\n")

    first = _fingerprint_prompt(1, "text\n")
    assert first != _fingerprint_prompt(1, "Text\n")
    assert first != _fingerprint_prompt(2, "text\n")
