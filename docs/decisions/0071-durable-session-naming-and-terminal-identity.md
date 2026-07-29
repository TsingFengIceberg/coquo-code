# 0071: Durable Session Naming and Terminal Identity

- Status: Accepted
- Date: 2026-07-29
- Scope: Session display-name persistence, bounded provider-generated titles, rename controls, and terminal identity

## Context

Workspace Sessions were identified only by UUID. The identifier is stable and remains necessary for exact resume, but it is difficult to distinguish several conversations in `/session list` or the persistent terminal toolbar. A useful display name must survive restart and resume, while a failed or cancelled first prompt must not become the durable identity of an otherwise empty Session.

The first user message contains enough task context for a model to produce a more useful title than deterministic Host truncation. That convenience must not expose tools to a title request, create unbounded provider cost, exceed the existing per-turn provider-invocation ceiling, permit duplicate automatic names, or commit a title separately from the turn that justifies it.

## Decision

New transcripts use `session_header` schema v2 and persist a bounded default name allocated as `New session N` while holding the workspace Session-directory lock. Existing `session_header` v1 records remain readable, are never rewritten, and use `New session <first-eight-session-id-characters>` while empty.

After the first normal assistant response succeeds, but before that first turn is durably committed, the pinned turn runtime may issue up to three dedicated Session-title requests. The versioned title prompt treats a JSON payload containing at most 4096 UTF-8 bytes of the first user message and prior rejected titles as untrusted data. Each request exposes no tools, uses a fixed 512-token output reserve so providers that count hidden reasoning against output can complete, and still accepts only one plain-text line bounded to 48 characters and 160 UTF-8 bytes. Anthropic and OpenAI-compatible adapters provide explicit no-tools count/create projections for this operation.

Automatic names are compared case-insensitively with every other Session in the workspace. A conflicting valid model title is added to the next request's rejected-title set. Three conflicts or malformed responses exhaust model attempts; a provider or preflight failure stops retries immediately. The Host then derives a bounded deterministic fallback and appends the first available stable suffix such as ` (2)`. Cancellation remains a `BaseException` and is never converted into fallback success.

Title generations and ordinary AgentLoop generations share the existing maximum of 24 provider invocations per user turn. The Host counts both the neutral committed-turn structure and available usage records, so fake and real runtimes cannot use title generation to exceed that ceiling. If the ordinary loop has already consumed all 24 invocations, naming goes directly to Host fallback.

The first turn and its chosen title are one failure-atomic persistence unit: new `turn_committed` schema v7 carries nullable paired `session_name` and `session_name_source` fields, and a first-turn title uses source `model` or `fallback`. The same record carries ordinary and title provider usage in invocation order. A title cannot appear on a later turn or coexist partially, and a failed append leaves both turn and title uncommitted. Existing `turn_committed` v1-v6 records replay unchanged; legacy unnamed turns retain the prior deterministic Host-derived display-name fallback without transcript rewriting.

`/session rename <name>` appends `session_named` v1 with source `manual`; `/session rename --auto` restores the first-turn model/fallback title, or the legacy deterministic title when no v7 title exists. Latest valid rename wins. Names normalize visible whitespace, reject empty values and Unicode control/format/line-separator characters, and are limited to 80 characters and 256 UTF-8 bytes. Names are display metadata, not resume selectors; the complete UUID remains exact identity.

`/session show`, `/session list`, and the real-TTY bottom toolbar display the Session name. The toolbar refreshes after a successful turn, rename, new Session, or resume. Real-TTY slash commands with Host output are echoed as one complete block containing the `›` input, the indented Host result, and one low-intensity separator; `/clear` remains a direct screen operation and `/exit` does not manufacture an empty result block.

## Compatibility and contracts

`session_header` v1/v2, `session_named` v1, and `turn_committed` v1-v7 replay are closed and fail-fast. No transcript is rewritten for migration. Names and title-request content do not enter ordinary provider history, the canonical Agent system prompt, tool contracts, Action Audit, compaction summaries, checkpoints, or Effective Context identity.

The dedicated title projection changes the provider adapter contract from v24 to v25. The canonical Agent system prompt remains v21, the 21 model-visible tools and their order remain unchanged, and ToolArguments v1, ActionIdentity v1, `turn_failed` v2, Action Audit v1, `context_compacted` v4, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged. The title prompt is an independent versioned Host contract.

## Explicit non-goals

- name-based resume, fuzzy search, tags, folders, pinning, deletion, or export;
- injecting a Session name or title request into ordinary model-visible history;
- unlimited duplicate retries or guarantees that manual names are globally unique;
- background renaming, renaming after every turn, or a separate paid title request after commit;
- rewriting legacy headers or turns solely to normalize their display names.

## Verification

Deterministic tests cover title-prompt identity and bounds, no-tools native projections, 512-token reserve, text-only adapter parsing, malformed candidates, model conflicts and rejected-title retries, three-attempt numbered fallback, the shared 24-invocation ceiling, cancellation and provider-failure fallback boundaries, title usage accounting, v7 round trip and first-turn-only validation, v6 compatibility, case-insensitive atomic conflict checks, rename replay, unchanged Effective Context identity, slash help/completion, complete slash visual blocks, Session rendering, bounded toolbar presentation, and live toolbar refresh. The complete offline release gate remains required; no credential, network request, provider endpoint, or API cost is used.
