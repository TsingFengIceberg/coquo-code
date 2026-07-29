"""Bounded Host-owned next steps for known terminal failure classes."""

from __future__ import annotations

from leonervis_code.cli.presentation import render_provider_adapter_error
from leonervis_code.core.action_coordinator import ActionIdentityChangedError
from leonervis_code.core.approvals import ApprovalGrantError
from leonervis_code.core.compaction import CompactionError
from leonervis_code.core.orchestration import ProviderFailureKind
from leonervis_code.providers.errors import ProviderAdapterError
from leonervis_code.providers.manager import RuntimeProviderStateError
from leonervis_code.providers.profile import ProviderProfileError
from leonervis_code.providers.request_context import (
    ContextPreflightError,
    ContextPreflightErrorKind,
)
from leonervis_code.session_store import SessionStoreError
from leonervis_code.tools.git_repository import GitObservationError


def render_turn_failure(error: BaseException, *, provider_prefix: str = "Provider error") -> str:
    """Render one known turn failure plus a conservative user-controlled next step."""
    if isinstance(error, ContextPreflightError):
        if error.kind == ContextPreflightErrorKind.MODEL_OUTPUT_EXCEEDED:
            next_step = "Next: lower the output reserve with /output, then submit the prompt again."
        else:
            next_step = (
                "Next: run /context, then use /compact preview and /compact when earlier turns "
                "are eligible, or start a new Session."
            )
        return f"Context preflight error: {error}\n{next_step}"
    if isinstance(error, ProviderAdapterError):
        message = render_provider_adapter_error(error, prefix=provider_prefix)
        note = "No turn was committed. Tool side effects completed earlier remain in Action Audit."
        return f"{message}\n{note}\n{_provider_next_step(error)}"
    if isinstance(error, (ProviderProfileError, RuntimeProviderStateError)):
        return f"Runtime error: {error}\nNext: run /status and /provider list, then correct or select a valid profile."
    if isinstance(error, (ApprovalGrantError, ActionIdentityChangedError)):
        return (
            f"Action authorization error: {error}\n"
            "Next: run /actions to inspect the exact decision; re-read the target before requesting a new action."
        )
    if isinstance(error, SessionStoreError):
        return (
            f"Session error: {error}\n"
            "Next: run /session show and inspect the workspace Session transcript before retrying."
        )
    return (
        f"Turn failed: {type(error).__name__}: {error}\n"
        "Next: inspect /status and /actions before deciding whether to retry."
    )


def command_failure_guidance(error: Exception) -> str | None:
    """Return a short next step for known Host command failures."""
    if isinstance(error, ProviderAdapterError):
        return _provider_next_step(error)
    if isinstance(error, (ProviderProfileError, RuntimeProviderStateError)):
        return "Next: run /status and /provider list before retrying."
    if isinstance(error, SessionStoreError):
        return "Next: inspect /session show and the selected transcript before retrying."
    if isinstance(error, CompactionError):
        return "Next: run /compact preview; continue without compacting when the selection is ineligible."
    if isinstance(error, GitObservationError):
        return "Next: verify the workspace is a supported Git repository root and use a workspace-relative path."
    return None


def _provider_next_step(error: ProviderAdapterError) -> str:
    failure = error.failure
    kind = failure.kind
    if kind == ProviderFailureKind.OUTPUT_LIMIT:
        return (
            "Next: increase /output in the REPL or --max-output-tokens for one-shot only if "
            "the model supports it, or submit a narrower request."
        )
    if kind in {ProviderFailureKind.AUTHENTICATION, ProviderFailureKind.AUTHORIZATION}:
        return (
            "Next: verify the selected profile credential outside the transcript, then run /status."
        )
    if kind == ProviderFailureKind.MODEL_UNAVAILABLE:
        return "Next: run /provider list and select an available profile or model."
    if kind in {
        ProviderFailureKind.RATE_LIMITED,
        ProviderFailureKind.TIMEOUT,
        ProviderFailureKind.TRANSPORT,
        ProviderFailureKind.PROVIDER_UNAVAILABLE,
    }:
        retry = (
            f" after at least {failure.retry_after_seconds}s"
            if failure.retry_after_seconds is not None
            else " later"
        )
        return f"Next: retry the same prompt{retry}; Leonervis will not retry automatically."
    if kind == ProviderFailureKind.RESPONSE_INVALID:
        return "Next: retry with a narrower request; if it repeats, inspect provider compatibility."
    if kind == ProviderFailureKind.CONTENT_REFUSAL:
        return "Next: revise the request or choose a provider whose policy permits the task."
    return (
        "Next: run /status, verify the selected model and output budget, then revise the request."
    )
