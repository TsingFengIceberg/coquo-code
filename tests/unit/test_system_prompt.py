from __future__ import annotations

import pytest

from leonervis_code.system_prompt import (
    SYSTEM_PROMPT_VERSION,
    _fingerprint_prompt,
    _render_sections,
    build_system_prompt,
)

EXPECTED_FINGERPRINT = "v42-f7adf27d5504adf0cfd7de2475b3489506b9252fc1a584993194d4ec8c19d74f"


def test_canonical_system_prompt_has_reviewed_version_text_and_fingerprint() -> None:
    prompt = build_system_prompt()

    assert prompt.version == SYSTEM_PROMPT_VERSION == 42
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
    assert "additionally contains the independent `web_search` Host tool" in prompt.text
    assert "Provider-native search and the Host `web_search` tool are distinct" in prompt.text
    assert "enables it by default" in prompt.text
    assert "Brave and Tavily are disabled when a Session starts" in prompt.text
    assert "explicit fallback after Provider-native search" in prompt.text
    assert "Host never invents or extracts a fallback query" in prompt.text
    assert "requires `danger-full-access`" in prompt.text
    assert "Tavily basic search consumes one API credit" in prompt.text
    assert "do not retry it automatically" in prompt.text
    assert "`web_fetch`, `compare_files`, `git_blame`, `git_refs`, `json_query`" in prompt.text
    assert "Use `web_search` to discover candidate URLs and `web_fetch`" in prompt.text
    assert "one `network-write` action" in prompt.text
    assert "fixed catalog additionally contains `tool_search` and `tool_promote`" in prompt.text
    assert "exact names returned by an earlier successful `tool_search`" in prompt.text
    assert "a `dangerous` Host action by default" in prompt.text
    assert "exact user-configured local policy" in prompt.text
    assert "every remote Streamable HTTP contract remains `dangerous`" in prompt.text
    assert "Host-owned bearer or OAuth credentials that are never model-visible" in prompt.text
    assert "MCP Prompts are non-authoritative templates" in prompt.text
    assert "workspace Root URI only after explicit Host configuration" in prompt.text
    assert "without one it is denied" in prompt.text
    assert "does not mutate the active ToolSet" in prompt.text
    assert "must not be retried automatically" in prompt.text
    assert (
        "additionally contains `skill_search`, `skill_load`, and `skill_read_resource`"
        in prompt.text
    )
    assert "At most four distinct Skills and 65536 cumulative instruction bytes" in prompt.text
    assert "Binary resources cannot enter model context" in prompt.text
    assert "compact summary that merely mentions the Skill does not reactivate it" in prompt.text
    assert "can only intersect action tools already present" in prompt.text
    assert "never extracts entries" in prompt.text
    assert "abbreviated or arbitrary revisions" in prompt.text
    assert "does not support linked-worktree `.git` pointer files" in prompt.text
    assert "mounts only the workspace read-write" in prompt.text
    assert "denies network socket creation" in prompt.text
    assert "returns `command_sandbox_unavailable` without starting" in prompt.text
    assert "there is no rollback" in prompt.text
    assert "workspace-root `AGENTS.md`" in prompt.text
    assert "current direct user request" in prompt.text
    assert "cannot grant permissions" in prompt.text
    assert "[Leonervis durable Task Stage]" in prompt.text
    assert "`task_propose_plan`" in prompt.text
    assert "`task_report_reflection`" in prompt.text
    assert "`task_report_blocker`" in prompt.text
    assert "`task_propose_completion`" in prompt.text
    assert "`task_propose_start`" in prompt.text
    assert "`task_accept_admission`" in prompt.text
    assert "`task_accept_plan`" in prompt.text
    assert "`task_confirm_completion`" in prompt.text
    assert "current user explicitly accepts" in prompt.text
    assert "complete ordinary Session Turn must commit" in prompt.text
    assert "without a slash command" in prompt.text
    assert "cannot satisfy Host-check or independent-reviewer criteria" in prompt.text
    assert "A pending proposal is not a Task, permission, approval, or execution" in prompt.text
    assert "execution or correction Stage" in prompt.text
    assert "driver remains bounded and foreground-only" in prompt.text
    assert "completion proposal" in prompt.text
    assert "cannot verify any criterion" in prompt.text
    assert "deterministic read-only Host checks" in prompt.text
    assert "independent no-tools review" in prompt.text
    assert "Host completes a Task only after a current completion proposal" in prompt.text
    assert "Do not request the four Stage coordination tools" in prompt.text
    assert "Declarative Host Hooks" in prompt.text
    assert "A Hook can never grant authority" in prompt.text
    assert "each evaluation records the exact Hook snapshot it used" in prompt.text
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
