# 0087: Durable Stage Lifecycle and Turn Evidence

- Status: Accepted
- Date: 2026-07-31

## Context

ADR 0086 creates durable Task identity but cannot prove that any bounded advancement began, terminated, or corresponds to real Session history. Adding `/task continue` directly would leave crashes, concurrent continues, provider failure, and false model completion claims without a durable transaction boundary. A Stage must therefore exist as an append-only lifecycle before it can own one ordinary AgentLoop Turn.

## Decision

The Task transcript adds three closed schema-v1 records:

- `stage_started`: a durable start barrier with canonical Stage UUID4, contiguous 1-based number, exact owner Session, bounded immediate objective, and UTC timestamp;
- `stage_committed`: a terminal fact matching the active Stage and linking one exact `turn_committed` Session record by Session ID, 1-based Turn number, record sequence, raw JSONL-line SHA-256, and timestamp;
- `stage_failed`: a terminal fact matching the active Stage and one closed reason: `cancelled`, `provider-error`, `turn-not-committed`, `host-error`, or `interrupted`.

Strict replay requires one header followed by alternating start and terminal records, exact sequence numbers, unique Stage IDs, contiguous Stage numbers, matching identities, the Task owner Session, valid evidence shapes, and nondecreasing timestamps. No Stage yields `ready`; a committed Stage yields `paused`; a failed Stage yields `blocked`. An unterminated durable start is conservatively `interrupted` during ordinary inspection. Only the live writer that owns the lease renders it as `stage-in-progress`.

`TaskStore.open()` returns one foreground `TaskWriter` holding an exclusive nonblocking transcript lock. The writer candidate-replays every append before writing, verifies the transcript pathname and inode, enforces transcript limits, appends with no-follow semantics, and fsyncs before updating memory. A write or fsync failure after append may have begun raises `TaskAppendCommitError(record_may_be_visible=true)`, poisons that writer, and requires release plus strict inspection instead of retry.

`SessionStore.turn_evidence()` strictly snapshots one Session and accepts only an actual `TurnCommitted` at the selected positive record sequence. It returns no prompt, assistant text, tool arguments, or tool results. It derives the Turn number and hashes the exact newline-terminated raw JSONL record. `TaskWriter.commit_stage()` obtains this evidence itself; callers cannot supply a claimed digest. The timestamps must prove `stage_started <= turn_committed <= stage_committed`, so an old historical Turn cannot be reused as new progress.

Task list summaries now include Stage count. Task show renders the latest Stage objective and outcome, exact Turn evidence for a commit, a stable failure reason, or explicit interrupted recovery guidance. These remain read-only Host views.

## Compatibility

Existing header-only Task transcripts replay unchanged as `ready`; no Task transcript is rewritten. Session records are not changed. The canonical system prompt remains v23, provider adapter contract remains v26, all 21 model-visible tools and 8/32/24 budgets remain unchanged, ToolArguments remains v1, Effective Context remains `ctx-v5`/`ctx-v6`, and Session, compaction, and Action Audit schemas do not advance.

## Invariants

- A Stage start is durable before future provider work may begin.
- At most one Stage is unresolved in one Task transcript.
- A Stage terminal record exactly matches its start and never exists alone.
- A committed Stage references a real committed Turn in the Task owner Session; model prose cannot manufacture evidence.
- An unresolved Stage is never shown as live after its writer lease is gone.
- Uncertain append durability disables further writes through that writer and never triggers automatic retry.
- Stage state never enlarges Turn budgets or bypasses PermissionGate, approval, Action Audit, tool bounds, causality, or Session durability.

## Non-goals

- `/task continue`, provider invocation, automatic Stage objectives, completion proposals, acceptance verification, cumulative Task budgets, or automatic recovery;
- background execution, scheduling, workflows, SubAgents, teams, worktrees, or heartbeat/lane infrastructure;
- copying Session dialogue or Action Audit bodies into Task records;
- changing model-visible prompts, tools, provider projection, permission policy, Session resume, or compaction.
