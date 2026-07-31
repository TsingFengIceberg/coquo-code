# 0092: Adaptive Foreground Task Orchestration

## Status

Accepted.

## Context

Leonervis Tasks could already persist plans, execute each accepted step as an ordinary AgentLoop Turn, propose completion, and bind structured acceptance evidence to the current completion Stage. A failed acceptance check, however, remained a terminal user-visible fact: it was not projected into a bounded next Stage, there was no typed reflection or correction lifecycle, a replacement plan lacked predecessor provenance, and `/task run` could only consume fixed plan steps. Long work also needed a durable pause control and a bounded derived context checkpoint without rewriting the complete Task transcript.

These additions must preserve the existing hierarchy:

```text
Task -> bounded Stage -> ordinary Turn -> permissioned and audited Action
```

Reflection is a decision strategy, not a new authority. A foreground driver is orchestration, not a background worker, unbounded retry loop, workflow engine, or substitute for PermissionGate, approval, Action Audit, Session commit, and acceptance.

## Decision

Current acceptance checks are projected into the next Task Stage as bounded criterion index, source, outcome, and evidence. Checks remain bound to their completion proposal; starting any later Stage invalidates them for completion without deleting their records.

Add `reflection` and `correction` to schema-v2 `StageKind`. A reflection Stage is one ordinary committed Session Turn whose exact provider request has `allow_tools=false`. Its final response must contain one closed `TASK_REFLECTION_JSON:` object with `recommendation`, bounded `summary`, and a conditionally required `next_objective`. The Host appends schema-v1 `task_reflection_recorded` only after the Stage and Session Turn commit. Reflection cannot execute tools, verify acceptance, grant permission, or complete a Task.

A correction Stage reuses the normal tool-capable AgentLoop, PermissionGate, approvals, Action Audit, per-Turn budgets, cancellation, and atomic Session commit. Completion proposals may reference committed execution or correction Stages. Old completion proposals and evidence remain historical and cannot satisfy a later correction proposal.

New `task_plan_proposed` records use record-local schema v2. The first plan has no predecessor. Every later proposal names the immediately preceding plan and a bounded revision reason, and may bind the current reflection ID. A revised plan is only a proposal and must be explicitly accepted before execution. Schema-v1 plan proposals remain readable without transcript rewriting.

Add `/task drive <id> [1-16]` as a bounded foreground state machine. It may propose an initial plan, run accepted steps, perform deterministic Host checks, run a no-tools reflection after failed Host checks, execute one advised correction or continuation, or propose a revised plan. It stops with a typed reason at terminal state, pause, recovery requirement, cumulative budget, Stage limit, plan acceptance, plan exhaustion, human evidence, independent review, manual completion, or reflection escalation. It never automatically invokes an independent reviewer: `/task next` reports that the explicit review uses the current provider with tools disabled and may consume tokens or API cost.

Add schema-v1 `task_pause_changed` records. Pause blocks only automatic driving; explicit Stage, verification, recovery, and lifecycle commands remain available. `/task resume` removes that driver-only block. `/task next` is read-only and performs no provider call or Task mutation.

Add schema-v1 `task_context_checkpoint` records. A checkpoint is a Host-derived bounded snapshot containing source sequence, checkpoint chain, accepted-plan identity and progress, current completion Stage, unresolved criterion indices, and latest reflection identity. It contains no dialogue, tool arguments, command output, or full acceptance bodies. The candidate record is strictly replayed and append+fsync committed before becoming current. The complete Task JSONL is never deleted or rewritten. Stage framing may use the checkpoint plus a shorter recent-Stage suffix, while full replay remains authoritative.

## Compatibility And Versions

The canonical system prompt advances to v26 because reflection, correction, revised-plan, feedback, and foreground-driver behavior are model visible. The provider adapter contract remains v26: adapters already project the existing provider-neutral `allow_tools` request flag, and no native response shape changes. The 21 tool schemas and order, ToolArguments v1, ordinary Turn budgets, Session and Action Audit schemas, and Effective Context representation versions `ctx-v5`/`ctx-v6` remain unchanged. The changed prompt updates current Effective Context identities; it does not require a representation-version bump.

Legacy Task headers, Stage v1, and plan-proposal v1 records remain readable without rewriting. New reflection, pause, and Task-checkpoint records are v1. New plan proposals are v2.

## Consequences

- Failed deterministic acceptance evidence can causally influence the next bounded decision without becoming system authority.
- Correction side effects remain subject to exactly the same action controls as ordinary work.
- Reflection and plan revision are durable and inspectable rather than hidden prompt conventions.
- Driver progress is bounded, foreground, cancellable, and truthful about every stop.
- Independent review remains an explicit cost-bearing user action.
- Task checkpoints improve long-history framing but are not transcript compaction, deletion, memory, or proof that omitted work succeeded.
- Background execution, scheduling, parallel Stages, SubAgents, autonomous reviewer spending, general retry, and dynamic workflow generation remain out of scope.
