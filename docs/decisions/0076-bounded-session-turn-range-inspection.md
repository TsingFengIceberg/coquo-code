# 0076: Bounded Session Turn-range Inspection

- Status: Accepted
- Date: 2026-07-30
- Scope: explicit 1-based navigation within complete Session turns

## Context

The latest-turn preview cannot inspect an older location returned by cross-Session search. Reinterpreting the existing preview count as an offset would make its compact syntax ambiguous.

## Decision

Add standalone `session turns <selector> <start> --count N` and REPL `/session turns <latest|complete-UUID> <start> [count]`. Start is a positive 1-based complete-turn number and count is one through ten, defaulting to three. The result retains chronological numbering and shows only final user and assistant text with terminal controls escaped and total rendering capped at 32 KiB.

The command uses existing-only strict replay with tail repair disabled. A start beyond the committed turn count is rejected; an empty Session accepts only start one and returns no turns. Tool causality, audit, usage, and compaction data remain available through their existing dedicated views.

## Compatibility

This Host-only read projection changes no model-visible behavior, provider request, transcript record, context representation, picker identity, current Session, or `latest` pointer. System prompt v21, adapter contract v25, and the 21-tool catalog remain unchanged.

## Non-goals

- mutable rewind, resume-at-turn, or deletion of later history;
- arbitrary raw record ranges or partial tool-use/result display;
- unbounded scrolling or provider-generated summaries.
