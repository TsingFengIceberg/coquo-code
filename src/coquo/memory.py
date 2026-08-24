"""Provider-neutral contracts for bounded long-term semantic memory.

This slice defines storage data, scope, and lifecycle vocabulary only.  Memory
is not model authority, and no automatic recall or extraction is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any, Mapping
from uuid import uuid4

from coquo.core.actions import canonical_uuid4

MEMORY_SCHEMA_VERSION = 1
MEMORY_MAX_CONTENT_BYTES = 16 * 1024
MEMORY_MAX_CATEGORY_LENGTH = 64
MEMORY_MAX_SCOPE_ID_LENGTH = 256
MEMORY_MAX_SOURCE_SESSION_ID_LENGTH = 128
MEMORY_MAX_EVENTS = 10_000
MEMORY_MAX_EVENT_BYTES = 128 * 1024
MEMORY_MAX_EVENT_LOG_BYTES = 16 * 1024 * 1024
MEMORY_MAX_SEARCH_RESULTS = 100

_ASCII_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")


class MemoryError(ValueError):
    """Base error for invalid or unavailable semantic-memory state."""


class MemoryScope(StrEnum):
    """Durability/visibility bucket for one semantic-memory record."""

    USER = "user"
    WORKSPACE = "workspace"
    TASK = "task"
    TEAM = "team"
    CHILD = "child"


class MemoryStatus(StrEnum):
    """Lifecycle state of a semantic-memory record."""

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    STALE = "stale"
    DELETED = "deleted"
    EVICTED = "evicted"


class MemoryRecallMode(StrEnum):
    """Host recall switch.  ``off`` is the safe default."""

    OFF = "off"
    ON = "on"


class MemoryWriteMode(StrEnum):
    """Automatic extraction policy; explicit Host CRUD is separate."""

    OFF = "off"
    PROPOSE = "propose"
    AUTO = "auto"


def utc_now() -> str:
    """Return a canonical UTC timestamp for durable memory records."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryError(f"memory {field} must be non-blank text")
    if value != value.strip():
        raise MemoryError(f"memory {field} must not have surrounding whitespace")
    if "\x00" in value:
        raise MemoryError(f"memory {field} must not contain NUL")
    if len(value) > maximum:
        raise MemoryError(f"memory {field} exceeds {maximum} characters")
    return value


def _scope_id(value: str) -> str:
    result = _text(value, "scope_id", maximum=MEMORY_MAX_SCOPE_ID_LENGTH)
    if _ASCII_TOKEN.fullmatch(result) is None:
        raise MemoryError("memory scope_id must use bounded portable characters")
    return result


def _optional_text(value: str | None, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum=maximum)


