# 0061: Process-local Runtime Output Budget Control

- Status: Accepted
- Date: 2026-07-28
- Scope: temporary ordinary-generation output budgets, target-aware updates, CLI/REPL control, and runtime accounting continuity

## Context

ADR 0060 makes provider output exhaustion truthful and observable, but recovering from a 4096-token truncation still requires editing or replacing a named profile and restarting the runtime. A user needs a bounded way to increase or reduce the next generation's requested output reserve without mutating durable provider configuration. The value influences provider request construction, model-output validation, context-window preflight, context meters, route fingerprints, and prepared-action runtime identity, so it cannot be a terminal-only variable or an adapter-side override.

The control must remain distinct from automatic continuation. Replaying an output-limited attempt could duplicate text or already completed tools, and partial text is not a committed causal prefix. A temporary budget also must not silently become a persisted Session selection or billing record.

## Decision

Global `--max-output-tokens` applies a process-local startup override to ordinary prompt or interactive mode. The REPL adds `/output` inspection, `/output <tokens>` update, and `/output reset`. Values must be ASCII positive integers from 1 through 100,000,000. Inspection reports the effective budget, configured profile or direct-route default, source (`profile`, `route`, `cli`, or `runtime`), and known model maximum. A fake runtime rejects mutation because it has no real provider route.

`RuntimeProviderManager` owns both the configured default and optional temporary override. An update prepares a complete replacement route, provider client, and capability outside the active runtime, then assesses the current committed Effective Context with that exact candidate. Known model-output or context overflow rejects the candidate and closes it without changing the current provider, route, generation, budget, usage, profile, or Session. Unknown input count applies with explicit warning semantics; every real invocation still performs full preflight. Successful installation atomically swaps the provider and increments runtime generation so prepared action leases and pinned turns cannot cross the route change.

Changing only output budget preserves actual usage accumulated since the current profile/runtime target was selected, but invalidates the latest context meter because it contains the previous reserve. A model override retains and re-screens the temporary budget against the new target. A provider-profile or active-selection switch clears the temporary override and installs the destination default. Reset clears override provenance even when its numeric value equals the default.

Profile files and Session selection are not modified, and the command itself appends no `runtime_changed` record. Later turn, failure, and action bindings already capture the exact effective `max_output_tokens`, route fingerprint, and runtime generation as audit provenance. Resume continues to select runtime independently and never recreates a temporary budget from historical bindings. The existing compaction summary limit remains bounded by the effective runtime budget, known model maximum, and the Host's 4096-token compaction cap.

## Contracts and compatibility

Provider native requests already carry `RuntimeProviderRoute.max_output_tokens`, so successful wire schemas and adapter parsing do not change. Provider adapter contract remains v22. The control is Host CLI/runtime behavior and is not exposed to the model; canonical system prompt remains v19, the 17 model-visible tools and order remain unchanged, and Effective Context `ctx-v3`/`ctx-v4` identity does not include generation options. ToolArguments v1, ActionIdentity v1, `turn_committed` schema v5, Action Audit v1, and `context_compacted` v2/v3 remain unchanged. Existing profiles and Sessions require no migration or rewrite.

## Explicit non-goals

- automatically retrying, continuing, or committing an output-limited response;
- allowing one assistant turn to change its pinned budget after generation starts;
- persisting temporary overrides in provider profiles or restoring them from Session bindings;
- adding temperature, reasoning-effort, or arbitrary provider parameter mutation commands;
- changing provider/model fallback policy, compaction's Host maximum, or token pricing;
- treating a larger output budget as proof that a provider will produce a complete answer.

## Verification

Deterministic tests cover CLI parsing and propagation, REPL inspection/set/reset, fake-runtime rejection, bounded values, provider reconstruction, active-operation exclusion, known model-limit rejection, current-context screening, failure atomicity, equal-value reset, model-switch preservation, profile-switch clearing, route and BindingSnapshot provenance, usage continuity, context-meter invalidation, presentation, and unchanged profile/Session state. The full offline release gate remains required; real credentials, network access, and provider API cost are not used without separate authorization.
