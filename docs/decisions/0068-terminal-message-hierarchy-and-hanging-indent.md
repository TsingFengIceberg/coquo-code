# 0068: Terminal Message Hierarchy and Hanging Indent

- Status: Accepted
- Date: 2026-07-29
- Scope: TTY role separation, wrapped-line alignment, Host metadata styling, and Markdown stream presentation

## Context

The persistent inline frontend made the next prompt continuously available, but its scrollback still depended on loosely coupled marker and body writes. A streamed Markdown response could emit the assistant `• ` marker before any render-safe body existed. The application could redraw between those writes, visually separating or obscuring the marker. Submitted multiline prompts and terminal-wrapped role text also lacked one shared body-column contract, while ordinary tool, context, usage, and slash-command information competed visually with user and assistant messages.

Terminal protocols provide colors, intensity, indentation, and text attributes, but they do not provide a portable way for an application to select a smaller font for individual lines. Leonervis therefore needs a hierarchy that works with ANSI styling disabled and does not weaken safety-critical approval, warning, failure, cancellation, partial, or outcome-unknown information.

## Decision

Real TTY conversation blocks use stable role prefixes: submitted user text starts with `› ` and assistant text starts with `• `. Both reserve a two-column prefix and render every explicit or width-induced continuation from the body column. The live prompt editor already supplies the same two-column continuation prefix. A new visible conversation message block is separated from the preceding block by a dim short rule, indented by two columns and bounded to roughly one third of terminal width or 24 cells rather than spanning the screen. Submitted plain text is escaped and wrapped by display width; assistant Markdown renders against terminal width minus the role prefix before every rendered line receives its hanging indent.

The assistant marker and first visible Markdown body are emitted in one frontend write. Incomplete streaming Markdown remains buffered without exposing a marker-only line. Later render-safe chunks use the continuation prefix, and each companion or final assistant document starts a distinct `• ` block. User and assistant regions retain a blank-line boundary while the frontend remains inline rather than entering an alternate screen.

Host-generated tool, context, usage, compaction, status, and slash-command messages are indented as secondary blocks. Routine informational and successful Host messages use dim or dim-green ANSI styling when color is enabled. Warnings and approval-related facts remain yellow or otherwise explicit, and errors remain red. `NO_COLOR` removes styling but preserves indentation, labels, role markers, separators, and hanging alignment. Approval previews retain their existing diff colors and exact risk content.

One-shot stdout/stderr separation, redirected output, injected streams without the role UI, raw Markdown pipe behavior, and durable Session/Audit records remain unchanged. This is a Host-only presentation change: canonical system prompt v21, provider adapter contract v24, the 21-tool order and schemas, ToolArguments v1, ActionIdentity v1, Effective Context identities, and all Session, compaction, turn, and Action Audit schemas remain unchanged.

## Explicit non-goals

- per-line font-size control, which portable terminal protocols do not support;
- alternate-screen panels, retained widgets, mouse interaction, or collapsible tool output;
- changing model-visible role content, provider streaming semantics, tool execution, permissions, approval identity, or durable records;
- muting warnings, denials, failures, partial results, cancellation, or durability uncertainty;
- reconstructing history from ephemeral frontend output.

## Verification

Deterministic tests cover display-width wrapping, explicit multiline input, Markdown body-width reservation, marker deferral until visible streamed content exists, continuation indentation, muted routine Host events, unmuted failure structure, persistent-frontend role output, no-color behavior, and all previous presentation contracts. The complete offline release gate remains required.
