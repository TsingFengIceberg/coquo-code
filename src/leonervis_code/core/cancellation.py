"""Cooperative cancellation shared by terminal workers and bounded operations."""

from __future__ import annotations

from threading import Event


class TurnCancelled(BaseException):
    """Stop one uncommitted turn at a checked cancellation boundary."""


class TurnCancellation:
    """Thread-safe, single-direction cancellation token for one user turn."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> bool:
        """Request cancellation and report whether this call changed the token."""
        if self._event.is_set():
            return False
        self._event.set()
        return True

    def check(self) -> None:
        if self._event.is_set():
            raise TurnCancelled()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)
