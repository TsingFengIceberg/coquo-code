from __future__ import annotations

import pytest

from coquo.browser import (
    BrowserAction,
    BrowserAutomation,
    BrowserAutomationError,
    BrowserPolicy,
)


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
