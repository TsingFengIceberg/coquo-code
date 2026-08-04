from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from leonervis_code.agent.loop import AgentLoop
from leonervis_code.agent.tool_events import (
    AssistantToolTextReceived,
    ProviderInvocationPreflighted,
    ProviderInvocationUsageReceived,
    ProviderSearchActivityReceived,
    ProviderSearchSummaryReceived,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestLimited,
    ToolRequestSkipped,
    ToolRequestStarted,
    ToolResultDetails,
    ToolTurnSummaryCommitted,
)
from leonervis_code.cli.presentation import (
    BLUE,
    GREEN,
    RED,
    RESET,
    YELLOW,
    MAX_SESSION_PREVIEW_RENDER_BYTES,
    MAX_TOOL_LEDGER_RENDER_BYTES,
    ToolDetailMode,
    render_compact_preview,
    render_compaction_history,
    render_context_inspection,
    render_context_meter,
    render_git_diff,
    render_git_log,
    render_git_show,
    render_git_status,
    render_host_message,
    render_action_audits,
    render_activity_line,
    render_message,
    render_message_separator,
    render_output_budget,
    render_output_budget_rejection,
    render_output_budget_update,
    render_prompt,
    render_prompt_toolbar,
    render_prompt_event,
    render_provider_adapter_error,
    render_resume_rejection,
    render_runtime_status,
    render_runtime_switch,
    render_session_resume,
    render_session_diagnosis,
    render_session_export,
    render_session_info,
    render_session_preview,
    render_session_search,
    render_session_turn_range,
    render_switch_rejection,
    render_turn_trace,
    render_tool_ledgers,
    render_durable_usage_summary,
    render_usage_summary,
)
from leonervis_code.providers.manager import (
    CurrentTargetContextAssessment,
    OutputBudgetUpdateResult,
    RuntimeStatus,
)
from leonervis_code.providers.errors import output_limit_error
from leonervis_code.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    RequestTokenCount,
    RequestTokenCountMethod,
)
from leonervis_code.providers.streaming import (
    ProviderSearchObservation,
    ProviderSearchPhase,
)
from leonervis_code.core.compaction import CompactionTrigger
from leonervis_code.core.contracts import (
    AssistantText,
    ToolArguments,
    ToolOutcomeEntry,
    ToolRequestOutcome,
    ToolTurnLedger,
    UserMessage,
)
from leonervis_code.core.permissions import (
    PermissionAction,
    PermissionDecision,
    PermissionReason,
    PermissionResult,
)
from leonervis_code.session import (
    AutoCompactionCommitted,
    AutoCompactionNotApplied,
    AutoCompactionStarted,
    CompactContextResult,
    CompactContextPreview,
    CompactionHistoryEntry,
    CompactionHistoryResult,
    DurableUsageOperation,
    DurableUsageSnapshot,
    EffectiveContextInspection,
    ResumeEffect,
    SessionResumeResult,
    SessionTitleFallbackApplied,
    TurnUsageCompleted,
)
from leonervis_code.providers.usage import (
    ProviderInvocationKind,
    ProviderInvocationUsage,
    ProviderTokenUsage,
    ProviderUsageTotals,
    RuntimeUsageTracker,
)
from leonervis_code.session_records import (
    ActionAuditStatus,
    ApprovalAuditOutcome,
    BindingSnapshot,
    SessionNameSource,
    SessionTitleFallbackReason,
)
from leonervis_code.session_store import (
    LatestUpdateStatus,
    SessionInfo,
    ToolLedgerQueryResult,
    TurnToolLedger,
)
from leonervis_code.tools.glob import GlobTool
from leonervis_code.tools.grep import GrepTool
from leonervis_code.tools.list_directory import ListDirectoryTool
from leonervis_code.tools.read_file import ReadFileTool
from leonervis_code.tools.git_diff import GitDiffScope, GitDiffSnapshot
from leonervis_code.tools.git_log import GitLogEntry, GitLogSnapshot
from leonervis_code.tools.git_show import GitShowSnapshot
from leonervis_code.tools.git_status import GitStatusEntry, GitStatusSnapshot


def test_render_git_changes_is_bounded_clear_and_terminal_safe() -> None:
    status = GitStatusSnapshot(
        (
            GitStatusEntry("new\nname.txt", "added", "clean"),
            GitStatusEntry("renamed.txt", "renamed", "clean", "old.txt"),
        ),
        True,
        "unused",
    )
    rendered_status = render_git_status(status)
    assert "2 visible changes (truncated)" in rendered_status
    assert "new\\nname.txt" in rendered_status
    assert "renamed.txt <- old.txt" in rendered_status
    assert "omitted" in rendered_status

    diff = GitDiffSnapshot(
        GitDiffScope.UNSTAGED,
        ".",
        "diff --git a/a b/a\n+unsafe\x1b[2J\r\n",
        False,
    )
    rendered_diff = render_git_diff(diff)
    assert "Git diff (unstaged):" in rendered_diff
    assert "\\x1b[2J\\x0d" in rendered_diff
    assert "\x1b" not in rendered_diff


def test_render_git_history_and_commit_are_copyable_and_terminal_safe() -> None:
    commit_id = "a" * 40
    history = GitLogSnapshot(
        (
            GitLogEntry(
                commit_id,
                ("b" * 40,),
                "2026-07-29T01:02:03+08:00",
                "unsafe\nsubject\x1b[2J",
                True,
            ),
        ),
        "src/app.py",
        True,
        "unused",
    )
    rendered_history = render_git_log(history)
    assert commit_id in rendered_history
    assert "unsafe\\nsubject\\x1b[2J" in rendered_history
    assert "subject truncated" in rendered_history
    assert "\x1b" not in rendered_history

    shown = GitShowSnapshot(
        commit_id,
        ("b" * 40,),
        "2026-07-29T01:02:03+08:00",
        ".",
        "message\x1b[2J\n",
        False,
        "+patch\r\n",
        True,
        "unused",
    )
    rendered_show = render_git_show(shown)
    assert f"Git commit: {commit_id}" in rendered_show
    assert "message\\x1b[2J" in rendered_show
    assert "+patch\\x0d\n" in rendered_show
    assert "Patch (truncated):" in rendered_show
    assert "\x1b" not in rendered_show


@dataclass
class Info:
    session_id: str = "12345678-1234-4234-9234-123456789abc"


def test_output_limit_presentation_includes_requested_and_actual_usage() -> None:
    error = output_limit_error(
        provider_id="compatible",
        model_id="model",
        message="provider response reached the configured output-token limit",
        requested_output_tokens=4096,
        usage=ProviderTokenUsage(4900, 4096),
        partial_response_observed=True,
    )

    rendered = render_provider_adapter_error(error, prefix="Provider error")

    assert rendered.splitlines() == [
        "Provider error [output_limit]: provider response reached the configured output-token limit",
        "Output limit: requested 4096 tokens; provider reported 4096 output tokens and 4900 input tokens.",
        "The provider response was incomplete with partial content and was rejected.",
    ]


