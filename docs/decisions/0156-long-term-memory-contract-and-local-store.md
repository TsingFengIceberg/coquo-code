# ADR 0156: Long-Term Memory Contract and Local Store

## Status

Accepted

## Context

Coquo already has project instructions, durable Session history, context
compaction, and durable Task/Child/Team execution records. Those are different
artifacts and must not be silently treated as one semantic-memory database.
Long-term memory needs an explicit scope, lifecycle, and Host-owned switch
before model-driven recall or extraction is introduced.

DeerFlow provides the useful fact-lifecycle reference (candidate facts,
confirmation, correction, staleness, and bounded retention). Hermes provides the
useful provider/lifecycle reference, but this slice intentionally does not add a
remote backend or a runtime Provider abstraction.

## Decision

The first Memory contract is a bounded `MemoryRecord` with a UUID identity,
`user|workspace|task|team|child` scope, a portable scope ID, content, category,
confidence, source Session/turn provenance, timestamps, and one of
`candidate|confirmed|stale|deleted|evicted` statuses. Confirmed records require
an explicit confirmation timestamp. Deleted and evicted records are terminal;
records are never silently rewritten or removed from the local log.

The local backend is an append-only JSONL event log at
`.coquo/memory/events.jsonl`, protected by a workspace-local lock and bounded by
record, event, and log limits. Every event stores a complete current record and
is fsynced before the in-memory replay view changes. Replay rejects unknown
fields, invalid schema, malformed records, duplicate creation, status changes
after terminal state, partial/oversized events, and path/symlink violations.

Memory policy is Host configuration, separate from Provider profiles. The
workspace-local `.coquo/memory/config.json` has a master `enabled` switch,
`recall=off|on`, `write=off|propose|auto`, `tools`, and the fixed `local`
provider name. It defaults to disabled. Effective recall, write, and tool
exposure are forced off while the master switch is disabled. This slice exposes
Host-only `coquo memory status|configure|enable|disable` commands and explicit
record administration (`add|list|show|search|confirm|update|stale|delete`).

The explicit commands do not invoke a Provider, change a Session transcript,
or add model-visible tools. Automatic extraction, automatic recall, remote
providers, and Child/Team sharing are later slices. Manual Host administration
is intentionally available so a user can inspect and seed the local store while
the automatic policy remains disabled.

## Compatibility and security

- Existing Session, Task, Child, Team, Provider, tool, and observation schemas do
  not change.
- Memory content is future untrusted evidence, never system authority, a
  permission grant, or execution proof.
- Scope IDs are explicit; no scope is inferred from file names, model text, or
  wall-clock order.
- The local log contains no credentials, Provider request bodies, or automatic
  model output in this slice.
- A disabled configuration produces no effective automatic recall/write/tool
  capability; later runtime integration must preserve that gate.

## Verification

Deterministic tests cover default-off and effective switch gating, atomic config
replay, candidate confirmation/update/search/recall, scope filtering, terminal
state rejection, corrupt-log rejection, and the Host-only CLI management
surface. The complete test suite remains the release gate for later runtime
integration.

## References

- [Memory reference comparison](../references/memory-reference-comparison.md)
- [DeerFlow](../../learning-submodules/deer-flow)
- [Hermes Agent](../../learning-submodules/hermes-agent)
