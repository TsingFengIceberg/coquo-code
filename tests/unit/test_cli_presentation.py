from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from leonervis_code.agent.loop import AgentLoop
from leonervis_code.agent.tool_events import (
    AssistantToolTextReceived,
    ToolEventStatus,
    ToolRequestFinished,
    ToolRequestLimited,
    ToolRequestSkipped,
    ToolRequestStarted,
    ToolTurnSummaryCommitted,
)
from leonervis_code.cli.presentation import (
    BLUE,
    GREEN,
    RED,
    RESET,
    YELLOW,
    render_context_inspection,
    render_action_audits,
    render_message,
    render_prompt,
    render_prompt_toolbar,
    render_prompt_event,
    render_resume_rejection,
    render_runtime_status,
    render_runtime_switch,
    render_session_resume,
    render_switch_rejection,
)
from leonervis_code.providers.manager import CurrentTargetContextAssessment, RuntimeStatus
from leonervis_code.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    RequestTokenCount,
    RequestTokenCountMethod,
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
    EffectiveContextInspection,
    ResumeEffect,
    SessionResumeResult,
)
from leonervis_code.session_records import (
    ActionAuditStatus,
    ApprovalAuditOutcome,
    BindingSnapshot,
)
from leonervis_code.session_store import LatestUpdateStatus, SessionInfo
from leonervis_code.tools.glob import GlobTool
from leonervis_code.tools.grep import GrepTool
from leonervis_code.tools.list_directory import ListDirectoryTool
from leonervis_code.tools.read_file import ReadFileTool


@dataclass
class Info:
    session_id: str = "12345678-1234-4234-9234-123456789abc"


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
    assert render_prompt_toolbar(status(), Path("/workspace"), color=False) == (
        "  fake · /workspace"
    )
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

    assert kind == "info"
    assert "Source: full committed history" in rendered
    assert "Context ID: ctx-v3-" in rendered
    assert "Full history: 1 turn, 2 items" in rendered
    assert "Effective history: 1 turn, 2 items" in rendered
    assert "Input: 80 tokens (estimated)" in rendered
    assert "Fit: fits" in rendered
    assert "Remaining capacity: 0 tokens" in rendered

    unavailable, kind = render_context_inspection(
        inspection(tmp_path, None, "provider input assessment is unavailable for fake runtime")
    )
    assert kind == "warning"
    assert "Input: unavailable" in unavailable
    assert "Output reserve: unavailable" in unavailable
    assert "Fit: unknown" in unavailable
    assert "Diagnostic: provider input assessment is unavailable for fake runtime" in unavailable


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