def status(*, mode="fake", profile=None, provider="fake", model=None):
    return RuntimeStatus(
        mode=mode,
        profile=profile,
        selection_source="default",
        provider_id=provider,
        protocol=None,
        selected_model=model,
        wire_model=model,
        base_url=None,
        base_url_source=None,
        credential_required=False,
        credential_present=False,
    )


def test_prompt_is_minimal_and_toolbar_shows_model_and_workspace() -> None:
    assert render_prompt(status(), Info(), color=False) == "› "
    assert render_message_separator(60, color=False) == f"  {'─' * 20}"
    assert render_message_separator(120, color=False) == f"  {'─' * 24}"
    assert render_prompt_toolbar(status(), Path("/workspace"), color=False) == (
        "  fake · /workspace"
    )


def test_activity_line_is_bounded_safe_and_text_only() -> None:
    rendered = render_activity_line("Preparing provider request", color=False)
    unsafe = render_activity_line("Running\x1b[2J\ncommand", color=False)
    bounded = render_activity_line("x" * 100, color=False)

    assert rendered == "  Preparing provider request..."
    assert unsafe == "  Running?[2J?command..."
    assert len(bounded.removeprefix("  ")) == 72
    assert bounded.endswith("...")
    assert render_activity_line("Saving Session.", color=False).endswith("Session.")

    with pytest.raises(ValueError, match="status"):
        render_activity_line(" ", color=False)


def test_output_budget_presentation_distinguishes_effective_default_and_rejection() -> None:
    current = RuntimeStatus(
        mode="real",
        profile="work",
        selection_source="project",
        provider_id="custom",
        protocol="openai_chat_completions",
        selected_model="model",
        wire_model="model",
        base_url="http://127.0.0.1:11434/v1",
        base_url_source="profile",
        credential_required=False,
        credential_present=False,
        max_output_tokens=8192,
        default_max_output_tokens=4096,
        max_output_tokens_source="runtime",
        model_max_output_tokens=16_000,
    )
    message, kind = render_output_budget(current)
    assert kind == "info"
    assert message.splitlines() == [
        "Effective output budget: 8192 tokens (runtime)",
        "Configured default: 4096 tokens",
        "Model maximum: 16000 tokens",
        "Scope: current process only; provider profile and resumed Session selection are unchanged.",
    ]

    fit = ContextFitReport(
        target=None,
        input_count=RequestTokenCount(1000, RequestTokenCountMethod.ESTIMATED),
        requested_output_tokens=8192,
        context_window_limit=32_000,
        model_output_limit=16_000,
        decision=ContextFitDecision.FITS,
    )
    updated, update_kind = render_output_budget_update(
        OutputBudgetUpdateResult(current, fit, 4096, True)
    )
    assert update_kind == "success"
    assert "Output budget changed: 4096 -> 8192 tokens (runtime)." in updated
    assert "Committed context fits: input=1000 (estimated)" in updated

    overflow = ContextFitReport(
        target=None,
        input_count=RequestTokenCount.unknown("not counted"),
        requested_output_tokens=20_000,
        context_window_limit=32_000,
        model_output_limit=16_000,
        decision=ContextFitDecision.MODEL_OUTPUT_EXCEEDED,
    )
    assert render_output_budget_rejection(overflow) == (
        "Output budget change rejected: reserve=20000 > model max output=16000. "
        "Current output budget is unchanged."
    )


def test_context_meter_toolbar_and_usage_summary_are_bounded_and_explicit() -> None:
    report = ContextFitReport(
        target=None,
        input_count=RequestTokenCount(72_400, RequestTokenCountMethod.ESTIMATED),
        requested_output_tokens=8_000,
        context_window_limit=128_000,
        model_output_limit=16_000,
        decision=ContextFitDecision.FITS,
    )
    tracker = RuntimeUsageTracker()
    tracker.record_context(report)
    cursor = tracker.turn_cursor()
    tracker.record(ProviderInvocationKind.TURN, ProviderTokenUsage(70_000, 846))
    usage = tracker.finish_turn(cursor)

    meter = render_context_meter(report, invocation_index=2, invocation_limit=24)
    assert meter == ("[context 2/24] [██████▒░░░] input 72.4k + reserve 8.0k / 128.0k · estimated")
    assert "ctx ██████▒░░░ 63%" in render_prompt_toolbar(
        status(mode="real", provider="openai", model="gpt-5"),
        Path("/workspace"),
        color=False,
        usage=usage,
    )
    assert render_prompt_event(ProviderInvocationPreflighted(2, 24, report))[0] == meter
    assert (
        "70.0k in / 846 out"
        in render_prompt_event(
            ProviderInvocationUsageReceived(2, 24, ProviderTokenUsage(70_000, 846))
        )[0]
    )
    assert "Latest turn: 70.0k in / 846 out" in render_usage_summary(usage)
    assert "Latest compaction invocation: none" in render_usage_summary(usage)
    assert "Turn usage:" in render_prompt_event(TurnUsageCompleted(usage))[0]


def test_durable_usage_presentation_distinguishes_unknown_and_legacy() -> None:
    operations = (
        DurableUsageOperation(
            3,
            "2026-07-28T00:00:00.000000Z",
            "turn",
            "committed",
            "custom",
            "model",
            (
                ProviderInvocationUsage(
                    1,
                    ProviderInvocationKind.TURN,
                    ProviderTokenUsage(120, 30),
                ),
                ProviderInvocationUsage(2, ProviderInvocationKind.TURN, None),
            ),
        ),
        DurableUsageOperation(
            4,
            "2026-07-28T00:00:01.000000Z",
            "turn",
            "failed",
            "custom",
            "model",
            None,
        ),
    )
    snapshot = DurableUsageSnapshot(
        operations,
        ProviderUsageTotals(120, 30, 1, 1),
        1,
    )

    summary = render_durable_usage_summary(snapshot)
    assert "Session usage: 120 in / 30 out · known=1 unknown=1" in summary
    assert "legacy usage unavailable=1" in summary
    turns = render_durable_usage_summary(snapshot, turns=True)
    assert "record #3 committed · custom/model" in turns
    assert "record #4 failed · custom/model · legacy usage unavailable" in turns
    assert (
        render_prompt_toolbar(
            status(mode="real", profile="work-openai", provider="openai", model="gpt-5"),
            Path.home() / "Projects" / "leonervis-code",
            color=False,
        )
        == "  gpt-5 · ~/Projects/leonervis-code"
    )


