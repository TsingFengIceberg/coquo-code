# 0093: TTY Host Wrapping and Process-local Command History

## Status

Accepted.

## Context

The persistent TTY already gave user and assistant bodies hanging indentation, but dim Host blocks and `  │ ` execution traces only prefixed explicit logical lines. When one long line reached the terminal edge, terminal-owned wrapping resumed at column zero and visually detached the continuation from its Host block. The prompt buffer also rebuilt history exclusively from committed Session turns after every slash command. Because slash commands intentionally do not enter the Session transcript, pressing Up immediately after one could not recall it.

The correction must remain presentation-only. It must not persist Host commands as conversation, expose them to a provider, alter Session replay, or turn display wrapping into runtime evidence.

## Decision

When the persistent TTY knows its bounded current width, render plain Host blocks into display-width-aware visual lines before styling. Every Host-block continuation receives the two-space prefix, and every in-Turn trace continuation receives the complete `  │ ` rail. Existing logical newlines, control escaping, warning/error emphasis, and non-TTY output remain intact. Approval previews keep their existing styled rendering path rather than feeding ANSI-decorated text through the plain wrapper.

Initialize the TTY history from the current Session's bounded committed user prompts, then record every accepted ordinary prompt and single-line slash command in a bounded process-local history. Up/Down and Ctrl-R use that shared history. Slash commands remain absent from provider history, Session JSONL, Action Audit, compaction, and Effective Context. They disappear at process exit.

When a slash command changes the current Session, rebuild the ordinary-prompt base from the target Session and retain the command that performed the switch. This preserves cross-Session prompt isolation without making the switch command impossible to recall.

## Compatibility And Versions

This is Host-only terminal presentation and volatile input state. Canonical system prompt remains v26, provider adapter contract remains v26, all model-visible tool schemas and budgets remain unchanged, and no Session, Task, Action Audit, compaction, or Effective Context representation version changes.

## Consequences

- Long dim status and execution lines remain visibly attached to their Host block after wrapping.
- Up/Down and Ctrl-R can recall slash commands during the same process.
- Resuming a process restores committed user prompts but cannot restore earlier process-local slash commands.
- Session switching continues to isolate committed prompts from the previous Session.
- No terminal history entry becomes authority, execution evidence, approval, or durable conversation merely because it is recallable.
