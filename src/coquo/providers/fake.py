"""Deterministic provider implementations for learning and tests."""

from __future__ import annotations

from collections.abc import Sequence

from coquo.core.contracts import (
    AssistantText,
    ConversationRequest,
    ProviderResponse,
    UserMessage,
)
from coquo.core.session_title import (
    SessionTitleRequest,
    fallback_session_title,
)
from coquo.providers.streaming import ProviderResponseOutcome


class ScriptedFakeProvider:
    """Record structured contexts and return deterministic scripted responses."""

    def __init__(self, script: Sequence[ProviderResponse | Exception] | None = None) -> None:
        """Create a default echo fake or consume the supplied response script."""
        self._script = list(script) if script is not None else None
        self._next_outcome = 0
        self._received_requests: list[ConversationRequest] = []
        self._received_title_requests: list[SessionTitleRequest] = []

    @property
    def received_requests(self) -> tuple[ConversationRequest, ...]:
        """Return immutable snapshots of every provider request."""
        return tuple(self._received_requests)

    @property
    def received_title_requests(self) -> tuple[SessionTitleRequest, ...]:
        return tuple(self._received_title_requests)

    def generate_session_title_outcome(
        self, request: SessionTitleRequest
    ) -> ProviderResponseOutcome:
        """Simulate a separate no-tools title generation without consuming the turn script."""
        self._received_title_requests.append(request)
        return ProviderResponseOutcome(
            AssistantText(fallback_session_title(request.source_text)),
            False,
            None,
        )

    def respond(self, request: ConversationRequest) -> ProviderResponse:
        """Record ``request`` and return its next deterministic outcome."""
        self._received_requests.append(request)
        if self._script is None:
            latest_user = next(
                item for item in reversed(request.history) if isinstance(item, UserMessage)
            )
            return AssistantText(text=f"Fake response: {latest_user.text}")
        if self._next_outcome == len(self._script):
            raise RuntimeError("fake provider script is exhausted")

        outcome = self._script[self._next_outcome]
        self._next_outcome += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def insert_next(self, responses: Sequence[ProviderResponse | Exception]) -> None:
        """Insert deterministic outcomes before the unconsumed script tail."""
        if self._script is None:
            raise RuntimeError("cannot insert outcomes into the echo fake provider")
        self._script[self._next_outcome : self._next_outcome] = list(responses)
