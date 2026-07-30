# 0075: Bounded Cross-Session Final-text Search

- Status: Accepted
- Date: 2026-07-30
- Scope: Host-only literal search across durable Session final dialogue

## Context

Names, filters, pinning, and preview make Sessions browsable, but a user who remembers a phrase still has to inspect candidates manually. Searching raw transcripts would also expose tool payloads, Action Audit, usage, and compaction summaries that are outside the conversation-oriented lookup goal.

## Decision

Add standalone `session search <query> --limit N` and REPL `/session search <query>`. Search is case-sensitive literal matching over each complete turn's final user and final assistant text, independently per logical line. Matches report the Session name and full UUID, 1-based turn, role, line number, and a bounded excerpt. They do not search tool companion text, ToolResult, Action Audit, provider usage, failures, or compaction summaries.

The query is printable single-line text bounded to 256 characters and 1024 UTF-8 bytes. One search scans at most 10,000 directory entries, selects at most 100 canonical transcript candidates in stable UUID order, reads at most 16 MiB of transcript data, returns at most 100 matches, bounds each excerpt to 320 characters and 2048 bytes, and renders at most 32 KiB. Candidate, byte, match, or rendering truncation is explicit and never proves omitted content has no match.

Each selected transcript uses existing-only no-symlink validation and strict replay with repair disabled. Any selected corruption fails the search rather than being skipped. Search takes no writer lease, invokes no provider, appends no record, and changes neither current Session nor `latest`, runtime, history, Effective Context, or picker snapshot.

## Compatibility

This is a Host-only read projection. Canonical system prompt remains v21, provider adapter contract remains v25, the 21 model-visible tools and their order remain unchanged, and every transcript, compaction, Action Audit, ToolArguments, and Effective Context schema remains unchanged.

## Non-goals

- regex, fuzzy, semantic, case-insensitive, indexed, or cross-workspace search;
- searching raw tool, audit, usage, summary, or provider payload text;
- name-based resume identity or automatic Session switching.
