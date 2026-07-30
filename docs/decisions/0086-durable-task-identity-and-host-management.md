# 0086: Durable Task Identity and Host Management

- Status: Accepted
- Date: 2026-07-31

## Context

An ordinary Leonervis user turn may already cross many provider invocations and tool batches, but it remains bounded by one turn's 8-request response batches, 32 admitted tool requests, and 24 provider invocations. Work that must pause, survive restart, and continue through several bounded turns needs a durable identity above Session turns. Reusing the Session transcript as an implicit task record would conflate conversation history with objective lifecycle, while increasing the ordinary turn budget would only make one failure domain larger.

## Decision

Leonervis introduces an explicit workspace-scoped Task hierarchy:

```text
one Task
  -> multiple bounded Stages
     -> each Stage uses an ordinary Turn budget
        -> each Action still passes PermissionGate and Action Audit
```

This first slice implements only Task identity and Host management. A `task_header` schema-v1 record contains a canonical UUID4, canonical workspace identity, one existing owner Session UUID, a bounded nonblank objective, zero to 16 bounded nonduplicate acceptance criteria, workspace scope, and a canonical UTC creation timestamp. Its derived status is `ready`.

Each Task has an independent append-only transcript at:

```text
<workspace>/.leonervis-code/tasks/<workspace-fingerprint>/<task-id>.jsonl
```

`TaskStore` validates workspace ownership, exact filename identity, closed fields, bounds, no-follow regular-file storage, complete newline boundaries, and strict replay. Creation writes and fsyncs a private temporary file, installs it without replacement through an exclusive hard link, fsyncs the directory, removes the temporary name, and fsyncs again. A failure after the final Task name appears reports that visibility instead of claiming no effect. Listing and inspection never create or repair state.

The standalone CLI exposes `task create`, `task list`, and `task show`. The REPL exposes `/task start`, `/task list`, and `/task show`. REPL creation binds the current Session; standalone creation defaults to `latest` or accepts an exact Session UUID. These are Host commands: they invoke no provider or model-visible tool, consume no turn budget, append no Session record, and create no Action Audit. Creating a Task is not permission to execute its future Actions.

## Compatibility

Task transcripts are a new independent schema family and do not rewrite or extend Session records. The canonical system prompt remains v23, provider adapter contract remains v26, the 21 model-visible tools and 8/32/24 budgets remain unchanged, ToolArguments remains v1, current Effective Context remains `ctx-v5`/`ctx-v6`, and Session, compaction, and Action Audit schemas do not advance.

## Invariants

- A Task belongs to exactly one canonical workspace and references one existing workspace Session at creation.
- Task creation is durable before success is reported; an uncertain post-install state is explicit.
- Task listing and inspection are strict, bounded, no-follow, and side-effect free.
- Task text is untrusted data and terminal rendering escapes controls.
- A Task never enlarges a Turn budget or bypasses PermissionGate, approval, tool hard bounds, Action Audit, causality, or Session commit rules.
- Future Stage records must link committed Turn and Action Audit evidence without copying or rewriting those facts.

## Non-goals

- Stage records or execution, `/task continue`, automatic planning, plan-to-Task conversion, retries, workflow generation, scheduling, background workers, or multi-agent delegation;
- changing model-visible prompts, tools, provider projection, Effective Context, Session resume, compaction, or permission semantics;
- treating an objective or acceptance criterion as system authority, approval, or execution proof.
