import pytest

from leonervis_code.cli.failure_guidance import render_turn_failure, tool_result_guidance
from leonervis_code.core.orchestration import ProviderFailureKind
from leonervis_code.providers.errors import adapter_error, output_limit_error
from leonervis_code.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    ContextPreflightError,
    ContextPreflightErrorKind,
    RequestTokenCount,
    RequestTokenCountMethod,
)


def test_output_limit_guidance_preserves_commit_and_audit_truth() -> None:
    error = output_limit_error(
        provider_id="compatible",
        model_id="model",
        message="output limit reached",
        requested_output_tokens=4096,
    )

    rendered = render_turn_failure(error)

    assert "Provider error [output_limit]: output limit reached" in rendered
    assert "No turn was committed" in rendered
    assert "Tool side effects completed earlier remain in Action Audit" in rendered
    assert "increase /output in the REPL or --max-output-tokens for one-shot" in rendered


def test_retryable_provider_guidance_never_claims_automatic_retry() -> None:
    error = adapter_error(
        provider_id="compatible",
        model_id="model",
        kind=ProviderFailureKind.RATE_LIMITED,
        code="rate_limit",
        message="try later",
        retryable=True,
        retry_after_seconds=30,
    )

    rendered = render_turn_failure(error)

    assert "retry the same prompt after at least 30s" in rendered
    assert "will not retry automatically" in rendered


def test_context_failure_guidance_distinguishes_output_and_context_limits() -> None:
    output_report = ContextFitReport(
        None,
        RequestTokenCount(10, RequestTokenCountMethod.ESTIMATED),
        200,
        1000,
        100,
        ContextFitDecision.MODEL_OUTPUT_EXCEEDED,
    )
    context_report = ContextFitReport(
        None,
        RequestTokenCount(990, RequestTokenCountMethod.ESTIMATED),
        20,
        1000,
        None,
        ContextFitDecision.CONTEXT_EXCEEDED,
    )

    output = render_turn_failure(
        ContextPreflightError(ContextPreflightErrorKind.MODEL_OUTPUT_EXCEEDED, output_report)
    )
    context = render_turn_failure(
        ContextPreflightError(ContextPreflightErrorKind.CONTEXT_WINDOW_EXCEEDED, context_report)
    )

    assert "lower the output reserve with /output" in output
    assert "run /context" in context
    assert "/compact preview" in context


@pytest.mark.parametrize(
    ("result_code", "expected"),
    [
        ("mcp_result_limit", "narrower or paginated"),
        ("mcp_result_truncated", "bounded result limit"),
        ("mcp_configuration_stale", "refresh /mcp catalog"),
        ("mcp_live_tool_missing", "old promoted contract"),
        ("mcp_runtime_catalog_mismatch", "submit a new Turn"),
        ("mcp_schema_invalid", "inspect the candidate again"),
        ("mcp_session_changed", "refresh /mcp catalog"),
        ("mcp_auth_required", "redacted MCP OAuth"),
        ("mcp_credential_invalid", "run mcp doctor"),
        ("mcp_environment_missing", "server status"),
        ("mcp_cleanup_incomplete", "inspect /mcp status"),
        ("mcp_cancelled", "do not automatically repeat"),
        ("mcp_http_transport_failed", "side effects may be uncertain"),
        ("mcp_timeout", "inspect /actions last"),
        ("mcp_transport_closed", "/mcp status"),
        ("mcp_transport_failed", "do not automatically repeat"),
        ("mcp_tool_reported_error", "revise the MCP arguments"),
    ],
)
def test_mcp_tool_result_guidance_is_specific_and_conservative(
    result_code: str, expected: str
) -> None:
    guidance = tool_result_guidance("mcp_fixture_read_1234567890", result_code)

    assert guidance is not None
    assert expected in guidance


def test_mcp_tool_result_guidance_ignores_unknown_codes_and_non_mcp_tools() -> None:
    assert tool_result_guidance("mcp_fixture_read_1234567890", "unknown") is None
    assert tool_result_guidance("read_file", "mcp_timeout") is None
    assert tool_result_guidance("mcp_fixture_read_1234567890", None) is None
