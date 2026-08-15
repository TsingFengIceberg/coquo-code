# ADR 0143: Parent-Owned Team Control Approval and Reply Evidence

## Status

Accepted for B7, B8.1, and B8.2. The controls are model-visible only in the
ordinary parent ToolSet after the atomic contract migration described below.

## Decision

Team control requests are a fixed, closed set of eleven parent-only contracts:
`team_create`, `team_add_member`, `team_status`, `team_message_send`,
`team_message_show`, `team_message_read`, `team_work_create`,
`team_schedule_start`, `team_schedule_wait`, `team_work_review`, and `team_close`.
Their schemas reject unknown fields, non-canonical UUIDs, invalid conditional review
arguments, duplicate dependencies, and values outside the existing Team bounds. The
definitions are exposed through the active Registry only for ordinary parent turns.
Children, Team Children, Task Stages, compact-summary requests, and frozen restricted
ToolSets do not receive them.

Every parent Turn has volatile, reset-on-exit Team budgets: eight accepted mutations,
one Team creation, four member additions, one schedule start, four exact reply-body
shows, and at most 30 requested wait seconds per request and 60 per Turn. Reservation
happens before observation or durable effect, and pending approval identities are
cleared on success, failure, and cancellation.

Team mutation approval is separate from `PermissionGate` and `ActionCoordinator`. Its
content-free identity binds the parent Session, Effective Context, ToolUse, control,
canonical arguments digest, target or preallocated Team ID, approval mode, and (for a
schedule) run identity, Provider route, fixed Child ToolSet, and per-run/Child cost
ceilings. The decision is accepted, rejected, or cancelled. The decision record is
append+fsync durable before the mutation; a rejected or cancelled decision creates no
Team artifact. An accepted Team effect is not rolled back when a later parent Turn
commit or final response fails.

Parent Session audit records use schema v1 and are content-free:
`team_control_decided` stores only identity/digest, target, control, mode, and outcome;
`team_message_delivered_to_parent` stores only the reply body digest and exact Team,
assignment, Child Session, Child Turn, and handoff coordinates. Strict replay enforces
parent ownership, canonical identities, unique ToolUse/message receipts, closed fields,
and legacy transcript preservation. Reply bodies remain in the Team ledger, never in
Session audit. `team_message_show` persists its receipt before returning the exact body;
`team_message_read` requires that receipt, and model work completion additionally
requires the matching reviewed assignment, completed Child handoff, reply ID, and
parent receipt. Release and cancel require an explicit bounded note and cannot be
reported as completion.

The Host owns all Team effects and verifies Team owner, assignment, handoff, and reply
provenance before model delivery or review. Team controls grant no workspace write,
command, network, MCP, Skill, Task, Child-delegation, or B6 capability. Fixed
read-only-investigator members, process-local scheduling, no retry, no daemon, and the
existing explicit review boundary remain unchanged.

The B7.2 dispatcher reuses the existing AgentLoop/AgentRuntime/ProjectSession causal
path while keeping `TEAM_CONTROL` separate from `PermissionGate` and
`ActionCoordinator`. It validates parent ownership, reserves per-Turn budgets, and
returns bounded ordinary ToolResults; accepted Team effects remain durable when a
later Provider or parent-Turn commit fails.

The B8.1 contract migration is atomic: Registry generation 8 exposes 61 catalog
definitions and 57 ordinary-parent tools; the canonical system prompt is v47, the
Provider adapter contract is v47, and Effective Context uses v25/v26. Legacy
contexts v23/v24 remain readable without transcript rewriting. Anthropic and
OpenAI-compatible projections use the same eleven Team definitions, while compact
requests expose no tools.

B8.2 proves the complete fake-Provider causal path: parent Team creation, fixed
read-only members, bounded parallel schedule waves, exact Child and mailbox evidence,
parent delivery receipts, explicit review, dependency progress, isolated failure,
cancellation, and lease-based abandoned-run recovery without a Provider call. The
schedule remains process-local and bounded; it never retries, auto-completes work,
runs as a daemon, or grants B6 writable members/worktrees.

## Compatibility

The change adds only new Session audit record types and keeps ordinary turn and Team
record replay compatible. Assignment schema-v3 schedule provenance is nullable and
schema-v1/v2 assignments remain readable. Existing sessions and v23/v24 contexts
replay without rewriting; new ordinary parent turns use Registry generation 8,
prompt v47, Provider contract v47, and Effective Context v25/v26. Existing Host Team
message/work APIs retain their behavior.

## Consequences

Approval, delivery, and review can be recovered and audited without trusting model text
or treating a successful Child as verified work. Parent model access is now available
through the fixed eleven-tool contract, while all restricted runtimes remain least
privilege and the schedule never escapes the owning process.

## Rejected alternatives

Routing Team mutations through `PermissionGate` would incorrectly grant workspace
authority. Persisting message bodies in parent Session audit would duplicate sensitive
content and break the content-free handoff boundary. Marking a reply read from status
or completing work from Child terminal state alone would lose causal evidence, so both
operations require their exact durable receipts.
