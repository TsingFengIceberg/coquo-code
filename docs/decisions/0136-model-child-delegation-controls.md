# ADR 0136: Model Child Delegation Controls

- Status: Accepted
- Date: 2026-08-14

## Context

A3-A7 provide Host-created read-only Child Runs, detached execution, bounded
supervision, cancellation/recovery, evidence-backed handoffs, and content-free
parent delivery receipts. The ordinary parent model still cannot request those
operations. Exposing controls before approval, provenance, per-Turn state, and
causal dispatch are complete would allow incomplete delegation behavior into a
versioned Provider contract.

## Proposed decision

Define four closed Child controls in fixed order:
`child_spawn(objective)`, `child_status(child_run_id)`,
`child_wait(child_run_id, timeout_seconds)`, and
`child_cancel(child_run_id, reason)`. Classify them as
`ToolExecutionKind.CHILD_CONTROL`, with no `PermissionAction`. They use the
ordinary ToolUse/ToolResult ledger, event, request, and Provider-invocation
budgets, but never enter `ActionCoordinator`.

Every Child-control call must be the only ToolUse in its Provider response. A
successful control does not force final text; a parent may issue another
control in a later response. Volatile `ChildControlState` limits one parent Turn
to four successful spawns and 60 cumulative requested wait seconds, with 30
seconds maximum per wait request. The state and any pending approval identity
are cleared after every success, failure, or cancellation exit.

Delegation approval is separate from Action permission approval. Under `ask`,
the terminal shows the exact bounded objective, selected non-secret route and
model, fixed read-only Child tools, one-Turn budgets, spawn number,
process-local limitation, and additional Provider-cost warning. The immutable
identity binds parent Session, Effective Context, ToolUse, objective digest,
route fingerprint, Child ToolSet, budgets, depth one, and approval mode. `auto`
removes interaction but changes none of those bounds.

Every decision appends and fsyncs a content-free
`child_delegation_decided` schema-v1 parent Session audit before Child
creation. Rejection or cancellation therefore creates no Child Run or detached
Session. An accepted decision creates one transcript atomically with a header
and `child_run_delegated` schema-v1 source record before the existing admission
and Session-binding records. The source binds the exact parent context,
ToolUse, decision sequence/digest, depth one, and source `model`. Existing
Host-created header-to-admission transcripts remain valid without this record.

Status, wait, and cancellation validate durable parent ownership on every
request. Wait reserves its entire requested duration before observing state.
When a wait observes a terminal Child it publishes and delivers the A7 handoff
with the exact model ToolUse receipt. A parent Turn failure after a real spawn
does not roll back Child state or detached Provider work. Append/fsync
uncertainty remains fatal to that transition and is never converted to a
retryable model error.

## Exposure and version migration

The definitions append to ordinary parent ToolSets only. Child, Task Stage, and
compact-summary requests exclude them. Built-in source and Tool Registry
generation advance from 6 to 7, system prompt from v44 to v45, Provider adapter
contract from v45 to v46, and new full/compacted Effective Context identities
from v21/v22 to v23/v24. Versions 21/22 remain explicit strict legacy
representations; Session and Child additions are record-local schema v1 and no
transcript is rewritten.

A deterministic fake-provider path spawns two real supervised Children in one
parent Turn, performs parent workspace work, waits for and delivers both
handoffs, commits one causal parent Turn, and strictly replays the parent plus
both detached Child Sessions. Children remain read-only, one-Turn, depth-one,
process-local, and unable to delegate.

## Alternatives rejected

- Routing delegation through `ActionCoordinator`: delegation is admission and
  Provider-cost authorization, not a workspace or network `PermissionAction`.
- Creating a Child before asking: rejection or cancellation would leave an
  unauthorized durable Child and possibly detached Provider work.
- Injecting handoff text directly into parent history: it would fabricate
  conversation causality and bypass ordinary ToolResult accounting.
- Exposing A8.1 definitions early: partial model contracts would require an
  avoidable intermediate version and allow requests before all Host behavior is
  proven.
