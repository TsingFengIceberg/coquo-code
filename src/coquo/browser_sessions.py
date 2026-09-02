"""Durable browser session identities and injected runtime lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Callable
from uuid import UUID, uuid4

from coquo.browser import BrowserAutomation, BrowserBackend, BrowserPolicy


class BrowserSessionError(RuntimeError):
    """Raised when browser session persistence or lifecycle is invalid."""


@dataclass(frozen=True)
class BrowserSessionRecord:
    session_id: str
    policy: BrowserPolicy
    state: str
    created_at: str
    updated_at: str
    backend_name: str


class BrowserSessionStore:
    def __init__(self, workspace: Path) -> None:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise BrowserSessionError("browser workspace is invalid")
        self.root = root / ".coquo" / "browser" / "v1"
        self.path = self.root / "sessions.json"
        self._guard = Lock()

    def create(
        self, policy: BrowserPolicy, *, backend_name: str = "injected"
    ) -> BrowserSessionRecord:
        if not isinstance(policy, BrowserPolicy) or not backend_name or len(backend_name) > 128:
            raise BrowserSessionError("browser session configuration is invalid")
        now = _now()
        record = BrowserSessionRecord(str(uuid4()), policy, "created", now, now, backend_name)
        with self._guard:
            records = list(self.list())
            records.append(record)
            self._write(records)
        return record

    def get(self, session_id: str) -> BrowserSessionRecord:
        try:
            UUID(session_id)
        except (ValueError, AttributeError):
            raise BrowserSessionError("browser session ID is invalid") from None
        for record in self.list():
            if record.session_id == session_id:
                return record
        raise BrowserSessionError("browser session was not found")

    def transition(self, session_id: str, state: str) -> BrowserSessionRecord:
        if state not in {"created", "open", "closed", "recovery-required"}:
            raise BrowserSessionError("browser session state is invalid")
        current = self.get(session_id)
        if current.state == "closed" and state != "closed":
            raise BrowserSessionError("closed browser session cannot reopen implicitly")
        updated = BrowserSessionRecord(
            current.session_id,
            current.policy,
            state,
            current.created_at,
            _now(),
            current.backend_name,
        )
        self._write([updated if item.session_id == session_id else item for item in self.list()])
        return updated

    def list(self) -> tuple[BrowserSessionRecord, ...]:
        if not self.path.exists():
            return ()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError
            return tuple(_decode(item) for item in payload)
        except (OSError, ValueError, json.JSONDecodeError, TypeError, KeyError) as error:
            raise BrowserSessionError("browser session store is invalid") from error

    def _write(self, records: list[BrowserSessionRecord]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = [_encode(item) for item in records]
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        temporary.replace(self.path)


class BrowserSessionManager:
    def __init__(
        self,
        store: BrowserSessionStore,
        backend_factory: Callable[[BrowserSessionRecord], BrowserBackend],
    ) -> None:
        self.store = store
        self.backend_factory = backend_factory
        self._active: dict[str, BrowserAutomation] = {}

    def open(self, session_id: str, *, approve=None) -> BrowserAutomation:
        record = self.store.get(session_id)
        if record.state == "closed":
            raise BrowserSessionError("browser session is closed")
        browser = BrowserAutomation(self.backend_factory(record), record.policy, approve=approve)
        self._active[session_id] = browser
        self.store.transition(session_id, "open")
        return browser

    def close(self, session_id: str) -> BrowserSessionRecord:
        browser = self._active.pop(session_id, None)
        if browser is not None:
            browser.close()
        return self.store.transition(session_id, "closed")

    def recover(self) -> tuple[BrowserSessionRecord, ...]:
        recovered: list[BrowserSessionRecord] = []
        for record in self.store.list():
            if record.state == "open":
                recovered.append(self.store.transition(record.session_id, "recovery-required"))
        return tuple(recovered)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode(record: BrowserSessionRecord) -> dict[str, object]:
    return {
        "session_id": record.session_id,
        "state": record.state,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "backend_name": record.backend_name,
        "policy": {
            "allowed_origins": list(record.policy.allowed_origins),
            "max_steps": record.policy.max_steps,
            "timeout_seconds": record.policy.timeout_seconds,
            "allow_http_localhost": record.policy.allow_http_localhost,
        },
    }


def _decode(value: object) -> BrowserSessionRecord:
    if not isinstance(value, dict):
        raise ValueError
    policy = value["policy"]
    if not isinstance(policy, dict):
        raise ValueError
    return BrowserSessionRecord(
        value["session_id"],
        BrowserPolicy(
            tuple(policy["allowed_origins"]),
            policy["max_steps"],
            policy["timeout_seconds"],
            policy["allow_http_localhost"],
        ),
        value["state"],
        value["created_at"],
        value["updated_at"],
        value["backend_name"],
    )


__all__ = [
    "BrowserSessionError",
    "BrowserSessionManager",
    "BrowserSessionRecord",
    "BrowserSessionStore",
]
