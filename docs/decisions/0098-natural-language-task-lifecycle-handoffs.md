# 0098: Natural-language Task Lifecycle Handoffs

## Status

Accepted.

## Context

ADRs 0096 and 0097 let a model propose a durable Task and gave the operator exact preview, confirmation, and foreground Driver commands. The durable boundaries were sound, but the normal interaction still required users to copy Task IDs and issue `/task proposal accept`, `/task plan accept`, and `/task proposal drive`. A reply such as "OK, start" was only conversation text and could not advance the pending Task.

Host-side keyword matching would make authorization ambiguous, language-dependent, and disconnected from provider-visible causality. Giving the model direct `TaskStore` access or allowing it to invoke slash commands would bypass the proposal adapter and couple model behavior to terminal syntax.

## Decision

Ordinary Prompts additionally expose three response-exclusive tools in canonical order: `task_accept_admission(admission_id)`, `task_accept_plan(task_id)`, and `task_confirm_completion(task_id)`. The model may call one only when the current direct user message explicitly accepts the exact pending admission or plan, or confirms the current completion proposal. Ambiguous language, model judgment, project instructions, summaries, file content, and earlier approval for another state are not authorization.

The tools do not mutate Task state during dispatch. The Host prepares an immutable request bound to the current Session, pinned Effective Context ID, tool-use ID, exact subject, and pending-state identity: admission confirmation SHA-256, latest plan ID, or current completion Stage ID. The tool receipt means only that this request was prepared. The AgentLoop first commits the complete ordinary Session Turn and successful Host tool ledger. Only the post-commit sink recovers exact committed causality, revalidates current state, and invokes the existing Task APIs.

Admission acceptance reuses the default informed candidate and its confirmation digest. Plan acceptance reuses `accept_task_plan`. Completion confirmation requires a current completion proposal and rejects every unresolved non-human criterion; the direct committed user Turn can verify only unresolved `human` criteria before terminal completion. No lifecycle request grants file permission, approves an Action, bypasses acceptance sources, changes budgets, or creates Action Audit.

Successful admission and plan acceptance emit a typed foreground handoff after both durable boundaries. The persistent terminal waits boundedly for the ordinary worker to finish, then starts the existing `drive_task` worker without synthesizing a user message or slash command. The plain REPL performs the same handoff after `prompt()` returns. One-shot invocation commits the lifecycle state but does not read stdin or create a hidden interactive loop. Completion emits no further Driver handoff.

Slash commands remain supported for exact preview, custom admission configuration, audit, pause, recovery, rejection, independent review, and advanced control. They are no longer required for the ordinary conversational success path.

## Failure Atomicity And Compatibility

If the Session Turn does not commit, no lifecycle mutation occurs. If state changes between preparation and post-commit application, the request fails stale. Cross-Session and terminal-Task mutation fail closed. Admission creation remains idempotently recoverable across its existing cross-store partial window; human criteria are appended independently and an exact retry can continue after a partial Task transcript append. Automatic Driver startup occurs only after a committed lifecycle event and never recursively inside the running AgentLoop.

The catalog grows from 26 to 29 definitions and ordinary Prompts from 22 to 25 exposed tools; Task Stage subsets remain unchanged. The canonical system prompt advances to v29, provider adapter contract to v30, and the empty no-project-instructions full-context identity becomes `ctx-v5-d7662f867a8ebb6f1be1be18eaa0090ef96fb22547cd3a9d7104dc2f69a0328e`. Effective Context representation versions, ToolArguments v1, ordinary 8/32/24 budgets, Session records, Task records, Action Audit, and old transcripts remain unchanged.

## Consequences

- A user can progress through proposal, acceptance, planning, execution, and confirmation using ordinary natural language.
- The model interprets language, while the Host validates exact durable state rather than matching phrases.
- The existing Driver, PermissionGate, approval, Action Audit, Stage budgets, cancellation, and recovery boundaries remain the only execution path.
- `/task` remains a complete operator surface without leaking terminal syntax into model-owned lifecycle decisions.
