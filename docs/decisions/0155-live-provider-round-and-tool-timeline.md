# ADR 0155: Live Provider Round and Tool Timeline

## Status

Accepted

## Context

One user prompt is one durable Session Turn, but its Agent loop may perform many
Provider invocations and tool executions before the final response commits. The
terminal already received text deltas and tool lifecycle events, yet a long
Provider wait could remain visually under `Preparing turn`, followed by a burst
of fast tool lines. That presentation hid the distinction between a logical
Turn and its internal model rounds.

Claw-Code's documented streaming contract distinguishes run/turn starts,
assistant deltas, completed assistant rounds, and tool results. Coquo adopts
that conceptual separation for its existing CLI architecture; it does not copy
the reference TUI, prompts, wire format, or implementation.

## Decision

Each Agent-loop Provider request emits process-local
`ProviderInvocationStarted` before preflight or Provider I/O and exactly one
`ProviderInvocationFinished` after a final-text response, tool request, failure,
or cancellation. Events carry only the bounded invocation index, limit,
content-free purpose/outcome, tool count, and Host-measured elapsed
milliseconds. Elapsed duration is volatile observation metadata, not
Provider-reported usage. Provider errors remain authoritative in the existing
failure path, and tool arguments/results continue to use existing safe
summaries.

The automatic first-Turn Session-title request uses the same invocation budget
and emits the same paired events with purpose `session-title`. This makes the
previously hidden request visible at its real shared index and avoids an
unexplained jump such as round 1 to round 3. Invalid-title retries each receive
their own index; Provider failure and cancellation close the started title
round exactly once before existing fallback or cancellation behavior.

The persistent TTY displays one logical Turn boundary, each model round, tool
start/result lines, streamed assistant text, and a dynamic elapsed indicator
while a Provider invocation is open. Each permanent completion line includes
its elapsed duration, so copied scrollback distinguishes a slow Provider round
from delayed terminal delivery even though the spinner itself is ephemeral.
When an invocation remains open for five seconds, the TTY also emits a bounded
Host-only `still waiting` heartbeat every five seconds. The heartbeat is
deliberately low frequency and contains no Provider response or request body.
`Turn committed` is displayed only after the Session prompt call returns from
its durable commit path. A failed or cancelled Turn never receives that label.

Lifecycle events remain FIFO and non-droppable in the bounded frontend queue;
assistant text deltas are preserved as separate events so each received stream
chunk can be flushed independently. Event-sink failures
remain observational and cannot change Provider, tool, or commit outcomes.
Process-local ObservationStream projection stays content-free.

Non-TTY successful prompt and eval commands suppress the new round boundary
lines so existing stdout/stderr automation contracts remain stable. Interactive
TTY sessions show them by default. A public NDJSON event mode is not introduced
in this slice and would require a separate explicit versioned contract.

## Compatibility and security

No Session record, observation ledger, Provider wire request, system prompt,
model-visible tool, permission rule, or tool execution contract changes. The
canonical system prompt was reviewed and remains unchanged because the new
events are Host-only presentation facts. No raw Provider response, reasoning,
tool input, tool output, credential, header, or request body is added to the
timeline.

## Verification

Deterministic tests cover multi-round tool ordering, immediate terminal flush,
dynamic and permanent elapsed status, visible shared Session-title numbering,
five-second wait heartbeats, final-text completion before durable confirmation,
Provider failure and cancellation closure, per-delta queue preservation, content-free
ObservationStream projection, logical Turn start/commit boundaries, and quiet
non-TTY success output.
