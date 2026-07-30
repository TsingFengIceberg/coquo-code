"""Structured contracts shared by the sequential model-tool loop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import TYPE_CHECKING, Callable, Protocol, TypeAlias

from leonervis_code.core.project_instructions import ProjectInstructionsSnapshot

if TYPE_CHECKING:
    from leonervis_code.core.compaction import EffectiveContextSummary

_SYSTEM_PROMPT_FINGERPRINT_DOMAIN = b"leonervis-code-system-prompt\0"
TOOL_ARGUMENTS_VERSION = 1
MAX_TOOL_ARGUMENTS_BYTES = 16 * 1024
MAX_ASSISTANT_TOOL_TEXT_CHARACTERS = 32 * 1024
MAX_ASSISTANT_TOOL_TEXT_BYTES = 32 * 1024
MAX_TOOL_OUTCOME_ENTRIES = 40


def system_prompt_fingerprint(version: int, text: str) -> str:
    """Return the stable domain-separated identity for exact prompt text."""
    if type(version) is not int or version < 1:
        raise ValueError("system prompt version must be positive")
    if not isinstance(text, str):
        raise ValueError("system prompt text must be text")
    encoded = (
        _SYSTEM_PROMPT_FINGERPRINT_DOMAIN
        + str(version).encode("ascii")
        + b"\0"
        + text.encode("utf-8")
    )
    return f"v{version}-{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class SystemPromptSnapshot:
    """One immutable, versioned system prompt sent with a provider request."""

    version: int
    text: str
    fingerprint: str


@dataclass(frozen=True)
class UserMessage:
    """One user text input in an ordered in-memory conversation."""

    text: str


@dataclass(frozen=True)
class AssistantText:
    """The final visible assistant text for one completed conversation turn."""

    text: str


@dataclass(frozen=True)
class ToolArguments:
    """Immutable versioned provider-neutral arguments for one tool use."""

    version: int
    canonical_json: str

    def __post_init__(self) -> None:
        if self.version != TOOL_ARGUMENTS_VERSION:
            raise ValueError("unsupported tool arguments version")
        if not isinstance(self.canonical_json, str):
            raise ValueError("tool arguments canonical JSON must be text")
        try:
            decoded = json.loads(self.canonical_json)
        except json.JSONDecodeError:
            raise ValueError("tool arguments canonical JSON is invalid") from None
        if not isinstance(decoded, dict):
            raise ValueError("tool arguments must be a JSON object")
        canonical = self._canonicalize(decoded)
        if canonical != self.canonical_json:
            raise ValueError("tool arguments canonical JSON is not canonical")

    @classmethod
    def from_mapping(
        cls,
        arguments: dict[str, object],
        *,
        version: int = TOOL_ARGUMENTS_VERSION,
    ) -> ToolArguments:
        """Validate and freeze one JSON object in deterministic canonical form."""
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        return cls(version=version, canonical_json=cls._canonicalize(arguments))

    def as_mapping(self) -> dict[str, object]:
        """Return a fresh mutable projection of the frozen argument object."""
        value = json.loads(self.canonical_json)
        if not isinstance(value, dict):
            raise ValueError("tool arguments must decode to a JSON object")
        return value

    @staticmethod
    def _canonicalize(arguments: dict[str, object]) -> str:
        try:
            canonical = json.dumps(
                arguments,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            encoded = canonical.encode("utf-8")
        except (TypeError, ValueError, OverflowError, UnicodeEncodeError):
            raise ValueError("tool arguments are not canonical JSON") from None
        if len(encoded) > MAX_TOOL_ARGUMENTS_BYTES:
            raise ValueError(f"tool arguments exceed {MAX_TOOL_ARGUMENTS_BYTES} bytes")
        return canonical


@dataclass(frozen=True)
class ToolUse:
    """One provider-requested tool and optional atomic assistant companion text."""

    tool_use_id: str
    name: str
    arguments: ToolArguments
    assistant_text: str | None = None

    def __post_init__(self) -> None:
        text = self.assistant_text
        if text is None:
            return
        _validate_assistant_tool_text(text)


@dataclass(frozen=True)
class AssistantToolBatch:
    """One assistant response containing ordered provider-requested tool uses."""

    tool_uses: tuple[ToolUse, ...]
    assistant_text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool_uses, tuple) or not self.tool_uses:
            raise ValueError("assistant tool batch must contain tool uses")
        seen_ids: set[str] = set()
        for request in self.tool_uses:
            if not isinstance(request, ToolUse):
                raise ValueError("assistant tool batch contains an invalid tool use")
            if request.assistant_text is not None:
                raise ValueError("assistant tool batch text must be stored on the batch")
            if request.tool_use_id in seen_ids:
                raise ValueError("assistant tool batch contains a duplicate tool use ID")
            seen_ids.add(request.tool_use_id)
        if self.assistant_text is not None:
            _validate_assistant_tool_text(self.assistant_text)


@dataclass(frozen=True)
class ToolResult:
    """One host-produced result corresponding to a ``ToolUse`` request."""

    tool_use_id: str
    content: str
    is_error: bool = False
    truncated: bool = False


class ToolRequestOutcome(StrEnum):
    """Host-observed terminal accounting state for one provider request."""

    SUCCEEDED = "succeeded"
    ERROR = "error"
    DENIED = "denied"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PARTIAL = "partial"
    OUTCOME_UNKNOWN = "outcome-unknown"
    SKIPPED_AFTER_FAILURE = "skipped-after-failure"
    REJECTED_OVER_BUDGET = "rejected-over-budget"


@dataclass(frozen=True)
class ToolOutcomeEntry:
    """One immutable Host outcome tied to an exact provider tool-use ID."""

    tool_use_id: str
    tool_name: str
    request_index: int
    outcome: ToolRequestOutcome
    result_code: str | None = None

    def __post_init__(self) -> None:
        _validate_ledger_text(self.tool_use_id, "tool outcome ID")
        _validate_ledger_text(
            self.tool_name,
            "tool outcome name",
            ascii_only=True,
            control_free=True,
        )
        if type(self.request_index) is not int or self.request_index <= 0:
            raise ValueError("tool outcome request index must be positive")
        if type(self.outcome) is not ToolRequestOutcome:
            raise ValueError("tool outcome status is invalid")
        if self.result_code is not None:
            _validate_ledger_text(
                self.result_code,
                "tool outcome result code",
                ascii_only=True,
                control_free=True,
                max_characters=160,
            )
        required_code = {
            ToolRequestOutcome.SKIPPED_AFTER_FAILURE: "prior_batch_action_not_succeeded",
            ToolRequestOutcome.REJECTED_OVER_BUDGET: "batch_exceeds_remaining_budget",
        }.get(self.outcome)
        if required_code is not None and self.result_code != required_code:
            raise ValueError("synthetic tool outcome requires its canonical result code")


@dataclass(frozen=True)
class ToolTurnLedger:
    """Ordered per-request Host truth for one committed user turn."""

    entries: tuple[ToolOutcomeEntry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            raise ValueError("tool turn ledger entries must be a tuple")
        if len(self.entries) > MAX_TOOL_OUTCOME_ENTRIES:
            raise ValueError("tool turn ledger exceeds its entry limit")
        seen_ids: set[str] = set()
        for expected_index, entry in enumerate(self.entries, start=1):
            if type(entry) is not ToolOutcomeEntry:
                raise ValueError("tool turn ledger contains an invalid entry")
            if entry.request_index != expected_index:
                raise ValueError("tool turn ledger request indexes must be continuous")
            if entry.tool_use_id in seen_ids:
                raise ValueError("tool turn ledger contains a duplicate tool-use ID")
            seen_ids.add(entry.tool_use_id)

    @property
    def requested(self) -> int:
        return len(self.entries)

    @property
    def admitted(self) -> int:
        return self.requested - self.count(ToolRequestOutcome.REJECTED_OVER_BUDGET)

    @property
    def dispatched(self) -> int:
        return self.admitted - self.count(ToolRequestOutcome.SKIPPED_AFTER_FAILURE)

    def count(self, outcome: ToolRequestOutcome) -> int:
        """Derive one status count without maintaining mutable aggregate state."""
        if type(outcome) is not ToolRequestOutcome:
            raise ValueError("tool outcome status is invalid")
        return sum(entry.outcome == outcome for entry in self.entries)


@dataclass(frozen=True)
class ConversationTurn:
    """One completed user/final-assistant pair for REPL history display."""

    user: UserMessage
    assistant: AssistantText


@dataclass(frozen=True)
class CommittedTurn:
    """One complete causal turn ready for durable persistence and memory commit."""

    items: tuple[ConversationItem, ...]
    user: UserMessage
    assistant: AssistantText
    tool_ledger: ToolTurnLedger = ToolTurnLedger()


ConversationItem: TypeAlias = (
    UserMessage | AssistantText | ToolUse | AssistantToolBatch | ToolResult
)
TurnCommitter: TypeAlias = Callable[[CommittedTurn], None]
ProviderResponse: TypeAlias = AssistantText | ToolUse | AssistantToolBatch


def _validate_assistant_tool_text(text: str) -> None:
    if not isinstance(text, str) or not text:
        raise ValueError("assistant tool text must be non-empty text or null")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("assistant tool text must be valid UTF-8") from None
    if "\x00" in text:
        raise ValueError("assistant tool text must not contain NUL")
    if (
        len(text) > MAX_ASSISTANT_TOOL_TEXT_CHARACTERS
        or len(encoded) > MAX_ASSISTANT_TOOL_TEXT_BYTES
    ):
        raise ValueError("assistant tool text exceeds the supported size")


def _validate_ledger_text(
    value: str,
    label: str,
    *,
    ascii_only: bool = False,
    control_free: bool = False,
    max_characters: int = 4096,
) -> None:
    if not isinstance(value, str) or not value or len(value) > max_characters:
        raise ValueError(f"{label} is invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{label} must be valid UTF-8") from None
    if ascii_only and not value.isascii():
        raise ValueError(f"{label} must be ASCII")
    controls = ("\x00", "\r", "\n", "\x1b") if control_free else ("\x00",)
    if any(character in value for character in controls):
        raise ValueError(f"{label} contains control characters")


@dataclass(frozen=True)
class ConversationRequest:
    """Provider-neutral model request with system policy separate from history."""

    system_prompt: SystemPromptSnapshot
    history: tuple[ConversationItem, ...]
    effective_summary: EffectiveContextSummary | None = None
    allow_tools: bool = True
    project_instructions: ProjectInstructionsSnapshot | None = None

    def __post_init__(self) -> None:
        if type(self.allow_tools) is not bool:
            raise ValueError("conversation request allow_tools must be boolean")
        if self.project_instructions is not None and not isinstance(
            self.project_instructions, ProjectInstructionsSnapshot
        ):
            raise ValueError("conversation request project instructions are invalid")


class ConversationProvider(Protocol):
    """Produce one structured assistant response from a complete request snapshot."""

    def respond(self, request: ConversationRequest) -> ProviderResponse:
        """Return final assistant text or one requested tool action."""
