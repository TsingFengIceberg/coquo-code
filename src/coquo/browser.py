"""Bounded browser automation contract with an optional Playwright backend.

Browser actions are Host-owned and intentionally separate from ordinary file
tools.  The default policy is deny-by-default for origins and scripts; a
caller must provide an allowlist and, for network-capable actions, an approval
callback.  Playwright remains optional so offline installations and tests do
not pull a browser runtime implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit


MAX_BROWSER_STEPS = 32
MAX_BROWSER_TEXT_BYTES = 64 * 1024
MAX_BROWSER_SELECTOR_CHARACTERS = 512
MAX_BROWSER_URL_CHARACTERS = 2048
MAX_BROWSER_TIMEOUT_SECONDS = 120
_SELECTOR = re.compile(r"[^\x00-\x1f\x7f]{1,512}\Z")


class BrowserAutomationError(RuntimeError):
    """Raised when browser policy or backend execution fails closed."""


class BrowserAction(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    EXTRACT_TEXT = "extract_text"
    SCREENSHOT = "screenshot"


class BrowserBackend(Protocol):
    def navigate(self, url: str, timeout_seconds: float) -> object: ...

    def click(self, selector: str, timeout_seconds: float) -> object: ...

    def fill(self, selector: str, value: str, timeout_seconds: float) -> object: ...

    def extract_text(self, selector: str | None, timeout_seconds: float) -> str: ...

    def screenshot(self, timeout_seconds: float) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class BrowserPolicy:
    allowed_origins: tuple[str, ...]
    max_steps: int = 12
    timeout_seconds: float = 30.0
    allow_http_localhost: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.allowed_origins, tuple) or not self.allowed_origins:
            raise ValueError("browser origin allowlist must not be empty")
        if any(not _canonical_origin(origin) for origin in self.allowed_origins):
            raise ValueError("browser origin allowlist contains an invalid origin")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= MAX_BROWSER_STEPS:
            raise ValueError("browser step limit is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not 0 < self.timeout_seconds <= MAX_BROWSER_TIMEOUT_SECONDS
        ):
            raise ValueError("browser timeout is invalid")
        if type(self.allow_http_localhost) is not bool:
            raise ValueError("browser localhost policy is invalid")


@dataclass(frozen=True)
class BrowserObservation:
    action: BrowserAction
    outcome: str
    value: str = ""
    step: int = 0


class BrowserAutomation:
    """Execute bounded browser actions through a Host-provided backend."""

    def __init__(
        self,
        backend: BrowserBackend,
        policy: BrowserPolicy,
        *,
        approve: Callable[[BrowserAction, str], bool] | None = None,
    ) -> None:
        if not isinstance(policy, BrowserPolicy):
            raise ValueError("browser policy is required")
        for name in ("navigate", "click", "fill", "extract_text", "screenshot", "close"):
            if not callable(getattr(backend, name, None)):
                raise ValueError(f"browser backend is missing {name}")
        if approve is not None and not callable(approve):
            raise ValueError("browser approval callback is invalid")
        self.backend = backend
        self.policy = policy
        self.approve = approve
        self._steps = 0
        self._closed = False

    @property
    def steps_used(self) -> int:
        return self._steps

    def navigate(self, url: str) -> BrowserObservation:
        parsed = _validate_url(url, self.policy)
        self._authorize(BrowserAction.NAVIGATE, parsed.geturl())
        self._step()
        try:
            self.backend.navigate(parsed.geturl(), self.policy.timeout_seconds)
        except Exception as error:
            return BrowserObservation(
                BrowserAction.NAVIGATE, "failed", type(error).__name__, self._steps
            )
        return BrowserObservation(BrowserAction.NAVIGATE, "completed", parsed.geturl(), self._steps)

    def click(self, selector: str) -> BrowserObservation:
        value = _validate_selector(selector)
        self._authorize(BrowserAction.CLICK, value)
        self._step()
        try:
            self.backend.click(value, self.policy.timeout_seconds)
        except Exception as error:
            return BrowserObservation(
                BrowserAction.CLICK, "failed", type(error).__name__, self._steps
            )
        return BrowserObservation(BrowserAction.CLICK, "completed", step=self._steps)

    def fill(self, selector: str, value: str) -> BrowserObservation:
        target = _validate_selector(selector)
        if (
            not isinstance(value, str)
            or "\x00" in value
            or len(value.encode("utf-8")) > MAX_BROWSER_TEXT_BYTES
        ):
            raise BrowserAutomationError("browser fill value is invalid")
        self._authorize(BrowserAction.FILL, target)
        self._step()
        try:
            self.backend.fill(target, value, self.policy.timeout_seconds)
        except Exception as error:
            return BrowserObservation(
                BrowserAction.FILL, "failed", type(error).__name__, self._steps
            )
        return BrowserObservation(BrowserAction.FILL, "completed", step=self._steps)

    def extract_text(self, selector: str | None = None) -> BrowserObservation:
        target = None if selector is None else _validate_selector(selector)
        self._authorize(BrowserAction.EXTRACT_TEXT, target or "document")
        self._step()
        try:
            value = self.backend.extract_text(target, self.policy.timeout_seconds)
        except Exception as error:
            return BrowserObservation(
                BrowserAction.EXTRACT_TEXT, "failed", type(error).__name__, self._steps
            )
        if not isinstance(value, str):
            raise BrowserAutomationError("browser backend returned non-text content")
        encoded = value.encode("utf-8")
        if len(encoded) > MAX_BROWSER_TEXT_BYTES:
            value = encoded[:MAX_BROWSER_TEXT_BYTES].decode("utf-8", errors="ignore")
        return BrowserObservation(BrowserAction.EXTRACT_TEXT, "completed", value, self._steps)

    def screenshot(self) -> BrowserObservation:
        self._authorize(BrowserAction.SCREENSHOT, "current-page")
        self._step()
        try:
            value = self.backend.screenshot(self.policy.timeout_seconds)
        except Exception as error:
            return BrowserObservation(
                BrowserAction.SCREENSHOT, "failed", type(error).__name__, self._steps
            )
        if not isinstance(value, bytes) or len(value) > MAX_BROWSER_TEXT_BYTES:
            raise BrowserAutomationError("browser screenshot exceeds the output limit")
        return BrowserObservation(
            BrowserAction.SCREENSHOT, "completed", f"bytes:{len(value)}", self._steps
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.backend.close()

    def _authorize(self, action: BrowserAction, detail: str) -> None:
        if self._closed:
            raise BrowserAutomationError("browser session is closed")
        if self.approve is None or not self.approve(action, detail):
            raise BrowserAutomationError("browser action requires Host approval")

    def _step(self) -> None:
        if self._steps >= self.policy.max_steps:
            raise BrowserAutomationError("browser step limit reached")
        self._steps += 1


class PlaywrightBrowserBackend:
    """Optional adapter around a Playwright Page-like object."""

    def __init__(self, page: Any) -> None:
        self.page = page

    def navigate(self, url: str, timeout_seconds: float) -> object:
        return self.page.goto(
            url, timeout=int(timeout_seconds * 1000), wait_until="domcontentloaded"
        )

    def click(self, selector: str, timeout_seconds: float) -> object:
        return self.page.locator(selector).click(timeout=int(timeout_seconds * 1000))

    def fill(self, selector: str, value: str, timeout_seconds: float) -> object:
        return self.page.locator(selector).fill(value, timeout=int(timeout_seconds * 1000))

    def extract_text(self, selector: str | None, timeout_seconds: float) -> str:
        locator = self.page.locator(selector) if selector is not None else self.page.locator("body")
        return locator.inner_text(timeout=int(timeout_seconds * 1000))

    def screenshot(self, timeout_seconds: float) -> bytes:
        del timeout_seconds
        return self.page.screenshot()

    def close(self) -> None:
        self.page.context.browser.close()


def _canonical_origin(value: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_BROWSER_URL_CHARACTERS:
        raise BrowserAutomationError("browser origin is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
    ):
        raise BrowserAutomationError("browser origin must be a credential-free HTTP(S) origin")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise BrowserAutomationError("non-local browser origins must use HTTPS")
    return f"{parsed.scheme}://{parsed.hostname.lower()}" + (
        f":{parsed.port}" if parsed.port else ""
    )


def _validate_url(value: str, policy: BrowserPolicy):
    if not isinstance(value, str) or len(value) > MAX_BROWSER_URL_CHARACTERS:
        raise BrowserAutomationError("browser URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.username
        or parsed.password
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
    ):
        raise BrowserAutomationError("browser URL must be credential-free HTTP(S)")
    if parsed.scheme == "http" and not policy.allow_http_localhost:
        raise BrowserAutomationError("HTTP navigation requires explicit localhost policy")
    origin = _canonical_origin(
        f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")
    )
    allowed = {_canonical_origin(item) for item in policy.allowed_origins}
    if origin not in allowed:
        raise BrowserAutomationError("browser URL origin is not allowlisted")
    return parsed


def _validate_selector(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _SELECTOR.fullmatch(value):
        raise BrowserAutomationError("browser selector is invalid")
    return value


__all__ = [
    "BrowserAction",
    "BrowserAutomation",
    "BrowserAutomationError",
    "BrowserBackend",
    "BrowserObservation",
    "BrowserPolicy",
    "PlaywrightBrowserBackend",
]
