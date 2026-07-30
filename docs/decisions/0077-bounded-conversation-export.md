# 0077: Bounded Conversation-only Session Export

- Status: Accepted
- Date: 2026-07-30
- Scope: read-only Markdown and JSON conversation export to stdout

## Context

Users need a portable view of a Session without copying an internal JSONL transcript whose records include runtime provenance, tool payloads, audit state, and implementation schemas. Export must clearly distinguish a readable conversation from a forensic transcript.

## Decision

Add standalone `session export [selector] --format markdown|json` and REPL `/session export <latest|complete-UUID> [markdown|json]`. Both formats include Session identity and every complete turn's final user and final assistant text. Markdown uses indented literal blocks; JSON uses an export-local closed `schema_version: 1`. Terminal controls are escaped before serialization.

Export is complete or fails: at most 1,000 turns and 1 MiB of selected text may be exported, and rendered output may not exceed 2 MiB. It does not silently truncate. Output goes only to stdout or the REPL result; this slice creates no user-selected file and therefore adds no workspace-write or approval path.

Tool companion text, ToolUse, ToolResult, tool ledgers, Action Audit, usage, failures, compaction summaries, raw records, credentials, and provider payloads are excluded. The append-only transcript remains the complete local audit source.

## Compatibility

Export is a Host-only strict replay projection and changes no persisted schema, model contract, context identity, current Session, runtime, `latest`, or picker snapshot. System prompt v21, adapter contract v25, and the 21-tool catalog remain unchanged.

## Non-goals

- full forensic or raw-transcript export;
- writing an export path, uploading, sharing, importing, or round-trip restoration;
- Markdown execution or terminal-control passthrough.