def test_action_audits_are_recent_bounded_and_redacted() -> None:
    def audit(sequence: int, path: str, status, *, result_code=None):
        return SimpleNamespace(
            identity=SimpleNamespace(
                tool_name="write_file",
                action=PermissionAction.WORKSPACE_CREATE,
                arguments=ToolArguments.from_mapping(
                    {"content": f"secret-content-{sequence}", "path": path}
                ),
            ),
            permission_result=PermissionResult(
                PermissionDecision.ASK,
                PermissionReason.APPROVAL_REQUIRED_WORKSPACE_CREATE,
            ),
            approval_outcome=ApprovalAuditOutcome.ACCEPTED,
            status=status,
            result_code=result_code,
            requested_sequence=sequence,
        )

    rendered = render_action_audits(
        (
            audit(1, "first.txt", ActionAuditStatus.SUCCEEDED, result_code="created"),
            audit(
                6,
                "odd\nname.txt",
                ActionAuditStatus.PARTIAL,
                result_code="durability\nunknown",
            ),
        ),
        1,
    )

    assert "Showing 1 most recent of 2 action audits." in rendered
    assert "Action #6: write_file" in rendered
    assert "class: workspace-create" in rendered
    assert "path: 'odd\\nname.txt'" in rendered
    assert "permission: ask (approval_required_workspace_create)" in rendered
    assert "approval: accepted" in rendered
    assert "result: partial (durability\\nunknown)" in rendered
    assert "first.txt" not in rendered
    assert "secret-content" not in rendered
    assert render_action_audits((), 20) == "No action audits yet."


def test_tool_ledgers_render_summary_details_and_legacy_availability() -> None:
    ledger = ToolTurnLedger(
        (
            ToolOutcomeEntry(
                "internal-tool-id",
                "mkdir",
                1,
                ToolRequestOutcome.ERROR,
                "invalid_request",
            ),
            ToolOutcomeEntry(
                "another-internal-id",
                "write_file",
                2,
                ToolRequestOutcome.SKIPPED_AFTER_FAILURE,
                "prior_batch_action_not_succeeded",
            ),
        )
    )
    result = ToolLedgerQueryResult(
        total_turns=3,
        turns=(
            TurnToolLedger(2, 4, "2026-07-28T00:00:00.000000Z", 4, None),
            TurnToolLedger(3, 7, "2026-07-28T00:01:00.000000Z", 5, ledger),
        ),
    )

    summary = render_tool_ledgers(result, details=False)
    detailed = render_tool_ledgers(result, details=True)

    assert "Showing 2 most recent of 3 committed turns." in summary
    assert "Turn #2 (record #4" in summary
    assert "tool ledger unavailable (legacy turn_committed v4)" in summary
    assert "requested=2 admitted=2 dispatched=1 succeeded=0 error=1 skipped=1" in summary
    assert "Details:" not in summary
    assert "Turn #3 requests:" in detailed
    assert "#1 mkdir: error (invalid_request)" in detailed
    assert "#2 write_file: skipped-after-failure" in detailed
    assert "internal-tool-id" not in detailed
    assert render_tool_ledgers(ToolLedgerQueryResult(0, ()), details=False) == (
        "No committed turns yet."
    )
    empty = ToolLedgerQueryResult(
        1,
        (TurnToolLedger(1, 1, "2026-07-28T00:00:00.000000Z", 5, ToolTurnLedger()),),
    )
    assert render_tool_ledgers(empty, details=True).endswith(
        "No persisted tool request details in selected turns."
    )


def test_tool_ledger_details_have_a_complete_line_output_bound() -> None:
    turns = []
    for turn_number in range(1, 21):
        entries = tuple(
            ToolOutcomeEntry(
                f"turn-{turn_number}-tool-{request_index}",
                "write_file",
                request_index,
                ToolRequestOutcome.ERROR,
                "x" * 160,
            )
            for request_index in range(1, 41)
        )
        turns.append(
            TurnToolLedger(
                turn_number,
                turn_number,
                "2026-07-28T00:00:00.000000Z",
                5,
                ToolTurnLedger(entries),
            )
        )

    rendered = render_tool_ledgers(
        ToolLedgerQueryResult(20, tuple(turns)),
        details=True,
    )

    assert len(rendered.encode("utf-8")) <= MAX_TOOL_LEDGER_RENDER_BYTES
    assert "[truncated: additional ledger entries omitted]" in rendered
    assert not rendered.endswith("\n")


def test_action_audits_explain_nonexecuted_and_interrupted_lifecycles() -> None:
    def audit(
        sequence: int,
        status: ActionAuditStatus,
        *,
        decision: PermissionDecision | None,
        reason: PermissionReason | None,
        approval: ApprovalAuditOutcome | None = None,
    ):
        permission = (
            PermissionResult(decision, reason)
            if decision is not None and reason is not None
            else None
        )
        return SimpleNamespace(
            identity=SimpleNamespace(
                tool_name="write_file",
                action=PermissionAction.WORKSPACE_OVERWRITE,
                arguments=ToolArguments.from_mapping({"content": "secret", "path": "note.txt"}),
            ),
            permission_result=permission,
            approval_outcome=approval,
            status=status,
            result_code=None,
            requested_sequence=sequence,
        )

    rendered = render_action_audits(
        (
            audit(1, ActionAuditStatus.REQUESTED, decision=None, reason=None),
            audit(
                2,
                ActionAuditStatus.DENIED,
                decision=PermissionDecision.DENY,
                reason=PermissionReason.DENIED_READ_ONLY_MODE,
            ),
            audit(
                3,
                ActionAuditStatus.AUTHORIZED,
                decision=PermissionDecision.ALLOW,
                reason=PermissionReason.ALLOWED_WORKSPACE_OVERWRITE_AUTO,
            ),
            audit(
                4,
                ActionAuditStatus.AWAITING_APPROVAL,
                decision=PermissionDecision.ASK,
                reason=PermissionReason.APPROVAL_REQUIRED_WORKSPACE_OVERWRITE,
            ),
            audit(
                5,
                ActionAuditStatus.ABANDONED,
                decision=PermissionDecision.ASK,
                reason=PermissionReason.APPROVAL_REQUIRED_WORKSPACE_OVERWRITE,
            ),
            audit(
                6,
                ActionAuditStatus.OUTCOME_UNKNOWN,
                decision=PermissionDecision.ASK,
                reason=PermissionReason.APPROVAL_REQUIRED_WORKSPACE_OVERWRITE,
                approval=ApprovalAuditOutcome.ACCEPTED,
            ),
        ),
        20,
    )

    assert "Action #1" in rendered
    assert "permission: pending\n  approval: not reached\n  result: requested" in rendered
    assert (
        "permission: deny (denied_read_only_mode)\n  approval: not requested\n  result: denied"
    ) in rendered
    assert (
        "permission: allow (allowed_workspace_overwrite_auto)\n"
        "  approval: not required\n"
        "  result: authorized"
    ) in rendered
    assert "approval: pending\n  result: awaiting-approval" in rendered
    assert "approval: not recorded\n  result: abandoned" in rendered
    assert "approval: accepted\n  result: outcome-unknown" in rendered


