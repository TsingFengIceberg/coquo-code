# ADR 0160: Bounded Memory Retrieval Index Cache

- Status: Accepted
- Date: 2026-09-01
- Scope: Process-local semantic retrieval acceleration and diagnostics

## Context

The first long-term-memory slice provides deterministic local semantic
retrieval, but each query recomputed features and vectors for every eligible
record. Recomputing is needlessly expensive for a long-lived REPL and makes it
difficult to distinguish a retrieval-quality issue from local indexing cost.
The durable JSONL event log must remain the sole memory source of truth.

## Decision

`SemanticMemoryRetriever` keeps a bounded process-local feature cache keyed by
the immutable memory ID. Each entry is validated against the record content,
status, updated timestamp, and scope ID before reuse. Any mismatch recomputes
the feature map and vector; records no longer returned by the provider are
removed from the cache. The cache is protected by a local lock and is never
serialized, shared with a Child, or treated as durable evidence.

`MemoryRetrievalResult` reports the eligible candidate count and cache hit/miss
counts. `MemoryRecallService` reuses one retriever instance per configured
strategy during the current runtime, so repeated prompts can benefit from the
cache. Existing scope filtering, confirmed-only recall, ranking thresholds,
recall touching, byte limits, and untrusted evidence framing are unchanged.

The durable memory event log remains authoritative across restart or process
boundaries. A future persistent index or learned/remote embedding backend must
use the same `MemoryRetriever` boundary, validate against replayed records, and
introduce its own explicit schema, rebuild, failure, and privacy decision.

## Compatibility and security

- No memory, Session, Provider, Tool, or Action Audit schema changes.
- Cache counters are bounded Host observations and contain no memory content.
- A stale or corrupted cache cannot alter recall authority; recomputation or a
  surfaced retrieval failure is required.
- The cache does not grant scope, permission, approval, or execution ability.

## Verification

Deterministic tests prove first-query misses, repeated-query hits, invalidation
after a durable record update, unchanged semantic ranking, and preservation of
the existing memory safety and scope boundaries. The normal release gate
remains required.
