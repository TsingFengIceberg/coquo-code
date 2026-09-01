"""Bounded Host-owned next steps for known terminal failure classes."""

from __future__ import annotations

from coquo.core.action_coordinator import ActionIdentityChangedError
from coquo.core.approvals import ApprovalGrantError
from coquo.core.compaction import CompactionError
from coquo.core.orchestration import ProviderFailureKind
from coquo.providers.errors import ProviderAdapterError
from coquo.providers.manager import RuntimeProviderStateError
from coquo.providers.profile import ProviderProfileError
from coquo.providers.reliability import ProviderReliabilityBudgetError
from coquo.providers.request_context import (
    ContextPreflightError,
    ContextPreflightErrorKind,
)
from coquo.session_store import SessionStoreError
from coquo.tools.git_repository import GitObservationError


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
        from coquo.cli.presentation import render_provider_adapter_error

        message = render_provider_adapter_error(error, prefix=provider_prefix)
        note = "No turn was committed. Tool side effects completed earlier remain in Action Audit."
        return f"{message}\n{note}\n{_provider_next_step(error)}"
    if isinstance(error, ProviderReliabilityBudgetError):
        return (
            f"Provider reliability budget error [{error.code}] after {error.attempts} attempt(s): {error}\n"
            "No turn was committed. Next: inspect /usage and /status, then lower the local provider budget or retry manually."
        )
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
    if isinstance(error, ProviderReliabilityBudgetError):
        return "Inspect /usage and /status; the local reliability budget stopped the operation before another attempt."
    if isinstance(error, (ProviderProfileError, RuntimeProviderStateError)):
        return "Next: run /status and /provider list before retrying."
    if isinstance(error, SessionStoreError):
        return "Next: inspect /session show and the selected transcript before retrying."
    if isinstance(error, CompactionError):
        return "Next: run /compact preview; continue without compacting when the selection is ineligible."
    if isinstance(error, GitObservationError):
        return "Next: verify the workspace is a supported Git repository root and use a workspace-relative path."
    return None


def tool_result_guidance(tool_name: str, result_code: str | None) -> str | None:
    """Return a conservative next step from one trusted tool result code."""
    if result_code is None:
        return None
    if tool_name.startswith("mcp_"):
        return _mcp_result_guidance(result_code)
    if tool_name != "run_command":
        return None
    if result_code == "command_sandbox_unavailable":
        return "Next: run /sandbox check; the requested command was not started."
    if result_code == "command_resource_limits_unavailable":
        return "Next: run /sandbox check; fixed command resource limits could not be enforced and the command was not started."
    if result_code == "command_cwd_invalid":
        return "Next: verify the workspace-relative cwd before requesting a new command."
    if result_code == "command_exited_nonzero":
        return "Next: inspect the reported stdout, stderr, and exit code before changing or rerunning the command."
    if result_code == "command_signaled":
        return "Next: inspect the signal and workspace state; do not assume command side effects were rolled back."
    if result_code == "command_timed_out":
        return "Next: inspect workspace state and /actions last before deciding whether a longer timeout is safe."
    if result_code == "command_cancelled":
        return (
            "Next: inspect workspace state and /actions last before requesting the command again."
        )
    if result_code in {
        "command_timeout_cleanup_incomplete",
        "command_cancel_cleanup_incomplete",
        "command_cleanup_incomplete",
        "command_sandbox_cleanup_incomplete",
    }:
        return (
            "Next: process cleanup is uncertain; inspect the Host and workspace before any retry."
        )
    return None


def _mcp_result_guidance(result_code: str) -> str | None:
    if result_code in {"mcp_result_limit", "mcp_result_truncated"}:
        return (
            "Next: request a narrower or paginated MCP operation; repeating the same call will "
            "hit the same bounded result limit."
        )
    if result_code in {
        "mcp_configuration_stale",
        "mcp_live_tool_missing",
        "mcp_runtime_catalog_mismatch",
        "mcp_schema_invalid",
        "mcp_session_changed",
    }:
        return (
            "Next: refresh /mcp catalog, inspect the candidate again, and submit a new Turn; the "
            "old promoted contract must not be reused."
        )
    if result_code in {
        "mcp_auth_required",
        "mcp_credential_invalid",
        "mcp_environment_missing",
    }:
        return (
            "Next: inspect redacted MCP OAuth and server status outside the model transcript, then "
            "run mcp doctor for the configured server."
        )
    if result_code == "mcp_cleanup_incomplete":
        return (
            "Next: inspect /mcp status before another call; process or remote-session cleanup is "
            "not confirmed."
        )
    if result_code in {
        "mcp_cancelled",
        "mcp_http_transport_failed",
        "mcp_timeout",
        "mcp_transport_closed",
        "mcp_transport_failed",
    }:
        return (
            "Next: inspect /actions last and /mcp status; delivery or side effects may be "
            "uncertain, so do not automatically repeat the call."
        )
    if result_code == "mcp_tool_reported_error":
        return "Next: revise the MCP arguments from the returned bounded error before a new call."
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
        return f"Next: retry the same prompt{retry}; Coquo will not retry automatically."
    if kind == ProviderFailureKind.RESPONSE_INVALID:
        return "Next: retry with a narrower request; if it repeats, inspect provider compatibility."
    if kind == ProviderFailureKind.CONTENT_REFUSAL:
        return "Next: revise the request or choose a provider whose policy permits the task."
    return (
        "Next: run /status, verify the selected model and output budget, then revise the request."
    )
