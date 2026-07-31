# 0089: Task Planning, Acceptance, Budgets, and Management

- Status: Accepted
- Date: 2026-07-31

## Context

One explicitly continued Stage provides crash-safe progress but does not define a bounded sequence, cumulative stop conditions, human completion authority, or lifecycle management. Letting a model declare the whole Task complete in ordinary prose would confuse a suggestion with Host evidence. Letting a long Task silently acquire fresh Turns would also make its total resource use unbounded even though each Stage remains locally bounded.

## Decision

Task transcripts gain closed append-only records for optional configuration, plan proposal and acceptance, completion proposal, acceptance verification, terminal outcome, rename, and archive state. Configuration contains a bounded display name, optional parent Task provenance, and cumulative inter-Stage budgets. Existing header-only Tasks retain their derived name and default budget without transcript rewriting.

`/task plan <task-id>` runs one planning Stage through the current provider. The model proposes one to 32 unique bounded Stage objectives in `TASK_PLAN_JSON`; the Host rejects a proposal that cannot fit the Task's remaining Stage budget. `/task plan accept` is an explicit Host record and does not execute anything. `/task run <task-id> [1-16]` then executes accepted steps serially in the foreground, one ordinary Turn per Stage. Progress advances only for committed execution Stages whose objectives match the accepted steps in order; unrelated manual Stages do not silently skip plan work. The run stops on its command limit, plan exhaustion, a completion proposal, Task budget exhaustion, interruption, or a terminal state.

Task budgets default to 32 Stages, 768 provider invocations, and 1,024 tool requests, with optional input/output token ceilings. They are admission ceilings checked between Stages. Once admitted, a Stage retains the complete ordinary Turn contract and is not truncated to fit a smaller Task remainder; therefore its final recorded usage may meet or exceed a cumulative ceiling, after which no later Stage is admitted. Committed Stages and normally observed failed attempts both charge their Host-observed counters. A legacy or crash-recovered Stage with unavailable accounting blocks further provider/tool admission, and also blocks configured token ceilings, instead of treating unknown work as zero. This preserves the rule that every Stage is an ordinary bounded Turn rather than creating dynamic hidden Turn limits.

An execution Stage may append a model `task_completion_proposed` record only after its committed Turn. This changes status to `completion-proposed`, not `completed`. Human `/task verify` records are bound to that exact current completion Stage and one declared acceptance-criterion index. If later work invalidates the proposal, earlier verifications no longer count; a new completion proposal requires fresh evidence. `/task complete` succeeds only with a current model proposal and every declared criterion verified. Tasks with no criteria still require the current proposal. Explicit `/task cancel` and `/task fail` produce separate terminal outcomes and reasons; terminal lifecycle cannot resume, while rename and archive metadata remain available for organization.

Management adds bounded list filters by count, status, active/archive state, and case-insensitive name substring; strict show and complete Stage timeline views; rename; reversible archive; parent-linked derive; explicit recover; acceptance verification; and completed/cancelled/failed terminal commands. Host-only management neither invokes a provider nor enters ordinary conversation history. Derived Tasks are independent current-Session Tasks with immutable parent provenance, not child execution threads or shared mutable lifecycles.

## Compatibility

Task record upgrades are local to the Task transcript family. New Stage start, commit, and failure records use schema v2 while v1 remains replayable. New configuration, plan, completion, acceptance, terminal, rename, and archive records use schema v1. Session transcripts, Action Audit, ToolArguments, tool contracts, provider adapter v26, and compaction records do not change. The model-visible Task framing and protocol are covered by system prompt v24 and ADR 0088.

## Invariants

- A plan is a proposal until explicitly accepted and never proves that a step ran.
- Every planned execution step still owns one normal Turn and all normal action controls.
- Cumulative Task budgets stop admission between Stages; they never create an unbounded or silently reset Turn.
- Plan progress follows exact accepted order and only committed execution Stages.
- Model completion is a proposal; only the Host can append acceptance evidence and a terminal completed record.
- Acceptance evidence is scoped to the current completion proposal and cannot be reused after later work.
- Completed, cancelled, and failed Tasks are terminal; rename/archive remain organizational metadata only.
- Derived Tasks share provenance, not execution state, budgets, permissions, or completion.

## Non-goals

- autonomous plan acceptance, deterministic generation of a workflow language, or a DAG scheduler;
- unattended continuation, background daemons, retries, cron, remote workers, or notification services;
- Task-level blanket approval, shared approval across Stages, or bypass of Action Audit;
- subtask workers, SubAgents, teams, branch/worktree orchestration, or Task dependency scheduling;
- treating the cumulative budget as a CPU, memory, wall-clock, billing, or provider-quota enforcement system.
