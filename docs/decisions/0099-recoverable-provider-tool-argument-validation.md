# 0099: Recoverable Provider Tool Argument Validation

## Status

Accepted.

## Context

OpenAI-compatible and Anthropic adapters previously passed every native tool input through the complete Leonervis tool validator before creating a provider-neutral `ToolUse`. This correctly rejected malformed calls, but it collapsed two materially different cases into `response_invalid`: JSON that could not be represented safely, and a bounded JSON object for a known ordinary tool that violated only that tool's schema or hard bounds.

Some compatible providers do not enforce every projected JSON Schema constraint. A valid `write_file` call with content beyond the 4,096-byte tool limit therefore aborted the complete Turn before the Host could return an `invalid_request` result. The model could not observe or correct its call even though Leonervis could preserve its exact bounded arguments and causal identity.

## Decision

Provider adapters continue to require one parseable JSON object, a nonempty unique tool-use ID, a known exposed tool name, and canonical `ToolArguments` no larger than 16 KiB. Task coordination and lifecycle tools continue to receive complete adapter-side schema validation because invalid control arguments must never reach Task proposal or lifecycle durability boundaries.

For the 21 ordinary workspace and command tools, adapters now freeze a valid bounded JSON object without applying the tool-specific validator. The existing Host tool dispatcher remains authoritative for exact keys, types, workspace paths, sizes, symlinks, stale state, permissions, approvals, and execution. A rejected ordinary call becomes a matching error `ToolResult` and Host ledger entry, after which the exact frozen ToolUse and result are projected to the next provider continuation. Provider history projection therefore replays bounded ordinary-tool arguments exactly instead of revalidating a call already rejected by the Host.

The `write_file.content` provider schema additionally declares `maxLength: 4096` as a provider-facing character constraint. The existing execution limit remains both 4,096 characters and 4,096 UTF-8 bytes; schema guidance, permission, or approval cannot increase it.

Invalid JSON, non-object input, unknown tools, duplicate or malformed IDs, arguments exceeding the global 16 KiB canonical bound, and malformed Task coordination calls still fail the provider response without committing a Turn. No raw provider payload is logged or repaired heuristically.

## Versioning And Compatibility

The provider adapter contract advances to v31. The canonical system prompt remains v29 because its model-visible behavioral text already states that tool failures must be corrected and the `write_file` byte limit is unchanged. The catalog order and tool count remain unchanged, but the exact `write_file` schema changes the no-project-instructions empty full-context identity to `ctx-v5-e681ce5f35a3bd5b4d0591912d49119c767e97ad87b9ecad6806777c3a6caecd`. Effective Context representations, ToolArguments v1, Session/Task/Action Audit schemas, budgets, and old transcripts remain unchanged without rewriting.

## Consequences

- Compatible providers can recover in the same Turn from bounded ordinary-tool schema mistakes.
- The Host remains the only authority that accepts or executes a tool action.
- Exact invalid calls and their error results remain causally paired and auditable when they are representable.
- Truly malformed or globally oversized provider responses remain fail closed.
