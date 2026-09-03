# ADR 0168: Production Runtime Foundations for Browser, Workers, Marketplace, A2A, and Observability

## Status

Accepted as a bounded local-production hardening slice.

## Context

The project already had safe contracts for browser actions, remote work
envelopes, marketplace metadata, A2A Tasks, and content-free observations. The
remaining gap was lifecycle durability: a process restart or a second Host
process could lose the control-plane state even though the authoritative local
ledgers were safe.

## Decision

- `PlaywrightBrowserRuntime` lazily imports Playwright, starts an explicitly
  selected engine/context/page, never downloads browser binaries, and closes
  resources in dependency order. Missing dependencies, launch failures, and
  incomplete close operations are typed fail-closed errors. Browser session
  records use a locked atomic JSON store with fsync and an explicit
  `recovery-required -> created` acknowledgement before reopening.
- `PersistentRemoteTransport` stores authenticated submit, claim, heartbeat,
  completion, and expiry events in a workspace-bound locked JSONL ledger. Worker
  identities use HMAC proofs. An expired lease produces an `unknown` terminal
  result and is never silently requeued. `RemoteWorkerClient` and the
  loopback-only `coquo-remote-worker` service expose the same protocol without
  automatic retries.
- Marketplace entries may use publisher-bound Ed25519 signatures with a
  `public_key_id`. `MarketplaceTrustStore` persists active/revoked keys for
  rotation. Legacy digest signatures remain an explicit compatibility mode and
  can be disabled. Quarantine, approval, installation, revocation, and rollback
  are replayable from a locked lifecycle ledger.
- A2A Tasks are recorded in a workspace-bound ledger. Repeated input message IDs
  are idempotent. On process restart, non-terminal Tasks become protocol
  `failed` with a recovery-required message; the provider never fabricates a
  completion or retries unknown work. Optional Bearer authentication protects
  task routes while AgentCard discovery remains public.
- `PersistentObservationStore` appends only the existing redacted
  `ObservationEvent` projection with cross-process locking, fsync, bounded
  retention, cursor gaps, and an epoch file. It is diagnostic state, not a
  second source of Session/Task/Child/Team truth. ProjectSession live events use
  this store, and `observe timeline runtime` reads the durable projection.

## Security and recovery boundaries

These facilities remain local single-user or loopback services. They do not
claim a hostile OS sandbox, exactly-once external effects, a distributed worker
fleet, public marketplace hosting, or multi-tenant identity. Credentials and
provider/browser content are not written to the new ledgers. Unknown outcomes,
stale cursors, revoked keys, missing browser binaries, and failed cleanup remain
visible and fail closed.

## Verification

Deterministic tests cover lazy browser startup and close ordering, locked
session recovery, remote worker authentication and restart replay, Ed25519
verification and key revocation, A2A replay/idempotence, and observation
cursor-gap recovery. The complete offline release gate remains required; no
real provider, browser download, or external worker call is part of CI.
