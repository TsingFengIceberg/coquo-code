from __future__ import annotations

from coquo.browser import BrowserPolicy
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