def test_action_audit_renders_copy_paths_without_internal_state() -> None:
    audit = SimpleNamespace(
        identity=SimpleNamespace(
            tool_name="copy_file",
            action=PermissionAction.WORKSPACE_CREATE,
            arguments=ToolArguments.from_mapping(
                {"source": "src/a.bin", "destination": "backup/a.bin"}
            ),
        ),
        permission_result=PermissionResult(
            PermissionDecision.ALLOW,
            PermissionReason.ALLOWED_WORKSPACE_CREATE_AUTO,
        ),
        approval_outcome=None,
        status=ActionAuditStatus.SUCCEEDED,
        result_code="file_copied",
        requested_sequence=12,
    )

    rendered = render_action_audits((audit,), 20)

    assert "Action #12: copy_file" in rendered
    assert "source: 'src/a.bin'" in rendered
    assert "destination: 'backup/a.bin'" in rendered
    assert "result: succeeded (file_copied)" in rendered


def test_toolbar_sanitizes_and_bounds_model_fields() -> None:
    first = status(mode="real", profile="safe|name\x1b[31m", provider="custom", model="one")
    second = status(mode="real", profile="safe|name\x1b[31m", provider="custom", model="two")

    assert render_prompt_toolbar(first, Path("/workspace"), color=False).startswith("  one · ")
    assert render_prompt_toolbar(second, Path("/workspace"), color=False).startswith("  two · ")

    unsafe = status(mode="real", provider="custom", model="safe\x1b[31m\nmodel")
    assert render_prompt_toolbar(unsafe, Path("/workspace"), color=False) == (
        "  safe?[31m?model · /workspace"
    )
    long = status(mode="real", provider="custom", model="a" * 60)
    assert render_prompt_toolbar(long, Path("/workspace"), color=False).startswith(
        "  aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa... · "
    )


def test_toolbar_shows_bounded_session_name_between_model_and_context() -> None:
    session = SimpleNamespace(name="Review provider adapters")

    assert (
        render_prompt_toolbar(
            status(mode="real", provider="custom", model="model-one"),
            Path("/workspace"),
            color=False,
            session=session,
        )
        == "  model-one · Review provider adapters · /workspace"
    )

    unsafe = SimpleNamespace(name="session\x1b[31m\nname" + "x" * 40)
    rendered = render_prompt_toolbar(
        status(),
        Path("/workspace"),
        color=False,
        session=unsafe,
    )
    assert rendered.startswith("  fake · session?[31m?namexxxxxxxxxxxx... · ")
    assert "\x1b" not in rendered
    assert "\n" not in rendered

    archived = render_prompt_toolbar(
        status(),
        Path("/workspace"),
        color=False,
        session=SimpleNamespace(name="Old review", archived=True),
    )
    assert "Old review [archived]" in archived
    pinned_archived = render_prompt_toolbar(
        status(),
        Path("/workspace"),
        color=False,
        session=SimpleNamespace(name="Kept review", archived=True, pinned=True),
    )
    assert "Kept review [pinned archived]" in pinned_archived


def test_session_info_and_title_event_render_safe_fallback_reason(tmp_path: Path) -> None:
    info = SessionInfo(
        session_id="12345678-1234-4234-9234-123456789abc",
        path=tmp_path / "session.jsonl",
        workspace=str(tmp_path),
        workspace_fingerprint="v1-" + "a" * 64,
        created_at="2026-07-18T00:00:00.000000Z",
        record_count=2,
        turn_count=1,
        closed=False,
        binding=BindingSnapshot.fake(),
        name="Fallback title",
        name_source=SessionNameSource.FALLBACK,
        archived=True,
        pinned=True,
        title_fallback_reason=SessionTitleFallbackReason.PROVIDER_OUTPUT_LIMIT,
    )

    rendered = render_session_info(info)
    assert "Archived: yes" in rendered
    assert "Pinned: yes" in rendered
    assert "Title fallback: provider output limit" in rendered
    message, kind = render_prompt_event(
        SessionTitleFallbackApplied(SessionTitleFallbackReason.PROVIDER_OUTPUT_LIMIT)
    )
    assert message == "Session naming used a Host fallback: provider output limit."
    assert kind == "warning"


def test_session_preview_escapes_controls_and_enforces_output_bound(tmp_path: Path) -> None:
    info = SessionInfo(
        session_id="12345678-1234-4234-9234-123456789abc",
        path=tmp_path / "session.jsonl",
        workspace=str(tmp_path),
        workspace_fingerprint="v1-" + "a" * 64,
        created_at="2026-07-30T00:00:00.000000Z",
        record_count=2,
        turn_count=1,
        closed=True,
        binding=BindingSnapshot.fake(),
        name="Preview",
        name_source=SessionNameSource.MANUAL,
    )
    preview = SimpleNamespace(
        info=info,
        total_turns=1,
        turns=(
            SimpleNamespace(
                user=SimpleNamespace(text="hello\x1b[31m"),
                assistant=SimpleNamespace(text="x" * (MAX_SESSION_PREVIEW_RENDER_BYTES * 2)),
            ),
        ),
    )

    rendered = render_session_preview(preview)

    assert "hello\\x1b[31m" in rendered
    assert "\x1b" not in rendered
    assert rendered.endswith("[Session preview truncated at 32768 UTF-8 bytes.]")
    assert len(rendered.encode("utf-8")) <= MAX_SESSION_PREVIEW_RENDER_BYTES


def test_provider_search_events_render_content_free_progress_and_degradation() -> None:
    progress, progress_kind = render_prompt_event(
        ProviderSearchActivityReceived(ProviderSearchPhase.SEARCHING)
    )
    malformed, malformed_kind = render_prompt_event(
        ProviderSearchSummaryReceived(ProviderSearchObservation(1, 0, ("search",), 2, 1, 2))
    )
    missing, missing_kind = render_prompt_event(
        ProviderSearchSummaryReceived(ProviderSearchObservation(1, 0, ("open_page",), 0, 0))
    )

    assert progress == "Provider search: searching"
    assert progress_kind == "info"
    assert malformed.startswith("Provider search discarded malformed citations:")
    assert "discarded_citations=2" in malformed
    assert malformed_kind == "warning"
    assert missing.startswith("Provider search completed; structured citations unavailable.")
    assert missing_kind == "warning"


