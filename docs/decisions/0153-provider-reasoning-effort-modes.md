# ADR 0153: Provider Reasoning Effort Modes

## Status

Accepted

## Decision

Coquo exposes the broad, provider-neutral Host effort union:
`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`. The mode is
separate from `max_output_tokens`: effort selects a provider reasoning policy,
while the output budget limits the visible response size.

The selected mode is pinned in the runtime route and included in its route
fingerprint and redacted Session binding. `/effort` changes are allowed only
between turns and append a `runtime_changed` audit; they do not rewrite
conversation history or store reasoning content. Legacy bindings without the
field replay as an unset mode.

Named profiles declare the provider's native string contract with a native kind,
the supported native level names, an explicit Host-to-native mapping, and an
optional default Host effort. OpenAI-compatible Chat Completions and Responses
send the mapped value through their native effort field. Anthropic Messages
uses the adaptive string contract (`thinking: {type: adaptive}` plus
`output_config.effort`) through the same profile mapping. A missing mapping is
an explicit failure; there is no implicit `max -> high` conversion. The
legacy numeric Anthropic `budget_tokens` contract is intentionally unsupported.
Direct routes without a profile preserve the selected Host spelling as a
provider-native passthrough; profiles are the authoritative way to declare and
validate capabilities.

The CLI accepts all seven Host levels for prompt and interactive invocations.
Profile add/replace accepts the native kind, repeatable native level names,
repeatable `HOST=NATIVE` mappings, and an optional default effort. REPL users
can inspect or change the process-local value with `/effort` and
`/effort <level>|reset`. Unsupported routes fail closed; no silent fallback
changes the selected Host mode.

Child execution carries the selected redacted binding for provenance, but
does not gain a larger capability, tool budget, or permission ceiling from a
higher effort mode.

## Compatibility and non-goals

The fields are optional when replaying older profile and binding objects. This
slice does not add provider discovery, numeric Anthropic thinking budgets,
reasoning-text persistence, or any coupling between effort and permissions,
tool budgets, Child counts, concurrency, or loop limits.

## Verification

Deterministic tests cover CLI parsing, route rendering, process-local runtime
switch and reset, legacy binding replay, OpenAI-compatible request mapping,
OpenAI Responses request mapping, Anthropic adaptive string projection, profile
mapping validation, and rejection of legacy numeric budget mappings.
