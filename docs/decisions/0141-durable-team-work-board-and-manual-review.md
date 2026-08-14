# ADR 0141: Durable Team Work Board and Manual Review

## Status

Accepted for the B4 Team slices.

## Decision

Team work coordination uses immutable work-item records in the existing append-only
workspace-bound Team JSONL transcript. Each work item has a bounded title and objective,
up to sixteen dependencies, and dependencies may reference only earlier work items in
the same Team. This backward-only rule proves acyclicity during replay without mutable
graph repair. Readiness is derived: an item is `blocked` until every dependency is
`completed`, then becomes `ready`.

Assignment is a Host-only manual operation. `work assign` appends the existing Team
assignment record with an optional work-item ID and reuses the established fresh Child
Run, detached Session, fixed read-only ToolSet, mailbox binding, and terminal handoff
saga. One work item and one member may each have at most one nonterminal attempt. A
completed Child proves only that a bounded Turn and handoff were committed; it moves the
work item to `review`, never directly to `completed`.

The owner explicitly chooses `work complete` with a bounded evidence note after exact
terminal handoff evidence is observed, or chooses `work release` to return the item to
derived `ready`/`blocked` for another manual attempt. Failed, cancelled, and interrupted
Children can be reviewed and released, but cannot satisfy completion. `work cancel` is
allowed only before or after review and never cancels a Child implicitly. A release does
not allocate a replacement Child; the next explicit assignment creates new assignment,
Child, Session, mailbox, and reply identities.

Team close requires all assignments to be terminal-observed, all work items to be
`completed` or `cancelled`, all owner messages delivered or cancelled, and all member
replies read. Member leave retains its exact assignment and inbound-message gates. Close
and leave report blocking IDs and do not implicitly cancel, retry, schedule, or repair
coordination state. Recovery reconciles only exact existing IDs and cross-ledger
evidence; it never starts a ready item or creates a replacement attempt.

The durable Task state machine remains a separate owner-Session abstraction. Work items
are not Tasks, do not import Task acceptance or budgets, and are not model-visible tools.
No scheduler, queue, background worker, database, RPC service, or new execution thread
is introduced; assignment execution continues through the existing bounded supervisor
and foreground Host commands.

## Consequences

- Work progress and dependency readiness survive restart with strict replay and mixed
  legacy/new assignment records.
- Human review remains the authority boundary between Child output and work completion.
- A failed or released attempt preserves its immutable audit history while allowing a
  later explicit attempt with distinct provenance.
- Close diagnostics expose exact unfinished work, messages, replies, and mailbox
  observations instead of silently discarding them.

## Rejected alternatives

Reusing the durable Task ledger would conflate owner planning, budgets, acceptance, and
Team coordination. Mutable dependency edits would require cycle detection and repair
semantics that are unnecessary for this bounded board. A scheduler or persistent worker
would expand the product into background execution and introduce unplanned lifecycle
and recovery claims. Marking work complete from Child status or final prose alone would
turn untrusted model output into Host acceptance evidence.