def test_session_management_projection_renderers_are_safe_and_structured(tmp_path: Path) -> None:
    info = SessionInfo(
        session_id="12345678-1234-4234-9234-123456789abc",
        path=tmp_path / "session.jsonl",
        workspace=str(tmp_path),
        workspace_fingerprint="v1-" + "a" * 64,
        created_at="2026-07-30T00:00:00.000000Z",
        record_count=2,
        turn_count=1,
        closed=True,
        binding=BindingSnapshot.fake(),
        name="Preview",
        name_source=SessionNameSource.MANUAL,
    )
    turn = SimpleNamespace(
        user=SimpleNamespace(text="hello\x1b[31m"),
        assistant=SimpleNamespace(text="answer"),
    )

    ranged = render_session_turn_range(
        SimpleNamespace(info=info, total_turns=1, start_turn=1, turns=(turn,))
    )
    searched = render_session_search(
        SimpleNamespace(
            query="hello",
            candidate_sessions=1,
            scanned_sessions=1,
            scanned_transcript_bytes=100,
            matches=(
                SimpleNamespace(
                    info=info,
                    turn_number=1,
                    role="user",
                    line_number=1,
                    excerpt="hello\x1b[31m",
                ),
            ),
            truncated=False,
        )
    )
    exported = render_session_export(SimpleNamespace(info=info, turns=(turn,)), "json")
    diagnosis = render_session_diagnosis(
        SimpleNamespace(
            session_id=info.session_id,
            status=SimpleNamespace(value="repairable_tail"),
            code="incomplete_final_record",
            transcript_bytes=120,
            record_count=2,
            turn_count=1,
            recoverable_tail_bytes=20,
        )
    )

    assert "Turn #1" in ranged
    assert "hello\\x1b[31m" in ranged
    assert "Search completed" in searched
    assert "\\u001b" not in exported
    assert json.loads(exported)["turns"][0]["user"] == "hello\\x1b[31m"
    assert "Recoverable incomplete tail bytes: 20" in diagnosis


def test_prompt_and_toolbar_have_safe_fallbacks() -> None:
    assert render_prompt(None, None, color=False) == "› "
    assert render_prompt_toolbar(None, Path("/workspace"), color=False) == "  /workspace"
    assert (
        render_prompt_toolbar(
            status(mode="real", provider="custom", model=None),
            Path("/workspace"),
            color=False,
        )
        == "  custom · /workspace"
    )


def test_runtime_status_renders_context_capability_without_changing_prompt() -> None:
    resolved = RuntimeStatus(
        **{
            **status(
                mode="real", profile="work", provider="anthropic", model="claude-opus-4-8"
            ).__dict__,
            "protocol": "anthropic_messages",
            "base_url": "https://api.anthropic.com",
            "base_url_source": "default",
            "context_window_tokens": 1_000_000,
            "context_window_source": "builtin_catalog",
        }
    )

    rendered = render_runtime_status(resolved)

    assert "Context window: 1000000 tokens (builtin_catalog)" in rendered
    assert "1000000" not in render_prompt_toolbar(resolved, Path("/workspace"), color=False)


def inspection(tmp_path, report=None, diagnostic=None, *history):
    loop = AgentLoop(
        None,
        ReadFileTool(tmp_path),
        GlobTool(tmp_path),
        GrepTool(tmp_path),
        ListDirectoryTool(tmp_path),
        initial_history=tuple(history),
    )
    target = CurrentTargetContextAssessment(status(), report, diagnostic)
    return EffectiveContextInspection(loop.effective_context_snapshot(), target)


def test_context_inspection_renders_fit_unknown_and_capacity(tmp_path) -> None:
    fits = ContextFitReport(
        target=None,
        input_count=RequestTokenCount(80, RequestTokenCountMethod.ESTIMATED),
        requested_output_tokens=20,
        context_window_limit=100,
        model_output_limit=40,
        decision=ContextFitDecision.FITS,
    )
    rendered, kind = render_context_inspection(
        inspection(tmp_path, fits, None, UserMessage("x"), AssistantText("y"))
    )

    assert kind == "warning"
    assert "Source: full committed history" in rendered
    assert "Context ID: ctx-v7-" in rendered
    assert "Full history: 1 turn, 2 items" in rendered
    assert "Effective history: 1 turn, 2 items" in rendered
    assert "Input: 80 tokens (estimated)" in rendered
    assert "Fit: fits" in rendered
    assert "Remaining capacity: 0 tokens" in rendered
    assert "Pressure: near full (100%); next prompt may auto-compact" in rendered

    unavailable, kind = render_context_inspection(
        inspection(tmp_path, None, "provider input assessment is unavailable for fake runtime")
    )
    assert kind == "warning"
    assert "Input: unavailable" in unavailable
    assert "Output reserve: unavailable" in unavailable
    assert "Fit: unknown" in unavailable
    assert "Diagnostic: provider input assessment is unavailable for fake runtime" in unavailable
    assert "Pressure: unknown" in unavailable


def test_compaction_preview_and_history_render_without_summary_or_binding() -> None:
    report = ContextFitReport(
        target=None,
        input_count=RequestTokenCount(72, RequestTokenCountMethod.ESTIMATED),
        requested_output_tokens=8,
        context_window_limit=100,
        model_output_limit=20,
        decision=ContextFitDecision.FITS,
    )
    preview = CompactContextPreview(
        source_context_id="ctx-v4-" + "a" * 64,
        full_turn_count=6,
        effective_turn_count=4,
        summary_present=True,
        eligible=True,
        reason=None,
        summarized_turn_count=2,
        retained_turn_count=2,
        target_assessment=CurrentTargetContextAssessment(status(), report),
    )

    rendered, kind = render_compact_preview(preview)

    assert kind == "warning"
    assert "Selection: summarize 2" in rendered
    assert "Pressure: auto-compact range (80%)" in rendered
    assert "did not generate a summary or modify the Session" in rendered

    history = CompactionHistoryResult(
        total_checkpoints=2,
        checkpoints=(
            CompactionHistoryEntry(
                sequence=9,
                occurred_at="2026-07-28T00:00:00.000000Z",
                schema_version=3,
                trigger=CompactionTrigger.HIGH_WATER,
                high_water_percent=80,
                full_turn_count=6,
                summarized_turn_count=2,
                retained_turn_count=2,
                previous_checkpoint_sequence=4,
            ),
        ),
    )
    rendered = render_compaction_history(history)
    assert "Showing 1 most recent of 2" in rendered
    assert "high water at 80%" in rendered
    assert "summarized 2, retained 2" in rendered
    assert "token counts are unavailable" in rendered
    assert "summary" not in rendered.lower().replace("summarized", "")
    assert render_compaction_history(CompactionHistoryResult(0, ())) == (
        "No durable compaction checkpoints yet."
    )


