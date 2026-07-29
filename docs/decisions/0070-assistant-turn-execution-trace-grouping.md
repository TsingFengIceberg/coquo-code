# 0070: Assistant Turn Execution Trace Grouping

- Status: Accepted
- Date: 2026-07-29
- Scope: TTY assistant-turn grouping, Host execution rails, and conversation separators

## Context

The role hierarchy introduced by ADR 0068 separated user text, assistant text, and secondary Host information, but it treated every assistant document as a new top-level message block. The event sink therefore inserted a conversation separator before companion text and again before final text. Context preflight, tool activity, informed approval, usage, and final assistant text from one user turn appeared as unrelated blocks even though they were one causal AgentLoop execution.

These events do not all have the same authority. Companion and final text are provider-authored assistant content. Context meters, tool lifecycle lines, approval previews, failure diagnostics, ledgers, and usage summaries are Host-owned observations. They must not be relabeled as model speech, but they should remain visually attached to the assistant turn whose execution produced them.

## Decision

The persistent real-TTY frontend presents one user submission and its complete AgentLoop execution as one conversation turn. It inserts one blank visual boundary before the first visible turn output, whether that output is assistant text or a Host fact. Every provider-authored assistant document retains the `• ` role marker. Host-owned execution facts within that turn use a `  │ ` rail on every logical line, preserving semantic color and approval diff styling. This includes context preflight, tool start/result, approval request and preview, usage, compaction, ledger, cancellation, and failure output. If a provider requests a tool without companion text, the frontend begins directly with the railed Host fact and does not invent an empty assistant marker.

Switching between assistant text and Host events no longer creates a conversation separator. The frontend emits one low-intensity short separator only after `TurnFinished`, after final assistant text or terminal failure output and before the next live `›` prompt. Multiple assistant iterations and tool continuations therefore remain in one visual turn while their authority remains explicit.

Slash-command results remain ordinary Host blocks because they are handled outside a model turn. One-shot, redirected, non-TTY, and injected-stream paths retain their existing output contracts. The rail is ephemeral presentation only and is never persisted or projected to a provider.

## Compatibility and contracts

This is a Host-only terminal presentation change. It does not alter provider responses, AgentLoop causality, permission or approval decisions, tool execution, Action Audit, Session replay, transcript content, or Effective Context identity. Canonical system prompt remains v21, provider adapter contract remains v24, and the 21-tool catalog, ToolArguments v1, ActionIdentity v1, `turn_committed` v6, `turn_failed` v2, `context_compacted` v4, and `ctx-v3`/`ctx-v4` representations remain unchanged.

## Explicit non-goals

- representing Host execution facts as assistant-authored text;
- adding alternate-screen panels, collapsible traces, mouse interaction, or background turns;
- changing approval content, redaction, durability, cancellation, or failure atomicity;
- reconstructing durable history from ephemeral terminal layout.

## Verification

Deterministic tests cover companion text, tool events, multiline informed approval previews, final streamed text, one separator per completed turn, cancellation/failure trace styling, Markdown marker deferral, no-color rails, and unchanged role-free sink behavior. The complete offline release gate remains required.
