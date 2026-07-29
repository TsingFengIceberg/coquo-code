# 0067: Persistent Inline Terminal Frontend

- Status: Accepted
- Date: 2026-07-29
- Scope: interactive TTY ownership, background turns, approval mediation, cooperative cancellation, and plain-stream compatibility

## Context

The previous REPL created one `PromptSession`, waited for submission, synchronously ran `ProjectSession.prompt()`, let provider/tool event sinks write directly to stdout, and only then created the next prompt. The input marker and bottom toolbar therefore disappeared throughout generation and tool execution. Moving the turn to a thread without redesigning approval and cancellation would be unsafe: the old approval handler read stdin in the worker, while command cancellation depended on same-thread `KeyboardInterrupt`.

The pinned Claw-Code reference at `learning-submodules/claw-code` commit `4ea31c1bc91c4e9bcbd67d51c550c01e127e6d0d` was reviewed. Its current Rust CLI also follows a synchronous `read_line -> run_turn` flow and writes provider/tool output directly; `rust/TUI-ENHANCEMENT-PLAN.md` is an aspirational document rather than runtime truth. Leonervis borrows only the conceptual input/render/runtime separation, inline default, safe Markdown boundaries, and deterministic non-TTY fallback. No reference code is copied and the submodule is not a runtime dependency.

## Decision

Real interactive stdin/stdout now use one non-full-screen `prompt_toolkit.Application`. It owns the multiline buffer, slash completion, Session-derived history, fixed bottom toolbar, approval answer state, key bindings, resize/redraw behavior, and inline scrollback writes. Submitting a turn clears the active buffer immediately, leaves a new prompt and toolbar visible, and starts one non-daemon background worker. Users may edit one draft while busy; Enter does not queue or insert another message and retains the draft with a busy status.

The frontend uses a closed `TerminalViewState` and typed local events reduced by a pure transition function. A bounded process-local queue serializes worker events to the application. Consecutive assistant deltas may be coalesced; tool, approval, failure, cancellation, completion, and durable final facts are never discarded. The UI thread is the only TTY writer. Existing terminal event presentation and Markdown rendering operate against memory, then the application emits safe rendered text through inline `run_in_terminal`. Renderer failure remains best-effort and cannot affect provider/tool execution, Action Audit, or turn commit.

`TerminalApprovalBroker` preserves the synchronous `ActionCoordinator` callback while removing stdin ownership from the worker. It publishes the exact immutable `HumanApprovalRequest`, blocks on a thread-safe single resolution, and lets the application accept only y/yes, n/no/default, or c/cancel. The user's draft is saved while approval owns the input buffer and restored after resolution or cancellation. One-shot prompts retain noninteractive cancellation and injected/non-TTY stream tests retain the prior stream handler.

Each background turn receives one `TurnCancellation`. Ctrl-C requests cancellation; Ctrl-D requests cancellation and exits only after worker cleanup. The token is checked before and after provider invocations, at every streamed text delta, around tool dispatch, during approval waits, and during `run_command`. Command waiting now polls the token and reuses bounded TERM-to-KILL process-group cleanup. A blocking non-stream provider or SDK call can only observe cancellation after it returns; a provider stream can normally stop at the next delivered chunk. No unsafe asynchronous thread exception injection is used.

Final display follows durable truth. `ProjectSession.prompt()` returns only after the existing durable commit, and the worker publishes completion afterward. Cancellation never installs partial assistant text as a turn. Tool side effects and Action Audit records already completed before cancellation remain truthful and are not rolled back. Action Audit finish persistence failure continues to produce only `outcome-unknown`. Exit never closes the Session while the worker remains active.

## Compatibility and contracts

One-shot `prompt`, redirects, injected streams, and non-TTY stdout/stderr retain their existing synchronous byte-oriented paths. Slash commands remain Host-owned; while a model turn is active, Enter cannot dispatch either a prompt or a state-mutating slash command. Full-screen TUI behavior is not introduced.

This is a Host-only execution and presentation change. The canonical system prompt remains v21, provider adapter contract remains v24, the 21 model-visible tools and order remain unchanged, and ToolArguments v1, ActionIdentity v1, `turn_committed` v6, `turn_failed` v2, Action Audit v1, `context_compacted` v4, and Effective Context `ctx-v3`/`ctx-v4` do not change. Existing Sessions require no rewrite.

## Explicit non-goals

- queued prompts, message insertion, subagents, background tasks, or parallel turns;
- alternate-screen/full-screen TUI, mouse interaction, retained conversation panels, or collapsible output;
- force-cancelling arbitrary blocking provider SDK calls or claiming bounded network cancellation;
- changing permission decisions, approval identity, action hard bounds, Session durability, or compaction transactions;
- replacing one-shot stdout/stderr contracts or persisting live frontend events;
- copying Claw-Code implementation details or making a reference repository a runtime dependency.

## Verification

Deterministic tests cover reducer legality, delta coalescing, persistent prompt lifecycle with pipe input, busy draft retention, no second prompt dispatch, Ctrl-D cleanup, cancellation token behavior, process-group cancellation, and all previous REPL/approval/Markdown contracts. The full offline release gate remains required. No credential, network request, real provider, or API cost is used.
