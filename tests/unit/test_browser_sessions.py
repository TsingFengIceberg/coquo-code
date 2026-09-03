from __future__ import annotations

import pytest

from coquo.browser import BrowserPolicy, BrowserRuntimeError, PlaywrightBrowserRuntime
from coquo.browser_sessions import BrowserSessionManager, BrowserSessionStore


class Backend:
    def navigate(self, *_):
        pass

    def click(self, *_):
        pass

    def fill(self, *_):
        pass

    def extract_text(self, *_):
        return "ok"

    def screenshot(self, *_):
        return b"png"

    def close(self):
        pass


def test_browser_session_store_persists_lifecycle_without_page_data(tmp_path):
    policy = BrowserPolicy(("https://example.test",))
    store = BrowserSessionStore(tmp_path)
    record = store.create(policy)
    manager = BrowserSessionManager(store, lambda _record: Backend())
    browser = manager.open(record.session_id, approve=lambda *_: True)
    assert browser.extract_text().value == "ok"
    closed = manager.close(record.session_id)
    assert closed.state == "closed"
    assert "ok" not in store.path.read_text()


def test_browser_session_recovery_marks_open_session(tmp_path):
    store = BrowserSessionStore(tmp_path)
    record = store.create(BrowserPolicy(("https://example.test",)))
    store.transition(record.session_id, "open")
    recovered = BrowserSessionManager(store, lambda _record: Backend()).recover()
    assert recovered[0].state == "recovery-required"
    manager = BrowserSessionManager(store, lambda _record: Backend())
    assert manager.reset_recovery(record.session_id).state == "created"


class _Resource:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    def close(self):
        self.calls.append(self.name)


class _Context(_Resource):
    def __init__(self, calls):
        super().__init__("context", calls)
        self.browser = _Resource("browser", calls)

    def new_page(self):
        return _Resource("page", self.calls)


class _Engine:
    def __init__(self, calls):
        self.calls = calls

    def launch(self, *, headless):
        assert headless is True
        browser = _Resource("browser", self.calls)
        browser.new_context = lambda: _Context(self.calls)
        return browser


class _Playwright:
    def __init__(self, calls):
        self.calls = calls
        self.chromium = _Engine(calls)

    def stop(self):
        self.calls.append("playwright")


def test_real_playwright_runtime_is_lazy_and_closes_in_resource_order():
    calls = []
    runtime = PlaywrightBrowserRuntime.start(playwright_factory=lambda: _Playwright(calls))
    runtime.close()
    assert calls == ["page", "context", "browser", "playwright"]


def test_real_playwright_runtime_maps_launch_failure_to_structured_error():
    class Broken:
        chromium = object()

        def stop(self):
            pass

    with pytest.raises(BrowserRuntimeError) as caught:
        PlaywrightBrowserRuntime.start(playwright_factory=Broken)
    assert caught.value.code == "engine-unavailable"
