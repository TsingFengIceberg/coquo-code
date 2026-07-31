# 0100: Persistent Activity Indicator and Task Output Alignment

## Status

Accepted.

## Context

The persistent TTY already exposed typed runtime phases in its bottom toolbar, but that compact label was easy to miss during a long provider request or a multi-Stage Task run. Task orchestration also exercised a stream-recovery path that rendered a complete assistant response without the normal role prefix. In the plain streaming path, explicit newlines after the first assistant line likewise lacked the two-space hanging indent. Those paths could make model text appear attached to the terminal edge even though ordinary Markdown and Host trace rendering already preserved visual ownership.

The correction must improve live feedback without inventing durable progress, exposing sensitive tool arguments, changing Task execution, or allowing terminal rendering failures to affect model, tool, permission, audit, or Session behavior.

## Decision

Add one conditional text-only activity row immediately above the persistent prompt editor. It is visible only while a turn is starting or active, contains no symbol or animation, and takes its text only from the typed frontend reducer, including provider preparation, response generation, tool execution, approval, compaction, Session persistence, and Task lifecycle phases. The row disappears in the idle `Ready` state. Labels are terminal-safe, bounded, and contain no file content, command arguments, provider payload, or Task body.

Render every complete assistant response through the same role-aware hanging-prefix path, including the defensive stream-mismatch fallback used during Task orchestration. Markdown and plain complete responses start with `• ` and continue under the body column. Plain streamed responses also restore the two-space continuation prefix after every explicit model newline. Existing display-width wrapping for Markdown, Host blocks, and `  │ ` traces remains authoritative for terminal-edge wrapping.

The activity row is ephemeral prompt-toolkit state. It is not written to stdout as conversation content and does not enter Session transcripts, Task transcripts, Action Audit, provider history, compaction, Effective Context, or Eval evidence. Durable records remain the source of truth after cancellation, failure, resume, or crash.

## Compatibility And Versions

This is Host-only terminal presentation. Canonical system prompt remains v29, provider adapter contract remains v31, ToolArguments remains v1, Effective Context remains `ctx-v5`/`ctx-v6`, and all Session, Task, Action Audit, compaction, tool, permission, and budget contracts remain unchanged.

## Consequences

- A user can see active work near the newest prompt without relying only on the bottom toolbar.
- Task Stage transitions retain specific activity labels while ordinary turns continue to start as `Preparing turn`.
- Assistant fallback and explicit multiline plain output no longer lose role alignment.
- The activity label proves only that the foreground UI is processing an active turn; it is not execution, completion, or durability evidence.
- Non-TTY output, redirected Markdown behavior, provider traffic, and runtime cancellation semantics do not change.
