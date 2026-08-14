# ADR 0140: Durable Team Mailbox and Assignment Delivery

## Status

Accepted for the B3 Team slices.

## Decision

Team coordination uses the existing workspace-bound append-only Team JSONL ledger. An
owner-to-member message is a bounded `team_message_sent` record with a canonical UUID,
body SHA-256, one recipient member, and no caller-supplied sender. It begins `pending`
and remains in the transcript when it is delivered or cancelled. A member-to-owner
reply is created only by the Host after exact detached Child Session Turn and published
handoff evidence; it has one preallocated reply ID, immutable assignment/Session/Turn
provenance, and begins `unread`. `team_message_read` is an explicit owner operation;
there is no consume or delete operation.

Before a new Team assignment is admitted, the Host freezes an oldest-first bounded inbox
batch (at most eight messages and 12 KiB of bodies), preallocates delivery and reply IDs,
and appends `team_assignment_mailbox_bound`. The Child receives a deterministic v2
Host-framed prompt containing that exact batch as untrusted data. Generic and already
admitted v1 Children retain their original role prompt and tool contract. A completed
Team Child reply is appended only after the committed user-message digest matches the
reconstructed v2 prompt and the exact published handoff exists; a mailbox observation
then marks the frozen outbound messages delivered before the existing content-free Team
terminal observation. Failed, cancelled, or interrupted Children do not deliver inbox
messages or publish replies.

The parent Session, Provider, model-visible Tool Registry, and durable Task ledger do
not own or receive mailbox state. Standalone and REPL commands are Host-only and do not
append parent turns or invoke a Provider. Cross-ledger steps are explicit sagas with
exact IDs and replay checks; uncertain appends require reinspection and never allocate
replacement IDs.

## Consequences

- Message history is auditable and restart-safe, including unread, delivered, and
  cancelled state.
- Team v1 Child admissions remain byte-compatible while new Team admissions use a
  versioned mailbox role contract.
- Delivery and reply publication can be recovered independently without pretending that
  Team, Child, and Session files form one transaction.
- The mailbox is intentionally one-shot: peer messaging, broadcasts, live conversations,
  attachments, model-visible send tools, and notifications remain out of scope.

## Rejected alternatives

Mutable per-message JSON files and deletion-on-read would lose replay evidence and make
crash recovery ambiguous. A `team_send_message` model tool would make untrusted Child
prose an authority boundary and would bypass the exact Session/handoff causality. A new
queue, worker, or service process would add lifecycle and durability claims that the
local single-user Host does not need.