def _timestamp(value: str, field: str) -> str:
    result = _text(value, field, maximum=64)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError:
        raise MemoryError(f"memory {field} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise MemoryError(f"memory {field} must include a timezone")
    return result


@dataclass(frozen=True)
class MemoryRecord:
    """One bounded semantic-memory fact or pending candidate."""

    memory_id: str
    scope: MemoryScope
    scope_id: str
    content: str
    category: str
    confidence: float
    status: MemoryStatus
    created_at: str
    updated_at: str
    source_session_id: str | None = None
    source_turn: int | None = None
    confirmed_at: str | None = None
    last_recalled_at: str | None = None

    def __post_init__(self) -> None:
        try:
            canonical_uuid4(self.memory_id, "memory ID")
        except ValueError:
            raise MemoryError("memory_id must be a UUID") from None
        if not isinstance(self.scope, MemoryScope):
            raise MemoryError("memory scope is invalid")
        _scope_id(self.scope_id)
        content = _text(self.content, "content", maximum=MEMORY_MAX_CONTENT_BYTES)
        if len(content.encode("utf-8")) > MEMORY_MAX_CONTENT_BYTES:
            raise MemoryError("memory content exceeds its UTF-8 byte limit")
        category = _text(self.category, "category", maximum=MEMORY_MAX_CATEGORY_LENGTH)
        if _ASCII_TOKEN.fullmatch(category) is None:
            raise MemoryError("memory category must use bounded portable characters")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise MemoryError("memory confidence must be a number")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise MemoryError("memory confidence must be between 0.0 and 1.0")
        if not isinstance(self.status, MemoryStatus):
            raise MemoryError("memory status is invalid")
        _timestamp(self.created_at, "created_at")
        _timestamp(self.updated_at, "updated_at")
        _optional_text(
            self.source_session_id,
            "source_session_id",
            maximum=MEMORY_MAX_SOURCE_SESSION_ID_LENGTH,
        )
        if self.source_turn is not None and (
            type(self.source_turn) is not int or self.source_turn < 1
        ):
            raise MemoryError("memory source_turn must be a positive integer or null")
        if self.confirmed_at is not None:
            _timestamp(self.confirmed_at, "confirmed_at")
        if self.last_recalled_at is not None:
            _timestamp(self.last_recalled_at, "last_recalled_at")
        if self.status is MemoryStatus.CONFIRMED and self.confirmed_at is None:
            raise MemoryError("confirmed memory must have confirmed_at")
        if self.status is not MemoryStatus.CONFIRMED and self.confirmed_at is not None:
            raise MemoryError("only confirmed memory may have confirmed_at")

    @classmethod
    def candidate(
        cls,
        content: str,
        *,
        scope: MemoryScope,
        scope_id: str,
        category: str = "fact",
        confidence: float = 0.5,
        source_session_id: str | None = None,
        source_turn: int | None = None,
        memory_id: str | None = None,
        now: str | None = None,
    ) -> "MemoryRecord":
        timestamp = now or utc_now()
        return cls(
            memory_id=memory_id or str(uuid4()),
            scope=scope,
            scope_id=scope_id,
            content=content,
            category=category,
            confidence=confidence,
            status=MemoryStatus.CANDIDATE,
            created_at=timestamp,
            updated_at=timestamp,
            source_session_id=source_session_id,
            source_turn=source_turn,
        )

    def confirm(self, *, now: str | None = None) -> "MemoryRecord":
        if self.status in {MemoryStatus.DELETED, MemoryStatus.EVICTED}:
            raise MemoryError("terminal memory cannot be confirmed")
        timestamp = now or utc_now()
        return replace(
            self,
            status=MemoryStatus.CONFIRMED,
            updated_at=timestamp,
            confirmed_at=timestamp,
        )

    def update_fields(
        self,
        *,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
        now: str | None = None,
    ) -> "MemoryRecord":
        if self.status in {MemoryStatus.DELETED, MemoryStatus.EVICTED}:
            raise MemoryError("terminal memory cannot be updated")
        timestamp = now or utc_now()
        return replace(
            self,
            content=self.content if content is None else content,
            category=self.category if category is None else category,
            confidence=self.confidence if confidence is None else confidence,
            updated_at=timestamp,
        )

    def transition(self, status: MemoryStatus, *, now: str | None = None) -> "MemoryRecord":
        if status is MemoryStatus.CONFIRMED:
            return self.confirm(now=now)
        if self.status in {MemoryStatus.DELETED, MemoryStatus.EVICTED}:
            raise MemoryError("terminal memory cannot change status")
        timestamp = now or utc_now()
        return replace(
            self,
            status=status,
            updated_at=timestamp,
            confirmed_at=None,
        )

    def recalled(self, *, now: str | None = None) -> "MemoryRecord":
        if self.status in {MemoryStatus.DELETED, MemoryStatus.EVICTED}:
            raise MemoryError("terminal memory cannot be recalled")
        return replace(self, last_recalled_at=now or utc_now())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "memory_id": self.memory_id,
            "scope": self.scope.value,
            "scope_id": self.scope_id,
            "content": self.content,
            "category": self.category,
            "confidence": float(self.confidence),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_session_id": self.source_session_id,
            "source_turn": self.source_turn,
            "confirmed_at": self.confirmed_at,
            "last_recalled_at": self.last_recalled_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MemoryRecord":
        if not isinstance(value, Mapping):
            raise MemoryError("memory record must be an object")
        expected = {
            "schema_version",
            "memory_id",
            "scope",
            "scope_id",
            "content",
            "category",
            "confidence",
            "status",
            "created_at",
            "updated_at",
            "source_session_id",
            "source_turn",
            "confirmed_at",
            "last_recalled_at",
        }
        if set(value) != expected or value.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise MemoryError("memory record schema is invalid")
        try:
            scope = MemoryScope(value["scope"])
            status = MemoryStatus(value["status"])
        except (ValueError, TypeError):
            raise MemoryError("memory scope or status is invalid") from None
        return cls(
            memory_id=value["memory_id"],
            scope=scope,
            scope_id=value["scope_id"],
            content=value["content"],
            category=value["category"],
            confidence=value["confidence"],
            status=status,
            created_at=value["created_at"],
            updated_at=value["updated_at"],
            source_session_id=value["source_session_id"],
            source_turn=value["source_turn"],
            confirmed_at=value["confirmed_at"],
            last_recalled_at=value["last_recalled_at"],
        )
