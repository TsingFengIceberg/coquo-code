# 0084: Deterministic Offline Host Eval Baseline

- Status: Accepted
- Date: 2026-07-30
- Scope: establish a versioned, isolated, Host-fact evaluation baseline over complete coding-agent turns

## Context

The deterministic pytest suite verifies individual contracts and integrations, but it does not expose a product command that answers whether a complete fixed coding-agent task still reaches the correct final workspace and durable Host state. Future capabilities such as Skills, MCP, search, or subagents need a stable before/after baseline; comparing feature count or ad hoc real-model transcripts cannot distinguish a Harness regression from provider randomness.

The baseline must not weaken Leonervis boundaries merely to make scenarios easy to run. It must use the production ProjectSession and AgentLoop, avoid credentials and network, isolate every task from the caller's workspace, and score externally observable Host truth rather than accepting assistant success claims.

## Decision

Add the versioned `host-baseline-v1` suite behind `leonervis-code eval list` and `leonervis-code eval run [all|CASE] [--format text|json]`. Its immutable built-in fixtures define a prompt, exact initial UTF-8 files, a scripted fake-provider trajectory, permission and approval modes, and expected final facts. Version 1 contains four cases: bounded file read, controlled file creation, read-only write denial, and sequential batch stop after a failed first action.

Each case creates a fresh temporary workspace and separate temporary provider-configuration paths. A narrowly scoped `ProjectSession.open` fake-provider factory injection selects the scripted runtime without constructing a real route. The ordinary Session, AgentLoop, project-instruction loading, PermissionGate, tool preparation/execution, tool budget, commit, tool ledger, and Action Audit paths remain active. The runner never uses `-C`, a configured profile, credential environment values, live discovery, or network access.

After closing the Session, scoring strictly replays durable state through `SessionStore`. Five checks compare exact final-text identity, one committed turn, the complete non-Session workspace entry set and file byte identities, the ordered durable tool ledger, and ordered Action Audit outcomes. Reports contain only stable case metadata, statuses, counts, result codes, lengths, and SHA-256 identities. They exclude temporary paths, timestamps, random IDs, transcript content, tool content, and credentials. Exit status is 0 only when every selected case passes, 1 for a scored regression, and 2 for invalid Eval selection or forbidden runtime options.

## Compatibility

Eval is a Host-only development and inspection surface. The canonical system prompt remains v23, provider adapter contract remains v26, current Effective Context representations remain `ctx-v5`/`ctx-v6`, and model-visible tool names, order, descriptions, schemas, budgets, ToolArguments v1, ActionIdentity v1, Session records, Action Audit records, and compaction records do not change. Existing callers of `ProjectSession.open` are source compatible because fake-provider injection is optional and defaults to the existing runtime behavior.

## Invariants

- Every case runs in a newly created temporary workspace and cannot target the caller's workspace.
- Eval rejects `-C`, resume, model, profile, output-budget, protocol, endpoint, and credential-selection options.
- No case reads a real provider profile, credential value, live capability endpoint, or network resource.
- Scoring uses durable Host observations after Session close; assistant text cannot establish filesystem or execution success.
- Workspace scoring excludes only the reserved `.leonervis-code` Session-state subtree and compares every other observed entry.
- Stable JSON contains no temporary path, timestamp, random Session/action ID, or original final/tool text.
- A passing scripted case demonstrates deterministic Harness correctness for that trajectory, not real-model capability.

## Non-goals

- replacing pytest, lint, format, lock, diff, or CLI release gates;
- measuring real-provider quality, stochastic planning, generalization, latency, token cost, or model rankings;
- downloading external datasets or accepting arbitrary untrusted fixture files;
- executing `run_command`, sandbox benchmarks, network search, credentials, or paid APIs;
- CI history storage, trend dashboards, weighted scores, flaky retries, or pass-threshold tuning;
- changing prompts, tools, permissions, Session representations, or runtime behavior to improve a score.
