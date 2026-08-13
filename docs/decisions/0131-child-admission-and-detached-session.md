# ADR 0131: Child Admission and Detached Session Binding

- Status: Accepted
- Date: 2026-08-13

## Context

A queued Child Run is not enough evidence to begin Provider work. Starting from
that record could accidentally reuse the parent Session, switch `latest`, or
inherit a broader ToolSet and permission boundary. Preparation also needs to be
retryable after a crash between admission, Session creation, and binding.

## Decision

Preparation appends a closed `child_run_admitted` record containing one bounded,
redacted execution envelope, then creates or validates one fixed-ID detached
Child Session and appends `child_session_bound`. The Child Session uses the
normal workspace-bound Session format, but `publish_latest=False`; its ID is
distinct from the Child Run ID and it never changes the foreground runtime.

The A3 envelope fixes `read-only` permission, `auto` approval for the exposed
read-only actions, a deterministic ordered built-in ToolSet, a versioned role
contract, budgets, and redacted Provider route provenance. Credential values,
full prompts, environment maps, file contents, and Provider responses are never
serialized. The exact route and ToolSet are validated again before execution in
A4; A3 performs no Provider construction or invocation.

Preparation is serialized by the Child Run writer and uses append-only fsynced
records. A retry reuses only an exact existing admission or detached Session;
binding mismatch, unsafe/inaccessible Session state, and uncertain append
durability fail closed rather than guessing. A known preparation error is
recorded as `child_run_preparation_failed` and derives `failed`.

## Compatibility and boundaries

Legacy A1/A2 Child transcripts containing only a header or header plus
`child_run_cancelled` remain readable and unchanged. Session record schemas,
the canonical parent system prompt, Provider adapter contract, and Effective
Context identities do not change. The Child ToolSet excludes writes, commands,
network, MCP, Task/Skill authoring, discovery, delegation, and dynamic
promotion.

## Deferred work

Foreground execution, independent Provider construction, background workers,
cancellation, restart recovery, handoff, parent delivery, and model-visible
delegation remain A4-A8.
