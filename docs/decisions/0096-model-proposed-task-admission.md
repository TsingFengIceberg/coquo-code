# 0096: Model-proposed Task Admission

## Status

Accepted.

## Context

ADR 0095 lets a model coordinate an existing durable Task through Stage-scoped proposal tools, but ordinary prompts still require the operator to recognize long work and manually run `/task start`. Letting the model invoke human slash commands would couple it to terminal syntax, while letting it create a Task directly would collapse the distinction between a model request and a Host-owned lifecycle decision.

The missing boundary is admission: the model may explain why work should become a durable multi-Stage Task, but only the user may accept that proposal. The decision must survive restart, remain auditable without trusting assistant prose, and avoid duplicate Tasks if Task creation succeeds but the Session resolution append fails.

## Decision

The canonical catalog adds `task_propose_start(objective, reason, acceptance_criteria)` after the four Stage coordination tools. It is exposed only during an ordinary Prompt, so ordinary prompts now receive the original 21 tools plus this admission tool. Existing Task Stages retain their exact least-capability subsets and never receive `task_propose_start`.

The call is proposal-only. It receives no Action lease, does not pass through PermissionGate, creates no Action Audit record, and does not create, accept, start, or execute a Task. It must be the only tool call in its assistant response, shares the existing 8/32/24 Turn budgets, receives a bounded receipt, and forces the continuation to final text only.

`TaskAdmissionProposal` is immutable and binds the canonical objective, reason, one to sixteen acceptance criteria, pinned Effective Context ID, and tool-use ID. A domain-separated SHA-256 produces its deterministic `tap-v1-...` admission ID. The proposal becomes pending only after the complete ordinary Session Turn is durably committed. Replay derives pending proposals from the exact committed ToolUse, matching non-error ToolResult receipt, and matching successful Host tool-ledger entry; assistant claims alone create no proposal.

The operator manages only proposals in the current Session through:

- `/task proposals [pending|accepted|rejected|all]`
- `/task proposal show <admission-id>`
- `/task proposal accept <admission-id>`
- `/task proposal reject <admission-id> [reason]`

Accept first looks for an existing Task carrying the same admission ID. If none exists, it atomically creates a Task whose transcript stores `task_admission_origin` v1 immediately after the header, including proposal digest, source Session, source Turn record sequence, tool-use ID, and source context ID. It then appends `task_admission_resolved` v1 with the accepted Task ID to the source Session. If Task creation succeeds but the Session append fails, retry finds and returns the same sourced Task before appending the missing resolution. Duplicate Task provenance fails closed.

Reject appends only a rejected Session resolution with an optional bounded reason and creates no Task. Replay requires every resolution to reference an earlier committed proposal and permits exactly one resolution per admission ID. Acceptance and rejection are Host slash commands: they do not invoke a provider, execute a Stage, read one-shot stdin, or reuse PermissionGate approval.

## Compatibility And Versions

The canonical system prompt advances to v28 because ordinary-prompt model behavior changes. The provider adapter contract advances to v29 because count/create projections add the admission schema and ordinary prompts use a new exact tool subset.

The complete canonical catalog contains 26 definitions. Ordinary prompts expose 22, while all existing Task Stage subsets remain unchanged. Effective Context representations remain `ctx-v5` and `ctx-v6`; the no-project-instructions empty full-context identity becomes `ctx-v5-0112c304e7ae0718fad6efdc4e7f5b258d267d9922854d3846fe76f1e594abf0`.

Existing Session and Task transcripts remain readable without rewriting. New resolutions use record-local `task_admission_resolved` schema v1 and sourced Tasks use record-local `task_admission_origin` schema v1. `turn_committed` remains v8, ToolArguments remains v1, Action Audit and compaction schemas do not change, and the ordinary 8/32/24 budgets remain unchanged.

## Consequences

- The model can recommend durable Task admission without controlling Task creation or lifecycle authority.
- Pending state is derived from already durable Session causality instead of duplicating proposal data in another record.
- Acceptance is restart-safe and idempotent across the Task-create/Session-resolution durability boundary.
- Users can inspect, accept, or reject proposals without a provider call and without silently switching Sessions.
- Automatic acceptance, immediate REPL approval prompts, one-shot stdin confirmation, automatic Task driving after acceptance, cross-Session mutation, background work, and model-issued lifecycle commands remain out of scope.
