"""Closed immutable contracts for declarative Hook events and durable audit ledgers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


MAX_HOOK_AUDIT_ENTRIES = 128
MAX_HOOK_AUDIT_MATCHES = 64
MAX_HOOK_AUDIT_QUERY_ENTRIES = 100
_HOOK_ID = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_HOOK_SET_ID = re.compile(r"hooks-v(?:1|2)-[0-9a-f]{64}\Z")


class HookEvent(StrEnum):
    """Closed lifecycle events supported by declarative Hooks."""

    BEFORE_ACTION_AUTHORIZATION = "before_action_authorization"
    AFTER_ACTION = "after_action"
    TURN_COMMITTED = "turn_committed"
    TURN_FAILED = "turn_failed"
    TASK_STAGE_STARTED = "task_stage_started"
    TASK_STAGE_COMMITTED = "task_stage_committed"
    TASK_STAGE_FAILED = "task_stage_failed"
    TASK_BLOCKED = "task_blocked"
    TASK_TERMINATED = "task_terminated"

    @property
    def is_action_event(self) -> bool:
        return self in {
            HookEvent.BEFORE_ACTION_AUTHORIZATION,
            HookEvent.AFTER_ACTION,
        }


class HookEffect(StrEnum):
    """Authority-nonexpanding outcomes supported by the Hook engine."""

    CONTINUE = "continue"
    DENY = "deny"
    REQUIRE_ASK = "require_ask"
    ADVISORY = "advisory"


class HookActionOutcome(StrEnum):
    """Content-free terminal outcomes available to after-action matching."""

    SUCCEEDED = "succeeded"
    ERROR = "error"
    DENIED = "denied"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PARTIAL = "partial"
    OUTCOME_UNKNOWN = "outcome-unknown"


@dataclass(frozen=True)
class HookAuditMatch:
    """One content-free matched rule identity."""

    hook_id: str
    effect: HookEffect

    def __post_init__(self) -> None:
        if not isinstance(self.hook_id, str) or _HOOK_ID.fullmatch(self.hook_id) is None:
            raise ValueError("Hook audit match ID is invalid")
        if type(self.effect) is not HookEffect:
            raise ValueError("Hook audit match effect is invalid")


@dataclass(frozen=True)
class HookAuditEntry:
    """One deterministic Hook evaluation without messages or action arguments."""

    event: HookEvent
    hook_set_id: str
    subject_id: str
    matches: tuple[HookAuditMatch, ...]
    result: HookEffect
    tool_name: str | None = None
    permission_action: str | None = None
    source: str | None = None
    action_outcome: HookActionOutcome | None = None

    def __post_init__(self) -> None:
        if type(self.event) is not HookEvent:
            raise ValueError("Hook audit event is invalid")
        if (
            not isinstance(self.hook_set_id, str)
            or _HOOK_SET_ID.fullmatch(self.hook_set_id) is None
        ):
            raise ValueError("Hook audit set identity is invalid")
        _safe_identity(self.subject_id, "Hook audit subject")
        if (
            not isinstance(self.matches, tuple)
            or len(self.matches) > MAX_HOOK_AUDIT_MATCHES
            or any(type(match) is not HookAuditMatch for match in self.matches)
        ):
            raise ValueError("Hook audit matches are invalid")
        if tuple(sorted(self.matches, key=lambda match: match.hook_id)) != self.matches:
            raise ValueError("Hook audit matches are not canonical")
        if len({match.hook_id for match in self.matches}) != len(self.matches):
            raise ValueError("Hook audit matches contain duplicate IDs")
        if type(self.result) is not HookEffect:
            raise ValueError("Hook audit result is invalid")
        expected = _aggregate_effect(self.matches)
        if self.result is not expected:
            raise ValueError("Hook audit result contradicts matched effects")
        if self.event.is_action_event:
            _safe_identity(self.tool_name, "Hook audit tool name")
            _safe_identity(self.permission_action, "Hook audit permission action")
            if self.source not in {"builtin", "mcp"}:
                raise ValueError("Hook audit source is invalid")
            if self.event is HookEvent.AFTER_ACTION:
                if type(self.action_outcome) is not HookActionOutcome:
                    raise ValueError("after-action Hook audit requires an outcome")
            elif self.action_outcome is not None:
                raise ValueError("preauthorization Hook audit cannot contain an outcome")
        elif any(
            value is not None
            for value in (
                self.tool_name,
                self.permission_action,
                self.source,
                self.action_outcome,
            )
        ):
            raise ValueError("lifecycle Hook audit cannot contain action metadata")


@dataclass(frozen=True)
class HookAuditLedger:
    """One bounded ordered set of evaluations committed with an owning durable record."""

    entries: tuple[HookAuditEntry, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.entries, tuple)
            or len(self.entries) > MAX_HOOK_AUDIT_ENTRIES
            or any(type(entry) is not HookAuditEntry for entry in self.entries)
        ):
            raise ValueError("Hook audit ledger is invalid")


@dataclass(frozen=True)
class HookAuditObservation:
    """One read-only projection linking an evaluation to its owning durable record."""

    record_type: str
    record_sequence: int
    entry: HookAuditEntry

    def __post_init__(self) -> None:
        _safe_identity(self.record_type, "Hook audit record type")
        if type(self.record_sequence) is not int or self.record_sequence < 1:
            raise ValueError("Hook audit record sequence is invalid")
        if type(self.entry) is not HookAuditEntry:
            raise ValueError("Hook audit observation entry is invalid")


def bounded_hook_audit_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_HOOK_AUDIT_QUERY_ENTRIES:
        raise ValueError(f"Hook audit limit must be between 1 and {MAX_HOOK_AUDIT_QUERY_ENTRIES}")
    return value


def hook_audit_ledger_to_mapping(ledger: HookAuditLedger) -> dict[str, object]:
    if type(ledger) is not HookAuditLedger:
        raise ValueError("Hook audit ledger is invalid")
    ledger.__post_init__()
    return {
        "entries": [
            {
                "action_outcome": (
                    entry.action_outcome.value if entry.action_outcome is not None else None
                ),
                "event": entry.event.value,
                "hook_set_id": entry.hook_set_id,
                "matches": [
                    {"effect": match.effect.value, "hook_id": match.hook_id}
                    for match in entry.matches
                ],
                "permission_action": entry.permission_action,
                "result": entry.result.value,
                "source": entry.source,
                "subject_id": entry.subject_id,
                "tool_name": entry.tool_name,
            }
            for entry in ledger.entries
        ]
    }


def hook_audit_ledger_from_mapping(value: object) -> HookAuditLedger:
    if not isinstance(value, dict) or set(value) != {"entries"}:
        raise ValueError("Hook audit ledger must be a closed object")
    raw_entries = value["entries"]
    if not isinstance(raw_entries, list):
        raise ValueError("Hook audit ledger entries must be an array")
    entries: list[HookAuditEntry] = []
    fields = {
        "action_outcome",
        "event",
        "hook_set_id",
        "matches",
        "permission_action",
        "result",
        "source",
        "subject_id",
        "tool_name",
    }
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or set(raw_entry) != fields:
            raise ValueError("Hook audit entry must be a closed object")
        raw_matches = raw_entry["matches"]
        if not isinstance(raw_matches, list):
            raise ValueError("Hook audit matches must be an array")
        matches: list[HookAuditMatch] = []
        for raw_match in raw_matches:
            if not isinstance(raw_match, dict) or set(raw_match) != {"effect", "hook_id"}:
                raise ValueError("Hook audit match must be a closed object")
            matches.append(
                HookAuditMatch(
                    _required_text(raw_match["hook_id"], "Hook audit match ID"),
                    _enum(raw_match["effect"], HookEffect, "Hook audit match effect"),
                )
            )
        outcome = raw_entry["action_outcome"]
        entries.append(
            HookAuditEntry(
                event=_enum(raw_entry["event"], HookEvent, "Hook audit event"),
                hook_set_id=_required_text(raw_entry["hook_set_id"], "Hook audit set identity"),
                subject_id=_required_text(raw_entry["subject_id"], "Hook audit subject"),
                matches=tuple(matches),
                result=_enum(raw_entry["result"], HookEffect, "Hook audit result"),
                tool_name=_nullable_text(raw_entry["tool_name"], "Hook audit tool name"),
                permission_action=_nullable_text(
                    raw_entry["permission_action"], "Hook audit permission action"
                ),
                source=_nullable_text(raw_entry["source"], "Hook audit source"),
                action_outcome=(
                    None
                    if outcome is None
                    else _enum(outcome, HookActionOutcome, "Hook audit action outcome")
                ),
            )
        )
    return HookAuditLedger(tuple(entries))


def aggregate_hook_effect(matches: tuple[HookAuditMatch, ...]) -> HookEffect:
    """Return deterministic nonexpanding precedence for one matched set."""
    return _aggregate_effect(matches)


def _aggregate_effect(matches: tuple[HookAuditMatch, ...]) -> HookEffect:
    effects = {match.effect for match in matches}
    if HookEffect.DENY in effects:
        return HookEffect.DENY
    if HookEffect.REQUIRE_ASK in effects:
        return HookEffect.REQUIRE_ASK
    if HookEffect.ADVISORY in effects:
        return HookEffect.ADVISORY
    return HookEffect.CONTINUE


def _safe_identity(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} is invalid")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be text")
    return value


def _nullable_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _enum(value: object, enum_type, label: str):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    try:
        return enum_type(value)
    except ValueError:
        raise ValueError(f"{label} is unsupported") from None
