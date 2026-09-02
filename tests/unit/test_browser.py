from __future__ import annotations

import pytest

from coquo.browser import (
    BrowserAction,
    BrowserAutomation,
    BrowserAutomationError,
    BrowserPolicy,
)
from coquo.core.contracts import AssistantText, ToolArguments, ToolUse
from coquo.core.permissions import ApprovalMode, PermissionAction, PermissionMode
from coquo.providers.fake import ScriptedFakeProvider
from coquo.session import ProjectSession
from coquo.session_records import ActionAuditStatus
from coquo.tools.catalog import (
    BROWSER_ACTION_TOOL_NAME,
    TOOL_REGISTRY_SNAPSHOT,
    registry_snapshot_with_browser,
    tool_input_from_use,
    tool_use_from_input,
)
from coquo.tools.browser import browser_action_tool_snapshot, parse_browser_action


class FakeBrowser:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def navigate(self, url, _timeout):
        self.calls.append(("navigate", url))

    def click(self, selector, _timeout):
        self.calls.append(("click", selector))

    def fill(self, selector, value, _timeout):
        self.calls.append(("fill", (selector, value)))

    def extract_text(self, selector, _timeout):
        self.calls.append(("extract", selector))
        return "hello"

    def screenshot(self, _timeout):
        self.calls.append(("screenshot", None))
        return b"png"

    def close(self):
        self.calls.append(("close", None))


def test_browser_actions_are_allowlisted_approved_and_bounded() -> None:
    backend = FakeBrowser()
    seen: list[BrowserAction] = []
    browser = BrowserAutomation(
        backend,
        BrowserPolicy(("https://example.com",), max_steps=5),
        approve=lambda action, _detail: seen.append(action) or True,
    )
    assert browser.navigate("https://example.com/path").outcome == "completed"
    assert browser.click("#submit").outcome == "completed"
    assert browser.fill("#name", "Coquo").outcome == "completed"
    assert browser.extract_text().value == "hello"
    assert browser.screenshot().value == "bytes:3"
    with pytest.raises(BrowserAutomationError, match="step limit"):
        browser.click("#again")
    browser.close()
    assert seen[:2] == [BrowserAction.NAVIGATE, BrowserAction.CLICK]


def test_browser_rejects_unallowlisted_origins_and_requires_approval() -> None:
    with pytest.raises(BrowserAutomationError, match="HTTPS"):
        BrowserPolicy(("http://example.com",))
    backend = FakeBrowser()
    browser = BrowserAutomation(
        backend, BrowserPolicy(("https://example.com",)), approve=lambda *_: False
    )
    with pytest.raises(BrowserAutomationError, match="approval"):
        browser.navigate("https://example.com")
    with pytest.raises(BrowserAutomationError, match="allowlisted"):
        BrowserAutomation(
            backend,
            BrowserPolicy(("https://example.com",)),
            approve=lambda *_: True,
        ).navigate("https://other.example")


def test_browser_disallows_credentials_and_non_web_schemes() -> None:
    policy = BrowserPolicy(("https://example.com",))
    browser = BrowserAutomation(FakeBrowser(), policy, approve=lambda *_: True)
    with pytest.raises(BrowserAutomationError, match="credential-free"):
        browser.navigate("https://user:pass@example.com")
    with pytest.raises(BrowserAutomationError, match=r"HTTP\(S\)"):
        browser.navigate("javascript:alert(1)")


def test_browser_action_contract_is_closed_and_action_specific() -> None:
    snapshot = browser_action_tool_snapshot()
    assert snapshot.name == "browser_action"
    assert snapshot.as_mapping()["input_schema"]["additionalProperties"] is False

    request = ToolUse(
        "browser-1",
        "browser_action",
        ToolArguments.from_mapping({"action": "navigate", "url": "https://example.com"}),
    )
    parsed = parse_browser_action(request)
    assert parsed.action is BrowserAction.NAVIGATE
    assert parsed.url == "https://example.com"

    direct = tool_use_from_input(
        "browser-1b",
        BROWSER_ACTION_TOOL_NAME,
        {"action": "extract_text"},
    )
    assert tool_input_from_use(direct) == {"action": "extract_text"}

    with pytest.raises(ValueError, match="irrelevant fields"):
        parse_browser_action(
            ToolUse(
                "browser-2",
                "browser_action",
                ToolArguments.from_mapping(
                    {"action": "click", "selector": "#go", "url": "https://example.com"}
                ),
            )
        )


def test_browser_action_is_advertised_and_dispatched_through_session_action_audit(
    tmp_path,
) -> None:
    backend = FakeBrowser()
    browser = BrowserAutomation(
        backend,
        BrowserPolicy(("https://example.com",)),
        # ActionCoordinator owns the durable permission/approval decision;
        # this callback is the backend's final Host gate.
        approve=lambda *_: True,
    )
    provider = ScriptedFakeProvider(
        [
            ToolUse(
                "browser-1",
                BROWSER_ACTION_TOOL_NAME,
                ToolArguments.from_mapping({"action": "extract_text"}),
            ),
            # The browser observation is intentionally untrusted tool data.
            AssistantText("observed"),
        ]
    )
    session = ProjectSession.open(
        tmp_path,
        environment={},
        fake_provider_factory=lambda: provider,
        browser=browser,
        permission_mode=PermissionMode.DANGER_FULL_ACCESS,
        approval_mode=ApprovalMode.AUTO,
    )
    try:
        assert session.prompt("Inspect the page") == "observed"
        assert backend.calls == [("extract", None)]
        request = provider.received_requests[1]
        result = next(item for item in reversed(request.history) if item.tool_use_id == "browser-1")
        assert '"evidence":"untrusted"' in result.content
        audits = [
            audit
            for audit in session._writer.state.action_audits
            if audit.identity.tool_name == BROWSER_ACTION_TOOL_NAME
        ]
        assert audits and audits[-1].status is ActionAuditStatus.SUCCEEDED
    finally:
        session.close()


def test_browser_action_remains_hidden_without_a_runtime(tmp_path) -> None:
    snapshot = registry_snapshot_with_browser(tmp_path, TOOL_REGISTRY_SNAPSHOT, enabled=False)
    assert BROWSER_ACTION_TOOL_NAME not in snapshot.names


def test_browser_action_registry_is_added_only_for_configured_runtime(tmp_path) -> None:
    snapshot = registry_snapshot_with_browser(tmp_path, TOOL_REGISTRY_SNAPSHOT, enabled=True)
    assert snapshot.names[-1] == BROWSER_ACTION_TOOL_NAME
    assert snapshot.contract(BROWSER_ACTION_TOOL_NAME).permission_actions == (
        PermissionAction.NETWORK_READ,
    )
