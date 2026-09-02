# ADR 0167: Host-Coordinated Extensions and Isolated Session Observation

## Status

Accepted as one bounded hardening slice.

## Context

Declarative Skills and local Plugins need to perform Host actions without
creating a second permission or audit architecture. Local Web and IDE bridges
also need to observe the real `ProjectSession` lifecycle, including bounded
cursor reads and Session switches, without leaking stale events.

## Decision

`CoordinatedExtensionActionInvoker` adapts one Skill or Plugin action into the
existing `ActionCoordinator`. It supplies a synthetic exact action identity and
reuses the current PermissionGate, approval mode, lease, revalidation, durable
Action Audit, and ToolResult contract. Extension runners own only their
declarative validation and executor callback; they cannot grant permissions,
change the ToolSet, alter the system prompt, access the sandbox, or delegate
Child/Team work. When an invoker is supplied, legacy direct approval callbacks
are bypassed so every approval-eligible action uses the shared coordinator.

`ProjectSessionManager` remains a transport lifecycle adapter. Session creation
closes a newly opened Session when registration fails, background worker startup
failure rolls back the manager state, and close failure restores the exact
previous current Session. The Web `/v1/events` endpoint returns the same
cursor-aware bounded event batch as the IDE bridge.

`ObservationStream` increments an epoch and resets retained and queued events
when the attached Session context changes. Queue subscribers therefore cannot
deliver events from a previous Session to a new consumer; cursor gaps and
dropped counts remain explicit Host observations.

## Security and recovery boundaries

- Extension arguments are bounded canonical data and remain untrusted audit input.
- Approval and permission failures never execute the extension callback.
- Session and worker rollback is best effort and reports the original failure.
- Observation retention is diagnostic only; durable Session records remain the
  authoritative history.

## Verification

Deterministic tests cover Web event responses, Session creation rollback,
observation queue epoch isolation, and shared Skill/Plugin coordination. The
normal offline release gate remains required.
