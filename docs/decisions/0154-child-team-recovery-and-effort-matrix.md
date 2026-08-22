# ADR 0154: Child/Team Recovery Boundaries and Provider Effort Matrix

## Status

Accepted

## Decision

Concurrent Child workers may read Session transcripts while another worker is
appending. Read-only Session replay therefore takes a bounded stable snapshot;
transient size drift is retried, while persistent drift still fails closed.
This removes a false corruption result without weakening append-only replay or
writer locking.

Child supervisor recovery delegates to the durable execution-lease protocol.
An active worker is reported as `still_owned` and is never interrupted by a
second recovery pass. After the worker exits and releases its lease, recovery
may append one `interrupted` terminal record. Recovery does not retry, resume,
or execute a READY Child, and supervisor notifications remain observational.

Task-to-Child and Task-to-Team observation accepts only terminal Child evidence
with a publishable, identity-matching handoff. Failed, cancelled, and
interrupted Children converge to one failed Task stage; repeated observation is
idempotent and cannot append a second terminal stage. A missing or inconsistent
handoff remains a Host recovery error.

Provider effort verification covers every Host level across OpenAI Chat
Completions, OpenAI Responses, and Anthropic adaptive string profiles. Profile
gaps fail closed; numeric Anthropic `budget_tokens` remains unsupported.

## Compatibility and non-goals

No Session, Child, Team, Task, or Provider wire schema changes. This decision
does not add automatic retry, automatic completion, exactly-once claims,
distributed workers, or numeric Anthropic budgets.

## Verification

Deterministic tests cover concurrent Child replay, live-worker `still_owned`
recovery, post-exit interruption, Task/Team terminal failure convergence and
idempotency, and complete OpenAI Chat/Responses effort mapping matrices with
fail-closed unmapped levels.
