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
`recall=off|on`, `write=off|propose|auto`, `retrieval=text|semantic`, `tools`, and the fixed `local`
provider name. It defaults to disabled. Effective recall, write, and tool
exposure are forced off while the master switch is disabled. This slice exposes
Host-only `coquo memory status|configure|enable|disable` commands and explicit
record administration (`add|list|show|search|confirm|update|stale|delete`).
New configuration writes use schema v2. The reader continues to accept the
original v1 shape and the transitional v1 shape that already carried
`retrieval`, supplies `retrieval=text` when absent, and does not rewrite a
legacy file until the user explicitly changes configuration.
Invalid or unreadable configuration fails prompt/tool projection before a
Provider invocation; it is never silently treated as a disabled configuration.

The explicit commands do not invoke a Provider, change a Session transcript,
or add model-visible tools. Bounded recall runs at turn preparation: only
confirmed records in Host-authorized scopes are selected when both `enabled`
and `recall=on` are effective. Recall is frozen in the prepared turn, rendered
as a separate `[UNTRUSTED MEMORY EVIDENCE]` user block, and never enters
transcript history or permission state. The replaceable retrieval boundary
supports deterministic text retrieval and a `semantic` mode that reports a
bounded `text-fallback` until a local embedding backend is explicitly added.
Candidate and stale records are queried without recall mutation, final evidence
is deduplicated and bounded, and each selected confirmed record receives at
most one durable `recalled` event per prepared turn.

Explicit `remember:`/`remember that`/`请记住` requests are considered only
after a successful `turn_committed`. `write=propose` creates a candidate and
`write=auto` confirms it; unmarked model output is never extracted. Exact
deduplication, candidate consolidation, conflict enumeration, reinforcement,
stale review, and capacity eviction preserve append-only event history and
record a bounded reason. Confirmed conflicts are never silently overwritten.
Automatic extraction is itself a `workspace-create` Host Action after the Turn
commit. Read-only mode denies it, `approval=ask` remains authoritative, and the
PermissionGate decision and any execution/partial outcome are durable in the
Session Action Audit. A candidate that was durably created before an automatic
confirmation failure is reported as partial rather than rolled back or hidden.

Memory access is a Host-owned `MemoryAccessContext`, not a model capability.
The ordinary Host receives the current workspace scope; an active Host Task
may add its Task scope, and a Team scope appears only after an explicit
Host-side grant. Isolated Child runtimes receive no read or write scopes and
cannot extract memory. Scope failures fail closed, and memory data never
grants tools, permissions, approvals, or execution authority. `MemoryProvider`
is a replaceable Host-owned backend boundary; only the local implementation is
currently selectable. Model-visible `memory_search`, `memory_add`,
`memory_update`, and `memory_delete` appear only when both the master and
`tools` switches are effective and use the existing PermissionGate, Action
Audit, and untrusted ToolResult path. A model add creates a candidate and does
not silently confirm it; Child ToolSets never contain these tools.
Team grant/revoke controls are content-free Host Actions. Their successful
outcomes are replayed on Session resume only after the Team still exists and
the resumed Session is still its owner; missing or mismatched ownership drops
the grant. The REPL exposes only the Host-side `/team memory grant|revoke
<team-id>` routes; these controls are not model tools. Model-created records retain trusted Session/turn provenance, and
bounded update/delete reasons remain in the append-only memory event.

Recall, extraction, lifecycle changes, and memory-tool calls emit bounded
process-local content-free observations containing operation, outcome, Host
actor, scope kinds, counts, degradation, and bounded reasons. They never store
memory content, Provider request bodies, credentials, or tokens. Manual Host
administration remains available for inspection and seeding.
Consolidation validates every named duplicate before its first append. An I/O
failure after one of its events becomes durable is reported and observed as a
partial outcome. Event count is tracked independently from record count and is
checked before append, so reaching the event bound cannot create a log that its
own replay rejects. Automatic capacity eviction also emits a content-free
observation.

## Compatibility and security

- Existing Session, Task, Child, Team, Provider, tool, and observation schemas do
  not change.
- Memory config v1 remains readable without rewrite; schema v2 is used for new
  writes and adds the explicit retrieval field.
- Memory content is future untrusted evidence, never system authority, a
  permission grant, or execution proof.
- Scope IDs are explicit; no scope is inferred from file names, model text, or
  wall-clock order.
- Scope membership is supplied by Host Task/Team/Child runtime identities;
  Child defaults to an empty capability and Team access requires an explicit
  revocable Host grant.
- The local log and observation ledger contain no credentials, Provider request
  bodies, or unbounded model output.
- A disabled configuration produces no effective automatic recall/write/tool
  capability; later runtime integration must preserve that gate.
- Recall is bounded to eight records, 4 KiB per rendered record, and 16 KiB in
  total. Query failure is surfaced before Provider invocation rather than
  silently changing the requested context.
- Memory evidence is never a system prompt, project instruction, permission
  decision, action precondition, or proof that a Host action occurred.

## Verification

Deterministic tests cover default-off and effective switch gating, atomic config
replay, candidate confirmation/update/search/recall, scope filtering, terminal
state rejection, corrupt-log rejection, the Host-only CLI management surface,
turn preparation recall, untrusted evidence rendering, transcript exclusion,
and effective-context identity version 29/30. Additional tests cover Host
scope capability, Child fail-closed behavior, model-tool gating and audit,
provider fallback, observation bounds/privacy, and recovery boundaries.
Regression coverage also proves legacy config replay, pre-append event limits,
confirmed-only one-touch recall, extraction denial/approval audit, Team grant
resume/revoke, consolidation prevalidation/partial truth, mutation provenance
and reasons, and exact Anthropic/OpenAI Chat/OpenAI Responses memory projection
and count/create parity for ordinary and compacted effective context.

## References

- [Memory reference comparison](../references/memory-reference-comparison.md)
- [DeerFlow](../../learning-submodules/deer-flow)
- [Hermes Agent](../../learning-submodules/hermes-agent)
