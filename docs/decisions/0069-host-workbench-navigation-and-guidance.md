# 0069: Host Workbench Navigation and Failure Guidance

- Status: Accepted
- Date: 2026-07-29
- Scope: Session browsing, categorized slash help, Action Audit filters, known-failure guidance, and truthful terminal activity phases

## Context

The persistent inline frontend kept input available and exposed live activity, but several Host workbench tasks still required remembering syntax or scanning unfiltered output. `/session list` returned every workspace Session without a bound or runtime clue, `/help` was one dense command sentence, and `/actions` filtered only by final count. Known failures stated what happened but often left the user to infer a safe next step. The toolbar also collapsed most runtime work into `Working`, `Running tool`, or `Completing`; in particular, it had no event at the actual durable turn-append boundary.

These are Host navigation and presentation problems. They do not justify changing model instructions, tool schemas, provider parsing, persisted Session truth, or tool permissions.

## Decision

`/session list` now accepts an order-independent bounded count, `open|closed`, and exact `model=<name>` filter. Results remain newest-first, show current/latest markers, and include the redacted provider/model binding stored with each Session. Filtering reads already validated `SessionInfo` values and does not open, repair, resume, or rewrite a transcript. `/resume` continues to require `latest` or a complete Session ID; unstable list positions and ambiguous ID prefixes are not selectors.

`/help` now presents six groups and accepts exactly `session`, `tools`, `git`, `context`, `provider`, or `input`. Static completion exposes those topics. `/actions` accepts an order-independent bounded count plus exact `status=<status>` and `tool=<name>` filters over replayed Action Audit state. It does not parse model text, raw audit JSON, or result prose, and it does not add export, repair, retry, or mutation behavior.

Known turn and Host-command failure classes receive bounded `Next:` guidance. Guidance is selected only from trusted exception classifications and normalized provider metadata. It never claims that a retry occurred, never retries automatically, never claims rollback, and preserves the distinction between an uncommitted turn and tool side effects already recorded in Action Audit. Unknown failures receive only conservative inspection guidance.

The terminal reducer now maps existing typed events to explicit phases such as preparing a provider request, running a named canonical tool, processing a tool result, compacting context, recording usage, and finalizing a turn. `ProjectSession` emits a new process-local content-free `TurnCommitStarted` immediately before `SessionWriter.append_turn`; this is the sole basis for the `Saving Session` toolbar state. Event-sink failure remains best-effort and cannot prevent or alter the append. The post-return frontend phase is called `Finalizing turn`, not `Saving Session`, because persistence has already completed by then.

## Compatibility and contracts

All additions are Host-only queries, ephemeral events, or terminal text. No new record is appended for help, Session listing, audit filtering, guidance, or activity phases. Existing transcripts and Session selectors remain compatible. The canonical system prompt remains v21, provider adapter contract remains v24, the 21 model-visible tools and their order remain unchanged, and ToolArguments v1, ActionIdentity v1, `turn_committed` v6, `turn_failed` v2, Action Audit v1, `context_compacted` v4, and Effective Context `ctx-v3`/`ctx-v4` do not change.

## Explicit non-goals

- fuzzy Session search, list-index resume, transcript repair, deletion, export, or cross-workspace browsing;
- Action Audit JSON export, free-text search, automatic retry, replayed execution, or result-content disclosure;
- provider-internal progress percentages, background turns, queued prompts, or parallel work;
- persisted frontend events, full-screen retained panels, collapsible output, or mouse interaction;
- changing model-visible failure results, permission decisions, approval identity, execution, durability, or compaction behavior.

## Verification

Deterministic tests cover help groups, Session count/state/model filtering, Action Audit status/tool filtering, conservative provider/context guidance, typed provider/tool/result/commit phases, exact pre-append commit notification, fallback REPL output, one-shot failure output, and existing persistent frontend behavior. The complete offline release gate remains required; no credential, network request, provider endpoint, or API cost is used.