def test_runtime_switch_rendering_distinguishes_fits_unknown_and_rejection() -> None:
    fits = ContextFitReport(
        target=None,
        input_count=RequestTokenCount(80, RequestTokenCountMethod.ESTIMATED),
        requested_output_tokens=20,
        context_window_limit=100,
        model_output_limit=40,
        decision=ContextFitDecision.FITS,
    )
    message, kind = render_runtime_switch("Switched", fits, suffix="final guard remains")
    assert kind == "success"
    assert "input=80 (estimated) + reserve=20 <= window=100" in message
    assert "next provider invocation still runs full preflight" in message

    unknown = ContextFitReport(
        target=None,
        input_count=RequestTokenCount.unknown("counter failed safely"),
        requested_output_tokens=20,
        context_window_limit=100,
        model_output_limit=40,
        decision=ContextFitDecision.UNKNOWN,
    )
    message, kind = render_runtime_switch("Switched", unknown, suffix="final guard remains")
    assert kind == "warning"
    assert "compatibility not confirmed" in message
    assert "no history was deleted" in message

    exceeded = ContextFitReport(
        target=None,
        input_count=RequestTokenCount(81, RequestTokenCountMethod.EXACT),
        requested_output_tokens=20,
        context_window_limit=100,
        model_output_limit=40,
        decision=ContextFitDecision.CONTEXT_EXCEEDED,
    )
    rejected = render_switch_rejection(exceeded)
    assert "Current runtime and profile selection are unchanged" in rejected
    assert "/session new" in rejected
    assert "/compact" not in rejected


def test_resume_rendering_distinguishes_fit_unknown_fake_and_known_rejection(tmp_path) -> None:
    info = SessionInfo(
        session_id="12345678-1234-4234-9234-123456789abc",
        path=tmp_path / "session.jsonl",
        workspace=str(tmp_path),
        workspace_fingerprint="v1-" + "a" * 64,
        created_at="2026-07-18T00:00:00.000000Z",
        record_count=2,
        turn_count=1,
        closed=False,
        binding=BindingSnapshot.fake(),
    )
    fits = ContextFitReport(
        target=None,
        input_count=RequestTokenCount(80, RequestTokenCountMethod.ESTIMATED),
        requested_output_tokens=20,
        context_window_limit=100,
        model_output_limit=40,
        decision=ContextFitDecision.FITS,
    )
    fit_result = SessionResumeResult(
        info,
        ResumeEffect.APPLIED,
        CurrentTargetContextAssessment(status(), fits),
        "ctx-v1-" + "a" * 64,
        False,
        LatestUpdateStatus.UPDATED,
    )
    message, kind = render_session_resume(fit_result)
    assert kind == "success"
    assert "input=80 (estimated) + reserve=20 <= window=100" in message

    unknown = ContextFitReport(
        target=None,
        input_count=RequestTokenCount.unknown("counter failed safely"),
        requested_output_tokens=20,
        context_window_limit=100,
        model_output_limit=40,
        decision=ContextFitDecision.UNKNOWN,
    )
    unknown_result = SessionResumeResult(
        info,
        ResumeEffect.APPLIED,
        CurrentTargetContextAssessment(status(), unknown),
        "ctx-v1-" + "a" * 64,
        False,
        LatestUpdateStatus.UPDATED,
    )
    message, kind = render_session_resume(unknown_result)
    assert kind == "warning"
    assert "resume was applied" in message
    assert "no history was deleted" in message

    fake_result = SessionResumeResult(
        info,
        ResumeEffect.APPLIED,
        CurrentTargetContextAssessment(status(), None, "unavailable"),
        "ctx-v1-" + "a" * 64,
        False,
        LatestUpdateStatus.UPDATED,
    )
    message, kind = render_session_resume(fake_result)
    assert kind == "warning"
    assert "no provider request was made" in message

    exceeded = ContextFitReport(
        target=None,
        input_count=RequestTokenCount(81, RequestTokenCountMethod.EXACT),
        requested_output_tokens=20,
        context_window_limit=100,
        model_output_limit=40,
        decision=ContextFitDecision.CONTEXT_EXCEEDED,
    )
    rejected = render_resume_rejection(exceeded)
    assert "target transcript" in rejected
    assert "runtime are unchanged" in rejected
    assert "compact" not in rejected.lower()


def test_resume_rendering_reports_same_current_and_latest_partial_outcomes(tmp_path) -> None:
    info = SessionInfo(
        session_id="12345678-1234-4234-9234-123456789abc",
        path=tmp_path / "session.jsonl",
        workspace=str(tmp_path),
        workspace_fingerprint="v1-" + "a" * 64,
        created_at="2026-07-18T00:00:00.000000Z",
        record_count=1,
        turn_count=0,
        closed=False,
        binding=BindingSnapshot.fake(),
    )
    current = SessionResumeResult(
        info,
        ResumeEffect.ALREADY_CURRENT,
        None,
        "ctx-v1-" + "a" * 64,
        False,
        LatestUpdateStatus.UPDATED,
    )
    message, kind = render_session_resume(current)
    assert kind == "info"
    assert "already current" in message
    assert "no resume record" in message

    latest_failed = SessionResumeResult(
        info,
        ResumeEffect.APPLIED_LATEST_FAILED,
        CurrentTargetContextAssessment(status(), None, "unavailable"),
        "ctx-v1-" + "a" * 64,
        True,
        LatestUpdateStatus.FAILED_UNCHANGED,
        "latest failed",
    )
    message, kind = render_session_resume(latest_failed)
    assert kind == "error"
    assert "resume audit is durable" in message
    assert "latest pointer update failed" in message
    assert "crash tail was recovered" in message


def test_auto_compaction_events_render_without_content_leakage() -> None:
    started = AutoCompactionStarted(
        CompactionTrigger.HIGH_WATER,
        "ctx-v1-" + "a" * 64,
        60,
        "estimated",
        20,
        100,
        80,
    )
    result = CompactContextResult(
        "session",
        5,
        "ctx-v1-" + "a" * 64,
        "ctx-v2-" + "b" * 64,
        2,
        2,
        4,
        60,
        30,
        "estimated",
        ContextFitDecision.FITS,
        CompactionTrigger.HIGH_WATER,
    )

    message, kind = render_prompt_event(started)
    assert kind == "info"
    assert "80% high-water" in message
    message, kind = render_prompt_event(
        AutoCompactionCommitted(CompactionTrigger.HIGH_WATER, result)
    )
    assert kind == "success"
    assert "input 60 -> 30" in message
    assert "Full transcript and /history were preserved" in message
    message, kind = render_prompt_event(
        AutoCompactionNotApplied(
            CompactionTrigger.OVERFLOW,
            "candidate did not fit",
            False,
        )
    )
    assert kind == "error"
    assert "original prompt will not be sent" in message


