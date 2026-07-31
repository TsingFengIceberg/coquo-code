# 0088: Foreground Task Stage Execution and Recovery

- Status: Accepted
- Date: 2026-07-31

## Context

ADRs 0086 and 0087 define durable Task identity and a Stage ledger, but they deliberately stop before provider execution. A usable long-running Task must advance through the existing AgentLoop rather than introducing a second tool loop, and it must survive crashes between the Task start barrier, ordinary Session Turn commit, and Task terminal records without replaying actions whose outcome may already be visible.

## Decision

`/task continue <task-id> <stage-objective>` executes exactly one foreground Stage through one ordinary `ProjectSession.prompt()` call. The current Session must be the Task owner. The Stage receives the normal per-Turn limits of eight ordered tool calls per response, 32 admitted tool requests, and 24 provider invocations; every action still independently passes workspace containment, PermissionGate, approval, tool hard bounds, command sandboxing, Action Audit, and ordinary Turn causality and durability.

Before provider work, the Host builds one bounded user message beginning `[Leonervis durable Task Stage]`. It contains canonical JSON for the untrusted Task objective, acceptance criteria, accepted plan, bounded prior-Stage summaries, current Stage, cumulative usage, total budget, and remaining inter-Stage budget. This data is not system authority or permission. The Stage's new schema-v2 `stage_started` record durably stores its kind, exact Session record and Turn baselines, and SHA-256 of that complete user message before `ProjectSession.prompt()` begins.

After the ordinary Turn commits, schema-v2 `stage_committed` links the same exact Session record identity as before and additionally copies only bounded accounting: provider invocation and known/unknown token counts plus the ToolTurnLedger totals. A normally observed failure uses schema-v2 `stage_failed` to preserve the same content-free provider and tool-attempt counters even though no Turn committed. It never copies dialogue, tool arguments, results, approval content, or Action Audit bodies. Legacy schema-v1 Stage records remain readable and report unavailable cumulative accounting where no usage exists.

Execution responses end with one exact `TASK_COMPLETION_PROPOSAL: yes|no` final nonblank line; planning responses use one exact final `TASK_PLAN_JSON:` line. The canonical system prompt advances to v24 so providers know this framing is Host-owned, untrusted, bounded, and Task-only. The protocol line remains in the ordinary Session transcript for exact replay, while the direct Task result and valid streamed terminal display remove it. Invalid final text remains visible and the committed Turn remains inspectable even though Task metadata is rejected.

`/task recover <task-id>` never invokes a provider or repeats a tool. It reconciles an unresolved Stage by searching only after the durable Session baseline for a committed Turn whose user-message SHA-256 equals the stored Task prompt digest. No match durably fails the Stage as `interrupted`; one match commits the Stage; multiple matches fail closed as ambiguous without changing the Task. Recovery also handles a crash after `stage_committed` but before Task protocol metadata by reading that exact committed Turn and appending a missing plan or completion proposal. Invalid or absent protocol cannot manufacture metadata.

Provider failure, cooperative cancellation, missing Turn commit, and other Host failures map to closed Stage failure reasons. If an exception occurs after the Session Turn has already committed, the Host binds that evidence before reporting the exception so callers do not retry potentially completed actions. An uncertain Task append poisons the writer and requires strict inspection or recovery.

Both the line-oriented REPL and persistent prompt-toolkit terminal route Task execution through the existing event sink, approval broker, cancellation token, and background `TurnRunner`. `/task run` may execute several Stages serially, but terminal output preserves every non-streamed Stage response rather than only the last one and emits a Host event with the exact Stage count and stop reason.

## Compatibility

The provider adapter contract remains v26, all 21 model-visible tools and their order/schemas remain unchanged, ToolArguments remains v1, and ordinary 8/32/24 Turn limits do not change. The system prompt is v24 with fingerprint `v24-13d080166d18609edf6ab07b9b1b6f159643a682072319fa65137c93291a0671`. Effective Context representations remain `ctx-v5`/`ctx-v6`; because exact system-prompt content participates in identity, the empty full-context identity without project instructions becomes `ctx-v5-bd663ddc5d94403891caac9f91d76a319200967331a18163859e203cd6bbb116`. Historical identities and checkpoints remain valid, and no Session or Action Audit schema advances.

## Invariants

- A Task Stage never creates a second AgentLoop or enlarges an ordinary Turn budget.
- `stage_started` is durable before provider work; `stage_committed` references exactly one later committed Turn with the same user-message digest.
- Recovery reconciles durable evidence and never replays provider or tool effects.
- More than one matching Turn is ambiguous and causes no Task mutation.
- Task framing is untrusted user data and cannot grant permission, approval, or proof of completion.
- A committed Session Turn remains truthful even when later Task protocol parsing or append fails.
- A normally failed Stage charges observed provider and tool-attempt usage; crash-recovery gaps remain explicitly unknown.
- Task execution uses the same terminal approvals, cancellation, live tool events, Action Audit, Session commit, and command sandbox as an ordinary prompt.

## Non-goals

- background workers, detached execution, scheduling, heartbeats, queues, or automatic process restart;
- SubAgents, teams, worktrees, parallel Stages, or concurrent Actions;
- implicit retry, rollback, or interpreting model prose as executed work;
- changing provider wire tool projection, tool schemas, permissions, Session resume, or compaction semantics.
