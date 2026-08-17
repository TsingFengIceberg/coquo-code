"""Slash-command dispatch independent from terminal streams and ANSI rendering."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
import json
import shlex
from typing import Protocol

from coquo.cli.presentation import (
    DEFAULT_ACTION_AUDIT_COUNT,
    DEFAULT_COMPACTION_HISTORY_COUNT,
    DEFAULT_SESSION_LIST_COUNT,
    DEFAULT_SESSION_PREVIEW_TURNS,
    DEFAULT_SESSION_SEARCH_MATCHES,
    DEFAULT_TOOL_LEDGER_COUNT,
    HELP_TEXT,
    HELP_BY_TOPIC,
    HELP_TOPICS,
    MAX_ACTION_AUDIT_COUNT,
    MAX_COMPACTION_HISTORY_COUNT,
    MAX_SESSION_LIST_COUNT,
    MAX_SESSION_PREVIEW_TURNS,
    MAX_TOOL_LEDGER_COUNT,
    MCP_HELP,
    PROVIDER_HELP,
    SEARCH_HELP,
    SESSION_HELP,
    TASK_HELP,
    CHILD_HELP,
    TEAM_HELP,
    MessageKind,
    ToolDetailMode,
    render_compact_result,
    render_action_audits,
    render_context_inspection,
    render_compact_preview,
    render_compaction_history,
    render_command_sandbox_inspection,
    render_git_diff,
    render_git_log,
    render_git_show,
    render_git_status,
    render_mcp_catalog,
    render_mcp_probe_result,
    render_mcp_runtime_statuses,
    render_mcp_server_status,
    render_mcp_server_statuses,
    render_hook_doctor,
    render_hook_entry,
    render_hook_evaluations,
    render_hook_handler_runs,
    render_hook_set,
    render_provider_adapter_error,
    render_project_status,
    render_project_instructions_inspection,
    render_permission_matrix,
    render_output_budget,
    render_output_budget_rejection,
    render_output_budget_update,
    render_recent_history,
    render_runtime_status,
    render_runtime_switch,
    render_web_search_sources,
    render_resume_rejection,
    render_session_info,
    render_session_diagnosis,
    render_session_export,
    render_session_preview,
    render_session_repair,
    render_session_search,
    render_session_resume,
    render_session_summary,
    render_session_turn_range,
    render_skill_activation,
    render_skill_candidate,
    render_skill_conflicts,
    render_skill_doctor,
    render_skill_inventory,
    render_quarantined_skill_candidate,
    render_skill_candidate_list,
    render_skill_search,
    render_task_info,
    render_task_admission_info,
    render_task_admission_acceptance_preview,
    render_task_admission_summary,
    render_task_next_action,
    render_task_summary,
    render_child_run_info,
    render_child_run_summary,
    render_child_handoff,
    render_team_info,
    render_team_member,
    render_team_worktree,
    render_team_worktree_diff,
    render_team_summary,
    render_team_assignment_info,
    render_team_assignment_summary,
    render_team_message,
    render_team_message_summary,
    render_team_work_item,
    render_team_work_summary,
    render_team_schedule,
    render_task_timeline,
    render_task_verification_result,
    render_switch_rejection,
    render_tool_ledgers,
    render_tool_catalog,
    render_durable_usage_summary,
    render_usage_summary,
)
from coquo.core.compaction import CompactionError
from coquo.core.permissions import ApprovalMode, PermissionMode
from coquo.core.task_admission import canonical_task_admission_id
from coquo.cli.failure_guidance import command_failure_guidance
from coquo.cli.turn_runner import TaskTurnRequest
from coquo.providers.errors import ProviderAdapterError
from coquo.providers.manager import (
    RuntimeProviderStateError,
    RuntimeSwitchAuditError,
    RuntimeSwitchContextError,
)
from coquo.providers.profile import ProviderProfileError
from coquo.providers.profile import MAX_MODEL_OUTPUT_TOKENS
from coquo.providers.resolver import RuntimeRouteError
from coquo.session import SessionResumeConflictError, SessionResumeContextError
from coquo.session_store import SessionResumeCommitError, SessionStoreError
from coquo.session_records import (
    ActionAuditStatus,
    SessionRecordError,
    canonical_session_id,
)
from coquo.task_records import TaskRecordError, TaskStatus, canonical_task_id
from coquo.child_run_records import ChildRunStatus
from coquo.team_records import TeamMessageStatus, TeamStatus, TeamWorkStatus
from coquo.task_store import TaskAdmissionConfiguration
from coquo.tools.git_repository import GitObservationError
from coquo.tools.git_log import DEFAULT_GIT_LOG_LIMIT, MAX_GIT_LOG_LIMIT
from coquo.tools.catalog import TOOL_CATALOG
from coquo.tools.web_search import WebSearchPreparationError

TOP_LEVEL_COMMANDS = (
    "/help",
    "/history",
    "/actions",
    "/tools",
    "/tool-details",
    "/changes",
    "/commit",
    "/commits",
    "/exit",
    "/quit",
    "/status",
    "/permissions",
    "/sandbox",
    "/context",
    "/instructions",
    "/usage",
    "/output",
    "/compact",
    "/compactions",
    "/provider",
    "/search",
    "/mcp",
    "/skills",
    "/hooks",
    "/model",
    "/session",
    "/task",
    "/child",
    "/team",
    "/resume",
    "/clear",
)


@dataclass(frozen=True)
class SlashCompletionSpec:
    """One static Host command completion and its terminal-only description."""

    text: str
    description: str
    top_level: bool = False


SLASH_COMPLETIONS = (
    SlashCompletionSpec("/help", "Show Host commands", True),
    SlashCompletionSpec("/help session", "Session history, browsing, and resume"),
    SlashCompletionSpec("/help task", "Durable Task execution and lifecycle"),
    SlashCompletionSpec("/help tools", "Action Audit and tool outcomes"),
    SlashCompletionSpec("/help git", "Read-only Git observation"),
    SlashCompletionSpec("/help context", "Context, usage, and compaction"),
    SlashCompletionSpec("/help provider", "Provider and model selection"),
    SlashCompletionSpec("/help search", "Independent web search sources"),
    SlashCompletionSpec("/help mcp", "Configured MCP server inspection"),
    SlashCompletionSpec("/help skills", "Skill activation and package diagnostics"),
    SlashCompletionSpec("/help hooks", "Declarative Hook inspection"),
    SlashCompletionSpec("/help policy", "Permission, approval, and command sandbox"),
    SlashCompletionSpec("/help input", "Prompt editor controls"),
    SlashCompletionSpec("/history", "Show recent Session turns", True),
    SlashCompletionSpec("/actions", "Show recent Action Audit", True),
    SlashCompletionSpec("/tools", "Show durable tool ledgers", True),
    SlashCompletionSpec("/changes", "Show Git working changes", True),
    SlashCompletionSpec("/changes unstaged", "Show unstaged tracked patch"),
    SlashCompletionSpec("/changes staged", "Show staged tracked patch"),
    SlashCompletionSpec("/commit", "Show one reachable Git commit", True),
    SlashCompletionSpec("/commits", "Show recent reachable Git commits", True),
    SlashCompletionSpec("/status", "Show runtime status", True),
    SlashCompletionSpec("/permissions", "Inspect or preview permission policy", True),
    SlashCompletionSpec("/permissions read-only", "Preview read-only policy"),
    SlashCompletionSpec("/permissions read-only ask", "Preview read-only with approvals"),
    SlashCompletionSpec("/permissions read-only auto", "Preview read-only with auto approval"),
    SlashCompletionSpec("/permissions workspace-write", "Preview workspace-write policy"),
    SlashCompletionSpec(
        "/permissions workspace-write ask", "Preview workspace-write with approvals"
    ),
    SlashCompletionSpec(
        "/permissions workspace-write auto", "Preview workspace-write with auto approval"
    ),
    SlashCompletionSpec("/permissions danger-full-access", "Preview danger-full-access policy"),
    SlashCompletionSpec(
        "/permissions danger-full-access ask", "Preview danger-full-access with approvals"
    ),
    SlashCompletionSpec(
        "/permissions danger-full-access auto", "Preview danger-full-access with auto approval"
    ),
    SlashCompletionSpec("/sandbox", "Command sandbox diagnostics", True),
    SlashCompletionSpec("/sandbox check", "Verify command sandbox activation"),
    SlashCompletionSpec("/context", "Inspect Effective Context", True),
    SlashCompletionSpec("/instructions", "Inspect root AGENTS.md metadata", True),
    SlashCompletionSpec("/usage", "Show provider token usage", True),
    SlashCompletionSpec("/usage session", "Show durable Session usage"),
    SlashCompletionSpec("/usage turns", "Show recent durable turn usage"),
    SlashCompletionSpec("/output", "Inspect or change output budget", True),
    SlashCompletionSpec("/compact", "Compact earlier complete turns", True),
    SlashCompletionSpec("/compactions", "Show durable compaction history", True),
    SlashCompletionSpec("/provider", "Provider commands", True),
    SlashCompletionSpec("/search", "Web search source commands", True),
    SlashCompletionSpec("/mcp", "MCP server inspection", True),
    SlashCompletionSpec("/skills", "Skill activation and package diagnostics", True),
    SlashCompletionSpec("/hooks", "Declarative Hook inspection", True),
    SlashCompletionSpec("/model", "Override the current model", True),
    SlashCompletionSpec("/session", "Session commands", True),
    SlashCompletionSpec("/task", "Task commands", True),
    SlashCompletionSpec("/child", "Durable Child Run control", True),
    SlashCompletionSpec("/team", "Durable Team and member identity", True),
    SlashCompletionSpec("/resume", "Resume a Session", True),
    SlashCompletionSpec("/clear", "Clear terminal output", True),
    SlashCompletionSpec("/exit", "Exit the REPL", True),
    SlashCompletionSpec("/quit", "Exit the REPL", True),
    SlashCompletionSpec("/provider list", "List provider profiles"),
    SlashCompletionSpec("/provider current", "Show the current provider"),
    SlashCompletionSpec("/provider use", "Use a workspace provider profile"),
    SlashCompletionSpec("/search status", "Show web search source activation"),
    SlashCompletionSpec("/search sources", "List supported and available search sources"),
    SlashCompletionSpec("/search use provider", "Use provider-native search as primary"),
    SlashCompletionSpec("/search use brave", "Use Brave as the primary search source"),
    SlashCompletionSpec("/search use tavily", "Use Tavily as the primary search source"),
    SlashCompletionSpec(
        "/search use provider tavily", "Use Provider search with explicit Tavily fallback"
    ),
    SlashCompletionSpec("/search mode auto", "Let the Provider decide whether to search"),
    SlashCompletionSpec("/search mode required", "Require Provider search for each invocation"),
    SlashCompletionSpec("/search domains", "Set Provider search allowed domains"),
    SlashCompletionSpec("/search domains reset", "Clear Provider search domain filtering"),
    SlashCompletionSpec("/search context low", "Use low Provider search context"),
    SlashCompletionSpec("/search context medium", "Use medium Provider search context"),
    SlashCompletionSpec("/search context high", "Use high Provider search context"),
    SlashCompletionSpec("/search context reset", "Clear Provider search context override"),
    SlashCompletionSpec(
        "/search use brave tavily", "Activate Brave then Tavily; execute Brave only for now"
    ),
    SlashCompletionSpec(
        "/search use tavily brave", "Activate Tavily then Brave; execute Tavily only for now"
    ),
    SlashCompletionSpec("/search reset", "Restore provider-native default or disable search"),
    SlashCompletionSpec("/mcp list", "List configured MCP servers"),
    SlashCompletionSpec("/mcp status", "Show configured MCP server readiness"),
    SlashCompletionSpec("/mcp show", "Show one redacted MCP server configuration"),
    SlashCompletionSpec("/mcp probe", "Temporarily initialize and list MCP tools"),
    SlashCompletionSpec("/mcp catalog", "Refresh the normalized MCP quarantine catalog"),
    SlashCompletionSpec("/skills active", "Show Skills retained in Effective Context"),
    SlashCompletionSpec("/skills list", "List active and shadowed Skill packages"),
    SlashCompletionSpec("/skills show", "Show one bounded Skill package"),
    SlashCompletionSpec("/skills search", "Search active Skill metadata"),
    SlashCompletionSpec("/skills conflicts", "Show shadowed Skill package identities"),
    SlashCompletionSpec("/skills doctor", "Show Skill roots and catalog issues"),
    SlashCompletionSpec("/hooks active", "Show enabled Hooks in the current configuration"),
    SlashCompletionSpec("/hooks list", "List configured Hooks"),
    SlashCompletionSpec("/hooks show", "Show one configured Hook"),
    SlashCompletionSpec("/hooks doctor", "Validate current Hook configuration"),
    SlashCompletionSpec("/hooks evaluations", "Show durable Session Hook evaluations"),
    SlashCompletionSpec("/hooks runs", "Show audited local Hook handler executions"),
    SlashCompletionSpec("/hooks task", "Show durable Task Hook evaluations"),
    SlashCompletionSpec("/skills fetch", "Fetch a public Skill into quarantine"),
    SlashCompletionSpec("/skills candidates", "List quarantined Skill candidates"),
    SlashCompletionSpec("/skills candidate show", "Inspect one quarantined Skill candidate"),
    SlashCompletionSpec("/skills candidate reject", "Reject one quarantined Skill candidate"),
    SlashCompletionSpec("/skills install", "Install one quarantined Skill candidate"),
    SlashCompletionSpec("/session show", "Show current or selected Session metadata"),
    SlashCompletionSpec("/session preview", "Preview recent turns without switching"),
    SlashCompletionSpec("/session turns", "Show a specific complete-turn range"),
    SlashCompletionSpec("/session search", "Search final dialogue across Sessions"),
    SlashCompletionSpec("/session export", "Export final dialogue as Markdown or JSON"),
    SlashCompletionSpec("/session fork", "Fork complete turns into a new Session"),
    SlashCompletionSpec("/session doctor", "Diagnose one Session transcript"),
    SlashCompletionSpec("/session repair", "Repair one incomplete transcript tail"),
    SlashCompletionSpec("/session list", "Browse and filter workspace Sessions"),
    SlashCompletionSpec("/session new", "Start an empty Session"),
    SlashCompletionSpec("/session rename", "Rename the current Session"),
    SlashCompletionSpec("/session archive", "Archive the current Session"),
    SlashCompletionSpec("/session unarchive", "Unarchive the current Session"),
    SlashCompletionSpec("/session pin", "Pin the current Session"),
    SlashCompletionSpec("/session unpin", "Unpin the current Session"),
    SlashCompletionSpec("/session switch", "Build or use a recent Session picker"),
    SlashCompletionSpec("/session switch list", "Refresh the Session picker with filters"),
    SlashCompletionSpec("/task start", "Create a Task owned by the current Session"),
    SlashCompletionSpec("/task proposals", "List current-Session Task admission proposals"),
    SlashCompletionSpec("/task proposal show", "Show one Task admission proposal"),
    SlashCompletionSpec("/task proposal accept", "Accept and create one proposed Task"),
    SlashCompletionSpec("/task proposal reject", "Reject one Task admission proposal"),
    SlashCompletionSpec("/task proposal drive", "Drive one accepted Task proposal"),
    SlashCompletionSpec("/task list", "List workspace Tasks"),
    SlashCompletionSpec("/task show", "Show one workspace Task"),
    SlashCompletionSpec("/task continue", "Execute one bounded Task Stage"),
    SlashCompletionSpec("/task plan", "Generate a bounded Task plan proposal"),
    SlashCompletionSpec("/task plan accept", "Accept the latest Task plan"),
    SlashCompletionSpec("/task run", "Run accepted plan stages in the foreground"),
    SlashCompletionSpec("/task reflect", "Reflect on failed acceptance feedback"),
    SlashCompletionSpec("/task correct", "Run one recommended correction Stage"),
    SlashCompletionSpec("/task revise", "Propose a reflection-backed plan revision"),
    SlashCompletionSpec("/task drive", "Drive bounded adaptive Task stages"),
    SlashCompletionSpec("/task next", "Preview the next driver decision"),
    SlashCompletionSpec("/task checkpoint", "Append a bounded Task context checkpoint"),
    SlashCompletionSpec("/task pause", "Pause automatic Task driving"),
    SlashCompletionSpec("/task resume", "Resume automatic Task driving"),
    SlashCompletionSpec("/task recover", "Reconcile an interrupted Stage"),
    SlashCompletionSpec("/task verify", "Verify one acceptance criterion"),
    SlashCompletionSpec("/task verify host", "Run deterministic Host acceptance checks"),
    SlashCompletionSpec("/task review", "Run an independent no-tools acceptance review"),
    SlashCompletionSpec("/task complete", "Complete a fully accepted Task"),
    SlashCompletionSpec("/task cancel", "Cancel a Task"),
    SlashCompletionSpec("/task fail", "Fail a Task explicitly"),
    SlashCompletionSpec("/task rename", "Rename a Task"),
    SlashCompletionSpec("/task archive", "Archive a Task"),
    SlashCompletionSpec("/task unarchive", "Unarchive a Task"),
    SlashCompletionSpec("/task timeline", "Show the complete Task timeline"),
    SlashCompletionSpec("/task derive", "Derive a new Task with provenance"),
    SlashCompletionSpec("/child create", "Queue Child Run metadata"),
    SlashCompletionSpec("/child prepare", "Admit and bind a Child Session"),
    SlashCompletionSpec("/child run", "Run one Child in the foreground"),
    SlashCompletionSpec("/child start", "Queue one Child on local background workers"),
    SlashCompletionSpec("/child list", "List Child Runs by durable state"),
    SlashCompletionSpec("/child show", "Show one Child Run"),
    SlashCompletionSpec("/child cancel", "Request cancellation for one Child Run"),
    SlashCompletionSpec("/child wait", "Wait for one Child Run terminal state"),
    SlashCompletionSpec("/child recover", "Recover abandoned Child Runs"),
    SlashCompletionSpec("/child handoff", "Publish a terminal Child handoff"),
    SlashCompletionSpec("/child deliver", "Deliver a terminal Child handoff"),
    SlashCompletionSpec("/team create", "Create a durable Team"),
    SlashCompletionSpec("/team list", "List durable Teams"),
    SlashCompletionSpec("/team show", "Show one durable Team"),
    SlashCompletionSpec("/team close", "Close one durable Team"),
    SlashCompletionSpec("/team member add", "Add one Team member"),
    SlashCompletionSpec("/team member list", "List Team members"),
    SlashCompletionSpec("/team member show", "Show one Team member"),
    SlashCompletionSpec("/team member disable", "Disable one Team member"),
    SlashCompletionSpec("/team member enable", "Enable one Team member"),
    SlashCompletionSpec("/team member leave", "Leave one Team member"),
    SlashCompletionSpec("/team worktree status", "Show one isolated Team worktree"),
    SlashCompletionSpec("/team worktree diff", "Show bounded isolated Team worktree changes"),
    SlashCompletionSpec("/team worktree recover", "Observe isolated Team worktree recovery state"),
    SlashCompletionSpec("/team worktree retire", "Explicitly retire one isolated Team worktree"),
    SlashCompletionSpec("/team assignment create", "Create a queued Team assignment"),
    SlashCompletionSpec("/team assignment list", "List Team assignments"),
    SlashCompletionSpec("/team assignment show", "Show one Team assignment"),
    SlashCompletionSpec("/team assignment prepare", "Prepare one Team Child envelope"),
    SlashCompletionSpec("/team assignment run", "Run one Team assignment in the foreground"),
    SlashCompletionSpec("/team assignment start", "Start one Team assignment in background"),
    SlashCompletionSpec("/team assignment wait", "Wait for one Team assignment"),
    SlashCompletionSpec("/team assignment cancel", "Cancel one Team assignment"),
    SlashCompletionSpec("/team assignment handoff", "Publish one Team assignment handoff"),
    SlashCompletionSpec("/team assignment recover", "Recover Team assignment metadata"),
    SlashCompletionSpec("/team schedule run", "Run one foreground Team schedule wave"),
    SlashCompletionSpec("/team schedule start", "Start one background Team schedule wave"),
    SlashCompletionSpec("/team schedule status", "Show one Team schedule"),
    SlashCompletionSpec("/team schedule wait", "Wait for one Team schedule"),
    SlashCompletionSpec("/team schedule cancel", "Cancel one Team schedule"),
    SlashCompletionSpec("/team schedule recover", "Recover one Team schedule"),
    SlashCompletionSpec("/team message send", "Send a durable owner-to-member message"),
    SlashCompletionSpec("/team message list", "List durable Team messages"),
    SlashCompletionSpec("/team message show", "Show one durable Team message"),
    SlashCompletionSpec("/team message read", "Mark one member reply read"),
    SlashCompletionSpec("/team message cancel", "Cancel one pending owner message"),
    SlashCompletionSpec("/team work create", "Create a durable Team work item"),
    SlashCompletionSpec("/team work list", "List durable Team work items"),
    SlashCompletionSpec("/team work show", "Show one durable Team work item"),
    SlashCompletionSpec("/team work cancel", "Cancel one Team work item"),
    SlashCompletionSpec("/team work assign", "Assign ready Team work to a member"),
    SlashCompletionSpec("/team work complete", "Complete reviewed Team work"),
    SlashCompletionSpec("/team work release", "Release reviewed Team work"),
    SlashCompletionSpec("/tools details", "Show per-request ledger outcomes"),
    SlashCompletionSpec("/tools catalog", "Show tool permissions and availability"),
    SlashCompletionSpec("/actions last", "Show the most recent Action Audit"),
    SlashCompletionSpec("/tool-details", "Show live tool detail mode", True),
    SlashCompletionSpec("/tool-details compact", "Use compact live tool lines"),
    SlashCompletionSpec("/tool-details full", "Show bounded structured tool details"),
    SlashCompletionSpec("/compact preview", "Preview fixed compaction selection"),
    *tuple(
        SlashCompletionSpec(
            f"/actions status={status.value}",
            f"Filter actions by {status.value}",
        )
        for status in ActionAuditStatus
    ),
    *tuple(
        SlashCompletionSpec(
            f"/actions tool={tool.name}",
            f"Filter actions for {tool.name}",
        )
        for tool in TOOL_CATALOG
    ),
    *tuple(
        SlashCompletionSpec(f"/tools catalog {tool.name}", f"Inspect {tool.name}")
        for tool in TOOL_CATALOG
    ),
)


class ReplSession(Protocol):
    turns: tuple

    def action_audits(self): ...

    def tool_ledgers(self, limit: int): ...

    def git_status(self): ...

    def git_diff(self, scope: str): ...

    def git_log(self, limit: int, path: str): ...

    def git_show(self, commit_id: str, path: str): ...

    def inspect_mcp_servers(self): ...

    def inspect_mcp_server(self, name: str): ...

    def probe_mcp_server(self, name: str): ...

    def inspect_mcp_catalog(self): ...

    def inspect_mcp_runtime(self): ...

    def inspect_hooks(self): ...

    def status(self): ...

    def project_status(self): ...

    def inspect_command_sandbox(self): ...

    def inspect_context(self): ...

    def inspect_project_instructions(self): ...

    def fetch_skill_candidate(self, url: str): ...

    def list_skill_candidates(self): ...

    def inspect_skill_candidate(self, candidate_id: str): ...

    def reject_skill_candidate(self, candidate_id: str): ...

    def install_skill_candidate(self, candidate_id: str, *, scope: str | None = None): ...

    def usage(self): ...

    def session_usage(self): ...

    def turn_usage_history(self, limit: int = 10): ...

    def set_output_budget(self, max_output_tokens: int | None): ...

    def compact_context(self): ...

    def preview_compaction(self): ...

    def compaction_history(self, limit: int): ...

    def session_info(self): ...

    def latest_session_info(self): ...

    def inspect_session(self, selector: str): ...

    def preview_session(self, selector: str, limit: int): ...

    def session_turn_range(self, selector: str, start_turn: int, count: int): ...

    def search_sessions(self, query: str, limit: int): ...

    def export_session(self, selector: str): ...

    def fork_session(self, selector: str, through_turn: int): ...

    def diagnose_session(self, selector: str): ...

    def repair_session(self, selector: str): ...

    def list_sessions(self): ...

    def create_task(self, objective: str, acceptance_criteria: tuple[str, ...] = ()): ...

    def list_tasks(self): ...

    def create_child_run(self, objective: str): ...

    def prepare_child_run(self, child_run_id: str): ...

    def run_child_run(self, child_run_id: str): ...

    def list_child_runs(self, *, status=None): ...

    def inspect_child_run(self, child_run_id: str): ...

    def cancel_child_run(self, child_run_id: str, reason: str): ...

    def wait_child_run(self, child_run_id: str, timeout_seconds: float): ...

    def recover_child_runs(self, child_run_id: str | None = None, limit: int = 100): ...

    def publish_child_handoff(self, child_run_id: str): ...

    def deliver_child_handoff(self, child_run_id: str): ...

    def create_team(self, name: str): ...

    def list_teams(self, *, status=None): ...

    def inspect_team(self, team_id: str): ...

    def close_team(self, team_id: str): ...

    def add_team_member(
        self, team_id: str, name: str, *, role_contract: str = "read-only-investigator-v1"
    ): ...

    def list_team_members(self, team_id: str): ...

    def inspect_team_member(self, team_id: str, member_id: str): ...

    def disable_team_member(self, team_id: str, member_id: str, reason: str): ...

    def enable_team_member(self, team_id: str, member_id: str): ...

    def leave_team_member(self, team_id: str, member_id: str, reason: str): ...

    def inspect_team_worktree(self, worktree_id: str): ...

    def diff_team_worktree(self, worktree_id: str, *, max_bytes: int = 64 * 1024): ...

    def recover_team_worktree(self, worktree_id: str): ...

    def retire_team_worktree(self, worktree_id: str): ...

    def create_team_assignment(self, team_id: str, member_id: str, objective: str): ...

    def list_team_assignments(self, team_id: str, *, limit: int = 100): ...

    def inspect_team_assignment(self, team_id: str, assignment_id: str): ...

    def recover_team_assignments(
        self, team_id: str, assignment_id: str | None = None, *, limit: int = 100
    ): ...

    def prepare_team_assignment(self, team_id: str, assignment_id: str): ...

    def run_team_assignment(self, team_id: str, assignment_id: str): ...

    def start_team_assignment(self, team_id: str, assignment_id: str): ...

    def wait_team_assignment(self, team_id: str, assignment_id: str, timeout_seconds: float): ...

    def cancel_team_assignment(self, team_id: str, assignment_id: str, reason: str): ...

    def publish_team_assignment_handoff(self, team_id: str, assignment_id: str): ...

    def send_team_message(self, team_id: str, member_id: str, body: str): ...

    def list_team_messages(
        self, team_id: str, *, limit: int = 100, member_id: str | None = None, status=None
    ): ...

    def inspect_team_message(self, team_id: str, message_id: str): ...

    def read_team_message(self, team_id: str, message_id: str): ...

    def cancel_team_message(self, team_id: str, message_id: str, reason: str): ...

    def create_team_work(
        self, team_id: str, title: str, objective: str, dependency_ids: tuple[str, ...] = ()
    ): ...

    def list_team_work(self, team_id: str, *, limit: int = 100, status=None): ...

    def inspect_team_work(self, team_id: str, work_item_id: str): ...

    def cancel_team_work(self, team_id: str, work_item_id: str, reason: str): ...

    def assign_team_work(self, team_id: str, work_item_id: str, member_id: str): ...

    def complete_team_work(self, team_id: str, work_item_id: str, evidence: str): ...

    def release_team_work(self, team_id: str, work_item_id: str, reason: str): ...

    def inspect_task(self, task_id: str): ...

    def hook_evaluations(self, limit: int = 20): ...

    def hook_handler_runs(self, limit: int = 20): ...

    def task_hook_evaluations(self, task_id: str, limit: int = 20): ...

    def list_task_admissions(self): ...

    def inspect_task_admission(self, admission_id: str): ...

    def preview_task_admission_acceptance(
        self, admission_id: str, configuration: TaskAdmissionConfiguration
    ): ...

    def accept_task_admission(
        self,
        admission_id: str,
        configuration: TaskAdmissionConfiguration,
        *,
        confirmation_sha256: str,
    ): ...

    def accepted_task_for_admission(self, admission_id: str): ...

    def reject_task_admission(self, admission_id: str, reason: str | None = None): ...

    def derive_task(self, parent_task_id: str, objective: str): ...

    def recover_task(self, task_id: str): ...

    def accept_task_plan(self, task_id: str): ...

    def verify_task_acceptance(self, task_id: str, criterion_index: int, evidence: str): ...

    def verify_task_host(self, task_id: str): ...

    def review_task_acceptance(self, task_id: str): ...

    def preview_task_next(self, task_id: str): ...

    def checkpoint_task(self, task_id: str): ...

    def set_task_driver_paused(self, task_id: str, paused: bool, reason: str | None = None): ...

    def complete_task(self, task_id: str): ...

    def cancel_task(self, task_id: str, reason: str): ...

    def fail_task(self, task_id: str, reason: str): ...

    def rename_task(self, task_id: str, name: str): ...

    def set_task_archived(self, task_id: str, archived: bool): ...

    def rename_session(self, name: str | None = None): ...

    def set_session_archived(self, archived: bool): ...

    def set_session_pinned(self, pinned: bool): ...

    def new_session(self): ...

    def switch_session(self, selector: str): ...

    def list_profiles(self): ...

    def use_profile(self, name: str, *, scope: str): ...

    def set_model(self, model: str): ...

    def inspect_web_search_sources(self): ...

    def set_web_search_sources(self, sources: tuple[str, ...]): ...

    def reset_web_search_sources(self): ...

    def set_native_search_mode(self, mode: str): ...

    def set_native_search_domains(self, domains: tuple[str, ...] | None): ...

    def set_native_search_context(self, size: str | None): ...


@dataclass(frozen=True)
class SlashResult:
    """One stream-independent result from slash-command dispatch."""

    handled: bool
    exit: bool = False
    message: str | None = None
    kind: MessageKind = "plain"
    clear_screen: bool = False
    task_request: TaskTurnRequest | None = None


_NOT_HANDLED = SlashResult(handled=False)
DEFAULT_SESSION_SWITCH_COUNT = 10
MAX_SESSION_SWITCH_COUNT = 20


@dataclass
class ToolDetailSettings:
    """Mutable process-local REPL presentation state."""

    mode: ToolDetailMode = ToolDetailMode.COMPACT


@dataclass
class SessionSwitchCatalog:
    """Process-local mapping from displayed picker numbers to exact Session IDs."""

    session_ids: tuple[str, ...] = ()

    def replace(self, session_ids: tuple[str, ...]) -> None:
        self.session_ids = session_ids

    def clear(self) -> None:
        self.session_ids = ()

    def consume(self, number: int) -> str | None:
        session_ids = self.session_ids
        self.clear()
        if not 1 <= number <= len(session_ids):
            return None
        return session_ids[number - 1]


def dispatch_slash(
    command: str,
    session: ReplSession,
    *,
    tool_details: ToolDetailSettings | None = None,
    session_switch: SessionSwitchCatalog | None = None,
) -> SlashResult:
    """Dispatch one exact slash command without writing terminal output."""
    if not command.startswith("/") or "\n" in command or "\r" in command:
        return _NOT_HANDLED
    if command in {"/exit", "/quit"}:
        return SlashResult(handled=True, exit=True)
    if command.startswith("/exit ") or command.startswith("/quit "):
        name = command.split(maxsplit=1)[0]
        return _usage(f"Usage: {name}")
    if command == "/help":
        return _info(HELP_TEXT)
    if command.startswith("/help "):
        topic = command.removeprefix("/help ")
        if topic in HELP_BY_TOPIC:
            return _info(HELP_BY_TOPIC[topic])
        return _usage(f"Usage: /help [{'|'.join(HELP_TOPICS)}]")
    if command == "/clear":
        return SlashResult(handled=True, clear_screen=True)
    if command.startswith("/clear "):
        return _usage("Usage: /clear")
    if command == "/session":
        return _info(SESSION_HELP)
    if command == "/provider":
        return _info(PROVIDER_HELP)
    if command == "/search":
        return _info(SEARCH_HELP)
    if command == "/mcp":
        return _info(MCP_HELP)
    if command == "/hooks":
        return _call(
            lambda: render_hook_set(session.inspect_hooks(), active_only=True), kind="info"
        )
    if command == "/hooks active":
        return _call(
            lambda: render_hook_set(session.inspect_hooks(), active_only=True), kind="info"
        )
    if command == "/hooks list":
        return _call(lambda: render_hook_set(session.inspect_hooks()), kind="info")
    if command == "/hooks doctor":
        return _call(
            lambda: render_hook_doctor(session.inspect_hooks()),
            kind="info",
            failure_prefix="Hook diagnosis failed",
        )
    if command == "/hooks evaluations" or command.startswith("/hooks evaluations "):
        parts = command.split()
        if len(parts) not in {2, 3} or (len(parts) == 3 and not _positive_ascii_integer(parts[2])):
            return _usage("Usage: /hooks evaluations [1-100]")
        limit = 20 if len(parts) == 2 else int(parts[2])
        if limit > 100:
            return _usage("Usage: /hooks evaluations [1-100]")
        return _call(
            lambda: render_hook_evaluations(session.hook_evaluations(limit)),
            kind="info",
            failure_prefix="Hook evaluation inspection failed",
        )
    if command == "/hooks runs" or command.startswith("/hooks runs "):
        parts = command.split()
        if len(parts) not in {2, 3} or (len(parts) == 3 and not _positive_ascii_integer(parts[2])):
            return _usage("Usage: /hooks runs [1-100]")
        limit = 20 if len(parts) == 2 else int(parts[2])
        if limit > 100:
            return _usage("Usage: /hooks runs [1-100]")
        return _call(
            lambda: render_hook_handler_runs(session.hook_handler_runs(limit), limit),
            kind="info",
            failure_prefix="Hook handler run inspection failed",
        )
    if command == "/hooks task" or command.startswith("/hooks task "):
        parts = command.split()
        if len(parts) not in {3, 4} or (len(parts) == 4 and not _positive_ascii_integer(parts[3])):
            return _usage("Usage: /hooks task <task-id> [1-100]")
        limit = 20 if len(parts) == 3 else int(parts[3])
        if limit > 100:
            return _usage("Usage: /hooks task <task-id> [1-100]")
        return _call(
            lambda: render_hook_evaluations(session.task_hook_evaluations(parts[2], limit)),
            kind="info",
            failure_prefix="Task Hook evaluation inspection failed",
        )
    if command == "/hooks show" or command.startswith("/hooks show "):
        parts = command.split()
        if len(parts) != 3:
            return _usage("Usage: /hooks show <hook-id>")
        return _call(
            lambda: render_hook_entry(session.inspect_hooks().get(parts[2])),
            kind="info",
            failure_prefix="Hook inspection failed",
        )
    if command.startswith("/hooks "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(
            subcommand, ("active", "list", "show", "doctor", "evaluations", "runs", "task")
        )
        return _usage(
            f"Unknown Hook command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Usage: /hooks <active|list|show|doctor|evaluations|runs|task>"
        )
    if command == "/skills":
        return _call(lambda: render_skill_activation(session.inspect_skills()), kind="info")
    if command == "/skills active":
        return _call(lambda: render_skill_activation(session.inspect_skills()), kind="info")
    if command == "/skills list":
        return _call(
            lambda: render_skill_inventory(session.inspect_skill_inventory()[0]),
            kind="info",
            failure_prefix="Skill inventory inspection failed",
        )
    if command == "/skills doctor":
        return _call(
            lambda: render_skill_doctor(*session.inspect_skill_inventory()),
            kind="info",
            failure_prefix="Skill diagnosis failed",
        )
    if command.startswith("/skills show "):
        name = command.removeprefix("/skills show ")
        if not name or any(character.isspace() for character in name):
            return _usage("Usage: /skills show <name>")
        return _call(
            lambda: render_skill_candidate(session.inspect_skill_inventory()[0].get(name)),
            kind="info",
            failure_prefix="Skill inspection failed",
        )
    if command.startswith("/skills search "):
        query = command.removeprefix("/skills search ").strip()
        if not query:
            return _usage("Usage: /skills search <query>")
        return _call(
            lambda: render_skill_search(
                session.inspect_skill_inventory()[0].search(query, limit=8), query
            ),
            kind="info",
            failure_prefix="Skill search failed",
        )
    if command == "/skills conflicts":
        return _call(
            lambda: render_skill_conflicts(session.inspect_skill_inventory()[0]),
            kind="info",
            failure_prefix="Skill conflict inspection failed",
        )
    if command.startswith("/skills fetch "):
        url = command.removeprefix("/skills fetch ").strip()
        if not url or any(character.isspace() for character in url):
            return _usage("Usage: /skills fetch <https-url>")
        return _call(
            lambda: render_quarantined_skill_candidate(session.fetch_skill_candidate(url)),
            kind="info",
            failure_prefix="Skill fetch failed",
        )
    if command == "/skills candidates":
        return _call(
            lambda: render_skill_candidate_list(session.list_skill_candidates()),
            kind="info",
            failure_prefix="Skill candidate listing failed",
        )
    if command.startswith("/skills candidate show "):
        candidate_id = command.removeprefix("/skills candidate show ").strip()
        if not candidate_id or any(character.isspace() for character in candidate_id):
            return _usage("Usage: /skills candidate show <candidate-id>")
        return _call(
            lambda: render_quarantined_skill_candidate(
                session.inspect_skill_candidate(candidate_id)
            ),
            kind="info",
            failure_prefix="Skill candidate inspection failed",
        )
    if command.startswith("/skills candidate reject "):
        candidate_id = command.removeprefix("/skills candidate reject ").strip()
        if not candidate_id or any(character.isspace() for character in candidate_id):
            return _usage("Usage: /skills candidate reject <candidate-id>")
        return _call(
            lambda: render_quarantined_skill_candidate(
                session.reject_skill_candidate(candidate_id)
            ),
            kind="info",
            failure_prefix="Skill candidate rejection failed",
        )
    if command.startswith("/skills install "):
        try:
            parts = shlex.split(command)
        except ValueError:
            return _usage("Usage: /skills install <candidate-id> [workspace|project|user]")
        if len(parts) not in {3, 4} or (
            len(parts) == 4 and parts[3] not in {"workspace", "project", "user"}
        ):
            return _usage("Usage: /skills install <candidate-id> [workspace|project|user]")
        return _call(
            lambda: render_quarantined_skill_candidate(
                session.install_skill_candidate(
                    parts[2], scope=None if len(parts) == 3 else parts[3]
                )
            ),
            kind="success",
            failure_prefix="Skill candidate installation failed",
        )
    if command == "/skills show" or command.startswith("/skills "):
        return _usage(
            "Usage: /skills [active|list|show <name>|search <query>|conflicts|doctor|"
            "fetch <url>|candidates|candidate show|candidate reject|install]"
        )
    if command == "/status":
        return _call(lambda: render_project_status(session.project_status()), kind="info")
    if command.startswith("/status "):
        return _usage("Usage: /status")
    if command == "/permissions" or command.startswith("/permissions "):
        return _permissions(command, session)
    if command == "/sandbox" or command == "/sandbox check":
        return _call(
            lambda: render_command_sandbox_inspection(session.inspect_command_sandbox()),
            kind="info",
            failure_prefix="Sandbox check failed",
        )
    if command.startswith("/sandbox "):
        return _usage("Usage: /sandbox check")
    if command == "/context":
        try:
            message, kind = render_context_inspection(session.inspect_context())
            return SlashResult(handled=True, message=message, kind=kind)
        except Exception as error:
            return _command_error(error, failure_prefix="Context inspection failed")
    if command.startswith("/context "):
        return _usage("Usage: /context")
    if command == "/instructions":
        return _call(
            lambda: render_project_instructions_inspection(session.inspect_project_instructions()),
            kind="info",
            failure_prefix="Project instruction inspection failed",
        )
    if command.startswith("/instructions "):
        return _usage("Usage: /instructions")
    if command == "/usage":
        return _call(lambda: render_usage_summary(session.usage()), kind="info")
    if command == "/usage session":
        return _call(
            lambda: render_durable_usage_summary(session.session_usage()),
            kind="info",
        )
    if command == "/usage turns":
        return _call(
            lambda: render_durable_usage_summary(
                session.turn_usage_history(),
                turns=True,
            ),
            kind="info",
        )
    if command.startswith("/usage "):
        return _usage("Usage: /usage [session|turns]")
    if command == "/tool-details" or command.startswith("/tool-details "):
        return _tool_details(command, tool_details)
    if command == "/changes":
        return _call(lambda: render_git_status(session.git_status()), kind="info")
    if command in {"/changes unstaged", "/changes staged"}:
        scope = command.split()[1]
        return _call(lambda: render_git_diff(session.git_diff(scope)), kind="plain")
    if command.startswith("/changes "):
        return _usage("Usage: /changes [unstaged|staged]")
    if command == "/commits" or command.startswith("/commits "):
        return _commits(command, session)
    if command == "/commit" or command.startswith("/commit "):
        return _commit(command, session)
    if command == "/output" or command.startswith("/output "):
        return _output(command, session)
    if command == "/compact preview":
        try:
            message, kind = render_compact_preview(session.preview_compaction())
            return SlashResult(handled=True, message=message, kind=kind)
        except Exception as error:
            return _command_error(error, failure_prefix="Compaction preview failed")
    if command == "/compact":
        try:
            return SlashResult(
                handled=True,
                message=render_compact_result(session.compact_context()),
                kind="success",
            )
        except Exception as error:
            result = _command_error(error, failure_prefix="Compaction failed")
            suffix = " Full history and effective context are unchanged."
            return SlashResult(
                handled=True,
                message=f"{result.message}{suffix}",
                kind=result.kind,
            )
    if command.startswith("/compact "):
        return _usage("Usage: /compact | /compact preview")
    if command == "/compactions" or command.startswith("/compactions "):
        return _compactions(command, session)
    if command == "/history" or command.startswith("/history "):
        return _history(command, session)
    if command == "/actions" or command.startswith("/actions "):
        return _actions(command, session)
    if command == "/tools" or command.startswith("/tools "):
        return _tools(command, session)
    if command == "/task":
        return SlashResult(handled=True, message=TASK_HELP, kind="info")
    if command == "/child":
        return SlashResult(handled=True, message=CHILD_HELP, kind="info")
    if command == "/team":
        return SlashResult(handled=True, message=TEAM_HELP, kind="info")
    if command == "/child create" or command.startswith("/child create "):
        return _child_create(command, session)
    if command == "/child prepare" or command.startswith("/child prepare "):
        return _child_prepare(command, session)
    if command == "/child run" or command.startswith("/child run "):
        return _child_run(command, session)
    if command == "/child start" or command.startswith("/child start "):
        return _child_start(command, session)
    if command == "/child list" or command.startswith("/child list "):
        return _child_list(command, session)
    if command == "/child show" or command.startswith("/child show "):
        return _child_show(command, session)
    if command == "/child cancel" or command.startswith("/child cancel "):
        return _child_cancel(command, session)
    if command == "/child wait" or command.startswith("/child wait "):
        return _child_wait(command, session)
    if command == "/child recover" or command.startswith("/child recover "):
        return _child_recover(command, session)
    if command == "/child handoff" or command.startswith("/child handoff "):
        return _child_handoff(command, session)
    if command == "/child deliver" or command.startswith("/child deliver "):
        return _child_deliver(command, session)
    if command.startswith("/child "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(
            subcommand,
            (
                "create",
                "prepare",
                "run",
                "start",
                "list",
                "show",
                "cancel",
                "wait",
                "recover",
                "handoff",
                "deliver",
            ),
        )
        return _usage(
            f"Unknown Child Run command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Type /help child for commands."
        )
    if command == "/team create" or command.startswith("/team create "):
        return _team_create(command, session)
    if command == "/team list" or command.startswith("/team list "):
        return _team_list(command, session)
    if command == "/team show" or command.startswith("/team show "):
        return _team_show(command, session)
    if command == "/team close" or command.startswith("/team close "):
        return _team_close(command, session)
    if command == "/team member add" or command.startswith("/team member add "):
        return _team_member_add(command, session)
    if command == "/team member list" or command.startswith("/team member list "):
        return _team_member_list(command, session)
    if command == "/team member show" or command.startswith("/team member show "):
        return _team_member_show(command, session)
    if command == "/team member disable" or command.startswith("/team member disable "):
        return _team_member_disable(command, session)
    if command == "/team member enable" or command.startswith("/team member enable "):
        return _team_member_enable(command, session)
    if command == "/team member leave" or command.startswith("/team member leave "):
        return _team_member_leave(command, session)
    if command.startswith("/team member "):
        subcommand = command.split(maxsplit=3)[2]
        suggestion = _suggest_token(
            subcommand, ("add", "list", "show", "disable", "enable", "leave")
        )
        return _usage(
            f"Unknown Team member command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Type /help team for commands."
        )
    if command == "/team worktree" or command.startswith("/team worktree "):
        return _team_worktree(command, session)
    if command == "/team assignment create" or command.startswith("/team assignment create "):
        return _team_assignment_create(command, session)
    if command == "/team assignment list" or command.startswith("/team assignment list "):
        return _team_assignment_list(command, session)
    if command == "/team assignment show" or command.startswith("/team assignment show "):
        return _team_assignment_show(command, session)
    if command == "/team assignment prepare" or command.startswith("/team assignment prepare "):
        return _team_assignment_prepare(command, session)
    if command == "/team assignment run" or command.startswith("/team assignment run "):
        return _team_assignment_run(command, session)
    if command == "/team assignment start" or command.startswith("/team assignment start "):
        return _team_assignment_start(command, session)
    if command == "/team assignment wait" or command.startswith("/team assignment wait "):
        return _team_assignment_wait(command, session)
    if command == "/team assignment cancel" or command.startswith("/team assignment cancel "):
        return _team_assignment_cancel(command, session)
    if command == "/team assignment handoff" or command.startswith("/team assignment handoff "):
        return _team_assignment_handoff(command, session)
    if command == "/team assignment recover" or command.startswith("/team assignment recover "):
        return _team_assignment_recover(command, session)
    if command.startswith("/team assignment "):
        subcommand = command.split(maxsplit=3)[2]
        suggestion = _suggest_token(
            subcommand,
            (
                "create",
                "list",
                "show",
                "prepare",
                "run",
                "start",
                "wait",
                "cancel",
                "handoff",
                "recover",
            ),
        )
        return _usage(
            f"Unknown Team assignment command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Type /help team for commands."
        )
    if command == "/team schedule run" or command.startswith("/team schedule run "):
        return _team_schedule_run(command, session)
    if command == "/team schedule start" or command.startswith("/team schedule start "):
        return _team_schedule_start(command, session)
    if command == "/team schedule status" or command.startswith("/team schedule status "):
        return _team_schedule_status(command, session)
    if command == "/team schedule wait" or command.startswith("/team schedule wait "):
        return _team_schedule_wait(command, session)
    if command == "/team schedule cancel" or command.startswith("/team schedule cancel "):
        return _team_schedule_cancel(command, session)
    if command == "/team schedule recover" or command.startswith("/team schedule recover "):
        return _team_schedule_recover(command, session)
    if command.startswith("/team schedule "):
        subcommand = command.split(maxsplit=3)[2]
        suggestion = _suggest_token(
            subcommand, ("run", "start", "status", "wait", "cancel", "recover")
        )
        return _usage(
            f"Unknown Team schedule command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Type /help team for commands."
        )
    if command == "/team message send" or command.startswith("/team message send "):
        return _team_message_send(command, session)
    if command == "/team message list" or command.startswith("/team message list "):
        return _team_message_list(command, session)
    if command == "/team message show" or command.startswith("/team message show "):
        return _team_message_show(command, session)
    if command == "/team message read" or command.startswith("/team message read "):
        return _team_message_read(command, session)
    if command == "/team message cancel" or command.startswith("/team message cancel "):
        return _team_message_cancel(command, session)
    if command.startswith("/team message "):
        subcommand = command.split(maxsplit=3)[2]
        suggestion = _suggest_token(subcommand, ("send", "list", "show", "read", "cancel"))
        return _usage(
            f"Unknown Team message command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Type /help team for commands."
        )
    if command == "/team work create" or command.startswith("/team work create "):
        return _team_work_create(command, session)
    if command == "/team work list" or command.startswith("/team work list "):
        return _team_work_list(command, session)
    if command == "/team work show" or command.startswith("/team work show "):
        return _team_work_show(command, session)
    if command == "/team work cancel" or command.startswith("/team work cancel "):
        return _team_work_cancel(command, session)
    if command == "/team work assign" or command.startswith("/team work assign "):
        return _team_work_assign(command, session)
    if command == "/team work complete" or command.startswith("/team work complete "):
        return _team_work_complete(command, session)
    if command == "/team work release" or command.startswith("/team work release "):
        return _team_work_release(command, session)
    if command.startswith("/team work "):
        subcommand = command.split(maxsplit=3)[2]
        suggestion = _suggest_token(
            subcommand, ("create", "list", "show", "cancel", "assign", "complete", "release")
        )
        return _usage(
            f"Unknown Team work command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Type /help team for commands."
        )
    if command.startswith("/team "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(
            subcommand,
            (
                "create",
                "list",
                "show",
                "close",
                "member",
                "assignment",
                "message",
                "work",
                "worktree",
            ),
        )
        return _usage(
            f"Unknown Team command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Type /help team for commands."
        )
    if command == "/task start" or command.startswith("/task start "):
        return _task_start(command, session)
    if command == "/task proposals" or command.startswith("/task proposals "):
        return _task_proposals(command, session)
    if command == "/task proposal show" or command.startswith("/task proposal show "):
        return _task_proposal_show(command, session)
    if command == "/task proposal accept" or command.startswith("/task proposal accept "):
        return _task_proposal_accept(command, session)
    if command == "/task proposal reject" or command.startswith("/task proposal reject "):
        return _task_proposal_reject(command, session)
    if command == "/task proposal drive" or command.startswith("/task proposal drive "):
        return _task_proposal_drive(command, session)
    if command == "/task list" or command.startswith("/task list "):
        return _task_list(command, session)
    if command == "/task show" or command.startswith("/task show "):
        return _task_show(command, session)
    if command == "/task continue" or command.startswith("/task continue "):
        return _task_continue(command)
    if command == "/task plan accept" or command.startswith("/task plan accept "):
        return _task_plan_accept(command, session)
    if command == "/task plan" or command.startswith("/task plan "):
        return _task_plan(command)
    if command == "/task run" or command.startswith("/task run "):
        return _task_run(command)
    if command == "/task reflect" or command.startswith("/task reflect "):
        return _task_turn_single(command, "reflect")
    if command == "/task correct" or command.startswith("/task correct "):
        return _task_correct(command)
    if command == "/task revise" or command.startswith("/task revise "):
        return _task_turn_single(command, "revise")
    if command == "/task drive" or command.startswith("/task drive "):
        return _task_drive(command)
    if command == "/task next" or command.startswith("/task next "):
        return _task_next(command, session)
    if command == "/task checkpoint" or command.startswith("/task checkpoint "):
        return _task_checkpoint(command, session)
    if command == "/task pause" or command.startswith("/task pause "):
        return _task_pause(command, session)
    if command == "/task resume" or command.startswith("/task resume "):
        return _task_resume(command, session)
    if command == "/task recover" or command.startswith("/task recover "):
        return _task_recover(command, session)
    if command == "/task verify host" or command.startswith("/task verify host "):
        return _task_verify_host(command, session)
    if command == "/task verify" or command.startswith("/task verify "):
        return _task_verify(command, session)
    if command == "/task review" or command.startswith("/task review "):
        return _task_review(command, session)
    if command == "/task complete" or command.startswith("/task complete "):
        return _task_simple_mutation(command, session, "complete")
    if command == "/task cancel" or command.startswith("/task cancel "):
        return _task_reasoned_terminal(command, session, "cancel")
    if command == "/task fail" or command.startswith("/task fail "):
        return _task_reasoned_terminal(command, session, "fail")
    if command == "/task rename" or command.startswith("/task rename "):
        return _task_rename(command, session)
    if command == "/task archive" or command.startswith("/task archive "):
        return _task_archive(command, session, True)
    if command == "/task unarchive" or command.startswith("/task unarchive "):
        return _task_archive(command, session, False)
    if command == "/task timeline" or command.startswith("/task timeline "):
        return _task_timeline(command, session)
    if command == "/task derive" or command.startswith("/task derive "):
        return _task_derive(command, session)
    if command.startswith("/task "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(
            subcommand,
            (
                "start",
                "proposals",
                "proposal",
                "list",
                "show",
                "continue",
                "plan",
                "run",
                "reflect",
                "correct",
                "revise",
                "drive",
                "next",
                "checkpoint",
                "pause",
                "resume",
                "recover",
                "verify",
                "review",
                "complete",
                "cancel",
                "fail",
                "rename",
                "archive",
                "unarchive",
                "timeline",
                "derive",
            ),
        )
        return _usage(
            "Unknown task command: "
            f"{subcommand}{_suggestion_line(suggestion)}\nType /help task for commands."
        )
    if command == "/session show" or command.startswith("/session show "):
        return _session_show(command, session)
    if command == "/session preview" or command.startswith("/session preview "):
        return _session_preview(command, session)
    if command == "/session turns" or command.startswith("/session turns "):
        return _session_turns(command, session)
    if command == "/session search" or command.startswith("/session search "):
        return _session_search(command, session)
    if command == "/session export" or command.startswith("/session export "):
        return _session_export(command, session)
    if command == "/session doctor" or command.startswith("/session doctor "):
        return _session_doctor(command, session)
    if command == "/session repair" or command.startswith("/session repair "):
        if session_switch is not None:
            session_switch.clear()
        return _session_repair(command, session)
    if command == "/session fork" or command.startswith("/session fork "):
        if session_switch is not None:
            session_switch.clear()
        return _session_fork(command, session)
    if command == "/session list" or command.startswith("/session list "):
        return _session_list(command, session)
    if command == "/session switch" or command.startswith("/session switch "):
        return _session_switch(command, session, session_switch)
    if command == "/session new" or command.startswith("/session new "):
        if command != "/session new":
            return _usage("Usage: /session new")
        if session_switch is not None:
            session_switch.clear()
        return _new_session(session)
    if command == "/session rename" or command.startswith("/session rename "):
        if session_switch is not None:
            session_switch.clear()
        return _rename_session(command, session)
    if command in {"/session archive", "/session unarchive"}:
        if session_switch is not None:
            session_switch.clear()
        return _archive_session(session, archived=command == "/session archive")
    if command.startswith("/session archive ") or command.startswith("/session unarchive "):
        return _usage("Usage: /session archive | /session unarchive")
    if command in {"/session pin", "/session unpin"}:
        if session_switch is not None:
            session_switch.clear()
        return _pin_session(session, pinned=command == "/session pin")
    if command.startswith("/session pin ") or command.startswith("/session unpin "):
        return _usage("Usage: /session pin | /session unpin")
    if command.startswith("/session "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(
            subcommand,
            (
                "show",
                "preview",
                "turns",
                "search",
                "export",
                "fork",
                "doctor",
                "repair",
                "list",
                "new",
                "rename",
                "archive",
                "unarchive",
                "pin",
                "unpin",
                "switch",
            ),
        )
        return _usage(
            "Unknown session command: "
            f"{subcommand}{_suggestion_line(suggestion)}\nUsage: "
            "/session <show|preview|turns|search|export|fork|doctor|repair|list|new|rename|archive|unarchive|pin|unpin|switch>"
        )
    if command == "/resume" or command.startswith("/resume "):
        if session_switch is not None:
            session_switch.clear()
        return _resume(command, session)
    if command == "/provider list" or command.startswith("/provider list "):
        if command != "/provider list":
            return _usage("Usage: /provider list")
        return _provider_list(session)
    if command == "/provider current" or command.startswith("/provider current "):
        if command != "/provider current":
            return _usage("Usage: /provider current")
        return _call(lambda: render_runtime_status(session.status()), kind="info")
    if command == "/provider use" or command.startswith("/provider use "):
        return _provider_use(command, session)
    if command.startswith("/provider "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(subcommand, ("list", "current", "use"))
        return _usage(
            f"Unknown provider command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Usage: /provider <list|current|use>"
        )
    if command == "/search status" or command.startswith("/search status "):
        if command != "/search status":
            return _usage("Usage: /search status")
        return _search_status(session)
    if command == "/mcp list":
        return _call(
            lambda: render_mcp_server_statuses(session.inspect_mcp_servers()),
            kind="info",
            failure_prefix="MCP server inspection failed",
        )
    if command == "/mcp status":
        return _call(
            lambda: (
                render_mcp_server_statuses(session.inspect_mcp_servers())
                + "\n"
                + render_mcp_runtime_statuses(session.inspect_mcp_runtime())
            ),
            kind="info",
            failure_prefix="MCP server inspection failed",
        )
    if command == "/mcp show" or command.startswith("/mcp show "):
        parts = command.split()
        if len(parts) != 3:
            return _usage("Usage: /mcp show <server-name>")
        return _call(
            lambda: render_mcp_server_status(session.inspect_mcp_server(parts[2])),
            kind="info",
            failure_prefix="MCP server inspection failed",
        )
    if command == "/mcp probe" or command.startswith("/mcp probe "):
        parts = command.split()
        if len(parts) != 3:
            return _usage("Usage: /mcp probe <server-name>")
        return _call(
            lambda: render_mcp_probe_result(session.probe_mcp_server(parts[2])),
            kind="success",
            failure_prefix="MCP probe failed",
        )
    if command == "/mcp catalog":
        return _call(
            lambda: render_mcp_catalog(session.inspect_mcp_catalog()),
            kind="info",
            failure_prefix="MCP catalog inspection failed",
        )
    if command.startswith("/mcp "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(subcommand, ("list", "status", "show", "probe", "catalog"))
        return _usage(
            f"Unknown MCP command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Usage: /mcp <list|status|show|probe|catalog>"
        )
    if command == "/search sources" or command.startswith("/search sources "):
        if command != "/search sources":
            return _usage("Usage: /search sources")
        return _search_status(session)
    if command == "/search reset" or command.startswith("/search reset "):
        if command != "/search reset":
            return _usage("Usage: /search reset")
        return _search_reset(session)
    if command == "/search use" or command.startswith("/search use "):
        return _search_use(command, session)
    if command == "/search mode" or command.startswith("/search mode "):
        return _search_mode(command, session)
    if command == "/search domains" or command.startswith("/search domains "):
        return _search_domains(command, session)
    if command == "/search context" or command.startswith("/search context "):
        return _search_context(command, session)
    if command.startswith("/search "):
        subcommand = command.split(maxsplit=2)[1]
        suggestion = _suggest_token(
            subcommand, ("status", "sources", "use", "mode", "domains", "context", "reset")
        )
        return _usage(
            f"Unknown search command: {subcommand}{_suggestion_line(suggestion)}\n"
            "Usage: /search <status|sources|use|mode|domains|context|reset>"
        )
    if command == "/model" or command.startswith("/model "):
        return _model(command, session)
    token = command.split(maxsplit=1)[0]
    suggestion = _suggest_token(token, TOP_LEVEL_COMMANDS)
    if suggestion is not None:
        return _usage(
            f"Unknown command: {command}.\nDid you mean {suggestion}?\nType /help for controls."
        )
    return _usage(f"Unknown command: {command}. Type /help for controls.")


def _search_status(session: ReplSession) -> SlashResult:
    return _call(
        lambda: render_web_search_sources(session.inspect_web_search_sources()),
        kind="info",
        failure_prefix="Search source inspection failed",
    )


def _search_use(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) < 3:
        return _usage("Usage: /search use <source> [source...]")
    try:
        configuration = session.set_web_search_sources(tuple(parts[2:]))
    except WebSearchPreparationError as error:
        return SlashResult(
            handled=True,
            message=f"Search source update rejected: {error}",
            kind="warning",
        )
    except Exception as error:
        return _command_error(error, failure_prefix="Search source update failed")
    return SlashResult(
        handled=True,
        message=render_web_search_sources(configuration),
        kind="success",
    )


def _search_reset(session: ReplSession) -> SlashResult:
    return _call(
        lambda: render_web_search_sources(session.reset_web_search_sources()),
        kind="success",
        failure_prefix="Search source reset failed",
    )


def _search_mode(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /search mode <auto|required>")
    return _call(
        lambda: render_web_search_sources(session.set_native_search_mode(parts[2])),
        kind="success",
        failure_prefix="Provider search mode update failed",
    )


def _search_domains(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) < 3:
        return _usage("Usage: /search domains <domain> [domain...] | reset")
    domains = None if parts[2:] == ["reset"] else tuple(parts[2:])
    return _call(
        lambda: render_web_search_sources(session.set_native_search_domains(domains)),
        kind="success",
        failure_prefix="Provider search domain update failed",
    )


def _search_context(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3 or parts[2] not in {"low", "medium", "high", "reset"}:
        return _usage("Usage: /search context <low|medium|high|reset>")
    size = None if parts[2] == "reset" else parts[2]
    return _call(
        lambda: render_web_search_sources(session.set_native_search_context(size)),
        kind="success",
        failure_prefix="Provider search context update failed",
    )


def _permissions(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    permission_mode: PermissionMode | None = None
    approval_mode: ApprovalMode | None = None
    if len(parts) in {2, 3}:
        try:
            permission_mode = PermissionMode(parts[1])
            if len(parts) == 3:
                approval_mode = ApprovalMode(parts[2])
        except ValueError:
            return _permissions_usage()
    elif len(parts) != 1:
        return _permissions_usage()
    return _call(
        lambda: render_permission_matrix(
            session.project_status(),
            permission_mode=permission_mode,
            approval_mode=approval_mode,
        ),
        kind="info",
    )


def _permissions_usage() -> SlashResult:
    return _usage(
        "Usage: /permissions | /permissions "
        "<read-only|workspace-write|danger-full-access> [ask|auto]"
    )


def _suggest_token(token: str, candidates: tuple[str, ...]) -> str | None:
    if not token or len(token) > 64 or any(character.isspace() for character in token):
        return None
    matches = get_close_matches(token, candidates, n=1, cutoff=0.7)
    return matches[0] if matches else None


def _suggestion_line(suggestion: str | None) -> str:
    return f"\nDid you mean {suggestion}?" if suggestion is not None else ""


def _history(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit() or int(parts[1]) <= 0:
        return _usage("Usage: /history <positive integer>")
    return _call(lambda: render_recent_history(session.turns, int(parts[1])))


def _commits(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=2)
    if len(parts) == 1:
        limit = DEFAULT_GIT_LOG_LIMIT
        path = "."
    elif len(parts) >= 2 and parts[1].isascii() and parts[1].isdigit():
        limit = int(parts[1])
        path = parts[2] if len(parts) == 3 else "."
    else:
        return _usage(f"Usage: /commits [1-{MAX_GIT_LOG_LIMIT}] [path]")
    if not 1 <= limit <= MAX_GIT_LOG_LIMIT or not path:
        return _usage(f"Usage: /commits [1-{MAX_GIT_LOG_LIMIT}] [path]")
    return _call(lambda: render_git_log(session.git_log(limit, path)), kind="info")


def _commit(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=2)
    if len(parts) not in {2, 3}:
        return _usage("Usage: /commit <full-commit-id> [path]")
    commit_id = parts[1]
    path = parts[2] if len(parts) == 3 else "."
    if not path:
        return _usage("Usage: /commit <full-commit-id> [path]")
    return _call(lambda: render_git_show(session.git_show(commit_id, path)), kind="plain")


def _actions(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if parts == ["/actions", "last"]:
        return _call(lambda: render_action_audits(session.action_audits(), 1), kind="info")
    count = DEFAULT_ACTION_AUDIT_COUNT
    status_filter: str | None = None
    tool_filter: str | None = None
    count_seen = False
    valid_statuses = {status.value for status in ActionAuditStatus}
    for argument in parts[1:]:
        if argument.isascii() and argument.isdigit() and not count_seen:
            count = int(argument)
            count_seen = True
        elif argument.startswith("status=") and status_filter is None:
            status_filter = argument.removeprefix("status=")
        elif argument.startswith("tool=") and tool_filter is None:
            tool_filter = argument.removeprefix("tool=")
        else:
            return _actions_usage()
    if not 1 <= count <= MAX_ACTION_AUDIT_COUNT:
        return _actions_usage()
    if status_filter is not None and status_filter not in valid_statuses:
        return _actions_usage()
    if tool_filter is not None and (not tool_filter or len(tool_filter) > 64):
        return _actions_usage()

    def render() -> str:
        audits = session.action_audits()
        if status_filter is not None:
            audits = tuple(audit for audit in audits if audit.status.value == status_filter)
        if tool_filter is not None:
            audits = tuple(audit for audit in audits if audit.identity.tool_name == tool_filter)
        if not audits and (status_filter is not None or tool_filter is not None):
            return "No action audits match the selected filters."
        return render_action_audits(audits, count)

    return _call(render, kind="info")


def _actions_usage() -> SlashResult:
    return _usage(
        f"Usage: /actions last | /actions [1-{MAX_ACTION_AUDIT_COUNT}] "
        "[status=<status>] [tool=<name>]"
    )


def _tools(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if parts == ["/tools", "catalog"]:
        return _call(lambda: render_tool_catalog(session.project_status()), kind="info")
    if len(parts) == 3 and parts[:2] == ["/tools", "catalog"]:
        tool_name = parts[2]
        if tool_name not in {definition.name for definition in TOOL_CATALOG}:
            suggestion = _suggest_token(tool_name, tuple(tool.name for tool in TOOL_CATALOG))
            return _usage(
                f"Unknown model-visible tool: {tool_name}{_suggestion_line(suggestion)}\n"
                "Usage: /tools catalog [tool-name]"
            )
        return _call(
            lambda: render_tool_catalog(session.project_status(), tool_name),
            kind="info",
        )
    details = False
    if len(parts) == 1:
        count = DEFAULT_TOOL_LEDGER_COUNT
    elif len(parts) == 2 and parts[1] == "details":
        count = DEFAULT_TOOL_LEDGER_COUNT
        details = True
    elif (
        len(parts) == 2
        and parts[1].isascii()
        and parts[1].isdigit()
        and 1 <= int(parts[1]) <= MAX_TOOL_LEDGER_COUNT
    ):
        count = int(parts[1])
    elif (
        len(parts) == 3
        and parts[1] == "details"
        and parts[2].isascii()
        and parts[2].isdigit()
        and 1 <= int(parts[2]) <= MAX_TOOL_LEDGER_COUNT
    ):
        count = int(parts[2])
        details = True
    else:
        return _usage(
            f"Usage: /tools catalog [tool-name] | /tools [1-{MAX_TOOL_LEDGER_COUNT}] | "
            f"/tools details [1-{MAX_TOOL_LEDGER_COUNT}]"
        )
    return _call(
        lambda: render_tool_ledgers(session.tool_ledgers(count), details=details),
        kind="info",
    )


def _tool_details(command: str, settings: ToolDetailSettings | None) -> SlashResult:
    parts = command.split()
    if len(parts) == 1:
        if settings is None:
            return _usage("Usage: /tool-details <compact|full>")
        return _info(f"Live tool details: {settings.mode.value} (process-local).")
    if len(parts) != 2 or parts[1] not in {"compact", "full"}:
        return _usage("Usage: /tool-details <compact|full>")
    if settings is None:
        return _usage("Usage: /tool-details <compact|full>")
    settings.mode = ToolDetailMode(parts[1])
    if settings.mode == ToolDetailMode.FULL:
        return SlashResult(
            handled=True,
            message=(
                "Live tool details: full (process-local). Future command starts show bounded "
                "structured argv, which may contain sensitive values; file, edit, patch, and "
                "search contents remain redacted."
            ),
            kind="warning",
        )
    return _info("Live tool details: compact (process-local).")


def _compactions(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) == 1:
        count = DEFAULT_COMPACTION_HISTORY_COUNT
    elif (
        len(parts) == 2
        and parts[1].isascii()
        and parts[1].isdigit()
        and 1 <= int(parts[1]) <= MAX_COMPACTION_HISTORY_COUNT
    ):
        count = int(parts[1])
    else:
        return _usage(f"Usage: /compactions [1-{MAX_COMPACTION_HISTORY_COUNT}]")
    return _call(
        lambda: render_compaction_history(session.compaction_history(count)),
        kind="info",
    )


def _output(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) == 1:
        message, kind = render_output_budget(session.status())
        return SlashResult(handled=True, message=message, kind=kind)
    if len(parts) != 2:
        return _usage("Usage: /output [1-100000000|reset]")
    if parts[1] == "reset":
        value = None
    elif (
        parts[1].isascii() and parts[1].isdigit() and 1 <= int(parts[1]) <= MAX_MODEL_OUTPUT_TOKENS
    ):
        value = int(parts[1])
    else:
        return _usage("Usage: /output [1-100000000|reset]")
    try:
        message, kind = render_output_budget_update(session.set_output_budget(value))
        return SlashResult(handled=True, message=message, kind=kind)
    except RuntimeSwitchContextError as error:
        return SlashResult(
            handled=True,
            message=render_output_budget_rejection(error.report),
            kind="error",
        )
    except Exception as error:
        return _command_error(error, failure_prefix="Output budget change failed")


@dataclass(frozen=True)
class _SessionFilters:
    count: int
    state: str | None = None
    archive: str | None = None
    pin: str | None = None
    model: str | None = None
    name: str | None = None


def _parse_session_filters(
    arguments: list[str],
    *,
    default_count: int,
    maximum_count: int,
) -> _SessionFilters | None:
    count = default_count
    state: str | None = None
    archive: str | None = None
    pin: str | None = None
    model: str | None = None
    name: str | None = None
    count_seen = False
    for argument in arguments:
        if argument.isascii() and argument.isdigit() and not count_seen:
            count = int(argument)
            count_seen = True
        elif argument in {"open", "closed"} and state is None:
            state = argument
        elif argument in {"active", "archived"} and archive is None:
            archive = argument
        elif argument in {"pinned", "unpinned"} and pin is None:
            pin = argument
        elif argument.startswith("model=") and model is None:
            model = argument.removeprefix("model=")
        elif argument.startswith("name=") and name is None:
            name = argument.removeprefix("name=")
        else:
            return None
    if not 1 <= count <= maximum_count:
        return None
    if model is not None and (not model or len(model) > 256):
        return None
    if name is not None and not _valid_session_name_filter(name):
        return None
    return _SessionFilters(count, state, archive, pin, model, name)


def _apply_session_filters(sessions, filters: _SessionFilters):
    if filters.state is not None:
        closed = filters.state == "closed"
        sessions = tuple(info for info in sessions if info.closed is closed)
    if filters.archive is not None:
        archived = filters.archive == "archived"
        sessions = tuple(info for info in sessions if info.archived is archived)
    if filters.pin is not None:
        pinned = filters.pin == "pinned"
        sessions = tuple(info for info in sessions if info.pinned is pinned)
    if filters.model is not None:
        sessions = tuple(
            info
            for info in sessions
            if getattr(info.binding, "selected_model", None) == filters.model
        )
    if filters.name is not None:
        needle = filters.name.casefold()
        sessions = tuple(info for info in sessions if needle in info.name.casefold())
    return sessions


def _session_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) == 2:
        return _call(lambda: render_session_info(session.session_info()), kind="info")
    if len(parts) != 3 or not _valid_session_read_selector(parts[2]):
        return _usage("Usage: /session show [latest|session-id]")
    return _call(
        lambda: render_session_info(session.inspect_session(parts[2])),
        kind="info",
        failure_prefix="Session inspection failed",
    )


def _session_preview(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {3, 4} or not _valid_session_read_selector(parts[2]):
        return _session_preview_usage()
    limit = DEFAULT_SESSION_PREVIEW_TURNS
    if len(parts) == 4:
        if not parts[3].isascii() or not parts[3].isdigit():
            return _session_preview_usage()
        limit = int(parts[3])
    if not 1 <= limit <= MAX_SESSION_PREVIEW_TURNS:
        return _session_preview_usage()
    return _call(
        lambda: render_session_preview(session.preview_session(parts[2], limit)),
        kind="info",
        failure_prefix="Session preview failed",
    )


def _session_preview_usage() -> SlashResult:
    return _usage(f"Usage: /session preview <latest|session-id> [1-{MAX_SESSION_PREVIEW_TURNS}]")


def _session_turns(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if (
        len(parts) not in {4, 5}
        or not _valid_session_read_selector(parts[2])
        or not _positive_ascii_integer(parts[3])
    ):
        return _session_turns_usage()
    count = DEFAULT_SESSION_PREVIEW_TURNS
    if len(parts) == 5:
        if not _positive_ascii_integer(parts[4]):
            return _session_turns_usage()
        count = int(parts[4])
    if count > MAX_SESSION_PREVIEW_TURNS:
        return _session_turns_usage()
    return _call(
        lambda: render_session_turn_range(
            session.session_turn_range(parts[2], int(parts[3]), count)
        ),
        kind="info",
        failure_prefix="Session turn inspection failed",
    )


def _session_turns_usage() -> SlashResult:
    return _usage(
        f"Usage: /session turns <latest|session-id> <start> [1-{MAX_SESSION_PREVIEW_TURNS}]"
    )


def _session_search(command: str, session: ReplSession) -> SlashResult:
    query = command.removeprefix("/session search").strip()
    if not query:
        return _usage("Usage: /session search <literal text>")
    return _call(
        lambda: render_session_search(
            session.search_sessions(query, DEFAULT_SESSION_SEARCH_MATCHES)
        ),
        kind="info",
        failure_prefix="Session search failed",
    )


def _session_export(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {3, 4} or not _valid_session_read_selector(parts[2]):
        return _usage("Usage: /session export <latest|session-id> [markdown|json]")
    format_name = parts[3] if len(parts) == 4 else "markdown"
    if format_name not in {"markdown", "json"}:
        return _usage("Usage: /session export <latest|session-id> [markdown|json]")
    return _call(
        lambda: render_session_export(session.export_session(parts[2]), format_name),
        kind="info",
        failure_prefix="Session export failed",
    )


def _session_doctor(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3 or not _valid_session_read_selector(parts[2]):
        return _usage("Usage: /session doctor <latest|session-id>")
    return _call(
        lambda: render_session_diagnosis(session.diagnose_session(parts[2])),
        kind="info",
        failure_prefix="Session diagnosis failed",
    )


def _session_repair(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3 or not _valid_session_read_selector(parts[2]):
        return _usage("Usage: /session repair <latest|session-id>")
    return _call(
        lambda: render_session_repair(session.repair_session(parts[2])),
        kind="success",
        failure_prefix="Session repair failed",
    )


def _session_fork(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if (
        len(parts) != 4
        or not _valid_session_read_selector(parts[2])
        or not _positive_ascii_integer(parts[3])
    ):
        return _usage("Usage: /session fork <latest|session-id> <through-turn>")

    def fork() -> str:
        info = session.fork_session(parts[2], int(parts[3]))
        return (
            f"Forked and selected Session: {info.name}\n"
            f"Session ID: {info.session_id}\n"
            f"Source: {info.forked_from_session_id} through turn #{info.forked_from_turn}\n"
            "Parent transcript, Action Audit, compaction checkpoints, and provider usage were "
            "not modified."
        )

    return _call(fork, kind="success", failure_prefix="Session fork failed")


def _positive_ascii_integer(value: str) -> bool:
    return value.isascii() and value.isdigit() and int(value) >= 1


def _valid_session_read_selector(value: str) -> bool:
    if value == "latest":
        return True
    try:
        canonical_session_id(value)
    except SessionRecordError:
        return False
    return True


def _task_start(command: str, session: ReplSession) -> SlashResult:
    objective = command.removeprefix("/task start").strip()
    if not objective:
        return _usage("Usage: /task start <objective>")
    return _call(
        lambda: "Created durable Task:\n" + render_task_info(session.create_task(objective)),
        kind="success",
        failure_prefix="Task creation failed",
    )


def _child_create(command: str, session: ReplSession) -> SlashResult:
    objective = command.removeprefix("/child create").strip()
    if not objective:
        return _usage("Usage: /child create <objective>")
    return _call(
        lambda: render_child_run_info(session.create_child_run(objective)),
        kind="success",
        failure_prefix="Child Run creation failed",
    )


def _child_prepare(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /child prepare <child-run-id>")
    prepare = getattr(session, "prepare_child_run", None)
    if not callable(prepare):
        return _command_error(
            RuntimeError("Child preparation is unavailable"),
            failure_prefix="Child Run preparation failed",
        )
    return _call(
        lambda: render_child_run_info(prepare(parts[2])),
        kind="success",
        failure_prefix="Child Run preparation failed",
    )


def _child_run(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /child run <child-run-id>")
    run = getattr(session, "run_child_run", None)
    if not callable(run):
        return _command_error(
            RuntimeError("Child execution is unavailable"),
            failure_prefix="Child Run execution failed",
        )
    return _call(
        lambda: render_child_run_info(run(parts[2])),
        kind="success",
        failure_prefix="Child Run execution failed",
    )


def _child_start(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /child start <child-run-id>")
    start = getattr(session, "start_child_run", None)
    if not callable(start):
        return _command_error(
            RuntimeError("Child background supervision is unavailable"),
            failure_prefix="Child Run start failed",
        )
    return _call(
        lambda: render_child_run_info(start(parts[2])),
        kind="success",
        failure_prefix="Child Run start failed",
    )


def _child_list(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) > 4:
        return _usage(
            "Usage: /child list [1-100] [status=queued|admitted|ready|running|cancelling|completed|cancelled|interrupted|failed]"
        )
    limit = 20
    status = None
    for part in parts[2:]:
        if part.isascii() and part.isdigit() and limit == 20:
            limit = int(part)
        elif part.startswith("status=") and status is None:
            status = part.removeprefix("status=")
        else:
            return _usage(
                "Usage: /child list [1-100] [status=queued|admitted|ready|running|cancelling|completed|cancelled|interrupted|failed]"
            )
    if not 1 <= limit <= 100 or (
        status is not None and status not in {item.value for item in ChildRunStatus}
    ):
        return _usage(
            "Usage: /child list [1-100] [status=queued|admitted|ready|running|cancelling|completed|cancelled|interrupted|failed]"
        )
    return _call(
        lambda: _render_child_list(session, limit, status),
        kind="info",
        failure_prefix="Child Run listing failed",
    )


def _render_child_list(session: ReplSession, limit: int, status: str | None) -> str:
    selected = None if status is None else ChildRunStatus(status)
    runs = session.list_child_runs(status=selected)[:limit]
    if not runs:
        return "No durable Child Runs found."
    return "\n".join(render_child_run_summary(run) for run in runs)


def _child_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /child show <child-run-id>")
    return _call(
        lambda: render_child_run_info(session.inspect_child_run(parts[2])),
        kind="info",
        failure_prefix="Child Run inspection failed",
    )


def _child_cancel(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=3)
    if len(parts) != 4:
        return _usage("Usage: /child cancel <child-run-id> <reason>")
    return _call(
        lambda: render_child_run_info(session.cancel_child_run(parts[2], parts[3])),
        kind="success",
        failure_prefix="Child Run cancellation failed",
    )


def _child_wait(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {3, 4}:
        return _usage("Usage: /child wait <child-run-id> [timeout-seconds]")
    try:
        timeout = float(parts[3]) if len(parts) == 4 else 30.0
    except ValueError:
        return _usage("Usage: /child wait <child-run-id> [timeout-seconds]")
    return _call(
        lambda: render_child_run_info(session.wait_child_run(parts[2], timeout)),
        kind="info",
        failure_prefix="Child Run wait failed",
    )


def _child_recover(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) > 3:
        return _usage("Usage: /child recover [child-run-id]")
    return _call(
        lambda: _render_child_recovery(session, parts[2] if len(parts) == 3 else None),
        kind="info",
        failure_prefix="Child Run recovery failed",
    )


def _child_handoff(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /child handoff <child-run-id>")
    return _call(
        lambda: render_child_handoff(session.publish_child_handoff(parts[2])),
        kind="info",
        failure_prefix="Child Run handoff failed",
    )


def _child_deliver(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /child deliver <child-run-id>")
    return _call(
        lambda: render_child_handoff(session.deliver_child_handoff(parts[2])),
        kind="info",
        failure_prefix="Child Run delivery failed",
    )


def _team_create(command: str, session: ReplSession) -> SlashResult:
    name = command.removeprefix("/team create").strip()
    if not name:
        return _usage("Usage: /team create <name>")
    return _call(
        lambda: render_team_info(session.create_team(name)),
        kind="success",
        failure_prefix="Team creation failed",
    )


def _team_list(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) > 4:
        return _usage("Usage: /team list [1-100] [status=open|closed]")
    limit = 20
    status = None
    for part in parts[2:]:
        if part.isascii() and part.isdigit() and limit == 20:
            limit = int(part)
        elif part.startswith("status=") and status is None:
            status = part.removeprefix("status=")
        else:
            return _usage("Usage: /team list [1-100] [status=open|closed]")
    if not 1 <= limit <= 100 or (
        status is not None and status not in {item.value for item in TeamStatus}
    ):
        return _usage("Usage: /team list [1-100] [status=open|closed]")
    return _call(
        lambda: _render_team_list(session, limit, status),
        kind="info",
        failure_prefix="Team listing failed",
    )


def _render_team_list(session: ReplSession, limit: int, status: str | None) -> str:
    selected = None if status is None else TeamStatus(status)
    teams = session.list_teams(status=selected)[:limit]
    if not teams:
        return "No durable Teams found."
    return "\n".join(render_team_summary(team) for team in teams)


def _team_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /team show <team-id>")
    return _call(
        lambda: render_team_info(session.inspect_team(parts[2])),
        kind="info",
        failure_prefix="Team inspection failed",
    )


def _team_close(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /team close <team-id>")
    return _call(
        lambda: render_team_info(session.close_team(parts[2])),
        kind="success",
        failure_prefix="Team close failed",
    )


def _team_member_add(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) < 4:
        if len(parts) == 3:
            return _usage("Usage: /team member add <team-id> <name>")
        return _usage(
            "Usage: /team member add <team-id> <name> [read-only-investigator-v1|isolated-workspace-writer-v1|isolated-coder-v1]"
        )
    roles = {
        "read-only-investigator-v1",
        "isolated-workspace-writer-v1",
        "isolated-coder-v1",
    }
    role = None
    if parts[-1] in roles:
        role = parts.pop()
    elif parts[-1].startswith("role="):
        role = parts.pop().split("=", 1)[1]
    if role is not None and role not in roles:
        return _usage("Unknown Team member role")
    team_id = parts[3]
    name = " ".join(parts[4:])
    if not name:
        return _usage("Usage: /team member add <team-id> <name> [role]")

    def add_member():
        if role is None:
            return session.add_team_member(team_id, name)
        return session.add_team_member(team_id, name, role_contract=role)

    return _call(
        lambda: render_team_member(add_member()),
        kind="success",
        failure_prefix="Team member creation failed",
    )


def _team_worktree(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) < 4:
        return _usage(
            "Usage: /team worktree status|diff|recover <worktree-id> or /team worktree retire <worktree-id> --confirm"
        )
    operation, worktree_id = parts[3], parts[4] if len(parts) >= 5 else ""
    if not worktree_id:
        return _usage(
            "Usage: /team worktree <status|diff|recover|retire> <worktree-id> [--confirm]"
        )
    if operation == "status":
        return _call(
            lambda: render_team_worktree(session.inspect_team_worktree(worktree_id)),
            kind="info",
            failure_prefix="Team worktree inspection failed",
        )
    if operation == "diff":
        return _call(
            lambda: render_team_worktree_diff(session.diff_team_worktree(worktree_id)),
            kind="info",
            failure_prefix="Team worktree diff failed",
        )
    if operation == "recover":
        return _call(
            lambda: render_team_worktree(session.recover_team_worktree(worktree_id)),
            kind="info",
            failure_prefix="Team worktree recovery failed",
        )
    if operation == "retire":
        if "--cancel" in parts:
            return SlashResult(
                handled=True, message="Team worktree retirement cancelled.", kind="info"
            )
        if "--confirm" not in parts:
            return _usage("Retirement requires explicit --confirm (or --cancel).")
        return _call(
            lambda: render_team_worktree(session.retire_team_worktree(worktree_id)),
            kind="success",
            failure_prefix="Team worktree retirement failed",
        )
    suggestion = _suggest_token(operation, ("status", "diff", "recover", "retire"))
    return _usage(f"Unknown Team worktree command: {operation}{_suggestion_line(suggestion)}")


def _team_member_list(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 4:
        return _usage("Usage: /team member list <team-id>")
    return _call(
        lambda: _render_team_members(session, parts[3]),
        kind="info",
        failure_prefix="Team member listing failed",
    )


def _render_team_members(session: ReplSession, team_id: str) -> str:
    members = session.list_team_members(team_id)
    if not members:
        return "No Team members found."
    return "\n".join(render_team_member(member) for member in members)


def _team_member_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team member show <team-id> <member-id>")
    return _call(
        lambda: render_team_member(session.inspect_team_member(parts[3], parts[4])),
        kind="info",
        failure_prefix="Team member inspection failed",
    )


def _team_member_disable(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=5)
    if len(parts) != 6:
        return _usage("Usage: /team member disable <team-id> <member-id> <reason>")
    return _call(
        lambda: render_team_member(session.disable_team_member(parts[3], parts[4], parts[5])),
        kind="success",
        failure_prefix="Team member disable failed",
    )


def _team_member_enable(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team member enable <team-id> <member-id>")
    return _call(
        lambda: render_team_member(session.enable_team_member(parts[3], parts[4])),
        kind="success",
        failure_prefix="Team member enable failed",
    )


def _team_member_leave(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=5)
    if len(parts) != 6:
        return _usage("Usage: /team member leave <team-id> <member-id> <reason>")
    return _call(
        lambda: render_team_member(session.leave_team_member(parts[3], parts[4], parts[5])),
        kind="success",
        failure_prefix="Team member leave failed",
    )


def _team_assignment_create(command: str, session: ReplSession) -> SlashResult:
    parts = command.removeprefix("/team assignment create").strip().split(maxsplit=2)
    if len(parts) != 3:
        return _usage("Usage: /team assignment create <team-id> <member-id> <objective>")
    return _call(
        lambda: render_team_assignment_info(
            session.create_team_assignment(parts[0], parts[1], parts[2])
        ),
        kind="success",
        failure_prefix="Team assignment creation failed",
    )


def _team_assignment_list(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {4, 5}:
        return _usage("Usage: /team assignment list <team-id> [1-100]")
    try:
        limit = int(parts[4]) if len(parts) == 5 else 100
    except ValueError:
        return _usage("Usage: /team assignment list <team-id> [1-100]")
    if not 1 <= limit <= 100:
        return _usage("Usage: /team assignment list <team-id> [1-100]")
    return _call(
        lambda: _render_team_assignment_list(session, parts[3], limit),
        kind="info",
        failure_prefix="Team assignment listing failed",
    )


def _render_team_assignment_list(session: ReplSession, team_id: str, limit: int) -> str:
    assignments = session.list_team_assignments(team_id, limit=limit)
    if not assignments:
        return "No Team assignments found."
    return "\n".join(render_team_assignment_summary(item) for item in assignments)


def _team_assignment_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team assignment show <team-id> <assignment-id>")
    return _call(
        lambda: render_team_assignment_info(session.inspect_team_assignment(parts[3], parts[4])),
        kind="info",
        failure_prefix="Team assignment inspection failed",
    )


def _team_assignment_prepare(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team assignment prepare <team-id> <assignment-id>")
    prepare = getattr(session, "prepare_team_assignment", None)
    if not callable(prepare):
        return _command_error(
            RuntimeError("Team assignment preparation is unavailable"),
            failure_prefix="Team assignment preparation failed",
        )
    return _call(
        lambda: render_team_assignment_info(prepare(parts[3], parts[4])),
        kind="success",
        failure_prefix="Team assignment preparation failed",
    )


def _team_assignment_run(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team assignment run <team-id> <assignment-id>")
    run = getattr(session, "run_team_assignment", None)
    if not callable(run):
        return _command_error(
            RuntimeError("Team assignment execution is unavailable"),
            failure_prefix="Team assignment execution failed",
        )
    return _call(
        lambda: render_team_assignment_info(run(parts[3], parts[4])),
        kind="success",
        failure_prefix="Team assignment execution failed",
    )


def _team_assignment_start(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team assignment start <team-id> <assignment-id>")
    start = getattr(session, "start_team_assignment", None)
    if not callable(start):
        return _command_error(
            RuntimeError("Team assignment background supervision is unavailable"),
            failure_prefix="Team assignment start failed",
        )
    return _call(
        lambda: render_team_assignment_info(start(parts[3], parts[4])),
        kind="success",
        failure_prefix="Team assignment start failed",
    )


def _team_assignment_wait(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {5, 6}:
        return _usage("Usage: /team assignment wait <team-id> <assignment-id> [timeout-seconds]")
    try:
        timeout = float(parts[5]) if len(parts) == 6 else 30.0
    except ValueError:
        return _usage("Usage: /team assignment wait <team-id> <assignment-id> [timeout-seconds]")
    wait = getattr(session, "wait_team_assignment", None)
    if not callable(wait):
        return _command_error(
            RuntimeError("Team assignment wait is unavailable"),
            failure_prefix="Team assignment wait failed",
        )
    return _call(
        lambda: render_team_assignment_info(wait(parts[3], parts[4], timeout)),
        kind="info",
        failure_prefix="Team assignment wait failed",
    )


def _team_assignment_cancel(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=5)
    if len(parts) != 6:
        return _usage("Usage: /team assignment cancel <team-id> <assignment-id> <reason>")
    cancel = getattr(session, "cancel_team_assignment", None)
    if not callable(cancel):
        return _command_error(
            RuntimeError("Team assignment cancellation is unavailable"),
            failure_prefix="Team assignment cancellation failed",
        )
    return _call(
        lambda: render_team_assignment_info(cancel(parts[3], parts[4], parts[5])),
        kind="success",
        failure_prefix="Team assignment cancellation failed",
    )


def _team_assignment_handoff(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team assignment handoff <team-id> <assignment-id>")
    handoff = getattr(session, "publish_team_assignment_handoff", None)
    if not callable(handoff):
        return _command_error(
            RuntimeError("Team assignment handoff is unavailable"),
            failure_prefix="Team assignment handoff failed",
        )
    return _call(
        lambda: render_child_handoff(handoff(parts[3], parts[4])),
        kind="info",
        failure_prefix="Team assignment handoff failed",
    )


def _team_assignment_recover(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {4, 5, 6}:
        return _usage("Usage: /team assignment recover <team-id> [assignment-id] [1-100]")
    assignment_id = parts[4] if len(parts) >= 5 else None
    try:
        limit = int(parts[5]) if len(parts) == 6 else 100
    except ValueError:
        return _usage("Usage: /team assignment recover <team-id> [assignment-id] [1-100]")
    if not 1 <= limit <= 100:
        return _usage("Usage: /team assignment recover <team-id> [assignment-id] [1-100]")

    def render() -> str:
        result = session.recover_team_assignments(parts[3], assignment_id, limit=limit)
        lines = [render_team_assignment_info(item) for item in result.recovered]
        lines.extend(
            f"Recovery {item.outcome}"
            f"{f' {item.assignment_id}' if item.assignment_id else ''}: {item.message}"
            for item in result.diagnostics
        )
        return "\n".join(lines) if lines else "No Team assignments require recovery."

    return _call(render, kind="info", failure_prefix="Team assignment recovery failed")


def _team_schedule_run(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {4, 5, 6}:
        return _usage("Usage: /team schedule run <team-id> [max-assignments] [max-parallel]")
    try:
        max_assignments = int(parts[4]) if len(parts) >= 5 else 32
        max_parallel = int(parts[5]) if len(parts) == 6 else 4
    except ValueError:
        return _usage("Usage: /team schedule run <team-id> [max-assignments] [max-parallel]")
    if not 1 <= max_assignments <= 32 or not 1 <= max_parallel <= 4:
        return _usage("Usage: /team schedule run <team-id> [max-assignments] [max-parallel]")
    return _call(
        lambda: render_team_schedule(
            session.run_team_schedule(
                parts[3], max_assignments=max_assignments, max_parallel=max_parallel
            )
        ),
        kind="success",
        failure_prefix="Team schedule run failed",
    )


def _team_schedule_start(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {4, 5, 6}:
        return _usage("Usage: /team schedule start <team-id> [max-assignments] [max-parallel]")
    try:
        max_assignments = int(parts[4]) if len(parts) >= 5 else 32
        max_parallel = int(parts[5]) if len(parts) == 6 else 4
    except ValueError:
        return _usage("Usage: /team schedule start <team-id> [max-assignments] [max-parallel]")
    if not 1 <= max_assignments <= 32 or not 1 <= max_parallel <= 4:
        return _usage("Usage: /team schedule start <team-id> [max-assignments] [max-parallel]")
    return _call(
        lambda: render_team_schedule(
            session.start_team_schedule(
                parts[3], max_assignments=max_assignments, max_parallel=max_parallel
            )
        ),
        kind="success",
        failure_prefix="Team schedule start failed",
    )


def _team_schedule_status(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {4, 5}:
        return _usage("Usage: /team schedule status <team-id> [schedule-run-id]")
    return _call(
        lambda: (
            render_team_schedule(state)
            if (
                state := session.team_schedule_status(
                    parts[3], parts[4] if len(parts) == 5 else None
                )
            )
            is not None
            else "No Team schedule found."
        ),
        kind="info",
        failure_prefix="Team schedule status failed",
    )


def _team_schedule_wait(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {5, 6}:
        return _usage("Usage: /team schedule wait <team-id> <schedule-run-id> [0-30]")
    try:
        timeout = float(parts[5]) if len(parts) == 6 else 30.0
    except ValueError:
        return _usage("Usage: /team schedule wait <team-id> <schedule-run-id> [0-30]")
    if not 0 <= timeout <= 30:
        return _usage("Usage: /team schedule wait <team-id> <schedule-run-id> [0-30]")
    return _call(
        lambda: (
            render_team_schedule(notification.state)
            if (notification := session.wait_team_schedule(parts[3], parts[4], timeout))
            and notification.state is not None
            else "No terminal Team schedule notification observed."
        ),
        kind="info",
        failure_prefix="Team schedule wait failed",
    )


def _team_schedule_cancel(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=5)
    if len(parts) != 6:
        return _usage("Usage: /team schedule cancel <team-id> <schedule-run-id> <reason>")
    return _call(
        lambda: render_team_schedule(session.cancel_team_schedule(parts[3], parts[4], parts[5])),
        kind="success",
        failure_prefix="Team schedule cancellation failed",
    )


def _team_schedule_recover(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {4, 5}:
        return _usage("Usage: /team schedule recover <team-id> [schedule-run-id]")
    return _call(
        lambda: render_team_schedule(
            session.recover_team_schedule(parts[3], parts[4] if len(parts) == 5 else None)
        ),
        kind="info",
        failure_prefix="Team schedule recovery failed",
    )


def _team_message_send(command: str, session: ReplSession) -> SlashResult:
    parts = command.removeprefix("/team message send").strip().split(maxsplit=2)
    if len(parts) != 3:
        return _usage("Usage: /team message send <team-id> <member-id> <body>")
    return _call(
        lambda: render_team_message(session.send_team_message(parts[0], parts[1], parts[2])),
        kind="success",
        failure_prefix="Team message send failed",
    )


def _team_message_list(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) < 4 or len(parts) > 6:
        return _usage("Usage: /team message list <team-id> [1-100] [status=<status>]")
    limit = 100
    member_id = None
    status = None
    for part in parts[4:]:
        if part.isascii() and part.isdigit() and limit == 100:
            limit = int(part)
        elif part.startswith("member=") and member_id is None:
            member_id = part.removeprefix("member=")
        elif part.startswith("status=") and status is None:
            status = part.removeprefix("status=")
        else:
            return _usage("Usage: /team message list <team-id> [1-100] [status=<status>]")
    if not 1 <= limit <= 100 or (
        status is not None and status not in {item.value for item in TeamMessageStatus}
    ):
        return _usage("Usage: /team message list <team-id> [1-100] [status=<status>]")
    selected = None if status is None else TeamMessageStatus(status)
    return _call(
        lambda: _render_team_messages(session, parts[3], limit, member_id, selected),
        kind="info",
        failure_prefix="Team message listing failed",
    )


def _render_team_messages(
    session: ReplSession, team_id: str, limit: int, member_id: str | None, status
) -> str:
    result = session.list_team_messages(team_id, limit=limit, member_id=member_id, status=status)
    if not result.messages:
        return "No Team messages found."
    return "\n".join(render_team_message_summary(message) for message in result.messages)


def _team_message_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team message show <team-id> <message-id>")
    return _call(
        lambda: render_team_message(session.inspect_team_message(parts[3], parts[4])),
        kind="info",
        failure_prefix="Team message inspection failed",
    )


def _team_message_read(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team message read <team-id> <message-id>")
    return _call(
        lambda: render_team_message(session.read_team_message(parts[3], parts[4])),
        kind="success",
        failure_prefix="Team message read failed",
    )


def _team_message_cancel(command: str, session: ReplSession) -> SlashResult:
    parts = command.removeprefix("/team message cancel").strip().split(maxsplit=2)
    if len(parts) != 3:
        return _usage("Usage: /team message cancel <team-id> <message-id> <reason>")
    return _call(
        lambda: render_team_message(session.cancel_team_message(parts[0], parts[1], parts[2])),
        kind="success",
        failure_prefix="Team message cancellation failed",
    )


def _team_work_create(command: str, session: ReplSession) -> SlashResult:
    parts = command.removeprefix("/team work create").strip().split(maxsplit=2)
    if len(parts) != 3:
        return _usage("Usage: /team work create <team-id> <title> <objective> [depends=<id>,...]")
    dependency_ids: tuple[str, ...] = ()
    objective = parts[2]
    if " depends=" in objective:
        objective, dependency_text = objective.rsplit(" depends=", 1)
        dependency_ids = tuple(item for item in dependency_text.split(",") if item)
        if not objective or not dependency_ids:
            return _usage(
                "Usage: /team work create <team-id> <title> <objective> [depends=<id>,...]"
            )
    return _call(
        lambda: render_team_work_item(
            session.create_team_work(parts[0], parts[1], objective, dependency_ids)
        ),
        kind="success",
        failure_prefix="Team work creation failed",
    )


def _team_work_list(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) < 4 or len(parts) > 6:
        return _usage("Usage: /team work list <team-id> [1-100] [status=<status>]")
    limit = 100
    status = None
    for part in parts[4:]:
        if part.isascii() and part.isdigit() and limit == 100:
            limit = int(part)
        elif part.startswith("status=") and status is None:
            status = part.removeprefix("status=")
        else:
            return _usage("Usage: /team work list <team-id> [1-100] [status=<status>]")
    if not 1 <= limit <= 100 or (
        status is not None and status not in {item.value for item in TeamWorkStatus}
    ):
        return _usage("Usage: /team work list <team-id> [1-100] [status=<status>]")
    selected = None if status is None else TeamWorkStatus(status)
    return _call(
        lambda: _render_team_work_list(session, parts[3], limit, selected),
        kind="info",
        failure_prefix="Team work listing failed",
    )


def _render_team_work_list(session: ReplSession, team_id: str, limit: int, status) -> str:
    result = session.list_team_work(team_id, limit=limit, status=status)
    if not result.items:
        return "No Team work items found."
    return "\n".join(render_team_work_summary(item) for item in result.items)


def _team_work_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 5:
        return _usage("Usage: /team work show <team-id> <work-item-id>")
    return _call(
        lambda: render_team_work_item(session.inspect_team_work(parts[3], parts[4])),
        kind="info",
        failure_prefix="Team work inspection failed",
    )


def _team_work_cancel(command: str, session: ReplSession) -> SlashResult:
    parts = command.removeprefix("/team work cancel").strip().split(maxsplit=2)
    if len(parts) != 3:
        return _usage("Usage: /team work cancel <team-id> <work-item-id> <reason>")
    return _call(
        lambda: render_team_work_item(session.cancel_team_work(parts[0], parts[1], parts[2])),
        kind="success",
        failure_prefix="Team work cancellation failed",
    )


def _team_work_assign(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 6:
        return _usage("Usage: /team work assign <team-id> <work-item-id> <member-id>")
    return _call(
        lambda: render_team_assignment_info(session.assign_team_work(parts[3], parts[4], parts[5])),
        kind="success",
        failure_prefix="Team work assignment failed",
    )


def _team_work_complete(command: str, session: ReplSession) -> SlashResult:
    parts = command.removeprefix("/team work complete").strip().split(maxsplit=2)
    if len(parts) != 3:
        return _usage("Usage: /team work complete <team-id> <work-item-id> <evidence>")
    return _call(
        lambda: render_team_work_item(session.complete_team_work(parts[0], parts[1], parts[2])),
        kind="success",
        failure_prefix="Team work completion failed",
    )


def _team_work_release(command: str, session: ReplSession) -> SlashResult:
    parts = command.removeprefix("/team work release").strip().split(maxsplit=2)
    if len(parts) != 3:
        return _usage("Usage: /team work release <team-id> <work-item-id> <reason>")
    return _call(
        lambda: render_team_work_item(session.release_team_work(parts[0], parts[1], parts[2])),
        kind="success",
        failure_prefix="Team work release failed",
    )


def _render_child_recovery(session: ReplSession, child_run_id: str | None) -> str:
    result = session.recover_child_runs(child_run_id)
    lines = [render_child_run_info(info) for info in result.recovered]
    lines.extend(
        f"Recovery {item.outcome}{f' {item.child_run_id}' if item.child_run_id else ''}: {item.message}"
        for item in result.diagnostics
    )
    return "\n".join(lines) if lines else "No abandoned Child Runs found."


def _task_proposals(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) not in {2, 3}:
        return _usage("Usage: /task proposals [pending|accepted|rejected|all]")
    status = parts[2] if len(parts) == 3 else "pending"
    if status not in {"pending", "accepted", "rejected", "all"}:
        return _usage("Usage: /task proposals [pending|accepted|rejected|all]")

    def render() -> str:
        proposals = session.list_task_admissions()
        if status != "all":
            proposals = tuple(item for item in proposals if item.status == status)
        if not proposals:
            return f"No {status} Task admission proposals in the current Session."
        return "\n".join(render_task_admission_summary(item) for item in proposals)

    return _call(render, kind="info", failure_prefix="Task admission listing failed")


def _task_proposal_show(command: str, session: ReplSession) -> SlashResult:
    admission_id = _task_admission_id_from_command(command, "show")
    if admission_id is None:
        return _usage("Usage: /task proposal show <admission-id>")
    return _call(
        lambda: render_task_admission_info(session.inspect_task_admission(admission_id)),
        kind="info",
        failure_prefix="Task admission inspection failed",
    )


def _task_proposal_accept(command: str, session: ReplSession) -> SlashResult:
    usage = (
        "Usage: /task proposal accept <admission-id> [<config-json>] | "
        "/task proposal accept <admission-id> confirm <sha256> [<config-json>]"
    )
    try:
        parts = shlex.split(command)
    except ValueError:
        return _usage(usage)
    if len(parts) not in {4, 5, 6, 7} or parts[:3] != ["/task", "proposal", "accept"]:
        return _usage(usage)
    try:
        admission_id = canonical_task_admission_id(parts[3])
    except ValueError:
        return _usage(usage)
    confirming = len(parts) >= 5 and parts[4] == "confirm"
    if confirming:
        if len(parts) not in {6, 7} or not _is_sha256(parts[5]):
            return _usage(usage)
        confirmation_sha256 = parts[5]
        raw_configuration = parts[6] if len(parts) == 7 else None
    else:
        if len(parts) not in {4, 5}:
            return _usage(usage)
        confirmation_sha256 = None
        raw_configuration = parts[4] if len(parts) == 5 else None
    try:
        configuration = _task_admission_configuration(raw_configuration)
    except Exception as error:
        return _command_error(error, failure_prefix="Task admission configuration invalid")
    if confirming:
        return _call(
            lambda: _render_accepted_task_admission(
                admission_id,
                session.accept_task_admission(
                    admission_id,
                    configuration,
                    confirmation_sha256=confirmation_sha256,
                ),
            ),
            kind="success",
            failure_prefix="Task admission acceptance failed",
        )

    def preview() -> str:
        candidate = session.preview_task_admission_acceptance(admission_id, configuration)
        command_suffix = ""
        if configuration != TaskAdmissionConfiguration():
            canonical = json.dumps(
                configuration.as_mapping(),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            command_suffix = f" {shlex.quote(canonical)}"
        confirmation_command = (
            f"/task proposal accept {admission_id} confirm "
            f"{candidate.confirmation_sha256}{command_suffix}"
        )
        return render_task_admission_acceptance_preview(candidate, confirmation_command)

    return _call(preview, kind="info", failure_prefix="Task admission preview failed")


def _task_admission_configuration(raw: str | None) -> TaskAdmissionConfiguration:
    if raw is None:
        return TaskAdmissionConfiguration()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"configuration is not valid JSON: {error.msg}") from None
    return TaskAdmissionConfiguration.from_mapping(value)


def _render_accepted_task_admission(admission_id: str, task) -> str:
    return (
        "Accepted Task admission and created durable Task:\n"
        + render_task_info(task)
        + "\nNext decision: /task next "
        + task.task_id
        + "\nStart bounded foreground driving: /task proposal drive "
        + admission_id
    )


def _is_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and value.isascii()
        and all(character in "0123456789abcdef" for character in value)
    )


def _task_proposal_reject(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=4)
    if len(parts) not in {4, 5} or parts[:3] != ["/task", "proposal", "reject"]:
        return _usage("Usage: /task proposal reject <admission-id> [reason]")
    try:
        admission_id = canonical_task_admission_id(parts[3])
    except ValueError:
        return _usage("Usage: /task proposal reject <admission-id> [reason]")
    reason = parts[4].strip() if len(parts) == 5 else None
    if reason == "":
        return _usage("Usage: /task proposal reject <admission-id> [reason]")
    return _call(
        lambda: (
            "Rejected Task admission:\n"
            + render_task_admission_info(session.reject_task_admission(admission_id, reason))
        ),
        kind="success",
        failure_prefix="Task admission rejection failed",
    )


def _task_proposal_drive(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    usage = "Usage: /task proposal drive <admission-id> [1-16]"
    if len(parts) not in {4, 5} or parts[:3] != ["/task", "proposal", "drive"]:
        return _usage(usage)
    try:
        admission_id = canonical_task_admission_id(parts[3])
    except ValueError:
        return _usage(usage)
    limit = 16
    if len(parts) == 5:
        if not parts[4].isascii() or not parts[4].isdigit() or not 1 <= int(parts[4]) <= 16:
            return _usage(usage)
        limit = int(parts[4])
    try:
        task = session.accepted_task_for_admission(admission_id)
    except Exception as error:
        return _command_error(error, failure_prefix="Task admission drive failed")
    return SlashResult(
        handled=True,
        task_request=TaskTurnRequest("drive", task.task_id, max_stages=limit),
    )


def _task_admission_id_from_command(command: str, operation: str) -> str | None:
    parts = command.split()
    if len(parts) != 4 or parts[:3] != ["/task", "proposal", operation]:
        return None
    try:
        return canonical_task_admission_id(parts[3])
    except ValueError:
        return None


def _task_list(command: str, session: ReplSession) -> SlashResult:
    arguments = command.split()[2:]
    limit = 20
    limit_seen = False
    status: str | None = None
    archived: bool | None = None
    name_query: str | None = None
    for argument in arguments:
        if argument.isascii() and argument.isdigit() and not limit_seen:
            limit = int(argument)
            limit_seen = True
        elif argument.startswith("status=") and status is None:
            status = argument.removeprefix("status=")
        elif argument in {"active", "archived"} and archived is None:
            archived = argument == "archived"
        elif argument.startswith("name=") and name_query is None:
            name_query = argument.removeprefix("name=").casefold()
        else:
            return _task_list_usage()
    if not 1 <= limit <= 100 or (
        status is not None and status not in {item.value for item in TaskStatus}
    ):
        return _task_list_usage()
    if name_query == "":
        return _task_list_usage()

    def render() -> str:
        tasks = session.list_tasks()
        if status is not None:
            tasks = tuple(task for task in tasks if task.status.value == status)
        if archived is not None:
            tasks = tuple(task for task in tasks if task.archived is archived)
        if name_query is not None:
            tasks = tuple(task for task in tasks if name_query in task.name.casefold())
        if not tasks:
            return "No durable Tasks found."
        return "\n".join(render_task_summary(info) for info in tasks[:limit])

    return _call(render, kind="info", failure_prefix="Task listing failed")


def _task_list_usage() -> SlashResult:
    return _usage("Usage: /task list [1-100] [status=<status>] [active|archived] [name=<text>]")


def _task_show(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /task show <task-id>")
    try:
        task_id = canonical_task_id(parts[2])
    except TaskRecordError:
        return _usage("Usage: /task show <task-id>")
    return _call(
        lambda: render_task_info(session.inspect_task(task_id)),
        kind="info",
        failure_prefix="Task inspection failed",
    )


def _task_continue(command: str) -> SlashResult:
    parts = command.split(maxsplit=3)
    if len(parts) != 4:
        return _usage("Usage: /task continue <task-id> <stage-objective>")
    task_id = _task_id_or_none(parts[2])
    if task_id is None or not parts[3].strip():
        return _usage("Usage: /task continue <task-id> <stage-objective>")
    return SlashResult(
        handled=True,
        task_request=TaskTurnRequest("continue", task_id, parts[3]),
    )


def _task_plan(command: str) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /task plan <task-id> | /task plan accept <task-id>")
    task_id = _task_id_or_none(parts[2])
    if task_id is None:
        return _usage("Usage: /task plan <task-id>")
    return SlashResult(handled=True, task_request=TaskTurnRequest("plan", task_id))


def _task_plan_accept(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 4:
        return _usage("Usage: /task plan accept <task-id>")
    task_id = _task_id_or_none(parts[3])
    if task_id is None:
        return _usage("Usage: /task plan accept <task-id>")
    return _call(
        lambda: (
            "Accepted latest Task plan:\n" + render_task_info(session.accept_task_plan(task_id))
        ),
        kind="success",
        failure_prefix="Task plan acceptance failed",
    )


def _task_run(command: str) -> SlashResult:
    parts = command.split()
    if len(parts) not in {3, 4}:
        return _usage("Usage: /task run <task-id> [1-16]")
    task_id = _task_id_or_none(parts[2])
    if task_id is None:
        return _usage("Usage: /task run <task-id> [1-16]")
    limit = 16
    if len(parts) == 4:
        if not parts[3].isascii() or not parts[3].isdigit() or not 1 <= int(parts[3]) <= 16:
            return _usage("Usage: /task run <task-id> [1-16]")
        limit = int(parts[3])
    return SlashResult(handled=True, task_request=TaskTurnRequest("run", task_id, max_stages=limit))


def _task_turn_single(command: str, operation: str) -> SlashResult:
    task_id = _single_task_id(command, operation)
    if task_id is None:
        return _usage(f"Usage: /task {operation} <task-id>")
    return SlashResult(handled=True, task_request=TaskTurnRequest(operation, task_id))


def _task_correct(command: str) -> SlashResult:
    parts = command.split(maxsplit=3)
    if len(parts) not in {3, 4} or (task_id := _task_id_or_none(parts[2])) is None:
        return _usage("Usage: /task correct <task-id> [stage-objective]")
    objective = parts[3].strip() if len(parts) == 4 else None
    if objective == "":
        return _usage("Usage: /task correct <task-id> [stage-objective]")
    return SlashResult(
        handled=True,
        task_request=TaskTurnRequest("correct", task_id, objective),
    )


def _task_drive(command: str) -> SlashResult:
    parts = command.split()
    if len(parts) not in {3, 4} or (task_id := _task_id_or_none(parts[2])) is None:
        return _usage("Usage: /task drive <task-id> [1-16]")
    limit = 16
    if len(parts) == 4:
        if not parts[3].isascii() or not parts[3].isdigit() or not 1 <= int(parts[3]) <= 16:
            return _usage("Usage: /task drive <task-id> [1-16]")
        limit = int(parts[3])
    return SlashResult(
        handled=True,
        task_request=TaskTurnRequest("drive", task_id, max_stages=limit),
    )


def _task_next(command: str, session: ReplSession) -> SlashResult:
    task_id = _single_task_id(command, "next")
    if task_id is None:
        return _usage("Usage: /task next <task-id>")
    return _call(
        lambda: render_task_next_action(session.preview_task_next(task_id)),
        kind="info",
        failure_prefix="Task next-decision preview failed",
    )


def _task_checkpoint(command: str, session: ReplSession) -> SlashResult:
    task_id = _single_task_id(command, "checkpoint")
    if task_id is None:
        return _usage("Usage: /task checkpoint <task-id>")
    return _call(
        lambda: (
            "Created Task context checkpoint:\n"
            + render_task_info(session.checkpoint_task(task_id))
        ),
        kind="success",
        failure_prefix="Task checkpoint failed",
    )


def _task_pause(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=3)
    if len(parts) not in {3, 4} or (task_id := _task_id_or_none(parts[2])) is None:
        return _usage("Usage: /task pause <task-id> [reason]")
    reason = parts[3].strip() if len(parts) == 4 else None
    return _call(
        lambda: (
            "Paused Task foreground driver:\n"
            + render_task_info(session.set_task_driver_paused(task_id, True, reason))
        ),
        kind="success",
        failure_prefix="Task pause failed",
    )


def _task_resume(command: str, session: ReplSession) -> SlashResult:
    task_id = _single_task_id(command, "resume")
    if task_id is None:
        return _usage("Usage: /task resume <task-id>")
    return _call(
        lambda: (
            "Resumed Task foreground driver:\n"
            + render_task_info(session.set_task_driver_paused(task_id, False))
        ),
        kind="success",
        failure_prefix="Task resume failed",
    )


def _task_recover(command: str, session: ReplSession) -> SlashResult:
    task_id = _single_task_id(command, "recover")
    if task_id is None:
        return _usage("Usage: /task recover <task-id>")
    return _call(
        lambda: (
            "Recovered interrupted Task Stage:\n" + render_task_info(session.recover_task(task_id))
        ),
        kind="success",
        failure_prefix="Task recovery failed",
    )


def _task_verify(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=4)
    if (
        len(parts) != 5
        or (task_id := _task_id_or_none(parts[2])) is None
        or not _positive_ascii_integer(parts[3])
        or not parts[4].strip()
    ):
        return _usage("Usage: /task verify <task-id> <criterion-number> <evidence>")
    return _call(
        lambda: (
            "Verified Task acceptance criterion:\n"
            + render_task_info(session.verify_task_acceptance(task_id, int(parts[3]), parts[4]))
        ),
        kind="success",
        failure_prefix="Task acceptance verification failed",
    )


def _task_verify_host(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 4 or (task_id := _task_id_or_none(parts[3])) is None:
        return _usage("Usage: /task verify host <task-id>")
    return _call(
        lambda: render_task_verification_result(session.verify_task_host(task_id)),
        kind="success",
        failure_prefix="Task Host verification failed",
    )


def _task_review(command: str, session: ReplSession) -> SlashResult:
    task_id = _single_task_id(command, "review")
    if task_id is None:
        return _usage("Usage: /task review <task-id>")
    return _call(
        lambda: render_task_verification_result(session.review_task_acceptance(task_id)),
        kind="success",
        failure_prefix="Task independent review failed",
    )


def _task_simple_mutation(command: str, session: ReplSession, operation: str) -> SlashResult:
    task_id = _single_task_id(command, operation)
    if task_id is None:
        return _usage(f"Usage: /task {operation} <task-id>")
    return _call(
        lambda: "Completed durable Task:\n" + render_task_info(session.complete_task(task_id)),
        kind="success",
        failure_prefix="Task completion failed",
    )


def _task_reasoned_terminal(command: str, session: ReplSession, operation: str) -> SlashResult:
    parts = command.split(maxsplit=3)
    if len(parts) != 4 or (task_id := _task_id_or_none(parts[2])) is None or not parts[3].strip():
        return _usage(f"Usage: /task {operation} <task-id> <reason>")
    method = session.cancel_task if operation == "cancel" else session.fail_task
    label = "cancelled" if operation == "cancel" else "failed"
    return _call(
        lambda: f"Task {label}:\n" + render_task_info(method(task_id, parts[3])),
        kind="success",
        failure_prefix=f"Task {operation} failed",
    )


def _task_rename(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=3)
    if len(parts) != 4 or (task_id := _task_id_or_none(parts[2])) is None or not parts[3].strip():
        return _usage("Usage: /task rename <task-id> <name>")
    return _call(
        lambda: (
            "Renamed durable Task:\n" + render_task_info(session.rename_task(task_id, parts[3]))
        ),
        kind="success",
        failure_prefix="Task rename failed",
    )


def _task_archive(command: str, session: ReplSession, archived: bool) -> SlashResult:
    operation = "archive" if archived else "unarchive"
    task_id = _single_task_id(command, operation)
    if task_id is None:
        return _usage(f"Usage: /task {operation} <task-id>")
    return _call(
        lambda: (
            ("Archived" if archived else "Unarchived")
            + " durable Task:\n"
            + render_task_info(session.set_task_archived(task_id, archived))
        ),
        kind="success",
        failure_prefix=f"Task {operation} failed",
    )


def _task_timeline(command: str, session: ReplSession) -> SlashResult:
    task_id = _single_task_id(command, "timeline")
    if task_id is None:
        return _usage("Usage: /task timeline <task-id>")
    return _call(
        lambda: render_task_timeline(session.inspect_task(task_id)),
        kind="info",
        failure_prefix="Task timeline failed",
    )


def _task_derive(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=3)
    if len(parts) != 4 or (task_id := _task_id_or_none(parts[2])) is None or not parts[3].strip():
        return _usage("Usage: /task derive <parent-task-id> <objective>")
    return _call(
        lambda: (
            "Derived durable Task:\n" + render_task_info(session.derive_task(task_id, parts[3]))
        ),
        kind="success",
        failure_prefix="Task derivation failed",
    )


def _single_task_id(command: str, operation: str) -> str | None:
    parts = command.split()
    if len(parts) != 3 or parts[:2] != ["/task", operation]:
        return None
    return _task_id_or_none(parts[2])


def _task_id_or_none(value: str) -> str | None:
    try:
        return canonical_task_id(value)
    except TaskRecordError:
        return None


def _session_list(command: str, session: ReplSession) -> SlashResult:
    filters = _parse_session_filters(
        command.split()[2:],
        default_count=DEFAULT_SESSION_LIST_COUNT,
        maximum_count=MAX_SESSION_LIST_COUNT,
    )
    if filters is None:
        return _session_list_usage()

    def render() -> str:
        sessions = session.list_sessions()
        if not sessions:
            return "No durable sessions found."
        sessions = _apply_session_filters(sessions, filters)
        total = len(sessions)
        sessions = sessions[: filters.count]
        if not sessions:
            return "No durable sessions match the selected filters."
        current_id = session.session_info().session_id
        latest_id = session.latest_session_info().session_id
        body = "\n".join(
            render_session_summary(
                info,
                current_session_id=current_id,
                latest_session_id=latest_id,
            )
            for info in sessions
        )
        if len(sessions) < total:
            return f"Showing {len(sessions)} most recent of {total} matching Sessions.\n{body}"
        return body

    return _call(render, kind="info")


def _session_list_usage() -> SlashResult:
    return _usage(
        f"Usage: /session list [1-{MAX_SESSION_LIST_COUNT}] [open|closed] "
        "[active|archived] [pinned|unpinned] [model=<name>] [name=<text>]"
    )


def _new_session(session: ReplSession) -> SlashResult:
    return _call(
        lambda: f"Started {session.new_session().name!r}; runtime provider unchanged.",
        kind="success",
        failure_prefix="Session creation failed",
    )


def _rename_session(command: str, session: ReplSession) -> SlashResult:
    argument = command.removeprefix("/session rename").strip()
    if not argument:
        return _usage("Usage: /session rename <name> | /session rename --auto")

    def rename() -> str:
        info = session.rename_session(None if argument == "--auto" else argument)
        return f"Session name: {info.name} ({info.name_source.value})"

    return _call(rename, kind="success", failure_prefix="Session rename failed")


def _archive_session(session: ReplSession, *, archived: bool) -> SlashResult:
    def change() -> str:
        before = session.session_info()
        info = session.set_session_archived(archived)
        state = "archived" if archived else "active"
        if before.archived == archived:
            return f"Session is already {state}: {info.name}"
        return (
            f"Session marked {state}: {info.name}. History, runtime, latest, and resume "
            "identity are unchanged."
        )

    operation = "archive" if archived else "unarchive"
    return _call(
        change,
        kind="success",
        failure_prefix=f"Session {operation} failed",
    )


def _pin_session(session: ReplSession, *, pinned: bool) -> SlashResult:
    def change() -> str:
        before = session.session_info()
        info = session.set_session_pinned(pinned)
        state = "pinned" if pinned else "unpinned"
        if before.pinned == pinned:
            return f"Session is already {state}: {info.name}"
        return (
            f"Session marked {state}: {info.name}. History, runtime, latest, and resume "
            "identity are unchanged."
        )

    operation = "pin" if pinned else "unpin"
    return _call(
        change,
        kind="success",
        failure_prefix=f"Session {operation} failed",
    )


def _session_switch(
    command: str,
    session: ReplSession,
    catalog: SessionSwitchCatalog | None,
) -> SlashResult:
    if catalog is None:
        return _command_error(
            SessionStoreError("Session switch catalog is unavailable"),
            failure_prefix="Session switch failed",
        )
    parts = command.split()
    if len(parts) == 3 and parts[2].isascii() and parts[2].isdigit():
        number = int(parts[2])
        selector = catalog.consume(number)
        if selector is None:
            return SlashResult(
                handled=True,
                message=(
                    "Session picker entry is unavailable. Run /session switch to build a fresh "
                    "numbered snapshot."
                ),
                kind="warning",
            )
        return _resume_selector(
            selector,
            session,
            retry_command="Run /session switch again before retrying a numbered selection.",
        )
    if len(parts) == 2:
        arguments: list[str] = []
    elif len(parts) >= 3 and parts[2] == "list":
        arguments = parts[3:]
    else:
        return _session_switch_usage()
    catalog.clear()
    filters = _parse_session_filters(
        arguments,
        default_count=DEFAULT_SESSION_SWITCH_COUNT,
        maximum_count=MAX_SESSION_SWITCH_COUNT,
    )
    if filters is None:
        return _session_switch_usage()

    def build() -> str:
        current_id = session.session_info().session_id
        sessions = tuple(
            info
            for info in _apply_session_filters(session.list_sessions(), filters)
            if info.session_id != current_id
        )
        total = len(sessions)
        sessions = sessions[: filters.count]
        if not sessions:
            catalog.clear()
            return "No other durable Sessions match the picker filters."
        catalog.replace(tuple(info.session_id for info in sessions))
        latest_id = session.latest_session_info().session_id
        body = "\n".join(
            f"{index}. "
            + render_session_summary(
                info,
                current_session_id=current_id,
                latest_session_id=latest_id,
            )
            for index, info in enumerate(sessions, start=1)
        )
        prefix = (
            f"Session picker snapshot: {len(sessions)} of {total} matching Sessions.\n"
            if len(sessions) < total
            else f"Session picker snapshot: {len(sessions)} matching Sessions.\n"
        )
        return f"{prefix}{body}\nSelect once with /session switch <number>."

    return _call(build, kind="info", failure_prefix="Session switch listing failed")


def _session_switch_usage() -> SlashResult:
    return _usage(
        f"Usage: /session switch | /session switch <number> | /session switch list "
        f"[1-{MAX_SESSION_SWITCH_COUNT}] [open|closed] [active|archived] "
        "[pinned|unpinned] [model=<name>] [name=<text>]"
    )


def _valid_session_name_filter(value: str) -> bool:
    if not value or len(value) > 80 or len(value.encode("utf-8")) > 256:
        return False
    return all(character.isprintable() and not character.isspace() for character in value)


def _resume(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 2:
        return _usage("Usage: /resume <latest|session-id>")
    return _resume_selector(
        parts[1],
        session,
        retry_command=f"Retry /resume {parts[1]}.",
    )


def _resume_selector(
    selector: str,
    session: ReplSession,
    *,
    retry_command: str,
) -> SlashResult:
    try:
        message, kind = render_session_resume(session.switch_session(selector))
        return SlashResult(handled=True, message=message, kind=kind)
    except SessionResumeContextError as error:
        return SlashResult(
            handled=True,
            message=render_resume_rejection(error.report),
            kind="error",
        )
    except SessionResumeConflictError as error:
        return SlashResult(
            handled=True,
            message=(
                f"Session resume was not committed: {error}. Current Session and runtime "
                f"are unchanged. {retry_command}"
            ),
            kind="warning",
        )
    except SessionResumeCommitError as error:
        return SlashResult(
            handled=True,
            message=(
                f"Session resume commit failed at {error.stage.value}: {error}. "
                "Inspect the target transcript before retrying."
            ),
            kind="error",
        )
    except Exception as error:
        return _command_error(error, failure_prefix="Session resume failed")


def _provider_list(session: ReplSession) -> SlashResult:
    def render() -> str:
        profiles = session.list_profiles()
        if not profiles:
            return "No provider profiles configured."
        return "\n".join(
            f"{profile.name}: {profile.provider_id}/{profile.model}" for profile in profiles
        )

    return _call(render, kind="info")


def _provider_use(command: str, session: ReplSession) -> SlashResult:
    parts = command.split()
    if len(parts) != 3:
        return _usage("Usage: /provider use <name>")
    try:
        result = session.use_profile(parts[2], scope="project")
        message, kind = render_runtime_switch(
            f"Using provider profile {result.status.profile} for this workspace",
            result.fit_report,
            suffix="active workspace selection updated",
        )
        return SlashResult(handled=True, message=message, kind=kind)
    except RuntimeSwitchContextError as error:
        return SlashResult(
            handled=True,
            message=render_switch_rejection(error.report),
            kind="error",
        )
    except RuntimeSwitchAuditError as error:
        return SlashResult(
            handled=True,
            message=(
                "Runtime changed, but Session audit persistence failed. "
                f"Effective profile: {error.result.status.profile or '<direct>'}."
            ),
            kind="error",
        )
    except Exception as error:
        return _command_error(error, failure_prefix="Provider switch failed")


def _model(command: str, session: ReplSession) -> SlashResult:
    parts = command.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        return _usage("Usage: /model <model>")
    model = parts[1].strip()
    try:
        result = session.set_model(model)
        message, kind = render_runtime_switch(
            f"Runtime model changed to {result.status.selected_model}",
            result.fit_report,
            suffix="profile was not modified",
        )
        return SlashResult(handled=True, message=message, kind=kind)
    except RuntimeSwitchContextError as error:
        return SlashResult(
            handled=True,
            message=render_switch_rejection(error.report),
            kind="error",
        )
    except RuntimeSwitchAuditError as error:
        return SlashResult(
            handled=True,
            message=(
                "Runtime changed, but Session audit persistence failed. "
                f"Effective model: {error.result.status.selected_model}."
            ),
            kind="error",
        )
    except Exception as error:
        return _command_error(error, failure_prefix="Model switch failed")


def _command_error(error: Exception, *, failure_prefix: str) -> SlashResult:
    if isinstance(error, ProviderAdapterError):
        message = render_provider_adapter_error(error, prefix=failure_prefix)
    elif isinstance(
        error,
        (
            CompactionError,
            GitObservationError,
            ProviderProfileError,
            RuntimeProviderStateError,
            RuntimeRouteError,
            SessionStoreError,
        ),
    ):
        message = f"{failure_prefix}: {error}"
    else:
        message = f"{failure_prefix}: unexpected internal error"
    guidance = command_failure_guidance(error)
    if guidance is not None:
        message = f"{message}\n{guidance}"
    return SlashResult(
        handled=True,
        message=message,
        kind="error",
    )


def _call(
    operation,
    *,
    kind: MessageKind = "plain",
    failure_prefix: str = "Command failed",
) -> SlashResult:
    try:
        return SlashResult(handled=True, message=operation(), kind=kind)
    except Exception as error:
        return _command_error(error, failure_prefix=failure_prefix)


def _usage(message: str) -> SlashResult:
    return SlashResult(handled=True, message=message, kind="warning")


def _info(message: str) -> SlashResult:
    return SlashResult(handled=True, message=message, kind="info")