def test_semantic_colors_are_traditional_and_optional() -> None:
    assert render_message("failed", "error", color=True) == f"{RED}failed{RESET}"
    assert render_message("done", "success", color=True) == f"{GREEN}done{RESET}"
    assert render_message("usage", "warning", color=True) == f"{YELLOW}usage{RESET}"
    assert render_message("info", "info", color=True) == f"{BLUE}info{RESET}"
    assert render_message("failed", "error", color=False) == "failed"


def test_tool_prompt_events_render_stable_safe_lines_and_semantic_kinds() -> None:
    assert render_prompt_event(AssistantToolTextReceived("I will inspect.\n")) == (
        "I will inspect.\n",
        "plain",
    )
    assert render_prompt_event(
        ToolRequestStarted("grep_regex", 1, 6, "include='src/**/*.py' pattern_bytes=14")
    ) == (
        "[tool 1/6] grep_regex include='src/**/*.py' pattern_bytes=14",
        "info",
    )
    assert render_prompt_event(
        ToolRequestFinished(
            "grep_regex",
            1,
            6,
            ToolEventStatus.SUCCEEDED,
            "ok",
            truncated=True,
        )
    ) == ("[tool 1/6] succeeded code=ok truncated=true", "success")
    assert render_prompt_event(ToolRequestLimited("read_file", 7, 6, "path='secret.txt'")) == (
        "[tool 7/6] read_file not executed: tool-call limit reached",
        "warning",
    )
    assert render_prompt_event(
        ToolRequestSkipped("mkdir", 2, 32, "prior_batch_action_not_succeeded")
    ) == (
        "[tool 2/32] mkdir skipped: prior_batch_action_not_succeeded",
        "warning",
    )
    ledger = ToolTurnLedger(
        (
            ToolOutcomeEntry("write-1", "write_file", 1, ToolRequestOutcome.SUCCEEDED),
            ToolOutcomeEntry(
                "write-2",
                "write_file",
                2,
                ToolRequestOutcome.SKIPPED_AFTER_FAILURE,
                "prior_batch_action_not_succeeded",
            ),
        )
    )
    assert render_prompt_event(ToolTurnSummaryCommitted(ledger)) == (
        "Tool summary: requested=2 admitted=2 dispatched=1 succeeded=1 skipped=1",
        "info",
    )

    expected_kinds = {
        ToolEventStatus.ERROR: "error",
        ToolEventStatus.DENIED: "warning",
        ToolEventStatus.REJECTED: "warning",
        ToolEventStatus.CANCELLED: "warning",
        ToolEventStatus.FAILED: "error",
        ToolEventStatus.PARTIAL: "warning",
        ToolEventStatus.OUTCOME_UNKNOWN: "error",
    }
    for status, expected_kind in expected_kinds.items():
        message, kind = render_prompt_event(
            ToolRequestFinished("write_file", 2, 6, status, "stable_code")
        )
        assert message == f"[tool 2/6] {status.value} code=stable_code"
        assert kind == expected_kind


def test_unknown_prompt_event_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported prompt event"):
        render_prompt_event(object())
    with pytest.raises(ValueError, match="detail mode"):
        render_prompt_event(object(), tool_detail_mode="full")  # type: ignore[arg-type]


def test_prompt_event_full_mode_expands_bounded_details_without_changing_default() -> None:
    event = ToolRequestStarted(
        "run_command",
        2,
        32,
        "command='bash' args=2 cwd='.' timeout=30s",
        (
            'argv: ["bash","-lc","uv run pytest"]',
            "cwd: '.'",
            "timeout_seconds: 30",
            "execution: shell interpreter 'bash'; shell source is argv[2]",
        ),
    )

    assert render_prompt_event(event) == (
        "[tool 2/32] run_command command='bash' args=2 cwd='.' timeout=30s",
        "info",
    )
    assert render_prompt_event(event, tool_detail_mode=ToolDetailMode.FULL) == (
        "[tool 2/32] run_command\n"
        '  argv: ["bash","-lc","uv run pytest"]\n'
        "  cwd: '.'\n"
        "  timeout_seconds: 30\n"
        "  execution: shell interpreter 'bash'; shell source is argv[2]",
        "info",
    )


def test_command_result_metadata_renders_compact_or_full_without_output_content() -> None:
    details = ToolResultDetails(
        "exit=0 duration=23ms stdout=12B stderr=0B",
        (
            "status: exited",
            "exit_code: 0",
            "duration_ms: 23",
            "stdout: captured=12 total=12 truncated=false",
            "stderr: captured=0 total=0 truncated=false",
            "cleanup_complete: true",
        ),
    )
    event = ToolRequestFinished(
        "run_command",
        1,
        32,
        ToolEventStatus.SUCCEEDED,
        "command_succeeded",
        result_details=details,
    )

    assert render_prompt_event(event) == (
        "[tool 1/32] succeeded code=command_succeeded exit=0 duration=23ms stdout=12B stderr=0B",
        "success",
    )
    assert render_prompt_event(event, tool_detail_mode=ToolDetailMode.FULL) == (
        "[tool 1/32] succeeded code=command_succeeded\n"
        "  status: exited\n"
        "  exit_code: 0\n"
        "  duration_ms: 23\n"
        "  stdout: captured=12 total=12 truncated=false\n"
        "  stderr: captured=0 total=0 truncated=false\n"
        "  cleanup_complete: true",
        "success",
    )
    assert "TOP_SECRET" not in render_prompt_event(event)[0]


@pytest.mark.parametrize(
    ("result_code", "guidance"),
    [
        ("command_sandbox_unavailable", "run /sandbox check"),
        ("command_timed_out", "inspect workspace state and /actions last"),
        ("command_signaled", "do not assume command side effects were rolled back"),
        ("command_cleanup_incomplete", "process cleanup is uncertain"),
    ],
)
def test_command_failures_append_result_code_guidance(result_code: str, guidance: str) -> None:
    message, kind = render_prompt_event(
        ToolRequestFinished(
            "run_command",
            1,
            32,
            ToolEventStatus.FAILED,
            result_code,
        )
    )

    assert guidance in message
    assert kind == "error"


def test_colored_readline_prompt_marks_only_nonprinting_sequences() -> None:
    prompt = render_prompt(status(), Info(), color=True, readline=True)

    assert "\001" in prompt and "\002" in prompt
    assert prompt.count("\001") == prompt.count("\002")
    assert "\x1b[" in prompt
    assert prompt.endswith("›\001\x1b[0m\002 ")


def test_colored_non_readline_prompt_has_no_readline_markers() -> None:
    prompt = render_prompt(status(), Info(), color=True, readline=False)

    assert "\x1b[" in prompt
    assert "\001" not in prompt
    assert "\002" not in prompt


