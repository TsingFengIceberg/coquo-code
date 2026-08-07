# ADR 0127: Audited Pinned Local Hook Handlers

- Status: Accepted
- Date: 2026-08-07

## Context

ADRs 0125 and 0126 established frozen declarative Hook matching, durable lifecycle observations, and a deliberate prohibition on free-form callbacks. Pure static effects are sufficient for fixed local policy, but they cannot perform a bounded deterministic check whose answer depends on local process logic. Adding a direct callback runner beside Tool execution would create a second authority and side-effect path outside PermissionGate, approval, Action Audit, cancellation, sandboxing, and Session durability.

## Decision

Advance Hook configuration and `HookSetSnapshot` to v3. Strictly read v1 and v2 configuration by decoding their absent handler field as `null`, but write only v3. A rule may optionally contain one local handler with a direct executable, at most 16 fixed arguments within a 6 KiB fixed-argv budget, a 1-30 second timeout, and an exact lowercase SHA-256 of the executable bytes. Executable rules must retain an empty static `continue` effect; their runtime result supplies the effective result. Configuration, import, fingerprinting, readiness checks, and enablement never execute the handler, and every imported rule is forced to disabled revision 1.

The Host resolves a workspace-relative executable without symlinks or an explicit absolute executable, requires a bounded executable regular file with execute permission, verifies its pinned digest, and constructs direct argv without shell parsing. It appends one Host-owned `--leonervis-hook-event-v1` argument followed by closed canonical JSON containing only protocol version, Hook and HookSet identity, event, safe subject identity, and content-free action classification where applicable. User messages, Tool arguments, file content, credentials, callback bodies, and arbitrary action results are never passed. The existing `RunCommandTool` environment allowlist and Linux bubblewrap boundary provide read-only Host root, writable workspace, private temporary/home paths, denied sockets, timeout, bounded output, and process-group cleanup.

Every invocation is represented as a synthetic `hook_handler` Action classified `dangerous`. It uses the current Session writer, ActionLease where a Turn is active, PermissionGate, `ask|auto`, exact approval preview, durable Action Audit, cancellation, and the same command sandbox as `run_command`. `read-only` and `workspace-write` deny it; enabling a rule never grants execution permission. The final executable identity is rechecked inside the audited execution phase. A stale executable becomes a closed failed Action and is not started.

A successful handler must write exactly one closed JSON object to bounded UTF-8 stdout: `{"version":1,"effect":"continue|deny|require_ask|advisory","message":"..."}`. `continue` requires an empty message; other effects require bounded text. Only `before_action_authorization` may return `deny` or `require_ask`; all observation events accept only `continue` or `advisory`. Preauthorization preparation, permission, approval, execution, timeout, stale, or protocol failure fails closed as `deny`. After-action and lifecycle failures become advisory feedback and never rewrite the authoritative action, Turn, or Task outcome. There is no automatic retry or rollback.

At most four handlers execute for one event and at most twelve execute during one ordinary Turn. Handler execution never recursively evaluates Hooks. Turn and Task lifecycle handlers run only after the corresponding authoritative Session or Task record commits; their process lifecycle remains in Session Action Audit rather than being inserted into the already committed lifecycle record. Action handlers run within their owning Turn and their resolved effect remains in that Turn's existing Hook audit ledger.

Provide `hooks fingerprint`, handler-aware `hooks add`, `hooks template local-handler`, strict workspace-local `hooks import`, readiness checks in `hooks doctor`, and bounded content-free `hooks runs [session]`. REPL `/hooks runs [count]` reads only the current replayed Session. Operator views may show Hook ID, event, executable path, digest, status, and result code, but never fixed handler arguments, event JSON, stdout, stderr, user content, Tool arguments, or credentials.

Advance approval preview to v6. Advance the system prompt to v43 and provider adapter contract to v44 because model-visible denial and advisory provenance now includes controlled handlers. HookSet identities become `hooks-v3`; full and compacted Effective Context become v21/v22 while v19/v20 remain strict legacy Hook-v2 representations. Tool Registry generation and Session/Task record schemas do not change because handlers add no model-visible Tool and reuse existing Action Audit records and Hook audit mappings.

## Consequences

- Local deterministic policy can be extended without giving Hooks a privileged execution path.
- A handler can write the workspace only after the same dangerous permission and approval boundary used by other local process execution; it still cannot access network sockets or credential environment values through this contract.
- Handler output is untrusted, strictly parsed, effect-limited, bounded, and absent from model history except for the normalized denial or advisory text that the Hook contract intentionally exposes.
- Lifecycle handler execution is truthful but post-commit: failure cannot retroactively invalidate an already durable Turn or Task transition.
- HTTP handlers, shell command strings, custom environments, credential injection, model or subagent calls, background scheduling, recursion, argument mutation, authoritative result rewriting, automatic retry, and rollback remain out of scope.
