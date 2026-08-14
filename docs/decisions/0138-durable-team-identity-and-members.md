# ADR 0138: Durable Team Identity and Member Registry

- Status: Accepted
- Date: 2026-08-14

## Context

Coquo already has durable, independently executable Child Runs, but it had no
workspace-bound aggregate identity for a user-managed group of future work. An
in-memory Team or a worker/thread object would lose identity on restart and
would incorrectly conflate membership with execution, permissions, or model
context. The first Team slice therefore needs a small durable Host ledger that
can be inspected without invoking a Provider.

## Decision

Each Team is one private append-only JSONL transcript under
`.coquo/teams/<workspace-fingerprint>/<team-id>.jsonl`. The header records a
canonical UUID, workspace and fingerprint, immutable owner Session ID, bounded
immutable display name, and creation time. The lifecycle is irreversible
`open -> closed`; close appends a record, never rewrites or deletes history.
Records use strict closed schema-v1 JSONL replay with contiguous sequences,
workspace/path identity checks, bounded record and directory sizes, no-follow
file handling, atomic creation, append+fsync, an exclusive writer, and
fail-closed uncertainty poisoning.

Members are durable identities in the same Team ledger. Each member has an
independent UUID, an immutable casefold-unique name reserved for the Team's
complete history, the fixed role contract `read-only-investigator-v1`, and a
bounded lifecycle `active <-> disabled -> left`. A Team has at most 64 members.
Disabling blocks only future assignment creation; leaving is terminal and does
not erase historical evidence. No member record stores a thread, Provider
binding, prompt context, mailbox, task queue, or workspace capability.

Standalone `coquo team ...` and REPL `/team ...` operations are Host-only. They
create, inspect, list, close, and transition Team/member identities without a
Provider call, model-visible tool, Session transcript turn, or Effective
Context change. REPL mutations require the selected Session to be the immutable
Team owner; standalone inspection and lifecycle commands address an exact Team
without silently switching or resuming a Session. Team close remains subject
to later assignment evidence checks, and B1 has no assignments, so a new Team
can close directly.

Future assignments will allocate a fresh Child Run and detached Child Session
for every objective. Reusing a member means reusing its durable identity and
audit lineage, never reusing a Child conversation or retaining a worker. Team
records will later contain assignment relationship evidence, while the Child
ledger remains execution-state authority. This ADR intentionally does not
introduce assignment execution, mailboxes, shared tasks, scheduling, write
permissions, model-visible Team tools, or process-surviving workers.

## Compatibility and invariants

- Existing Session, Task, Child, Skill, and ordinary transcript schemas replay
  unchanged and are never eagerly rewritten.
- Tool Registry generation 7, system prompt v45, Provider adapter contract
  v46, and Effective Context v23/v24 remain unchanged because Team state is
  Host-only.
- Team/member task text is untrusted audit data and never becomes system
  instructions or model context.
- Owner Session identity is immutable; Host operations never mutate its
  transcript, `latest` pointer, Provider binding, or Child ledgers.
- Corruption, unknown fields, invalid transitions, path escape, partial lines,
  and uncertain durability fail closed and require inspection/recovery rather
  than repair or blind retry.

## Alternatives rejected

- An in-memory registry: loses identity across restart and cannot support audit.
- A mutable JSON configuration file: weakens append-only replay and makes
  partial lifecycle transitions ambiguous.
- A member thread or long-lived model Session: confuses liveness with identity,
  retains context across objectives, and creates an unbounded worker surface.
- Exposing Team controls to the model in B1: expands the model contract before
  assignment permissions, causality, and cross-ledger recovery are designed.