def test_width_aware_host_blocks_keep_visual_continuation_indentation() -> None:
    host = render_host_message(
        "A long host-owned status line that must wrap without touching the terminal edge.",
        "info",
        color=False,
        width=40,
    )
    trace = render_turn_trace(
        "A long turn trace that must keep its rail on every wrapped visual line.",
        "info",
        color=False,
        width=40,
    )

    assert len(host.splitlines()) > 1
    assert all(line.startswith("  ") and len(line) <= 40 for line in host.splitlines())
    assert len(trace.splitlines()) > 1
    assert all(line.startswith("  │ ") and len(line) <= 40 for line in trace.splitlines())


def test_colored_prompt_toolbar_has_ansi_without_raw_controls() -> None:
    toolbar = render_prompt_toolbar(
        status(mode="real", provider="custom", model="unsafe\x1bmodel"),
        Path("/workspace\nname"),
        color=True,
    )

    assert toolbar.startswith("\x1b[")
    assert "unsafe?model · /workspace?name" in toolbar


def test_action_audit_renders_redacted_command_summary() -> None:
    audit = SimpleNamespace(
        identity=SimpleNamespace(
            tool_name="run_command",
            action=PermissionAction.DANGEROUS,
            arguments=ToolArguments.from_mapping(
                {
                    "argv": ["uv", "run", "pytest", "--token=secret"],
                    "cwd": "tests",
                    "timeout_seconds": 60,
                }
            ),
        ),
        permission_result=PermissionResult(
            PermissionDecision.ALLOW,
            PermissionReason.ALLOWED_DANGEROUS_AUTO,
        ),
        approval_outcome=None,
        status=ActionAuditStatus.SUCCEEDED,
        result_code="command_succeeded",
        requested_sequence=7,
    )

    rendered = render_action_audits((audit,), 20)

    assert "Action #7: run_command" in rendered
    assert "class: dangerous" in rendered
    assert "command: 'uv' (+3 args)" in rendered
    assert "cwd: 'tests'" in rendered
    assert "timeout: 60s" in rendered
    assert "--token=secret" not in rendered
    assert "result: succeeded (command_succeeded)" in rendered


def test_action_audit_renders_mkdir_relative_path_and_result() -> None:
    audit = SimpleNamespace(
        identity=SimpleNamespace(
            tool_name="mkdir",
            action=PermissionAction.WORKSPACE_CREATE,
            arguments=ToolArguments.from_mapping({"path": "src/pkg"}),
        ),
        permission_result=PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_CREATE,
        ),
        approval_outcome=ApprovalAuditOutcome.ACCEPTED,
        status=ActionAuditStatus.SUCCEEDED,
        result_code="directory_created",
        requested_sequence=8,
    )

    rendered = render_action_audits((audit,), 20)

    assert "Action #8: mkdir" in rendered
    assert "class: workspace-create" in rendered
    assert "path: 'src/pkg'" in rendered
    assert "permission: ask (approval_required_workspace_create)" in rendered
    assert "approval: accepted" in rendered
    assert "result: succeeded (directory_created)" in rendered
    assert "/root/" not in rendered


def test_action_audit_renders_move_relative_paths_and_result() -> None:
    audit = SimpleNamespace(
        identity=SimpleNamespace(
            tool_name="move_file",
            action=PermissionAction.WORKSPACE_MOVE,
            arguments=ToolArguments.from_mapping({"source": "src/a.py", "destination": "dst/b.py"}),
        ),
        permission_result=PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_MOVE,
        ),
        approval_outcome=ApprovalAuditOutcome.ACCEPTED,
        status=ActionAuditStatus.SUCCEEDED,
        result_code="file_moved",
        requested_sequence=9,
    )

    rendered = render_action_audits((audit,), 20)

    assert "Action #9: move_file" in rendered
    assert "class: workspace-move" in rendered
    assert "source: 'src/a.py'" in rendered
    assert "destination: 'dst/b.py'" in rendered
    assert "permission: ask (approval_required_workspace_move)" in rendered
    assert "approval: accepted" in rendered
    assert "result: succeeded (file_moved)" in rendered
    assert "/root/" not in rendered


def test_action_audit_renders_delete_relative_path_and_result() -> None:
    audit = SimpleNamespace(
        identity=SimpleNamespace(
            tool_name="delete_file",
            action=PermissionAction.WORKSPACE_DELETE,
            arguments=ToolArguments.from_mapping({"path": "obsolete.txt"}),
        ),
        permission_result=PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_DELETE,
        ),
        approval_outcome=ApprovalAuditOutcome.ACCEPTED,
        status=ActionAuditStatus.SUCCEEDED,
        result_code="file_deleted",
        requested_sequence=10,
    )

    rendered = render_action_audits((audit,), 20)

    assert "Action #10: delete_file" in rendered
    assert "class: workspace-delete" in rendered
    assert "path: 'obsolete.txt'" in rendered
    assert "permission: ask (approval_required_workspace_delete)" in rendered
    assert "approval: accepted" in rendered
    assert "result: succeeded (file_deleted)" in rendered
    assert "/root/" not in rendered


def test_action_audit_renders_delete_directory_relative_path_and_result() -> None:
    audit = SimpleNamespace(
        identity=SimpleNamespace(
            tool_name="delete_directory",
            action=PermissionAction.WORKSPACE_DELETE,
            arguments=ToolArguments.from_mapping({"path": "build/empty"}),
        ),
        permission_result=PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_DELETE,
        ),
        approval_outcome=ApprovalAuditOutcome.ACCEPTED,
        status=ActionAuditStatus.SUCCEEDED,
        result_code="directory_deleted",
        requested_sequence=11,
    )

    rendered = render_action_audits((audit,), 20)

    assert "Action #11: delete_directory" in rendered
    assert "class: workspace-delete" in rendered
    assert "path: 'build/empty'" in rendered
    assert "permission: ask (approval_required_workspace_delete)" in rendered
    assert "approval: accepted" in rendered
    assert "result: succeeded (directory_deleted)" in rendered
    assert "/root/" not in rendered


def test_action_audit_renders_patch_path_without_edit_content() -> None:
    audit = SimpleNamespace(
        identity=SimpleNamespace(
            tool_name="patch_file",
            action=PermissionAction.WORKSPACE_OVERWRITE,
            arguments=ToolArguments.from_mapping(
                {
                    "path": "src/app.py",
                    "edits": [{"old_text": "secret-before", "new_text": "secret-after"}],
                }
            ),
        ),
        permission_result=PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_OVERWRITE,
        ),
        approval_outcome=ApprovalAuditOutcome.ACCEPTED,
        status=ActionAuditStatus.SUCCEEDED,
        result_code="patched",
        requested_sequence=12,
    )

    rendered = render_action_audits((audit,), 20)

    assert "Action #12: patch_file" in rendered
    assert "path: 'src/app.py'" in rendered
    assert "result: succeeded (patched)" in rendered
    assert "secret-before" not in rendered
    assert "secret-after" not in rendered
