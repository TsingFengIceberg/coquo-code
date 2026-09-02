# Implemented Foundations and Design Evolution

> This document preserves the implementation narrative for Coquo's completed learning slices. The README is intentionally limited to primary commands and usage entry points. The ADRs under [`docs/decisions/`](./decisions/) remain the authoritative records for each slice's rationale, boundaries, and verification evidence.
>
> [中文](./implemented-foundations.md) | English

## Contents

- [Coquo Product Identity Migration](#coquo-product-identity-migration)
- [Canonical model system prompt](#canonical-model-system-prompt)
- [Foundation 3D: stable profile identity and durable Sessions](#foundation-3d-stable-profile-identity-and-durable-sessions)
- [Foundation 3C: named provider profiles and a real multi-turn REPL](#foundation-3c-named-provider-profiles-and-a-real-multi-turn-repl)
- [Foundation 3B: local multi-provider real-model path](#foundation-3b-local-multi-provider-real-model-path)
- [Foundation 2B: offline adapter-owned compatibility policy](#foundation-2b-offline-adapter-owned-compatibility-policy)
- [Foundation 4A: Permission Policy Contract](#foundation-4a-permission-policy-contract)
- [Foundation 4A Slice 3–4: Exact Action Identity and Durable Action Audit](#foundation-4a-slice-34-exact-action-identity-and-durable-action-audit)
- [Foundation 4A Slice 5–9: Approval Coordination and Controlled `write_file`](#foundation-4a-slice-59-approval-coordination-and-controlled-write_file)
- [Foundation 4A Slice 10: Action Audit Observability](#foundation-4a-slice-10-action-audit-observability)
- [Foundation 4B Slices 0–3: Exact Edit Preparation, Execution, and Authorization Composition](#foundation-4b-slices-03-exact-edit-preparation-execution-and-authorization-composition)
- [Foundation 4B Slice 4: Model-visible Exact Edit Integration](#foundation-4b-slice-4-model-visible-exact-edit-integration)
- [Foundation 4C Slices 0–3: Controlled Command Contract and Side-effect-free Preparation](#foundation-4c-slices-03-controlled-command-contract-and-side-effect-free-preparation)
- [Foundation 4C Slices 4–6: Bounded Command Execution and Process-group Cleanup](#foundation-4c-slices-46-bounded-command-execution-and-process-group-cleanup)
- [Foundation 4C Slices 7–9: Durable Model-visible Command Integration](#foundation-4c-slices-79-durable-model-visible-command-integration)
- [Fail-closed Linux `run_command` Sandbox](#fail-closed-linux-run_command-sandbox)
- [Host Workbench Diagnostics and Prompt History Search](#host-workbench-diagnostics-and-prompt-history-search)
- [Host Policy and Tool Discoverability](#host-policy-and-tool-discoverability)
- [Foundation 5A: Root AGENTS.md Project Instructions](#foundation-5a-root-agentsmd-project-instructions)
- [Deterministic Offline Host Eval Baseline](#deterministic-offline-host-eval-baseline)
- [Actual Coding Task Eval](#actual-coding-task-eval)
- [Durable Task Identity and Host Management](#durable-task-identity-and-host-management)
- [Durable Stage Lifecycle and Turn Evidence](#durable-stage-lifecycle-and-turn-evidence)
- [Foreground Task Stage Execution and Recovery](#foreground-task-stage-execution-and-recovery)
- [Task Planning, Acceptance, Budgets, and Management](#task-planning-acceptance-budgets-and-management)
- [Structured Task Acceptance and Independent Review](#structured-task-acceptance-and-independent-review)
- [Task Proposal Control Boundary](#task-proposal-control-boundary)
- [Natural-language Task Lifecycle Handoffs](#natural-language-task-lifecycle-handoffs)
- [Recoverable Provider Tool Argument Validation](#recoverable-provider-tool-argument-validation)
- [Bounded Independent Brave/Tavily Web Search](#bounded-independent-bravetavily-web-search)
- [Provider-native Web Search](#provider-native-web-search)
- [OpenAI Responses Protocol and Provider-owned History](#openai-responses-protocol-and-provider-owned-history)
- [Provider Search Resilience, Controls, and Observability](#provider-search-resilience-controls-and-observability)
- [Foundation 4D Slices 0–4: Controlled Single-directory Creation](#foundation-4d-slices-04-controlled-single-directory-creation)
- [Foundation 4E Slices 0–9: Controlled No-overwrite File Move](#foundation-4e-slices-09-controlled-no-overwrite-file-move)
- [Foundation 4F Slices 0–6: Controlled Regular-file Deletion](#foundation-4f-slices-06-controlled-regular-file-deletion)
- [Foundation 4G Slices 0–6: Controlled Empty-directory Deletion](#foundation-4g-slices-06-controlled-empty-directory-deletion)
- [Tool Batch A: Bounded Workspace Navigation](#tool-batch-a-bounded-workspace-navigation)
- [Tool Batch B: Process-isolated Regex Grep](#tool-batch-b-process-isolated-regex-grep)
- [Tool Batch C: Structured Exact Multi-edit Patch](#tool-batch-c-structured-exact-multi-edit-patch)
- [Shared Six-call Tool Budget](#shared-six-call-tool-budget)
- [Live Redacted Tool Activity](#live-redacted-tool-activity)
- [Provider-neutral Assistant Tool Text Representation](#provider-neutral-assistant-tool-text-representation)
- [Provider Mixed-response Inbound Normalization](#provider-mixed-response-inbound-normalization)
- [`turn_committed` v3 Assistant Tool Text Persistence](#turn_committed-v3-assistant-tool-text-persistence)
- [Provider Mixed-response History Projection](#provider-mixed-response-history-projection)
- [AgentLoop and Terminal Assistant Tool Text Integration](#agentloop-and-terminal-assistant-tool-text-integration)
- [Provider Streaming and Terminal Failure Atomicity](#provider-streaming-and-terminal-failure-atomicity)
- [TTY Markdown Rendering](#tty-markdown-rendering)
- [Exact Bounded Informed Approval](#exact-bounded-informed-approval)
- [Sequential Tool-call Budget Hardening](#sequential-tool-call-budget-hardening)
- [Bounded Multi-tool Response Batches](#bounded-multi-tool-response-batches)
- [Structured Tool Outcome Ledger](#structured-tool-outcome-ledger)
- [Durable Tool Ledger Inspection](#durable-tool-ledger-inspection)
- [Runtime Context Meter and Provider Token Usage](#runtime-context-meter-and-provider-token-usage)
- [Context and Compaction Observability](#context-and-compaction-observability)
- [Provider Output-limit and Compaction Failure Diagnostics](#provider-output-limit-and-compaction-failure-diagnostics)
- [Upstream Provider API Error Facts and Safe Display](#upstream-provider-api-error-facts-and-safe-display)
- [Unified Read-only Observation Timeline](#unified-read-only-observation-timeline)
- [Process-local Runtime Output Budget Control](#process-local-runtime-output-budget-control)
- [Durable Session Provider Usage Audit](#durable-session-provider-usage-audit)
- [Bounded Read-only Git Change Observation](#bounded-read-only-git-change-observation)
- [Bounded Reachable Git History Observation](#bounded-reachable-git-history-observation)
- [Opt-in Bounded Live Tool Details](#opt-in-bounded-live-tool-details)
- [Trusted Command Result Observability](#trusted-command-result-observability)
- [Host Workbench Navigation and Failure Guidance](#host-workbench-navigation-and-failure-guidance)
- [Assistant Turn Execution Trace Grouping](#assistant-turn-execution-trace-grouping)
- [Durable Session Naming and Terminal Identity](#durable-session-naming-and-terminal-identity)
- [Session Lifecycle Management and Naming Diagnostics](#session-lifecycle-management-and-naming-diagnostics)
- [Pinned Sessions and Snapshot-based Quick Switching](#pinned-sessions-and-snapshot-based-quick-switching)
- [Read-only Session Inspection and Bounded Turn Preview](#read-only-session-inspection-and-bounded-turn-preview)
- [Session Search, Turn Navigation, Export, Fork, and Repair](#session-search-turn-navigation-export-fork-and-repair)
- [Foundation 1D: Bounded Literal Grep](#foundation-1d-bounded-literal-grep-and-versioned-tool-arguments)
- [Foundation 1C: Bounded Workspace Glob](#foundation-1c-bounded-workspace-glob)
- [Foundation 1B: deterministic bounded read_file tool loop](#foundation-1b-deterministic-bounded-read_file-tool-loop)
- [Foundation 3H: Pre-turn Automatic Context Compaction](#foundation-3h-pre-turn-automatic-context-compaction)
- [Foundation 3G: Target-aware Resume Prepare/Commit](#foundation-3g-target-aware-resume-preparecommit)
- [Foundation 3F-2: Controlled Compact Transaction](#foundation-3f-2-controlled-compact-transaction)
- [Provider-neutral Effective Context Snapshot and `/context`](#provider-neutral-effective-context-snapshot-and-context)
- [Target-aware runtime switch UX](#target-aware-runtime-switch-ux)
- [Target-specific request counting and per-invocation preflight](#target-specific-request-counting-and-per-invocation-preflight)
- [Provider-owned model context capability](#provider-owned-model-context-capability)
- [Frozen Declarative Preauthorization Hooks](#frozen-declarative-preauthorization-hooks)
- [Durable Hook Observation and Audit](#durable-hook-observation-and-audit)
- [Audited Pinned Local Hook Handlers](#audited-pinned-local-hook-handlers)
- [B6: Writable Team Roles, Host-owned Linked Worktree, and Parent-only Integration](#b6-writable-team-roles-host-owned-linked-worktree-and-parent-only-integration)
- [Task–Child–Team Unified Orchestration Bridge](#taskchildteam-unified-orchestration-bridge)
- [Shared Agent Runtime and Durable Child Run Foundation](#shared-agent-runtime-and-durable-child-run-foundation)
- [ADR index](#adr-index)

## Coquo Product Identity Migration

At version `0.1.0` pre-alpha, the project moves its public identity from Leonervis Code to Coquo in one clean transition. The Python distribution, import package, sole CLI, and module entry point are all `coquo`; workspace state moves to `.coquo/`, XDG configuration and cache use `coquo/`, and product-owned environment variables use the `COQUO_` prefix. MCP client metadata, HTTP user agents, temporary files, terminal titles, and the model-visible role use the same identity, while the LEO graphic becomes a deterministic COQ mark.

The transition provides no legacy CLI, import package, or automatic runtime-data compatibility. Coquo neither reads, moves, nor deletes `.leonervis-code/` or old XDG directories. Git ignore rules, the command sandbox, and the independent reviewer continue protecting both old and new state paths so legacy sensitive data is not exposed by the rename. Product-owned fingerprint domains become `coquo-*`, placing old Session, Task, Action, ToolSet, Skill, Hook, and Effective Context identities outside the new runtime contract. Structured record algorithms do not otherwise change; the model system prompt advances to v44, provider adapter contract to v45, and built-in Tool Registry and source generation to 6. Historical ADRs retain the former name. [ADR 0128](./decisions/0128-coquo-product-identity-migration.md) records the complete decision.

## Canonical model system prompt

Coquo builds a provider-neutral `SystemPromptSnapshot` from `src/coquo/system_prompt.py`. The snapshot contains an explicit version, normalized text, and a domain-separated SHA-256 fingerprint. It is built once at the beginning of each user turn and remains pinned across every provider/tool continuation in that turn:

```text
SystemPromptSnapshot + neutral conversation history
  -> Anthropic Messages: top-level system + messages
  -> OpenAI-compatible: one leading system role + messages
  -> Scripted fake: record the same request snapshot
```

The canonical model system prompt is now version 22. It permits brief companion text for a whole response batch and states that the Host completely validates up to eight ordered calls before sequential execution. One user turn admits at most 32 tool requests and 24 provider invocations, with the last invocation restricted to text. During forced text-only finalization, the model must use the `Host tool ledger:` counts in the last real Tool result: `unused_admission_slots` is only unused capacity, while `tool_requests_closed=true` means no further call is possible even when a slot remains. The ordinary Agent still cannot initiate compaction. The current 21 model-visible tools include bounded `git_status`, `git_diff`, `git_log`, and `git_show`; PermissionGate, approval, Action Audit, and every per-tool hard bound remain Host-enforced, and a multi-call response never grants parallel execution.

It explicitly states that `run_command` must pass through the Linux bubblewrap and seccomp sandbox, while making no claim to recursive copying/deletion, ignore-aware or indexed search, fuzzy/free-form patching, non-empty directory deletion, directory movement, recursive mkdir, shell source strings, interactive PTYs, network allowlists, resource quotas, compaction initiation, project-instruction loading, or multi-agent capabilities. Prompt instructions also do not replace the Host's hard workspace, symlink, encoding, size, exact-state conflict, timeout/process cleanup, causality, audit, sandbox, and durability constraints.

The system prompt is not a `ConversationItem`, so `/history`, `ProjectSession.history`, and append-only Session JSONL contain only real user/assistant/tool causal chains. A new turn after resume uses the current binary's canonical prompt; schema-v2/v3 compact checkpoints store only compact-prompt, summary-framing, and trigger provenance without inserting the normal system prompt into conversation history.

The **model system prompt** and the terminal's `›` input marker plus `model · workspace` status line are different interfaces: the former is a model-visible contract, while the latter is only human-facing interaction and status presentation.

See [0012: first canonical model system prompt](./decisions/0012-first-canonical-model-system-prompt.md) for the detailed decision and [references/claw-code-prompts](./references/claw-code-prompts/README.md) for the Claw-Code prompt-structure study map.

## Foundation 3D: stable profile identity and durable Sessions

Profile-registry schema v3 uses an immutable UUID as reference identity, while each name remains a readable, mutable alias and each revision supports update-conflict checks. Schema v3 also adds an optional exact-model `context_window_tokens` override.

Legacy schema-v1 profiles deterministically map their original names to UUIDs. The reader accepts mixed v1, v2, and v3 user/project files, and a write upgrades only the file it actually changes:

```bash
uv run coquo provider show vendor
uv run coquo provider list --show-ids
uv run coquo provider rename vendor vendor-new --if-revision 1
uv run coquo provider replace vendor-new \
  --provider custom \
  --model vendor/model-v2 \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --if-revision 2
uv run coquo provider migrate
```

Every `prompt` or REPL invocation creates or opens:

```text
<workspace>/.coquo/sessions/<workspace-fingerprint>/<session-id>.jsonl
```

A Session uses append-only JSONL. A successful turn's user message, tool-use/result pairs, and final assistant text are written and fsynced as one complete commit record before in-memory history changes. Each open Session holds an exclusive writer lock.

Corrupt middle records, unknown schemas, and invalid tool pairing fail closed. Only an incomplete, unterminated crash tail can be truncated under controlled recovery, which also appends a recovery record.

```bash
uv run coquo prompt "First turn"
uv run coquo session list
uv run coquo session show latest
uv run coquo --resume latest prompt "Continue the previous turn"
uv run coquo -C ../another-workspace --resume latest
```

A bare launch creates a new Session, while `--resume latest` continues the workspace's latest pointer. Inside the REPL, `/session new` starts empty history without changing the current runtime provider, and `/resume <id>` switches to existing history. `[current]` marks the destination of the next REPL prompt, `[latest]` marks the current `latest.json` target, and `open/closed` describes transcript lifecycle rather than lock ownership; a closed Session remains resumable.

Sessions and runtime providers are decoupled. The transcript records the profile ID/revision, provider/protocol, model, endpoint, and non-secret fingerprints actually used for each historical turn solely as audit provenance. After resume, the working provider still comes from this invocation's `--profile`/`--model`, workspace active selection, user active selection, or fake fallback. The runtime never reconstructs a client from historical binding metadata, and later profile rename, replacement, or deletion does not block resume.

Sending old history to a newly selected provider is an explicit runtime choice. If the current adapter rejects that history, the failed turn is not committed.

A local Session can contain user input, model responses, source excerpts, and tool results, so `.coquo/` is sensitive runtime state and should not be committed, synchronized, or published. Known configured credential values are never written as binding data, but the system cannot generally detect an unknown secret that appears in user text or a file read by a tool.

`ProjectSession` exposes `session_id`, `transcript_path`, `session_info()`, `list_sessions()`, `new_session()`, `switch_session()`, and `resume=`. Switching Sessions replaces only durable history and preserves the current provider client.

See [0010: stable profile identity and durable Sessions](./decisions/0010-foundation-3d-stable-profile-identity-and-durable-sessions.md) for the detailed decision.

## Foundation 3C: named provider profiles and a real multi-turn REPL

Profile definitions live at:

```text
${XDG_CONFIG_HOME:-~/.config}/coquo/providers.json
```

A workspace stores only its active profile ID in `.coquo/provider.json`. Neither JSON file stores key values. The workspace directory is local runtime state and should be added to the target project's `.gitignore`.

```bash
# Built-in provider: protocol, default endpoint, and credential env come from the catalog
uv run coquo provider add work-openai \
  --provider openai \
  --model gpt-5

# Controlled custom OpenAI-compatible endpoint; store only the key's env-variable name
uv run coquo provider add local-qwen \
  --provider custom \
  --model Qwen/Qwen3.5 \
  --protocol openai-compatible \
  --base-url http://127.0.0.1:11434

uv run coquo provider add vendor \
  --provider custom \
  --model vendor/model \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY

uv run coquo provider list
uv run coquo provider show vendor
uv run coquo provider use local-qwen
uv run coquo provider use work-openai --scope user
uv run coquo provider clear --scope project
uv run coquo provider remove vendor
```

Selection precedence is explicit `--profile` → explicit direct `--model` → workspace active → user active → fake/offline. `--profile NAME --model MODEL` uses a process-local model override on that profile endpoint without rewriting the profile:

```bash
uv run coquo --profile work-openai --model gpt-5-mini \
  prompt "Explain this workspace"
uv run coquo --profile work-openai
```

Both `provider use` and REPL `/provider use` resolve the route, validate the credential, and construct a candidate SDK client before writing active configuration and swapping the current client. On failure, the old active selection and client remain intact. `/model` is likewise atomic and allowed only between turns.

Complete neutral history and tool-use/result pairs survive a provider switch. If the new provider rejects old history, the failed turn is not committed.

Other project modules can use the public facade directly:

```python
from pathlib import Path
from coquo import ProjectSession

with ProjectSession.open(Path.cwd(), profile="work-openai") as session:
    first = session.prompt("Explain the README first")
    session.set_model("gpt-5-mini")
    second = session.prompt("Continue")
```

`ProjectSession` also exposes `list_profiles()`, `use_profile()`, `use_profile_id()`, `clear_active()`, `status()`, `history`, and `turns`.

See [0009: named provider profiles and the runtime manager](./decisions/0009-foundation-3c-named-provider-profiles-and-runtime-manager.md) for the detailed decision.

## Foundation 3B: local multi-provider real-model path

With global `--model`, `prompt` resolves a real adapter through the shared resolver/factory:

```bash
export ANTHROPIC_API_KEY='...'
uv run coquo --model anthropic/claude-opus-4-8 \
  prompt "Explain this workspace"

export OPENAI_API_KEY='...'
uv run coquo --model openai/gpt-5 \
  prompt "Explain this workspace"

export XAI_API_KEY='...'
uv run coquo --model xai/grok-3 \
  prompt "Explain this workspace"

export DASHSCOPE_API_KEY='...'
uv run coquo --model dashscope/qwen-plus \
  prompt "Explain this workspace"

uv run coquo --model ollama/qwen3:8b \
  prompt "Explain this workspace"

export OPENROUTER_API_KEY='...'
uv run coquo --model openrouter/anthropic/claude-opus-4-8 \
  prompt "Explain this workspace"
```

The Anthropic path uses the official `anthropic` SDK. Every other built-in route reuses the official `openai` SDK through the Chat Completions wire adapter. Both clients are synchronous, non-streaming, and configured with `max_retries=0`.

Adapters currently declare the ordered `read_file(path)`, `glob(pattern)`, and `grep(query, include)` schemas. The three local tools jointly enforce workspace, UTF-8, files-only no-symlink, and bounded output/read constraints while sharing the per-turn budget.

A one-shot controlled OpenAI-compatible endpoint can also be supplied without persisting a provider or key:

```bash
export VENDOR_API_KEY='...'
uv run coquo \
  --model vendor/model \
  --provider-protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY \
  prompt "Explain this workspace"
```

Explicit provider namespaces win. Only registered bare `claude-*`, `gpt-*`, `grok-*`, `qwen-*`, and `kimi-*` families are inferred deterministically; an unknown bare model is never guessed from installed credentials.

Route and adapter configuration contain no secret value. A key is read only when the factory constructs the selected SDK client. The runtime does not read `.env`, OAuth, or keyrings, and it does not implement streaming, automatic retries/backoff, fallback execution, request token preflight, compaction, parallel tools, or cross-workspace Session resume.

A real route can be previewed without constructing a client or accessing the network:

```bash
uv run coquo --model openai/gpt-5 route
```

The fake fallback remains unchanged. If a workspace/user active profile exists, `prompt` and the bare REPL use that real profile even without an explicit selector:

```bash
uv run coquo provider clear --scope project
uv run coquo provider clear --scope user
uv run coquo prompt "Hello"   # fake with no active profile; no network
uv run coquo                   # fake REPL with no active profile; no network
```

See [0007: non-streaming Anthropic adapter](./decisions/0007-foundation-3a-anthropic-non-streaming-adapter.md) and [0008: local multi-provider runtime](./decisions/0008-foundation-3b-local-multi-provider-runtime.md) for the detailed decisions. Run live smoke checks only when the user explicitly chooses their own credentials, endpoints, and API budget.

## Foundation 2B: offline adapter-owned compatibility policy

`route` is a deterministic diagnostic surface for the control-plane and adapter-policy boundary:

```bash
uv run coquo route

uv run coquo route \
  --model beta \
  --max-output-tokens 32 \
  --fallback-model default

uv run coquo route \
  --model beta \
  --temperature 0.2
```

The route resolver owns **hard** admission rules: valid provider/model selection, enabled status, required tool-use/streaming capabilities, canonical option types and ranges, fallback validity, and Harness-owned field protection.

A selected adapter owns provider-native wire names and documented **soft** compatibility behavior. The fake `beta` model demonstrates the distinction: its requested `temperature` is omitted as a known fixed-sampling incompatibility, and `route` reports that decision instead of silently changing the request or issuing a false hard error.

Provider-specific extensions currently have a controlled Python API path only. They cannot override `model`, messages, tools, streaming, token-limit fields, or adapter-generated parameter fields. The CLI intentionally does not accept arbitrary JSON body overrides.

The Foundation 2B form of `route` is completely offline: it constructs no provider client, reads no environment variables, makes no network call, and reveals no credential reference/value. A global-`--model` route uses the real resolver to show provider, protocol, wire model, base-URL source, and `configured/missing/not required` status, while still constructing no client and sending no request. A successful preview is not proof that the remote provider will accept a request.

See [0005: provider-neutral model routing](./decisions/0005-foundation-2a-provider-neutral-model-routing.md) and [0006: adapter-owned compatibility policy](./decisions/0006-foundation-2b-adapter-owned-compatibility-policy.md) for the detailed decisions.

## Foundation 4A: Permission Policy Contract

Before exposing writes, the Host establishes a stateless, I/O-free `PermissionGate` policy kernel. The capability ceiling is fixed as `read-only | workspace-write | danger-full-access`, the interaction mode as `ask | auto`, and the two controls are orthogonal. Results are `allow | ask | deny` with a stable machine-readable reason. Policy action classes are `workspace-read | workspace-create | workspace-overwrite | dangerous | unknown`, and unknown fails closed under every configuration.

`read_file`, `glob`, and `grep` are classified as `workspace-read`, so every mode/approval combination allows them without terminal confirmation. Workspace create/overwrite is denied under `read-only` and follows `ask | auto` at a higher capability. Dangerous actions can only reach ask/allow under `danger-full-access`. PermissionGate reads no CLI, Session, provider, credential, or filesystem state; executes no Tool; creates no approval token; and cannot bypass workspace, symlink, size, timeout, conflict, causality, or durability hard bounds.

As a prerequisite boundary fix, `read_file` rejects every final or intermediate symlink component, including internal and broken links, while preserving normal nested UTF-8 reads and the 32 KiB bound. The local single-user v0 still does not claim to eliminate hostile concurrent TOCTOU between checks and open.

That policy slice made no model-visible change, so the canonical system prompt remained v4, the adapter contract remained v5, and existing Session/context representations did not advance. Slices 3–9 subsequently connected exact identity, audit, runtime approval, and controlled writes without changing the pure gate's responsibility.

See [0022: Foundation 4A Permission Policy Contract](./decisions/0022-foundation-4a-permission-policy-contract.md) for the complete decision.

## Foundation 4A Slice 3–4: Exact Action Identity and Durable Action Audit

After PermissionGate, the Host can construct an irreplaceable `ActionIdentity` v1 for one resolved action. The identity contains a Host-generated request UUID, provider `tool_use_id`, exact tool name, immutable `ToolArguments`, trusted action classification, workspace fingerprint, prepared-turn lease, and execution precondition. Sorted compact JSON and domain-separated SHA-256 produce an `act-v1-...` digest. The lease pins Session ID, a non-reconstructible lease UUID, runtime generation, and the `ctx-v1 | ctx-v2` Effective Context ID, so resume, runtime switching, or prepared-turn replacement cannot reuse an old approval.

Preconditions use the closed identity `none | path-absent | expected-state-sha256`. A single-use `ApprovalGrant` can only be issued for a deterministic PermissionGate `ask`; it is a Host-memory object, not a model-visible bearer token. Consumption must match the full identity, lease, and precondition, while a lock guarantees that concurrent consumption succeeds at most once. Mismatch, stale lease, stale precondition, and replay have stable rejection codes.

Session adds five append-only schema-v1 audit records: `action_requested`, `permission_decided`, `approval_resolved`, `action_execution_started`, and `action_execution_finished`. Replay recomputes policy, validates exact references and authorization, and rebuilds the lifecycle; the later write slice extends terminal finish to `succeeded | failed | partial`. The current sequential Harness permits at most one unresolved action. `turn_committed`, `runtime_changed`, `context_compacted`, and a clean `session_closed` cannot cross it. Action audit is retained in `ReplayState.action_audits` but never enters full/effective model history and is never deleted or summarized by compaction.

`action_execution_started` uses append+fsync as the durable barrier before side effects. If resume or `turn_failed` crosses an action that never started, replay derives `abandoned`; if durable start exists without finish, replay derives `outcome-unknown`. If finish audit fails after the executor returns, typed `ActionOutcomeAuditError` preserves the known outcome and storage cause. The Host must not misreport non-execution or retry a side effect merely to repair audit.

Slice 3–4 was still a Host-only contract when introduced, so system prompt v4, the three-read-tool order, and adapter contract v5 remained unchanged at that point; Slices 5–9 then completed runtime integration. See [0023: Foundation 4A Exact Action Identity, Single-use Approval Grant, and Durable Action Audit](./decisions/0023-foundation-4a-exact-action-identity-and-durable-audit.md) for the complete decision.

## Foundation 4A Slice 5–9: Approval Coordination and Controlled `write_file`

The central `ActionCoordinator` now strictly orders `action_requested -> permission_decided -> optional human resolution -> durable action_execution_started -> executor -> action_execution_finished`. Deny neither asks nor executes. An accepted ask issues and consumes an exact single-use grant, while reject/cancel returns a structured tool error. The executor can produce a side effect only after the start record has been appended and fsynced. Ordinary executor failure is safely attributed, but a final-audit failure after a side effect must propagate rather than pretending rollback or retrying.

`PreparedAgentTurn` binds one `ActionLease` after automatic compaction completes. Every provider continuation in the turn pins the same Session, runtime generation, Effective Context, and system prompt snapshot. The ProjectSession lock covers the complete provider/approval turn, so runtime switching, resume, or context replacement cannot occur while approval is pending. A stale automatic identity or accepted grant terminates the turn and appends `turn_failed` instead of becoming an ordinary ToolResult followed by another provider call.

The CLI adds `--permission-mode read-only|workspace-write|danger-full-access` and `--approval ask|auto`, defaulting to `read-only + ask`. A one-shot ask fail-safely cancels and never reads stdin. Only the REPL displays the trusted action class, relative path, and UTF-8 byte count, with accept/reject/cancel plus EOF/Ctrl-C fail-safe handling. Capability ceiling and interaction mode remain orthogonal, and automatic approval cannot bypass any executor hard bound.

The fixed model-visible order is now `read_file, glob, grep, write_file`, sharing at most three resolved/executed calls per user turn. A fourth request receives only the limit result and creates no action lifecycle. `write_file(path, content)` accepts one portable workspace-relative path and complete UTF-8 content; the model cannot supply an overwrite flag, expected hash, mkdir, delete, patch, or approval field. The Host observes the real target: absence becomes `workspace-create + path-absent`, while an existing UTF-8 regular file becomes `workspace-overwrite + expected-state-sha256`. A malformed or hard-rejected write returns an error ToolResult before permission eligibility, consumes budget, and creates no action audit.

Paths reject absolute forms, Windows drives, backslashes, `.`/`..`, empty components, repeated/trailing `/`, and every intermediate or final symlink. The parent must already exist and is never created automatically. Content is capped at both 4,096 characters and 4,096 UTF-8 bytes. An overwrite source is capped at 1 MiB, must be a UTF-8 regular file, and binds digest/device/inode/mode. Create uses a same-directory temporary file, file fsync, hard-link installation onto an absent target, cleanup, and parent fsync. Overwrite uses a mode-preserving temporary file, file fsync, an exact digest/inode recheck, `os.replace`, and parent fsync. Success returns deterministic JSON containing `bytes_written`, `operation`, and relative `path`.

If the target is already visible but temporary cleanup or directory fsync fails, result and audit use `partial`, instruct the user to inspect the workspace, and prohibit automatic retry. This differs from `outcome-unknown`, which means no finish record exists. If provider continuation or turn commit fails after a write, the observed file effect and action audit remain, the candidate turn is not committed, and `turn_failed` is recorded.

This model-visible change advances the canonical system prompt to v5 and the adapter contract to v6. Anthropic and OpenAI-compatible ordinary count/create projections expose the same four closed schemas in the same order; compact-summary requests still expose no tools and parallel calls remain disabled. `ToolArguments` remains v1, new `turn_committed` remains schema v2, action-audit records remain schema v1, `context_compacted` continues v2/v3 replay, and Effective Context representations remain `ctx-v1`/`ctx-v2`. The new prompt/tool snapshot naturally changes current-binary context IDs without rewriting historical checkpoints.

See [0024: Foundation 4A Approval Coordination, Runtime Integration, and Controlled `write_file`](./decisions/0024-foundation-4a-approval-coordination-and-controlled-write.md) for the complete decision. Bash, patch/edit, delete, mkdir, parallel actions, and portable full-filesystem CAS remain explicitly out of scope.

## Foundation 4A Slice 10: Action Audit Observability

Durable action audits previously existed only in Host transcript replay state. The standalone CLI can now inspect a selected Session with `session actions [latest|id] [--limit N]`, while the REPL uses `/actions [count]` for the current Session. The default is the 20 most recent entries and explicit counts are bounded from 1 through 100. A truncated view remains chronological and an empty Session has an explicit result.

Output retains only the human audit summary: request sequence, tool, trusted action class, workspace-relative path, permission decision/reason, approval outcome, and derived final status/result code. Complete write content, executor messages, the absolute workspace, request/tool-use/grant/lease IDs, digests, workspace fingerprints, and precondition hashes are not rendered. Paths and persisted result codes escape control characters so stored data cannot reshape the terminal presentation.

The standalone path validates an existing Session root and strictly replays with `allow_repair=False`; it creates no directory, takes no writer lease, repairs no tail, changes no latest pointer, and appends no record. The REPL path reads already-replayed state under the current Session lock, calls no provider, and never enters model history. Corrupt or unsafe transcripts continue to fail closed.

This is a Host-only observability change. The reviewed canonical system prompt remains v5, the four-tool order and shared three-call budget are unchanged, and the adapter contract remains v6. ToolArguments v1, `turn_committed` schema v2, action-audit schema v1, `context_compacted` v2/v3 replay, and `ctx-v1`/`ctx-v2` representations are unchanged. See [0025: Foundation 4A Action Audit Observability](./decisions/0025-foundation-4a-action-audit-observability.md). JSON export, filters, repair/retry, full forensic dumps, and remote audit remain out of scope.

## Foundation 4B Slices 0–3: Exact Edit Preparation, Execution, and Authorization Composition

During Slices 0–3, the Host first established an internal, not-yet-model-visible `edit_file(path, old_text, new_text)` engine. It only accepts an existing, non-symlink, strict UTF-8 regular file of at most 1 MiB. `old_text` must be non-empty and occur exactly once, with overlapping occurrences counted as multiple matches; `new_text` may be empty. Old and new text are each capped at 4,096 characters and 4,096 UTF-8 bytes, and the result remains capped at 1 MiB. Preparation only reads, validates, and constructs complete candidate bytes. It creates no temporary file, changes no target, and writes no action audit.

Execution reuses the controlled-overwrite boundary: same-directory temporary file, mode preservation, file fsync, digest/device/inode revalidation, atomic `os.replace`, and parent-directory fsync. Stale state or any pre-replace failure returns `edit_not_applied` without changing the target. If replacement is visible but directory durability is unknown, the result truthfully reports `partial / edited_durability_unknown`. Success reports the result byte count, `operation: edited`, relative path, and one replacement.

Exact edit maps to the existing `workspace-overwrite` action and keeps the original canonical arguments, `expected-state-sha256` precondition, prepared-turn lease, single-use approval grant, and append-only Action Audit. Independent composition tests cover read-only denial, ask accept/reject/cancel, auto allow, stale rejection when the source changes during approval, strict audit replay, and CLI redaction. Action Audit renders the path but not old or new text.

Slices 0–3 intentionally do not change the tool catalog, provider projections, AgentLoop, or ProjectSession dispatch. The canonical system prompt remains v5, the adapter contract remains v6, and the model-visible order remains `read_file, glob, grep, write_file` with the shared three-call budget. ToolArguments v1, `turn_committed` schema v2, action-audit schema v1, `context_compacted` v2/v3 replay, and `ctx-v1`/`ctx-v2` representations are unchanged. That stage intentionally left schema/order, provider parity, system prompt, Effective Context identity goldens, and runtime dispatch for unified integration in Slice 4; Slice 4 has now completed that work. See [0026: Foundation 4B Exact Edit Preparation, Execution, and Authorization Composition](./decisions/0026-foundation-4b-exact-edit-preparation-execution-and-authorization.md).

## Foundation 4B Slice 4: Model-visible Exact Edit Integration

Slice 4 connects the proven internal exact-edit engine to the ordinary Agent path. The canonical tool order is now `read_file, glob, grep, write_file, edit_file`, and all five continue to share at most three sequential executions per user turn. The public schema permits only the three string fields `path`, `old_text`, and `new_text`: `path` and `old_text` must be non-empty, whitespace-only `old_text` is valid, and `new_text` may be empty for exact deletion. The catalog retains the 4,096-character and 4,096-UTF-8-byte bound for every string.

Anthropic and OpenAI-compatible ordinary count/create projections now expose the fifth closed schema in the same order and decode it into the same immutable `ToolArguments`; compact-summary requests still carry no tools and parallel calls remain disabled. The provider adapter contract advances to v7. The canonical system prompt advances to v6, distinguishes small uniquely anchored `edit_file` changes from `write_file` create/full-content replacement, and requires the model to honor permission, approval, stale-state, and visible-partial results.

`ProjectSession` prepares edits before permission eligibility, so a missing target, zero or multiple matches, no-op, symlink, invalid UTF-8, or size violation returns only a structured Tool error and creates no Action Audit. An eligible edit always maps to `workspace-overwrite` and reuses the source SHA-256 precondition, prepared-turn lease, single-use approval grant, durable execution start, atomic replacement, and known-outcome audit. Success records `succeeded / edited`; a pre-replace failure records `failed / edit_not_applied`; a visible replacement with unknown directory durability records `partial / edited_durability_unknown`.

The changed tool/prompt snapshot naturally changes current-binary Effective Context IDs, while representations remain `ctx-v1` full history and `ctx-v2` compacted context. ToolArguments remains v1, new `turn_committed` remains schema v2, Action Audit records remain schema v1, and `context_compacted` continues v2/v3 replay; old transcripts and checkpoints are not rewritten. See [0027: Foundation 4B Model-visible Exact Edit Integration](./decisions/0027-foundation-4b-model-visible-exact-edit-integration.md). At the Foundation 4B stage, regex/fuzzy/hunk/multi-replacement patching, create/delete/rename/mkdir, multi-file transactions, and Bash/test execution remained explicitly out of scope; later Foundation 4C adds controlled command execution separately.

## Foundation 4C Slices 0–3: Controlled Command Contract and Side-effect-free Preparation

This stage establishes an internal, not-yet-model-visible `run_command(argv, cwd, timeout_seconds)` preparation boundary whose primary future use is running tests, lint, format checks, and build verification. The contract is deliberately not called `run_test`: test files are still local programs that may access outside the workspace, read credentials, use the network, change multiple files, or start child processes. Without an OS sandbox, the Host must not imply that they are read-only, safe, or rollback-capable.

A request must contain exactly an argv array, workspace-relative cwd, and timeout. Argv has 1–64 UTF-8 strings; the first is a nonblank executable, each item is capped at 1,024 characters/bytes, and aggregate argv content is capped at 8,192 bytes. NUL is rejected, while pipe, wildcard, and command-substitution characters remain literal arguments. Cwd is either `.` or a portable `/` path capped at 64 components and 4,096 characters/bytes; preparation checks the workspace root and walks cwd with `lstat`, rejecting missing paths, files, and every symlink. Timeout is fixed to 1–300 seconds, future stdout and stderr caps are 32 KiB each, and the model cannot provide environment overrides or raise these Host bounds; the future executor copies only a closed non-secret-oriented environment allowlist and does not automatically forward provider API keys.

`RunCommandTool.prepare()` only reads immutable ToolArguments and validates the workspace root and cwd. It performs no PATH parsing or executable lookup, writes no Session or Action Audit record, and neither imports nor starts subprocess. Frozen `PreparedRunCommand` stores the exact request, argv tuple, canonical cwd, timeout, `dangerous` classification, and `ActionPrecondition.none()`; revalidation can reject a workspace root or cwd that becomes missing, a file, or a symlink before a future spawn.

Commands reuse existing `PermissionAction.DANGEROUS` rather than adding a misleading workspace-contained `workspace-execute` class. Read-only and workspace-write therefore deny them, while only danger-full-access produces ask or allow according to approval mode. Existing ActionIdentity v1 already binds canonical argv/cwd/timeout, workspace fingerprint, prepared-turn lease, and Effective Context; any request, runtime-generation, or context change alters the digest. It does not hash the executable, test code, or entire project tree and does not pretend to provide portable filesystem CAS.

Slices 0–3 intentionally added no executor, process group, CLI presentation, durable command audit, catalog entry, provider projection, or AgentLoop/ProjectSession dispatch, so an ordinary prompt could not yet request or run a command at that stage. The canonical model system prompt remained v6, the provider adapter contract remained v7, and the five model-visible tools plus shared three-call budget were unchanged. ToolArguments v1, ActionIdentity v1, Session and Action Audit schemas, `context_compacted` v2/v3 replay, and `ctx-v1`/`ctx-v2` representations were also unchanged. Later Slices 4–9 have now completed execution, cleanup, durable coordination, and model integration. See [0028: Foundation 4C Controlled Command Contract and Side-effect-free Preparation](./decisions/0028-foundation-4c-controlled-command-contract-and-preparation.md) for the original stage boundary.


## Foundation 4C Slices 4–6: Bounded Command Execution and Process-group Cleanup

The Host executor now passes prepared argv directly to `subprocess.Popen`, fixes `shell=False` and `stdin=DEVNULL`, and creates a separate process session/group for every command. Coquo does not parse pipes, redirects, wildcards, variable expansion, or command substitution. An approved executable may itself be a shell and interpret its arguments, so direct argv is still not a sandbox. The executor rechecks every workspace-root/cwd component immediately next to the spawn boundary and returns `command_cwd_invalid` without starting a process if that boundary has become invalid. Ordinary path APIs cannot eliminate the remaining local TOCTOU window completely, so this is not a hostile-concurrency guarantee.

Commands inherit only a closed Host environment allowlist, with `PWD` replaced by the actual cwd; provider API keys and arbitrary project variables are not forwarded automatically. Independent readers continuously drain stdout and stderr to EOF, retain only the first 32 KiB of each, and record captured/total byte counts plus truncation. Valid UTF-8 returns as text; other bytes return as base64, avoiding locale-dependent decoding and pipe-buffer deadlocks.

Timeout or `KeyboardInterrupt` triggers bounded TERM-to-KILL process-group cleanup. The same path cleans a lingering group when the main process exits normally but a background child still holds the pipes, preventing the normal-return path from waiting indefinitely. Success, nonzero exit, invalid cwd, missing executable, signal, timeout, cancellation, and incomplete cleanup have stable JSON status/result codes. Timeout, cancellation, signal, or any cleanup uncertainty is partial even when the main process returned nonzero, because the process may already have produced irreversible side effects; the system neither retries automatically nor claims filesystem, network, credential, or resource isolation.

The executor still owns no permission, Session, or CLI behavior. See [0029: Foundation 4C Bounded Command Execution and Process-group Cleanup](./decisions/0029-foundation-4c-bounded-command-execution-and-process-cleanup.md).

## Foundation 4C Slices 7–9: Durable Model-visible Command Integration

`run_command` is now connected to ProjectSession and ActionCoordinator. The exact request is prepared and fixed as `dangerous`, then follows `action_requested → permission_decided → optional approval_resolved → revalidate/grant consume → action_execution_started → spawn/execute → action_execution_finished`. `Popen` is allowed only after `action_execution_started` append+fsync succeeds. Any earlier audit, approval, lease, or revalidation failure prevents spawn; if finish persistence fails after spawn, the turn stays uncommitted and replay truthfully derives `outcome-unknown` from started-without-finish.

The REPL's `approval=ask` shows argv, relative cwd, and timeout; one-shot ask still cancels safely without reading stdin. `session actions` and `/actions` show only the executable, extra-argument count, cwd, timeout, permission/approval, and lifecycle/result code, not full argv in the ordinary audit summary. The Session still stores exact ActionIdentity to validate the request, workspace fingerprint, prepared-turn lease, runtime generation, and Effective Context binding.

The canonical tool order is now `read_file, glob, grep, write_file, edit_file, run_command`, and all six still share at most three sequential executions per user turn. Anthropic and OpenAI-compatible ordinary count/create requests project the same sixth closed schema, compact-summary requests remain tool-free, and parallel calls remain disabled. The provider adapter contract advances to v8; the canonical system prompt advances to v7; the empty full-context golden becomes `ctx-v1-e6b5274ea57642fd614842c58dfa74def0b6f0c1319b2c312b7c54d61b834ce3`.

ToolArguments remains v1, new `turn_committed` remains schema v2, ActionIdentity and Action Audit remain v1, ordinary Session records remain v1, `context_compacted` continues v2/v3 replay, and Effective Context representations remain `ctx-v1`/`ctx-v2`. Old transcripts/checkpoints are not rewritten, and resume or compaction never reruns a command. See [0030: Foundation 4C Durable Model-visible Command Integration](./decisions/0030-foundation-4c-durable-model-visible-command-integration.md).

## Fail-closed Linux `run_command` Sandbox

Production `run_command` now always executes through fixed `/usr/bin/bwrap`. The Host root is presented read-only and the current workspace is remounted read-write at its original absolute path. `/tmp` is a private tmpfs, `/dev` is minimal, and Host `/proc`, `/sys`, and `/run` are hidden behind empty private views. Command-visible HOME, TMP, UV cache, and XDG paths point into private `/tmp`; when the original HOME exists, known credential, Git, cloud, container, and Agent-state paths are masked. Commands may still change workspace files irreversibly, and the sandbox provides no rollback, resource quota, or hostile-concurrency transaction.

This Host cannot reliably create a network namespace, so it generates a BPF filter through `libseccomp.so.2` and has bubblewrap install it after mount/namespace setup. The filter denies `socket`, `socketcall` when available, and `io_uring_setup`, blocking both Internet and Unix-domain socket creation. Bubblewrap must produce private `--info-fd` activation evidence, while `--block-fd` prevents requested argv from starting before Host validation and release. Missing Linux support, fixed bwrap, libseccomp, filter setup, spawn, or activation returns `command_sandbox_unavailable`; the original argv is never retried directly on the Host.

Dependency readiness now also reads fixed `/usr/bin/bwrap --help` under a closed environment, a two-second timeout, and a 64 KiB output limit, and requires every ADR 0080 option: `--disable-userns`, `--block-fd`, `--info-fd`, and `--seccomp`. An older bubblewrap missing any required capability is reported unavailable before a user command is attempted. Production launch arguments never degrade, and real sandbox tests run only when the same production activation probe is available. This Host-only correction changes no tool, permission, Session, prompt, provider, or Effective Context contract. See [0137: Command Sandbox Capability Readiness](./decisions/0137-command-sandbox-capability-readiness.md).

PermissionGate remains orthogonal: `run_command` still proceeds only within `danger-full-access` under ask or auto, and approval never disables sandboxing. Direct argv, `shell=False`, closed stdin/environment, the 1-to-300-second timeout, independent 32 KiB stdout/stderr retention, continuous drain, cancellation, and TERM-to-KILL process-group cleanup remain. Tool name, order, schema, provider projection, adapter contract v25, ToolArguments v1, ActionIdentity v1, Action Audit, and Session schemas do not change. The model-visible guarantee advances the system prompt to v22 and updates the current empty full-context identity to `ctx-v3-a28664ae5f5143fac7e7b5936d78cb59c31643eb1a07eb7f41d73167625d67f8`. See [0080: Fail-closed Linux Command Sandbox](./decisions/0080-fail-closed-linux-command-sandbox.md).

## Host Workbench Diagnostics and Prompt History Search

`/status` now combines one local-only `ProjectStatus` snapshot containing the current Session, permission/approval modes, latest observed context pressure, all three tool budgets, sandbox dependencies, and the existing redacted runtime status. It performs no provider count or generation, executes no user command, and modifies neither Session nor Action Audit. `/sandbox check` separately uses the production `RunCommandTool` activation path to execute fixed `/usr/bin/true`, verifying Linux, fixed bubblewrap, seccomp-filter construction, and the activation gate. The probe contains no user argv or model call and creates no durable audit; it authorizes no later command, and failure still has no Host fallback.

Existing `/tools` retains its durable-tool-ledger meaning. New `/tools catalog` displays permission classes and current-mode availability in canonical 21-tool order without breaking the old command. `/actions last` selects only the newest strictly replayed Action Audit view. Command approval now states the actual read-only Host, writable workspace, and socket-denial boundary. Trusted `run_command` result codes append conservative `Next:` guidance, while timeout, signal, cancellation, or cleanup uncertainty never triggers automatic retry or a rollback claim.

The persistent TTY connects Ctrl-R to a dedicated prompt_toolkit SearchToolbar for case-insensitive reverse search over the current Session's latest 1,000 committed user prompts. Accepting a match restores one editable draft and requires another Enter to submit. Existing history replacement on Session switches prevents cross-Session search. The existing `/clear` command gains real frontend regression coverage proving that it writes only the terminal reset sequence and calls no model or Session/history/transcript mutation. This slice changes only the Host workbench and input presentation: canonical system prompt remains v22, adapter contract remains v25, and the 21-tool schema/order, Effective Context identities, and all Session/Action Audit schemas remain unchanged. See [0081: Host Workbench Diagnostics and Prompt History Search](./decisions/0081-host-workbench-diagnostics-and-prompt-history-search.md).

## Host Policy and Tool Discoverability

`/tools catalog <tool-name>` reads order and input schema from the current canonical `TOOL_CATALOG`, displaying argument shapes, required state, permission class, current-policy availability, and one Host-maintained summary of major hard boundaries. It invokes no tool and never implies that runtime workspace, symlink, size, conflict, timeout, output, or durability validation can be skipped. The no-argument catalog and existing durable `/tools` ledgers remain compatible.

`/permissions` evaluates the real pure `PermissionGate` to display `allow | ask | deny` and the stable reason for all six action classes, while reporting sandbox dependency status separately so policy permission is not mistaken for execution readiness. Optional mode and approval arguments compute only a "policy preview (not applied)" and never change process configuration, authorize an action, or write Session state. `/help policy` consolidates the orthogonal permission, approval, and command-sandbox boundaries. Completion candidates now extend through canonical tool names, permission modes, Action Audit status/tool filters, and common subcommands. Unknown top-level, provider, session, or tool names show `Did you mean` only for one close candidate and never rewrite input or dispatch it automatically. Completeness coverage requires every canonical tool to have a hard-bound summary, while a real ProjectSession regression proves that these discovery commands invoke no provider and leave transcript, history, usage, Action Audit, and Session metadata unchanged. This Host-only slice leaves system prompt v22, adapter contract v25, the 21-tool schema/order, Effective Context identities, and every durable schema unchanged. See [0082: Host Policy and Tool Discoverability](./decisions/0082-host-policy-and-tool-discoverability.md).

## Foundation 5A: Root `AGENTS.md` Project Instructions

Coquo recognizes only `AGENTS.md` at the workspace root. Missing means no project instructions; an existing entry is opened from the root directory descriptor with no-follow semantics and must retain non-symlink regular-file identity. Content is strict UTF-8, may be empty, preserves exact LF or CRLF bytes, rejects NUL, and is capped at 32 KiB for both characters and UTF-8 bytes. The loader does not search parents, child directories, or a Git root, does not merge a hierarchy, and never automatically reads `CLAUDE.md`, `COQUO.md`, or another compatibility name. An invalid existing file fails clearly before an ordinary provider call instead of being treated as missing.

AgentLoop reads one `ProjectInstructionsSnapshot` while preparing each user turn and freezes it with the system prompt, tool catalog, and committed history. Every provider continuation, preflight, and ActionLease recheck in that turn reuses the same snapshot. Even if a tool overwrites `AGENTS.md` during the turn, that turn completes against the old snapshot and only the next turn reloads the new file. Manual and automatic compaction use one snapshot for source and candidate; a concurrent change during manual compaction conflicts under the existing CAS rule. Resume and Session switching do not restore a historical instruction copy and instead screen the next Effective Context against the current workspace file. Instruction text is not written to transcripts, checkpoints, Action Audit, or Session records.

The provider-neutral `ConversationRequest` carries a separate project-instructions field. Anthropic projects it as a second system text block after the canonical system prompt, while OpenAI-compatible projects it as a second system message. Count, create, and stream share the same construction, so the exact text participates in preflight and token counting. Dedicated compact-summary and Session-title requests do not expose project instructions. The canonical prompt states that project guidance is subordinate to Host policy, tool hard bounds, and the current direct user request; it cannot authorize actions, relax permission or approval, elevate ordinary file/tool output into instructions, or prove execution. `/instructions` displays only presence, relative path, UTF-8 byte count, representation, and fingerprint. It reveals no content, invokes no provider, consumes no tool budget, and mutates no Session state.

This model-visible change advances the system prompt to v23 with fingerprint `v23-3858281d3354288e15dd51569d896fe22c6e4842d8c8b5192dc4a2e296792a55`, while the provider wire projection advances the adapter contract to v26. Current full and compacted Effective Context representations advance to `ctx-v5`/`ctx-v6` and include either the exact instruction snapshot or explicit absence in identity. The empty full-context identity without project instructions is `ctx-v5-0700acbf613c3896f65ea82d5fa78f7139406f50e9b5227bcabedf223708d39b`. Legacy `ctx-v1` through `ctx-v4` remain valid and replayable. Session and Action Audit schemas do not advance, and old JSONL is not rewritten. See [0083: Foundation 5A Root AGENTS.md Project Instructions](./decisions/0083-foundation-5a-root-agents-project-instructions.md).

## Deterministic Offline Host Eval Baseline

`coquo eval` now provides the versioned `host-baseline-v3` suite. Its first four built-in cases cover bounded reading, an auto-policy controlled create, a read-only write denial, and skipping later actions after the first action in a batch fails. A fifth case covers model-proposed Task admission, exact user confirmation, foreground planning and execution, ordinary `skill_search` and `skill_load` inside the execution Stage, human verification, and completion. Each case fixes its prompt, initial UTF-8 files, scripted fake-provider responses, permission/approval modes, and expected Host facts. The runner always opens the real `ProjectSession` in a fresh temporary workspace with isolated provider-configuration paths, so execution crosses the ordinary AgentLoop, PermissionGate, tools, Session commit, and Action Audit without reading user credentials, real-provider configuration, or the network.

Scoring occurs after the Session closes. The runner strictly replays through `SessionStore` and compares committed-turn count, the complete workspace entry and file-byte identities, chronological durable tool ledgers across all relevant Turns, and Action Audit lifecycles. The Task case also verifies accepted admission, one uniquely sourced Task, final state, and Stage kinds/outcomes. Final assistant text is compared by exact UTF-8 byte count and SHA-256 identity, but cannot override workspace facts: a regression test proves that even a response claiming creation fails when the target file is absent. Text reports expand only failed checks, while stable JSON excludes temporary paths, timestamps, random UUIDs, and original text, making it suitable for local regression and later CI comparison. `eval list` lists cases, `eval run <id>` runs one, and `eval run all --format json` runs the machine-readable baseline.

This is a Host-correctness baseline, not a pytest replacement and not an evaluation of real-model planning quality, randomness, or generalization. It runs no credentials, network, API spend, command sandbox, performance benchmark, leaderboard, or external fixture. A scripted trajectory passing proves only that the fixed Harness path retains its declared invariants. This Host-only entry point leaves model-visible tools, system prompt v23, adapter contract v26, Effective Context, Session schemas, and Action Audit schemas unchanged. See [0084: Deterministic Offline Host Eval Baseline](./decisions/0084-deterministic-offline-host-eval-baseline.md).

## Actual Coding Task Eval

`coding-task-v1` adds two fixed small Python tasks, `inventory-validation` and `slug-normalization`. `eval task prepare TASK OUTPUT` materializes only the README, one production file to repair, and visible `unittest` files; it never copies Host-private tests. `eval task score TASK WORKSPACE` performs read-only scoring over an existing candidate. The scorer first checks the complete entry shape and SHA-256 identities of protected README/test files, then copies declared files through component-by-component no-follow reads into a new temporary scoring workspace before injecting private tests. The candidate receives no hidden files or test artifacts. Extra entries, protected changes, symlinks, special files, a file above 1 MiB, more than 4 MiB total, or more than 100 scanned entries fail or fail closed.

Visible and private tests use fixed `/usr/bin/python3 -m unittest discover ...` commands through the production `RunCommandTool` bubblewrap/seccomp sandbox; Eval has no direct-subprocess bypass. `eval task run TASK --real-provider` additionally requires an explicit `--profile`, `--profile-id`, or `--model`, then runs the ordinary ProjectSession, AgentLoop, PermissionGate, tools, and Action Audit under fixed `danger-full-access + auto` inside a newly created task directory. Without `--output`, that directory is removed after scoring; with `--output`, it remains for inspection. Tool lifecycle events go to stderr, while stdout contains only a stable score without workspace paths, provider text, or random IDs. Host checks cover agent completion, committed turn count, action certainty, workspace shape, protected files, visible tests, and hidden tests, independent of final model prose.

The ordinary command sandbox read-only mounts the Host root, so real-task Eval additionally masks the current Coquo source checkout inside bubblewrap; an installed build masks at least the evaluator module and bytecode cache before rebinding the task workspace. Hidden tests are generated only in a separate scoring directory after the agent Session closes, so model tools cannot inspect their text. This slice leaves all 21 model-visible tools, system prompt v23, adapter contract v26, `ctx-v5`/`ctx-v6`, ToolArguments, Session, Action Audit, and compaction schemas unchanged. It is not an arbitrary benchmark loader, model leaderboard, retry framework, or unauthorized provider smoke. See [0085: Actual Coding Task Eval](./decisions/0085-actual-coding-task-eval.md).

## Durable Task Identity and Host Management

Coquo now distinguishes `Task -> Stage -> Turn -> Action`: a Task is a user objective that can survive restart; each future Stage advances one bounded step while retaining the ordinary Turn's 8/32/24 budgets; every Action still passes PermissionGate, approval, tool hard bounds, and Action Audit. This first stage implements only the outer Task identity, not Stage execution.

Each Task has an independent `task_header` schema-v1 transcript at `<workspace>/.coquo/tasks/<workspace-fingerprint>/<task-id>.jsonl`. It stores a canonical UUID4, workspace identity, one existing owner Session, a bounded objective, at most 16 acceptance criteria, and a UTC creation timestamp; its current derived status is `ready`. TaskStore enforces no-follow regular-file reads, closed schemas, strict complete-line replay, bounded scans, and installation through a fsynced temporary file, exclusive hard link, and directory fsync. It does not claim complete non-creation if the final name is visible but durability is uncertain. List and inspect never create or repair state.

Standalone commands provide `task create/list/show`, while the REPL provides `/task start/list/show`; REPL creation binds the then-current Session. These are Host-only commands: they invoke neither provider nor tool, consume no Turn budget, write no Session transcript or Action Audit, and do not elevate Task text into system authority or Action authorization. System prompt remains v23, adapter contract remains v26, and the 21 model-visible tools, ToolArguments v1, `ctx-v5`/`ctx-v6`, and Session/compaction/Action Audit schemas remain unchanged. ADR 0087 subsequently adds Stage records and a writer lease; `/task continue` and execution recovery remain future slices. See [0086: Durable Task Identity and Host Management](./decisions/0086-durable-task-identity-and-host-management.md).

## Durable Stage Lifecycle and Turn Evidence

Task transcripts add closed schema-v1 `stage_started`, `stage_committed`, and `stage_failed` records. Replay requires strict start/terminal alternation after the header, exact record sequences, contiguous Stage numbers from one, unique Stage UUIDs, matching identities and owner Session, and nondecreasing timestamps. No Stage is `ready`; commit yields `paused`; failure yields `blocked`; an unterminated start is always `interrupted` during ordinary inspection, while only the current process holding the live writer may render `stage-in-progress`.

`TaskStore.open()` provides one nonblocking exclusive `TaskWriter`. Every append candidate-replays first, then validates pathname/inode and transcript limits, writes completely, and fsyncs before changing memory. An I/O or fsync failure after writing begins returns a typed "record may be visible" error and poisons the writer, requiring release plus strict inspection instead of automatic retry. `SessionStore.turn_evidence()` accepts only a real `turn_committed` record and returns Session ID, Turn number, record sequence, timestamp, and the exact newline-terminated raw JSONL-line SHA-256 without dialogue or tool bodies. The Host obtains commit evidence itself; callers cannot claim a digest.

`task list` now includes Stage count, while `task show` and `/task show` render the latest objective, outcome, committed Turn evidence, failure reason, or interrupted recovery guidance. This is still not an execution entry point: there is no `/task continue`, provider invocation, completion proposal, cumulative Task budget, or automatic recovery. System prompt v23, adapter contract v26, all 21 tools and 8/32/24 budgets, ToolArguments v1, `ctx-v5`/`ctx-v6`, and Session/compaction/Action Audit schemas remain unchanged. See [0087: Durable Stage Lifecycle and Turn Evidence](./decisions/0087-durable-stage-lifecycle-and-turn-evidence.md).

## Foreground Task Stage Execution and Recovery

`/task continue <task-id> <stage-objective>` now maps one Task Stage to a real `ProjectSession.prompt()`. It therefore reuses the ordinary AgentLoop, 8/32/24 Turn budget, PermissionGate, per-Action approval, tool hard bounds, command sandbox, Action Audit, and atomic Session commit instead of creating a second long-task tool loop. Execution requires the Task's owner Session to be current; a Task is never permission or approval.

The Host first builds one bounded UserMessage beginning `[Coquo durable Task Stage]`. Its canonical JSON contains only the Task objective, acceptance criteria, accepted plan, the latest 16 redacted Stage summaries, current Stage, cumulative usage, total budget, and remaining allowance. New `stage_started` and `stage_committed` schema v2 records respectively store the Session baseline and complete prompt SHA-256 before provider work, then copy provider/token/tool-ledger counts after a real Turn commit. A normally failed `stage_failed` schema-v2 record also stores content-free provider and tool-attempt counts. None copies dialogue, arguments, results, or audit bodies; legacy Stage v1 continues to replay with accounting explicitly unavailable.

`/task recover` invokes neither provider nor tool. It searches only after the durable baseline for a committed Turn with an exact user-message digest match. No match fails the Stage as `interrupted`; one match binds the real Turn; multiple matches leave the Task unchanged and fail closed. It can also restore a missing plan or completion record after the Stage committed but before that protocol metadata append. Provider failure, cooperative cancellation, missing Turn commit, and Host failure map to closed Stage failure reasons. If the Turn committed before an exception, the Host binds that evidence before reporting the error and never blindly replays side effects.

The canonical system prompt advances to v24 and states that Task framing is untrusted data. Execution ends with `TASK_COMPLETION_PROPOSAL: yes|no`; planning uses `TASK_PLAN_JSON:`, and each must be the final nonblank line. The protocol remains exact in Session transcript for recovery but is removed from valid Task results and streamed terminal display; `/task run` also reports its exact Stage count and stop reason. Adapter contract v26, all 21 tools, and their order/schemas remain unchanged. Effective Context representations remain `ctx-v5`/`ctx-v6`; because exact prompt content participates in identity, the current empty full-context ID without project instructions becomes `ctx-v5-bd663ddc5d94403891caac9f91d76a319200967331a18163859e203cd6bbb116`. See [0088: Foreground Task Stage Execution and Recovery](./decisions/0088-foreground-task-stage-execution-and-recovery.md).

## Task Planning, Acceptance, Budgets, and Management

`/task plan` spends one planning Stage to propose one to 32 bounded steps; `/task plan accept` only records explicit acceptance and executes no Action. `/task run` serially executes at most 16 accepted steps in the foreground, each with a fresh ordinary Turn. Progress advances only for committed execution Stages whose objectives exactly match the accepted plan in order, so unrelated manual Stages cannot skip planned work. A run stops at its command limit, plan exhaustion, completion proposal, budget exhaustion, interruption, or terminal state.

Default cumulative Stage/provider/tool allowances are 32/768/1,024, with optional input/output token ceilings. These are admission ceilings between Stages: an admitted Stage retains the complete ordinary Turn boundary and is never dynamically shortened. Committed Stages and normally failed attempts both charge their Host-observed usage, and no later Stage is admitted after a ceiling is met or exceeded. A legacy or crash-recovered Stage with unavailable usage blocks later provider/tool admission and any configured token ceiling instead of treating unknown work as zero.

A model `yes` appends only `completion-proposed`. `/task verify` binds human evidence to the current proposal's Stage and one acceptance criterion. If later work invalidates that proposal, old evidence does not carry forward. `/task complete` requires a current proposal and every criterion. Tasks also support closed completed/cancelled/failed outcomes, rename, reversible archive, complete timeline, list filtering, and independent derivation with immutable parent provenance. These Host management commands never enter ordinary model conversation.

New configuration, plan proposal/acceptance, completion proposal, acceptance verification, terminal, rename, and archive records each use schema v1 without changing Session, Action Audit, provider projection, or compaction schemas. Background workers, schedulers, SubAgents, teams, worktree orchestration, parallel Stages, and Task-level blanket approval remain unavailable. See [0089: Task Planning, Acceptance, Budgets, and Management](./decisions/0089-task-planning-acceptance-budgets-and-management.md).

## Structured Task Acceptance and Independent Review

A new Task may append a schema-v1 `task_acceptance_contract` before its first Stage, classifying at most 16 criteria as `human`, `path-exists`, `path-unchanged`, `command-succeeds`, `action-audit-certain`, or `independent-reviewer`, with a `manual` or `auto-verified` completion policy. Legacy header-only Tasks are not rewritten and continue to replay as human acceptance with manual completion. Every criterion kind has one trusted source: explicit human evidence, deterministic Host checks, or an independent reviewer; the wrong source cannot satisfy it.

`/task verify host` invokes no model. It performs no-follow path type checks, creation-time file SHA-256 baselines, owner-Session Action Audit certainty, or bounded command checks. Command checks reuse production `RunCommandTool` bubblewrap, seccomp, environment, timeout, output, and cleanup boundaries, but mount the workspace read-only and fail closed when the sandbox is unavailable. Every Host/reviewer attempt appends a schema-v1 `task_acceptance_checked`; only `passed` appends acceptance verification with the matching source.

`/task review` reuses the current provider/API/model route but constructs a separate no-tools request without Executor Session history. The reviewer sees only explicitly declared regular-file snapshots and bounded Host facts; `.git`, `.coquo`, and every `.env*` component are rejected. Its response must be strict JSON covering every requested criterion. Review usage is counted separately from ordinary Turns and compaction, and neither its response nor an error enters the Executor Session transcript.

Even `auto-verified` appends `completed` only after a current committed execution Stage has a model completion proposal and every criterion for that proposal has matching verification. A later Stage makes the old proposal, checks, and verifications ineffective for completion without deleting history. The canonical system prompt advances to v25; provider adapter v26, all 21 tool schemas, Session, Action Audit, and Effective Context representations remain unchanged. The no-project-instructions empty full-context ID becomes `ctx-v5-7fefaa42ca4226a17e7312fc723ecb3add2b6e8c96a0ac02671e69048156d401`. See [0090: Structured Task Acceptance and Independent Review](./decisions/0090-structured-task-acceptance-and-independent-review.md).

## Foundation 4D Slices 0–4: Controlled Single-directory Creation

The seventh model-visible tool, `mkdir(path)`, creates exactly one missing workspace-relative directory. Paths use portable `/` separators and are bounded by character count, UTF-8 byte count, component count, and per-component bytes. Absolute paths, Windows drives, backslashes, empty components, `.`, `..`, NUL, missing parents, non-directory parents, and every observed symlink are rejected before permission. Any existing filesystem entry at the target is also a hard rejection and creates no Action Audit. Preparation has no side effects and returns only an immutable path, the `workspace-create` classification, and a `path_absent` precondition.

`read-only` denies mkdir, while `workspace-write` and `danger-full-access` use the existing ask/auto policy. Approval binds the exact ActionIdentity and target absence; a target that appears while approval is pending becomes stale. One non-recursive directory creation may occur only after `action_execution_started` has been appended and fsynced. The executor rechecks the path and target, then fsyncs both the new directory and its parent. A pre-create failure records `failed / directory_not_created`; a visible directory whose durability cannot be confirmed records `partial / directory_created_durability_unknown`, with no automatic retry or rollback claim.

A provider-continuation or turn-commit failure does not delete an already-created directory. The durable Action Audit preserves the truthful result while the candidate turn remains uncommitted. CLI approval renders `workspace-create mkdir path='...'`; `session actions` and `/actions` show only the relative path, permission/approval lifecycle, and result. Ordinary path APIs still do not constitute an OS sandbox or a complete portable filesystem transaction under hostile concurrency; this tool follows the existing local single-user workspace boundary and grants no outside-workspace authority.

The canonical tool order is now `read_file, glob, grep, write_file, edit_file, run_command, mkdir`, and all seven retain the shared limit of three sequential executions per user turn. Anthropic and OpenAI-compatible ordinary count/create requests project the same closed schema, while compact-summary requests expose no tools. The provider adapter contract advances to v9, the canonical system prompt advances to v8, and the empty full-context golden becomes `ctx-v1-12b7d8f648ac4909132c0176de74297f8d00805b887e190d51767b6fc1e2c986`. ToolArguments v1, ActionIdentity v1, `turn_committed` schema v2, Action Audit schema v1, `context_compacted` v2/v3 replay, and `ctx-v1`/`ctx-v2` representations do not advance. See [0031: Foundation 4D Controlled Single-directory Creation](./decisions/0031-foundation-4d-controlled-single-directory-creation.md).

## Foundation 4E Slices 0–9: Controlled No-overwrite File Move

The eighth model-visible tool, `move_file(source, destination)`, moves one existing non-symlink regular file to a completely absent workspace-relative destination. Both paths use bounded portable `/` syntax. Both parents must already exist as real directories with no symlink in either path, and the source and destination parent must be on the same filesystem. A directory source, identical paths, missing parent, cross-filesystem move, or any existing destination entry is hard-rejected before permission evaluation, creates no Action Audit, does not create parents, and never replaces the target.

Side-effect-free preparation combines the source device/inode/mode/size/mtime/ctime/link count, both parent identities, and destination absence into one `expected-state-sha256`. Movement has its own `workspace-move` permission class: `read-only` denies, while `workspace-write` and `danger-full-access` follow ask/auto. Approval binds the exact ActionIdentity and a single-use grant; a change to the source, destination, or either parent while approval is pending becomes a stale precondition before execution. CLI approval and Action Audit show only the two workspace-relative paths and lifecycle result, not absolute paths, fingerprints, or internal IDs.

Filesystem effects are forbidden until `action_execution_started` has been appended and fsynced. The executor rechecks both parents through directory descriptors, revalidates source identity and destination absence, then performs `exclusive destination hard-link → destination-parent fsync → source unlink → source-parent fsync`. An exclusive hard link is used instead of ordinary `rename()` so a concurrently appearing destination is never overwritten. The tool does not read file content, so binary and larger regular files can also be moved.

Hard-link plus unlink is not a single filesystem transaction. If durability confirmation or source unlink fails after the destination link is visible, the Host truthfully reports a partial result: both names may remain, or the move may be visible while source-removal durability is unknown. The model must inspect both paths and must not retry automatically. Provider-continuation or turn-commit failure does not undo the real file effect; durable Action Audit retains the known result while the candidate turn remains uncommitted. If `action_execution_started` persistence fails, the destination must not appear.

The canonical tool order is now `read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file`, and all eight retain the shared limit of three sequential executions per user turn. Anthropic and OpenAI-compatible ordinary count/create requests project the same closed schema, while compact-summary requests expose no tools. The provider adapter contract advances to v10, the canonical system prompt advances to v9, and the empty full-context golden becomes `ctx-v1-b18f599515bec3196b10a2bf877d39f1da19f6a9eb3b4f1e123ccc3cd16da760`. ToolArguments v1, ActionIdentity v1, `turn_committed` schema v2, Action Audit schema v1, ordinary Session schemas, `context_compacted` v2/v3 replay, and `ctx-v1`/`ctx-v2` representations do not advance. Directory movement, destination replacement, cross-filesystem copy/delete fallback, file deletion, and recursive deletion remain out of scope. See [0032: Foundation 4E Controlled No-overwrite File Move](./decisions/0032-foundation-4e-controlled-no-overwrite-file-move.md).

## Foundation 4F Slices 0–6: Controlled Regular-file Deletion

The ninth model-visible tool, `delete_file(path)`, permanently deletes exactly one existing non-symlink regular file. Its path uses bounded portable workspace-relative syntax. The parent must already exist as a real directory, and no path component may be a symlink. A missing target, directory, symlink, invalid path, or unsafe parent is hard-rejected before permission evaluation and creates no Action Audit. The tool does not read content, so binary and larger regular files may be deleted; directory, glob, batch, recursive, trash, backup, and undo behavior remain out of scope.

Side-effect-free preparation freezes the target device/inode/mode/size/mtime/ctime/link count and parent identity into an `expected-state-sha256`. Deletion has its own `workspace-delete` permission class: `read-only` denies, while `workspace-write` and `danger-full-access` follow ask/auto. Approval shows only the workspace-relative path and binds the exact ActionIdentity, prepared-turn lease, and single-use grant. A target or parent change while approval is pending becomes a stale precondition before execution.

Filesystem effects are forbidden until `action_execution_started` has been appended and fsynced. The executor rechecks parent and target identity through a real parent-directory descriptor, unlinks the name, then fsyncs the parent. POSIX provides no portable conditional-unlink primitive used by this project, so a very small TOCTOU window remains between the final stat and unlink. This contract targets the current local single-user, controlled-concurrency model and does not claim resistance to a hostile process racing inside the same workspace.

Success returns `succeeded / file_deleted` and `{"operation":"deleted","path":"..."}`. A known no-delete result is `failed / file_not_deleted`. If unlink succeeds but parent fsync fails, the result is `partial / file_deleted_durability_unknown`. Partial means the name is already gone while durability is unknown; the model must inspect state first and must not retry automatically, because a retry could delete a later replacement at the same path. Provider-continuation or turn-commit failure does not undo a real deletion; durable Action Audit retains the effect while the candidate turn remains uncommitted. If `action_execution_started` persistence fails, the file must not disappear.

The canonical tool order is now `read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file`, and all nine retain the shared limit of three sequential executions per user turn. Anthropic and OpenAI-compatible ordinary count/create requests project the same closed schema, while compact-summary requests expose no tools. The provider adapter contract advances to v11, the canonical system prompt advances to v10, and the empty full-context golden becomes `ctx-v1-42200fbe6c48a76d91ac0dde71e12be0e41674b1ad06c8b82bf82a541e3049e8`. ToolArguments v1, ActionIdentity v1, `turn_committed` schema v2, Action Audit schema v1, ordinary Session schemas, `context_compacted` v2/v3 replay, and `ctx-v1`/`ctx-v2` representations do not advance. The next independent slice should consider only empty-directory removal and re-prove empty-state, concurrent-child, and parent-durability boundaries rather than expanding directly to recursive deletion. See [0033: Foundation 4F Controlled Regular-file Deletion](./decisions/0033-foundation-4f-controlled-regular-file-deletion.md).

## Foundation 4G Slices 0–6: Controlled Empty-directory Deletion

The tenth model-visible tool, `delete_directory(path)`, permanently deletes exactly one existing empty non-symlink directory. Paths retain the bounded portable workspace-relative grammar; the parent must already exist, be a real directory, and contain no symlink component. A missing target, regular file, symlink, non-empty target, invalid path, or unsafe parent is hard-rejected before permission evaluation and creates no Action Audit. An empty path cannot express the workspace root, so the workspace itself cannot be deleted; glob, batch, recursive, trash, backup, and undo behavior remain out of scope.

Side-effect-free preparation freezes the target directory device/inode/mode/mtime/ctime/link count, parent identity, and observed empty state into an `expected-state-sha256` precondition. Directory deletion reuses `workspace-delete`: `read-only` denies it, while both writable modes follow ask/auto. Approval shows only the workspace-relative path and binds the exact ActionIdentity, prepared-turn lease, and single-use grant. Target, parent, or directory-content changes during approval fail closed as stale.

No filesystem effect may occur before `action_execution_started` is appended and fsynced. The executor rechecks identity and empty state through real parent and no-follow target directory descriptors, then invokes OS `rmdir` and fsyncs the parent. The final `rmdir` atomically requires the target to remain empty, so a child created after the empty precheck safely causes failure and preserves the tree. A very small TOCTOU window remains between identity checking and name-based `rmdir`; the local single-user contract does not claim resistance to hostile concurrent workspace mutation.

Success is `succeeded / directory_deleted` with `{"operation":"deleted","path":"..."}`. A known no-delete result is `failed / directory_not_deleted`. If the name is removed but parent durability is unknown, the result is `partial / directory_deleted_durability_unknown` and must not be retried automatically. Provider-continuation or turn-commit failure does not undo a real deletion; durable Action Audit remains while the candidate turn is uncommitted. Final-audit failure retains `outcome-unknown` recovery semantics.

The canonical order is now `read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file, delete_directory`; all ten retain the shared limit of three sequential executions per user turn. The provider adapter contract advances to v12, the canonical system prompt advances to v11, and the empty full-context golden becomes `ctx-v1-64ce77996397ddd1f84a27248ddd3e47224948563db506e3bfbda96939799406`. ToolArguments v1, ActionIdentity v1, `turn_committed` schema v2, Action Audit schema v1, ordinary Session schemas, `context_compacted` v2/v3 replay, and `ctx-v1`/`ctx-v2` representations do not advance. Recursive and non-empty directory deletion remain explicitly prohibited. See [0034: Foundation 4G Controlled Empty-directory Deletion](./decisions/0034-foundation-4g-controlled-empty-directory-deletion.md).

## Foundation 1E: Bounded One-level Directory Listing

The eleventh model-visible tool, `list_directory(path)`, uses `.` for the workspace root and bounded portable workspace-relative directory syntax otherwise. The target must exist, must be a directory, and may contain no symlink component. The tool enumerates only direct children: it does not recurse, follow symlinks, read file content, or apply `.gitignore`. Hidden entries are included, and each item is classified as `file`, `directory`, `symlink`, or `other`.

Results are stably sorted by complete workspace-relative UTF-8 path and returned as `{"path":"...","type":"..."}` JSONL. One call scans at most 10,000 direct entries; exceeding the scan limit fails the whole call so raw filesystem enumeration order is never misreported as a stable prefix. After a complete scan, at most 200 items and 32 KiB are returned. Count or byte caps retain complete records and append `{"truncated":true}`; an untruncated empty result means the bounded scan observed an empty directory. An entry that disappears or cannot be no-follow statted fails the whole call, while concurrent directory mutation is still not claimed to provide an atomic snapshot.

`list_directory` reuses `workspace-read`, so every permission mode automatically allows it without human approval. It still passes through the prepared-turn lease, PermissionGate, durable Action Audit, exact tool-use/result pairing, and atomic turn commit. AgentLoop and ProjectSession retain explicit composition and dispatch; all eleven tools share three sequential executions per user turn and a fourth receives the structured limit result. New argument-bearing turns remain `turn_committed` schema v2, and existing Sessions, resume, and compaction require no rewriting or tool replay.

Anthropic and OpenAI-compatible ordinary count/create requests project the same eleventh closed schema, compact-summary requests remain tool-free, and parallel calls remain disabled. The provider adapter contract advances to v13, the canonical system prompt advances to v12, and the empty full-context golden becomes `ctx-v1-7776df09d6ace66621cee46719755307b7d816bccde25f61064b4205c689b3b2`. ToolArguments v1, ActionIdentity v1, Session and Action Audit schemas, `context_compacted` v2/v3 replay, and the `ctx-v1`/`ctx-v2` representations remain unchanged. Recursive trees, metadata/stat, symlink targets, and ignore-aware views remain out of scope. See [0035: Foundation 1E Bounded One-level Directory Listing](./decisions/0035-foundation-1e-bounded-directory-listing.md).

## Foundation 4H: Controlled Bounded Regular-file Copy

The twelfth model-visible tool, `copy_file(source, destination)`, accepts two bounded portable workspace-relative file paths. Both parents must already exist and neither path may contain a symlink. The source must be an existing regular file of at most 1 MiB, while the destination must be entirely absent. The tool copies raw bytes and basic source `rwx` permission bits, strips setuid/setgid/sticky special bits, and leaves the source unchanged. It does not require UTF-8 and does not copy owner, timestamps, ACLs, xattrs, sparse/reflink layout, or hard-link relationships.

Side-effect-free preparation performs an `O_NOFOLLOW` bounded source read and freezes source device/inode/mode/size/mtime/ctime/link count, content SHA-256, both parent identities, and destination absence. Exact state is revalidated after approval and again when execution starts; source content/identity, parent, or destination changes fail closed as stale/conflict. Prepared bytes are the approved snapshot, so the executor neither reinterprets text nor follows a replaced pathname.

`action_execution_started` must be durably appended before any filesystem effect. The executor exclusively creates a hidden temporary file in the destination parent, writes the prepared bytes, sets basic rwx permission bits, and fsyncs the file. It then installs the missing destination through an exclusive hard link, removes the temporary name, and fsyncs the parent. Success returns `file_copied` and compact JSON containing source, destination, and bytes_copied; a destination race never overwrites anything.

An ordinary pre-install failure with successful cleanup is `failed / file_not_copied`; temporary cleanup failure becomes `partial / temporary_cleanup_failed_destination_absent`. Cleanup or durability uncertainty after destination installation returns the corresponding partial result and requires workspace inspection without automatic retry. Provider-continuation or turn-commit failure does not undo a real copy; Action Audit retains the effect while the candidate turn remains uncommitted.

Copy reuses `workspace-create`, so `read-only` denies it and both writable modes follow ask/auto. Approval and redacted Action Audit show only the two relative source/destination paths, never source bytes, digests, preconditions, or absolute paths. `copy_file` is appended to the canonical order and all twelve tools share three sequential executions per user turn. The provider adapter contract advances to v14, the canonical system prompt advances to v13, and the empty full-context golden becomes `ctx-v1-0cd5ddd1c14a00ddcfc01b8879bc83e49a7f8fb5113d5e3d00d98a6f25c413f3`. ToolArguments v1, ActionIdentity v1, Session and Action Audit schemas, `context_compacted` v2/v3 replay, and `ctx-v1`/`ctx-v2` representations remain unchanged. Recursive/directory copy and destination overwrite remain out of scope. See [0036: Foundation 4H Controlled Bounded Regular-file Copy](./decisions/0036-foundation-4h-controlled-bounded-file-copy.md).

## Tool Batch A: Bounded Workspace Navigation

Batch A adds three read-only navigation tools after `read_file` and `list_directory`. `read_file_lines(path, start_line, line_count)` reads a 1-based logical-line window from one strict UTF-8 regular file of at most 1 MiB, with `start_line` at most 1,000,000, at most 200 lines, and at most 32 KiB of JSONL output. `stat_path(path)` accepts `.` for the workspace root and reports no-follow type, basic `rwx` mode, and nanosecond mtime, plus size for a regular file. A final symlink is observed as `symlink` without reading its target, while a parent symlink is rejected. `list_tree(path, max_depth)` recurses through 1–16 levels, includes hidden entries, follows no symlink, and reads no file content. Its 10,000-entry/1,000-directory scan caps fail the whole call, while its 500-result/32-KiB caps return complete JSONL records plus a truncation sentinel.

All three use one shared portable path contract: at most 4096 UTF-8 bytes/characters, 64 components, and 255 bytes per component, rejecting absolute paths, Windows drives, backslashes, NUL, and empty/`.`/`..` components. Directory descriptors reject parent symlinks component by component. They reuse `workspace-read`, require no human approval, and still pass through the prepared-turn lease, PermissionGate, durable Action Audit, causal pairing, and atomic turn commit. See [ADR 0037](./decisions/0037-batch-a-bounded-workspace-navigation.md).

## Tool Batch B: Process-isolated Regex Grep

`grep_regex(pattern, include)` provides case-sensitive Python `re` search applied independently to logical lines. It reuses literal grep's portable file selector, 1,000 candidates, 1 MiB per file, 16 MiB aggregate reads, 200 matches, and 32 KiB JSONL output. Selected files must be strict UTF-8, NUL-free, non-symlink regular files. A pattern is non-empty, single-line, and at most 4096 UTF-8 bytes; there is no flags argument, cross-line matching, index, or ignore-aware behavior.

Selection, reads, and regex matching all run in a spawned worker process under a fixed one-second whole-call timeout. On timeout the Host terminates and boundedly joins the worker, then kills and joins it if needed. Worker failure and invalid payloads return stable safe errors without blocking the Host or exposing tracebacks. This isolates CPU hangs but is not an OS sandbox and does not further restrict data already permitted by the workspace selector. See [ADR 0038](./decisions/0038-batch-b-process-isolated-regex-grep.md).

## Tool Batch C: Structured Exact Multi-edit Patch

`patch_file(path, edits)` accepts 1–16 `{"old_text":"...","new_text":"..."}` exact edits. Each old/new fragment is at most 4096 UTF-8 bytes, `old_text` is non-empty, and the complete arguments remain subject to ToolArguments v1's 16-KiB canonical JSON cap. The target is an existing non-symlink strict UTF-8 regular file, and source/result are each at most 1 MiB. Every `old_text` must occur exactly once in the same original snapshot and match ranges may not overlap. Sorting by original position and building one candidate prevents earlier replacements from changing later anchor meaning.

Patch reuses `workspace-overwrite`, a source SHA-256 precondition, post-approval revalidation, the durable `action_execution_started` barrier, and `WriteFileTool`'s mode-preserving atomic replacement. Approval and Action Audit show only the relative path, not edits, digests, or absolute paths. Ordinary failure preserves the source and stale approval expires. A replacement followed by directory-fsync failure returns `partial / patched_durability_unknown` and prohibits automatic retry. Provider continuation or turn-commit failure preserves the real effect and durable audit without committing the candidate turn. See [ADR 0039](./decisions/0039-batch-c-structured-exact-multi-edit-patch.md).

Batches A/B/C extend canonical order to 17 tools while retaining the shared three-call sequential budget. The provider adapter contract advances to v15, the canonical system prompt advances to v14, and the empty full-context golden becomes `ctx-v1-ac2b833bb46894c250e2b31370d47911b3464cfa2c71c23ded504f0ea65fd4cf`. ToolArguments v1 already canonically stores nested JSON edits, so ToolArguments, ActionIdentity, Session/Action Audit, compaction, and `ctx-v1`/`ctx-v2` representation versions do not advance. Old transcripts are not rewritten. Foundation 5A remains deferred.

## Shared Six-call Tool Budget

As the model-visible surface grew to 17 tools, the original shared three-call budget no longer covered a normal search, read, modify, verify, and recheck workflow within one turn. This slice raised the fixed Host limit at that time to six sequential requests per user turn, shared by every tool. Success, tool errors, permission denial, approval rejection/cancellation, and executor failure all consumed a request that entered normal dispatch and did not refund it. Approval mode did not change the budget. The three-layer budget in [0055](./decisions/0055-bounded-multi-tool-response-batches.md) later superseded this historical limit.

The first six requests pass through validation, PermissionGate, optional approval, Action Audit, and execution as usual. A seventh request enters none of those boundaries and receives only the structured limit result paired with its original `tool_use_id`. The model must then return final text; an eighth tool request deterministically stops without committing the candidate turn. A new user turn receives a fresh six-call budget, and the Host neither opens turns nor continues tasks automatically.

The canonical system prompt advances to v15. Tool schemas, order, and provider projection logic do not change, so the provider adapter contract remains v15. The empty full-context golden becomes `ctx-v1-ea0e03265910b48b3cd97e3ace999507379a5e5cf168c6898390870266df051f`. ToolArguments, ActionIdentity, Session/Action Audit, compaction, and context representation versions remain unchanged, and old transcripts are not rewritten. See [0040: Shared Six-call Tool Budget](./decisions/0040-shared-six-call-tool-budget.md).

## Live Redacted Tool Activity

This slice made AgentLoop emit typed started/finished events around every normal tool dispatch using the then-current shared six-call budget index. A seventh request emitted only a limited event and did not enter dispatch. If dispatch raises after the effect can no longer be stated reliably, the terminal status is explicitly `outcome-unknown`. ProjectSession maps `error | denied | rejected | cancelled | succeeded | failed | partial` from structured PermissionGate, approval-resolution, and ActionCoordinator execution metadata rather than parsing ToolResult text.

Default compact summaries are minimized per tool: workspace-relative paths, includes, byte/edit/argument counts, command basename, cwd, and timeout may appear; file/edit/query content, full argv, absolute paths, digests, leases, internal IDs, and raw results may not. Absolute paths observed before execution validation become `<absolute>`, control characters are escaped, and summary lengths are bounded. A reusable `TerminalEventSink` writes one-shot events to stderr while leaving stdout final-answer-only; the REPL writes them to its own stdout. Sink exceptions are isolated and cannot change tool execution, Action Audit, turn commit, or Session state. Later [0065](./decisions/0065-opt-in-bounded-live-tool-details.md) adds only an explicit process-local full mode to the REPL; compact and one-shot boundaries remain unchanged.

Live events are not written to the append-only transcript, do not participate in resume or compaction, never enter model history, and cannot replace durable Action Audit. This Host-only slice leaves tool schemas/order, system prompt v15, provider adapter v15, the empty Effective Context identity, and all Session/Action Audit/context representation versions unchanged. See [0041: Live Redacted Tool Activity Events](./decisions/0041-live-redacted-tool-activity-events.md).

## Provider-neutral Assistant Tool Text Representation

Coquo can now accurately represent "assistant text plus one tool call" internally. The existing immutable `ToolUse` has an optional `assistant_text` that atomically binds exact text to the same tool ID, name, and arguments. `None` remains the existing pure tool call; non-empty text is bounded to 32 KiB characters and 32 KiB of UTF-8 and is neither trimmed nor normalized. Effective Context identity and compact source preserve the text, while the tool-use/result causal pair remains indivisible.

This slice originally defined only the internal representation without enabling real providers to use it. Anthropic and OpenAI-compatible parsers, history serializers, AgentLoop, and `turn_committed` schema v2 all failed closed so text could not be silently lost during execution, audit, or persistence. ADRs 0043–0046 have since completed inbound normalization, Session v3, history projection, and runtime/terminal integration in sequence.

Existing pure tool-call identity payloads are unchanged, so system prompt v15, provider adapter contract v15, tool schemas/order, ToolArguments v1, Session/Action Audit schemas, and the `ctx-v1`/`ctx-v2` representation versions remain unchanged. See [0042: Provider-neutral Assistant Tool Text Representation](./decisions/0042-provider-neutral-assistant-tool-text-representation.md).

## Provider Mixed-response Inbound Normalization

Anthropic Messages and OpenAI-compatible Chat Completions now normalize their mixed native responses into `ToolUse.assistant_text`. Anthropic requires a `tool_use` stop reason and exactly one valid tool block, then concatenates all text blocks in wire order. OpenAI-compatible requires a `tool_calls` finish reason and exactly one valid function call, then preserves non-empty `message.content` exactly. Responses without companion text still produce the existing pure `ToolUse`, and text-only responses are unchanged.

Both parsers continue to reject multiple tools, wrong stop/finish reasons, unknown tools, malformed arguments, unsupported content, and empty, invalid, or oversized companion text. This slice originally stopped at the inbound boundary; ADRs 0044–0046 have since completed Session persistence, history projection, and runtime execution/presentation.

The provider response contract advances the adapter contract to v16 and naturally changes route fingerprints. The system prompt remains v15 and still asks the model to return only the tool call; tool schemas/order, the Effective Context golden, ToolArguments v1, and Session/context representations remain unchanged. See [0043: Provider Mixed-response Inbound Normalization](./decisions/0043-provider-mixed-response-inbound-normalization.md).

## `turn_committed` v3 Assistant Tool Text Persistence

New Session turns now use record-local schema v3. It retains v2's generic `arguments_version + arguments` and requires each `tool_use` to store nullable `assistant_text`: a mixed response stores exact text, while a pure tool call stores `null`. Companion text remains subject to non-empty, valid UTF-8, 32 KiB character/byte, no-NUL, and total Session-record bounds; malformed or unknown fields fail closed before append or replay.

The reader remains compatible with v1 single-path and v2 generic-arguments turns, interpreting both as having no companion text in memory. Old transcripts are neither migrated nor rewritten; resume only appends `session_resumed` and v3 turns after the original prefix. V3 replay restores text into the original `ToolUse` and preserves atomic tool-use/result causality. Full history remains complete, and a retained mixed pair is restored exactly after compact-checkpoint replay.

At this slice boundary, provider history serializers and AgentLoop still rejected mixed runtime; ADRs 0045–0046 have since connected the ordinary CLI. Its record-local compatibility conclusion remains unchanged: ToolArguments v1, Action Audit, `context_compacted` v2/v3, and the `ctx-v1`/`ctx-v2` representations did not change because Session advanced to v3. See [0044: `turn_committed` v3 Assistant Tool Text Persistence](./decisions/0044-turn-committed-v3-assistant-tool-text-persistence.md).

## Provider Mixed-response History Projection

Both real adapters now project provider-neutral mixed history back to their wire protocols without loss. Anthropic sends one assistant message ordered as `text block -> tool_use block`; OpenAI-compatible sends one assistant message containing both exact `content` and the single `tool_calls` entry. The matching `ToolResult` still follows immediately. Pure tool calls retain their existing wire shape, and companion text is neither split into a final answer, copied into the result, nor assigned a new tool ID.

At this slice boundary, serializers reused closed tool schemas and complete causal validation, so malformed text, unknown tools, multiple calls, and broken pairing failed closed. Ordinary count/create shared the same projection, while compact summaries exposed no tools. This change advanced the adapter contract to v17; tool catalog/order, the six-call budget, ToolArguments v1, `turn_committed` v3, Action Audit, compaction, and context representations remained unchanged. See [0045: Provider Mixed-response History Projection](./decisions/0045-provider-mixed-response-history-projection.md).

## AgentLoop and Terminal Assistant Tool Text Integration

The ordinary runtime now handles mixed responses end to end. AgentLoop emits the exact companion-text event before tool started/finished events and normal execution. The `ToolUse -> ToolResult` pair immediately enters provider continuation history, and only a later plain assistant text ends and durably commits the turn. One-shot writes companion text and tool events to stderr while stdout remains final-answer-only; the REPL writes them in the same order to its own output stream. Existing trailing newlines are not duplicated, and terminal-sink failure cannot alter execution, Action Audit, or commit.

The live companion event is not persisted and is not proof of execution; durable truth remains `turn_committed` v3, the complete transcript, and Action Audit. Provider-continuation or turn-commit failure does not commit candidate history, though completed tool side effects and audit cannot be rolled back. A mixed seventh request can only receive the limit result, and an eighth tool request stops without commit, so text cannot bypass the shared six-call budget. A deterministic ProjectSession scenario proves mixed execution, display, close/resume, and exact history projection after recovery.

The canonical system prompt advances to v16, and the empty full-context golden becomes `ctx-v1-bc29d5392990da88d9a0641d78cfc051d0d9e92b9f3452e90b1259ae16df2b58`. The adapter contract remains v17; ToolArguments v1, `turn_committed` v3, Action Audit, `context_compacted` v2/v3, and the `ctx-v1`/`ctx-v2` representations do not advance, and old transcripts are not rewritten. See [0046: AgentLoop and Terminal Assistant Tool Text Integration](./decisions/0046-agent-loop-and-terminal-assistant-tool-text-integration.md).

## Provider Streaming and Terminal Failure Atomicity

This streaming stage enabled Anthropic Messages and OpenAI-compatible Chat Completions to stream assistant text during one synchronous provider call. Each adapter strictly assembled native events or chunks, finish reasons, and fragmented tool JSON. At that stage, AgentLoop could execute only after one complete neutral `ToolUse` passed the known-tool schema, and multiple calls still failed closed. ADR 0055 later admits a complete bounded batch under the same parse-before-execute boundary.

The REPL displays text deltas immediately. Tool activity follows only after companion text is completely parsed, and final text receives a committed confirmation only after the Session turn append and fsync, so it is not printed twice. Provider interruption, Ctrl-C, or commit failure explicitly marks visible partial text as uncommitted. Because one-shot cannot know whether streamed text is a final answer or tool companion until completion, it buffers deltas: companion text and tool activity go to stderr, while stdout receives the final answer exactly once after durable commit. Runtime performs context preflight before the first delta and retains the original turn lease through the complete synchronous stream.

Deltas are not persisted and prove neither tool execution nor turn commit; durable truth remains `turn_committed` v3, Action Audit, and the complete transcript. The adapter contract advances to v19. The canonical system prompt was reviewed and remains v16; tool schema/order, the six-call budget, ToolArguments v1, ActionIdentity v1, Session/compaction schemas, the empty-context golden, and the `ctx-v1`/`ctx-v2` representations are unchanged. See [0047](./decisions/0047-provider-neutral-synchronous-response-streaming.md) through [0050](./decisions/0050-agentloop-runtime-and-terminal-streaming-integration.md).

## TTY Markdown Rendering

The REPL and TTY one-shot now use the locked Rich renderer for assistant Markdown. Headings, emphasis, lists, tables, and fenced code become terminal layout with optional ANSI syntax styling. Streaming emits complete blocks only at safe boundaries such as blank lines or closed fences, preventing incomplete fragments from misclassifying code. Tool companion text flushes after complete response classification, while the remaining final suffix flushes only after durable turn commit.

Non-TTY stdout/stderr, pipes, and redirects retain raw Markdown; `NO_COLOR` disables ANSI while preserving Markdown layout. Provider ESC, CR, NUL, and other terminal controls become visible escape text in the TTY copy, and Rich markup, emoji expansion, and terminal hyperlinks are disabled. Session, provider continuation, and Effective Context always use the exact original text, so renderer failures or version changes cannot alter resume or context identity.

This Host-only presentation slice adds Rich as a runtime dependency, while canonical system prompt v16, adapter contract v19, `turn_committed` v3, and all tool, permission, audit, compaction, and context contracts remain unchanged. See [0051: TTY Markdown Rendering](./decisions/0051-tty-markdown-rendering.md).

## Exact Bounded Informed Approval

Per-action REPL `ask` now presents prepared-action facts before reading the user's answer. `write_file`, `edit_file`, and `patch_file` generate a unified diff from the original UTF-8 snapshot and complete candidate frozen during preparation; the CLI does not reread the workspace for presentation. Creates, overwrites, empty files, same-content overwrites that still execute, missing final newlines, and truncated previews are explicit. A diff is bounded to 160 lines and 24 KiB, with at most 4096 bytes per displayed line; truncation affects presentation only, while approval remains bound to the complete candidate.

Copy, move, and file deletion show the prepared byte count. Directory creation/deletion and commands show essential facts about destination absence, permanent deletion, lack of automatic rollback, and the command boundary's lack of an OS/filesystem/network sandbox. Each preview carries the exact ActionIdentity digest and a closed tool kind; a mismatch fails closed before any Action Audit write. The terminal copy escapes C0/C1, Unicode format, and line/paragraph-separator controls and may apply safe colors, while ordinary live tool lines and `/actions` remain redacted.

The preview exists only for a REPL `ask`: it is not persisted and does not enter provider history, Session, resume, compaction, or Effective Context. One-shot ask still cancels without reading stdin, and auto approval does not show a preview. Existing precondition refresh, single-use grant consumption, and stale rejection still run after acceptance, so displaying a diff expands neither permission nor hard execution boundaries. This Host-only change retains canonical system prompt v16, adapter contract v19, the 17-tool schema/order, six-call budget, ToolArguments v1, ActionIdentity v1, Action Audit v1, `turn_committed` v3, `context_compacted` v2/v3, and the `ctx-v1`/`ctx-v2` representations. See [0052: Exact Bounded Informed Approval Previews](./decisions/0052-exact-bounded-informed-approval-previews.md).

## TTY Prompt Editor and Interaction Feedback

The REPL now reads input through an independent `PromptEditor` boundary. A real TTY uses locked prompt-toolkit with a minimal `›` marker, two-space continuation alignment, and a bounded, control-safe `model · workspace` status line below the editor. Enter submits and Alt+Enter inserts LF; if the terminal intercepts Alt, Esc followed by Enter is an equivalent fallback. Bracketed paste keeps multiline text in one buffer. Submitted text is not globally `strip()`-processed, so the model, Session, and resume retain exact indentation, line breaks, and a trailing newline. The REPL still ignores an all-whitespace buffer.

Before every input, the editor rebuilds up to 1000 entries and 4 MiB of committed user-prompt history from the current Session's complete turns. Up/Ctrl-R therefore work after process resume and switch sources after `/resume` or `/session new`; slash commands, cancelled drafts, and failed uncommitted turns cannot remain. Tab completion describes top-level commands and completes `/provider list|current|use` and `/session show|list|new`. `/clear` only emits the terminal clear sequence: it invokes no model, appends no transcript, and changes neither Session nor Effective Context.

Ctrl-C cancels a non-empty draft and resumes the REPL, while an empty buffer exits; Ctrl-D exits only from an empty buffer. Input is bounded to 256 KiB characters and 256 KiB of UTF-8 and rejects NUL. Non-TTY, pipe, redirect, and injected streams retain the deterministic single-line fallback. A multiline slash prefix is ordinary model text; a Host slash command must remain one line.

After real-TTY submission, ephemeral `• Working...` remains until the first visible assistant or tool-lifecycle event clears it. Assistant companion and final output both begin with `•`. One-shot, pipe, redirect, and injected streams gain no such feedback, so their stdout/stderr contracts remain unchanged. Feedback is not persisted; durable Session and Action Audit remain the facts after failure or recovery.

This is a Host-only input and presentation change. The canonical system prompt remains v16, the adapter contract remains v19, and the 17-tool schema/order and six-call budget, ToolArguments v1, ActionIdentity v1, Action Audit v1, `turn_committed` v3, `context_compacted` v2/v3, and the `ctx-v1`/`ctx-v2` representations remain unchanged. Exact multiline text naturally participates in a particular turn and context identity, but the identity representation does not advance. See [0053: TTY Prompt Editor and Interaction Feedback](./decisions/0053-tty-multiline-prompt-editor.md).

## Sequential Tool-call Budget Hardening

A real DeepSeek-compatible observation asked an empty workspace to create three directories and eight files, requiring at least 11 mutation calls and therefore not fitting the six-call turn budget. After a successful read-only `list_directory`, the provider sent a nonzero stream tool-call index in the next response, indicating another call in the same assistant response. The old adapter failed closed before executing any call from that response, but reported only an invalid index; Action Audit confirms that no mkdir or write occurred.

The OpenAI-compatible parser now classifies a positive tool-call index as unsupported multiple calls while continuing to reject malformed indexes, multiple delta entries, changed IDs, and other incomplete shapes. It neither selects only the first call, queues later calls, nor retries automatically. System prompt v17 requires work that cannot fit to use only the remaining sequential allowance, report completed and remaining work, and wait for a later user turn instead of packing a batch.

The Host still enforces six calls per turn. The 17-tool schema/order, `parallel_tool_calls=false` projection, permission/approval, Action Audit, Session schemas, and causality are unchanged. Because the accepted provider shape is unchanged, adapter contract v19 remains current. The new prompt fingerprint is `v17-1c66b2e9cf6b622477408f99106294b2cdab14a9983a7fb6b4d628218307b851`, the empty full-context identity is `ctx-v1-4bcd666498bd96b3af1aa59a1d6793b31cdcdcff1dc274db80c6f051f1e8b6da`, and the representations remain `ctx-v1`/`ctx-v2`. See [0054: Sequential Tool-call Budget Hardening](./decisions/0054-sequential-tool-call-budget-hardening.md).

## Bounded Multi-tool Response Batches

Coquo now accepts a bounded ordered batch of tool calls in one provider response. The neutral `AssistantToolBatch` stores response-wide companion text and multiple `ToolUse` values with unique IDs; one call retains the legacy `ToolUse` shape. The OpenAI-compatible adapter assembles calls from `tool_calls[]` or independent stream indexes, while Anthropic assembles content blocks. The complete response must pass count, ID, JSON, closed-schema, and causal validation before any action in that batch can run; one invalid call rejects the whole batch.

The Host remains sequential. Each admitted call enters PermissionGate, approval, Action Audit, and execution in provider order. A non-successful action causes later calls in the same batch to receive explicit skipped errors without execution. The three layers permit at most eight calls per response, 32 admitted requests per user turn, and 24 provider invocations with a final text-only invocation. A batch that cannot fit the remaining request budget gets zero execution and matching budget errors. Earlier successful side effects are not rolled back after later failure, while candidate history still waits for final assistant text and durable turn commit.

OpenAI-compatible history projects one assistant `tool_calls[]` message followed by ordered tool messages. Anthropic projects one assistant tool-block sequence followed by one user message containing ordered results. Count and create share the projection, and text-only invocations expose no tools. The canonical system prompt advances to v18, the adapter contract to v20, current `turn_committed` to schema v4, and current full/compacted Effective Context to `ctx-v3`/`ctx-v4`. ToolArguments v1, ActionIdentity/Action Audit, ordinary Session records, and `context_compacted` record schemas do not advance. Session v1/v2/v3 and legacy `ctx-v1`/`ctx-v2` checkpoints still replay without rewriting. The fingerprint is `v18-6ddfaa8302427bbe25c1ee28cee6b1e5975949da111a96876baa8e834cd86f8c`, and the empty full-context identity is `ctx-v3-9007cd576ff595afb6a103a199437d28580836f2a3a5b551819f0f8574d4cf80`. See [0055: Bounded Multi-tool Response Batches](./decisions/0055-bounded-multi-tool-response-batches.md).

## Structured Tool Outcome Ledger

AgentLoop now creates a Host-owned typed entry for every provider tool request, recording a continuous request index, exact tool-use ID, tool name, outcome, and safe result code. `requested`, `admitted`, `dispatched`, and every status count are derived from entries without parsing ToolResult or assistant prose. The 40-request regression scenario accurately distinguishes 24 successes, one error, and seven same-batch skips among 32 admitted requests, plus eight over-budget rejections.

Before budget or provider-invocation exhaustion forces text-only finalization, the Host appends a bounded canonical ledger summary to the last real ToolResult. This gives the model authoritative counts without inventing a user or system message. The summary separately reports `unused_admission_slots` and `tool_requests_closed=true`, preventing a model from treating a stranded slot as permission to continue after a whole batch cannot fit. After durable turn commit, the terminal separately shows `Tool summary:`; commit failure emits no summary, while event-sink failure still cannot affect execution, Action Audit, or commit. A turn that ends voluntarily does not make an extra provider call solely for the ledger.

New `turn_committed` schema v5 stores the typed ledger at top level outside conversation items and strictly validates each entry against tool request/result identity, order, and error flags. v1/v2/v3/v4 replay as empty legacy ledgers; resume appends only v5 without rewriting an old prefix. The top-level ledger does not enter provider history, compaction, or context identity. The model-visible annotation remains ordinary ToolResult content, so Effective Context representations stay `ctx-v3`/`ctx-v4`. System prompt v19 has fingerprint `v19-accfbb73aa611061c8a8cb6be5bb54012ce5809fbbe91050439383e3d35318b7`, and the empty full-context identity is `ctx-v3-29ff59405090ba544b2bacb144d5961daecc7d0d6359123a9262c097d0fa654d`. Provider wire shapes and projections are unchanged, so adapter contract v20 remains current. See [0056: Structured Tool Outcome Ledger](./decisions/0056-structured-tool-outcome-ledger.md).

## Durable Tool Ledger Inspection

Persisted ledgers are now directly inspectable from the terminal. The offline `session tools [selector] --limit N` command and current-REPL `/tools [count]` both read only strictly replayed Session state. They return the five most recent committed turns by default and at most 20, preserving each original turn number, record sequence, and commit timestamp. Summary mode shows derived per-turn counts; `--details` or `/tools details [count]` additionally expands continuous request indexes, tool names, typed outcomes, and safe result codes.

Presentation excludes tool-use IDs, tool arguments, paths, prompts, assistant text, ToolResult prose, absolute workspaces, approval grants, and Action identities. Detailed output is bounded to 32 KiB, truncates only between complete lines, and carries a sentinel. An empty schema-v5 ledger accurately means that the turn requested no tools; v1/v2/v3/v4 explicitly report unavailable because those schemas persisted no ledger instead of pretending they had zero requests. Strict replay failure still rejects the whole query. Inspection neither creates a Session root nor acquires a writer lease, invokes a provider or tool, or modifies latest, transcripts, runtime, Effective Context, or Action Audit.

This is a Host-only inspection slice. Canonical system prompt v19 and its fingerprint, provider adapter contract v20, ToolArguments v1, ActionIdentity v1, `turn_committed` v5, Action Audit and `context_compacted` schemas, and `ctx-v3`/`ctx-v4` representations all remain unchanged. See [0057: Durable Tool Ledger Inspection](./decisions/0057-durable-tool-ledger-inspection.md).

## Runtime Context Meter and Provider Token Usage

Before every real provider invocation, the runtime now publishes the `ContextFitReport` from that invocation's full preflight. The terminal renders a ten-cell block meter separating current input, requested output reserve, and remaining window; continuations after tool results update independently. Between REPL inputs, the toolbar retains the latest short context state. During synchronous generation the CLI uses inline events and does not claim to provide an asynchronous pinned bottom bar.

The Anthropic and OpenAI-compatible adapters carry provider-reported actual input/output usage in a Host-only response envelope outside `AssistantText`, conversation history, and Session state. Anthropic supports non-streaming usage plus `message_start`/`message_delta` stream usage. OpenAI-compatible requests `stream_options.include_usage` and accepts the usage-only chunk after stream completion in addition to non-streaming usage. Missing or malformed metadata increments an unknown-invocation count instead of becoming zero or being mixed with local estimates. Failed provider calls are also unknown because they may still incur remote usage.

The runtime reports the latest invocation, latest user turn, and totals since entering the current profile target, with ordinary turns and compaction separated. Tool continuations belong to the same turn; count-only inspections are measurements rather than generation usage. Successful `/provider use` or `/model` switches reset totals, while `/resume` and `/session new` do not. `/usage` shows per-invocation and aggregate facts. Accounting is process-local, not persisted, does not calculate money, and is not a cross-process billing ledger.

The provider adapter contract advances to v21 because the OpenAI-compatible stream request and both adapter response transports gain a usage contract. Canonical system prompt v19, the 17 tool schemas and order, ToolArguments v1, ActionIdentity v1, `turn_committed` v5, Action Audit, `context_compacted`, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged. See [0058: Runtime Context Meter and Provider Token Usage](./decisions/0058-runtime-context-meter-and-provider-token-usage.md).

## Context and Compaction Observability

The REPL adds `/compact preview` and `/compactions [count]`. Preview freezes the current Effective Context under the Session lock, reuses the fixed policy of at least four effective turns while retaining the latest two, and performs current-target assessment. It reports eligibility, selected turn counts, and pressure without building a summary request, taking a compaction lease, or writing a checkpoint. Fake and unknown targets remain explicitly unknown. An Anthropic official target may use a count-only API for exact inspection, but preview performs no generation.

`/compactions` selects the latest five and at most 20 `context_compacted` records from the already strictly replayed current Session state. It shows only sequence, timestamp, schema, manual/high-water/overflow trigger, the 80% threshold, full/summarized/retained turn counts, and previous checkpoint. Summary text, binding, context IDs, prompts, and credentials remain hidden. Existing v2/v3 checkpoints did not persist before/after token counts, so history marks those measurements unavailable instead of recomputing them or upgrading the schema.

`/context` and preview classify current `input + output reserve` pressure as normal, approaching the threshold at 70%-79%, auto-compact range at 80%-89%, near full at 90%-100%, overflow, or unknown. These levels are Host presentation only. An ordinary prompt still reassesses the exact initial request including pending user input under the existing 80% and overflow policy. `/usage` additionally shows the latest known or unknown compaction invocation in the current runtime. At this slice, runtime switches still reset all usage and Session state did not persist it; ADR 0062 later adds an independent Session audit.

This slice is Host-only observability. Canonical system prompt v19, provider adapter contract v21, the 17 tool schemas and order, ToolArguments v1, ActionIdentity v1, `turn_committed` v5, `context_compacted` v2/v3, and Effective Context `ctx-v3`/`ctx-v4` all remain unchanged. See [0059: Context and Compaction Observability](./decisions/0059-context-and-compaction-observability.md).

## Provider Output-limit and Compaction Failure Diagnostics

Anthropic and OpenAI-compatible now normalize exhausted output allowances for ordinary generation and compact summaries as `output_limit` instead of mixing them into general `response_invalid` failures. The structured error carries only the requested output limit, usable strict provider usage, and whether incomplete content was observed; it retains neither raw responses nor partial text. Non-streaming paths read valid usage before rejection. OpenAI-compatible streams retain a usage-only tail after the finish reason, while Anthropic streams combine input/output measurements from message start and delta events.

Runtime records known actual usage on the output-limit exception path, so a later `/usage` accurately includes the failed turn or compaction invocation; absent or malformed metadata remains unknown. One-shot and REPL presentation show the requested limit, actual input/output or explicit unavailability, and explain that the incomplete response is not a final answer or committed turn. Tool side effects completed earlier in the attempt are not rolled back and remain governed by Action Audit. Streamed partial text is ephemeral terminal output and is explicitly marked uncommitted.

A non-reducing compaction now retains comparable source/candidate input counts and the count method in a structured `CompactionCandidateError`, so the terminal can show evidence such as `input 4900 -> 5100 tokens; estimated`. The failure still installs no summary, appends no checkpoint, and changes no Effective Context; known process-local usage for the summary generation remains inspectable, and ADR 0062 later appends a separate `compaction_failed` audit for cross-restart evidence. The system does not automatically retry, commit partial text, or increase the profile output reserve.

Provider adapter contract advances to v22 for failure transport and exception-path usage accounting. Native requests, successful responses, and tool/history projection are unchanged. Canonical system prompt remains v19, while the 17 tools and order, ToolArguments v1, ActionIdentity v1, `turn_committed` v5, Action Audit v1, `context_compacted` v2/v3, and Effective Context `ctx-v3`/`ctx-v4` do not advance. See [0060: Provider Output-limit and Compaction Failure Diagnostics](./decisions/0060-provider-output-limit-and-compaction-failure-diagnostics.md).

## Process-local Runtime Output Budget Control

Ordinary prompt and REPL startup now accept global `--max-output-tokens`, and the REPL adds `/output`, `/output <tokens>`, and `/output reset`. Effective budgets are bounded from 1 through 100,000,000. Inspection shows the effective value, profile or direct-route default, source, and known model maximum. Fake runtime explicitly rejects an override, while profile files and Session selection remain unchanged.

Runtime treats a budget update as a new provider-route candidate: it reconstructs the provider and model capability, then screens the current committed Effective Context through the same target-aware boundary used by provider/model switches. Known model-output or context overflow preserves the old provider, route, generation, and budget. Unknown counts apply with a warning, while the next real invocation still performs full preflight. Only a successful screen atomically replaces the provider and advances generation, so a prepared action lease cannot cross the route change.

A budget update preserves process-local usage accumulated since the current profile was selected, but discards the latest context meter derived from the old reserve. `/model` retains and re-screens the temporary budget for the new model, while `/provider use` or active-selection changes clear it and restore the new profile default. `/output reset` also handles a temporary value numerically equal to the default. BindingSnapshot on later successful or failed turns naturally records the effective `max_output_tokens` and route fingerprint, but resume never restores the temporary override from historical bindings; the adjustment command itself appends no `runtime_changed` record.

This slice adds no automatic retry or continuation for truncated answers and does not change the Host's 4096-token compaction cap, profile schema, or successful provider response. Canonical system prompt remains v19, provider adapter contract remains v22, and the 17 tools and order, ToolArguments v1, ActionIdentity v1, `turn_committed` v5, Action Audit v1, `context_compacted` v2/v3, and Effective Context `ctx-v3`/`ctx-v4` remain unchanged. See [0061: Process-local Runtime Output Budget Control](./decisions/0061-process-local-runtime-output-budget-control.md).

## Durable Session Provider Usage Audit

Provider-reported token usage now lives not only in the process-local tracker but also on strictly replayed Session terminal facts. An ordinary successful turn stores every generation and tool continuation in order in `turn_committed` v6; a failed turn uses record-local `turn_failed` v2. Successful compaction uses `context_compacted` v4, while failed compaction appends Host-only `compaction_failed` v1. Each invocation stores either a bounded input/output pair or explicit unknown, never interpreting missing metadata as zero.

`/usage` retains its current-runtime/profile process-local scope. `/usage session` totals committed turns, failed turns, committed compactions, and failed compactions in the current Session, while `/usage turns` shows the ten most recent committed or failed turns. Resume and process restart preserve these queries. Legacy `turn_committed` v1-v5, `turn_failed` v1, and `context_compacted` v2/v3 lack the field and therefore render as legacy-unavailable instead of zero. Usage audit enters neither full/effective history nor summaries or context identity, and historical bindings never recreate the current runtime.

Successful usage commits atomically with its turn or checkpoint; failed usage commits with its safe failure classification. A non-reducing compaction still installs no checkpoint and changes no Effective Context, but leaves a separate failure audit. The system stores no raw response, partial text, credential, price, or provider billing subdivision, and makes no crash-proof billing claim for a provider call lacking a durable terminal record.

This slice leaves canonical system prompt v19, provider adapter contract v22, the 17 tools and order, ToolArguments v1, ActionIdentity v1, Action Audit v1, and Effective Context `ctx-v3`/`ctx-v4` unchanged. New records use `turn_committed` v6, `turn_failed` v2, and `context_compacted` v4; old prefixes are not rewritten. See [0062: Durable Session Provider Usage Audit](./decisions/0062-durable-session-provider-usage-audit.md).

## Bounded Read-only Git Change Observation

The model-visible surface now appends `git_status` and `git_diff` after the existing 17 tools. `git_status({})` parses staged, unstaged, and untracked path states from the workspace-root repository into stable JSONL without reading untracked file content. Complete raw status is limited to 1 MiB and 10,000 entries, while model output is limited to 200 records or 32 KiB with an explicit truncation sentinel. `git_diff(scope, path)` accepts only `staged | unstaged` plus `.` or one literal workspace-relative path, returns only tracked patches, and disables rename detection, external diff, text conversion, and submodule recursion. Its output is limited to 64 KiB with explicit truncation.

The dedicated runner uses fixed argv, `shell=False`, closed stdin, a five-second timeout, bounded pipe capture, and TERM-to-KILL process-group cleanup. It disables optional locks, pager, prompts, fsmonitor, untracked cache, hooks, external config/attributes, external diff, and submodule recursion. V1 requires the workspace itself to be the Git top level with an internal non-symlink `.git` directory. Linked-worktree pointers, `commondir`, object alternates, external config includes, configured external filters, unsafe metadata, and non-repository workspaces fail safely. This is a bounded Git-process boundary rather than an OS sandbox, and it accepts neither arbitrary Git argv/revisions nor writes.

The model tools remain `workspace-read` actions passing through PermissionGate, Action Audit, and the shared 8/32/24 budget. The REPL adds `/changes`, `/changes unstaged`, and `/changes staged` to render status or terminal-control-escaped root patches without provider invocation, tool-budget consumption, Session mutation, or Action Audit. Canonical system prompt advances to v20, provider adapter contract advances to v23, and the 19-tool catalog changes the empty full-context identity to `ctx-v3-cb7ce2ad36fc600b23c66362f02e4e139beee17e721a06eb490b82a7ae302a9e`. ToolArguments v1, ActionIdentity v1, Session/Action Audit schemas, and `ctx-v3`/`ctx-v4` representations do not advance, and old Sessions are not rewritten. See [0063: Bounded Read-only Git Change Observation](./decisions/0063-bounded-read-only-git-change-observation.md).

## Bounded Reachable Git History Observation

The model-visible surface appends `git_log(limit, path)` and `git_show(commit_id, path)` after the existing 19 tools. `git_log` walks only current-`HEAD`-reachable history, accepts a limit from 1 through 50 plus `.` or one literal workspace-relative path, and returns stable JSONL with complete commit/parent IDs, committer ISO time, a subject bounded to 1,024 bytes, and explicit subject truncation. Raw output is limited to 1 MiB and model output to 32 KiB. It does not enumerate `--all`, refs, reflogs, signatures, notes, authors/email addresses, or arbitrary revisions.

`git_show` accepts only a complete lowercase 40/64-hex ID and first uses fixed `merge-base --is-ancestor` behavior to prove that it is a commit reachable from current `HEAD`. It then returns one JSON metadata line, a commit message capped at 8 KiB, and a bounded tracked patch; total output is capped at 64 KiB with separate message and patch truncation. External diff, textconv, rename detection, signatures, color, submodule recursion, and replacement objects are disabled. Abbreviated/uppercase IDs, unreachable or non-commit objects, unborn HEAD, invalid paths, and non-UTF-8 or malformed output fail safely.

Both tools remain `workspace-read` actions passing through PermissionGate, Action Audit, and the shared 8/32/24 budget without approval. The REPL adds `/commits [count] [path]` and `/commit <full-id> [path]`, rendering complete IDs while escaping terminal controls in subjects, messages, and patches. They invoke no provider, consume no model-tool budget, and write neither Session nor Action Audit. Canonical system prompt advances to v21, provider adapter contract advances to v24, and the 21-tool catalog changes the empty full-context identity to `ctx-v3-bf336060a8cf9fb75df3766f81b6dae9ef175e8b6e0929f0a0ef10ebab387dd7`. ToolArguments v1, ActionIdentity v1, Session/Action Audit schemas, and `ctx-v3`/`ctx-v4` representations do not advance, and old Sessions are not rewritten. See [0064: Bounded Reachable Git History Observation](./decisions/0064-bounded-reachable-git-history-observation.md).

## Opt-in Bounded Live Tool Details

The REPL adds process-local `/tool-details`, `/tool-details compact`, and `/tool-details full`. Every launch starts in compact and one-shot remains compact; the setting is stored in neither profiles, Sessions, transcripts, Action Audit, nor Effective Context. Compact exactly preserves the existing redacted single line. Full makes tool starts multiline while still hiding file/edit/patch/search content. Ordinary tools expand only their existing safe summary, while `run_command` additionally shows structured JSON argv, cwd, timeout, and an execution annotation.

Command details do not misrepresent direct argv as shell source. Ordinary requests state that Host shell parsing is disabled. When the model explicitly requests a common shell with a `-c`-style option, the terminal identifies the shell interpreter and the argv position containing source. The argv line is limited to 7 KiB and all details to four lines/8 KiB with rendered-byte truncation. C0/C1 controls, Unicode format controls, and line/paragraph separators are escaped before terminal output. Enabling full warns that argv may contain sensitive values.

Only an explicit full-mode request makes AgentLoop derive details from immutable ToolArguments; compact and one-shot events carry no argv details, and TerminalEventSink then renders the selected form. Events remain best-effort ephemeral observation and cannot change permission, approval, execution, Action Audit, turn commit, or provider failure. Full mode provides no PTY, retained shell, stdin forwarding, or additional command authority. Review confirms no model-visible behavior change, so canonical system prompt remains v21, provider adapter contract remains v24, the 21-tool catalog and empty Effective Context identity are unchanged, and no Session/context schema advances. See [0065: Opt-in Bounded Live Tool Details](./decisions/0065-opt-in-bounded-live-tool-details.md).

## Trusted Command Result Observability

In addition to the existing model-visible ToolResult, `run_command` now emits a content-free typed observation directly from the executor. It records process status, exit code or signal, monotonic duration, stdout/stderr captured and total bytes, per-stream truncation, and cleanup completeness. The terminal never obtains these facts by parsing ToolResult JSON. The same observation generates both the existing JSON fields and Host event metadata, so provider-facing serialization does not become the trusted UI source.

Compact completion lines append exit or lifecycle status, duration, and both output byte counts; truncation and incomplete cleanup are explicit. `/tool-details full` expands the fields to at most six lines/2 KiB. Neither mode displays raw stdout/stderr text or base64, nor does it add exposure of argv, credentials, absolute paths, raw ToolResult, or provider payloads. Denial, approval rejection/cancellation, preparation failure, and executor exception carry no execution details. If Action Audit finish persistence fails, the terminal still reports only `outcome-unknown`; process metadata cannot masquerade as durable action completion.

Observations and result details exist only in the current live-event path and are stored in neither Sessions, Action Audit schemas, provider history, profiles, nor Effective Context. Review confirms no model-visible contract change, so canonical system prompt remains v21, provider adapter contract remains v24, the 21-tool catalog, tool schemas/order, empty Effective Context identity, and all Session/context schemas remain unchanged. See [0066: Trusted Command Result Observability](./decisions/0066-trusted-command-result-observability.md).

## Persistent Inline Terminal Frontend

A real TTY no longer follows “read one PromptSession, synchronously run the complete turn, then create the next PromptSession.” One non-full-screen `prompt_toolkit.Application` now retains the input area, toolbar, completion, history, approval focus, and inline scrollback. Submission clears the buffer immediately and leaves the next prompt visible. A draft remains editable while busy, but Enter cannot queue, insert, or dispatch a slash mutation. Approval saves and restores the draft, Ctrl-C requests cancellation, and Ctrl-D waits for active-worker cleanup before exit.

A closed `TerminalViewState`, pure reducer, and bounded local queue move assistant, tool, context, usage, compaction, and failure events from one background worker to the sole TTY renderer. Assistant deltas remain separate FIFO events so each received stream chunk can be flushed independently; tool, approval, failure, and durable-final facts cannot be lost. Renderer and terminal-sink failures remain best-effort and cannot affect execution, Action Audit, or turn commit. One-shot, redirect, injected-stream, and non-TTY paths remain synchronous.

`TurnCancellation` crosses ProjectSession, AgentLoop, provider streams, tool boundaries, the approval broker, and `run_command`. Commands poll cancellation and use the existing bounded TERM-to-KILL process-group cleanup. A blocking provider SDK call can observe cancellation only after return or at the next stream chunk, and no unsafe thread exception injection is used. This Host-only redesign preserves canonical system prompt v21, provider adapter contract v24, the 21-tool catalog, Effective Context identity, and every Session/Action Audit schema. See [0067: Persistent Inline Terminal Frontend](./decisions/0067-persistent-inline-terminal-frontend.md).

## Terminal Message Hierarchy and Hanging Indent

The real TTY separates conversation and Host process information into stable visual levels. Submitted user messages use `› ` and assistant bodies use `• `. Both reserve a two-column role prefix, and explicit newlines or automatic terminal-display-width wrapping continue from the body column. A new conversation message block is preceded by a low-intensity short rule indented by two columns and bounded to roughly one third of terminal width or 24 cells. Plain user text has terminal controls escaped before display-width wrapping. Assistant Markdown renders after subtracting the role prefix from available width, preventing long lines from returning to the terminal's left edge or crossing its right boundary.

Markdown streams no longer write `• ` by itself. Deltas without a safe rendering boundary stay in memory; the marker and first visible body appear in one frontend write, and later chunks use the continuation indent. Routine tool, context, usage, compaction, and slash output becomes an indented Host block with dim or dim-green styling in color mode. Warnings, approvals, errors, partial outcomes, and durability uncertainty retain high contrast. `NO_COLOR` removes ANSI styling only, not structure.

Terminal protocols cannot portably select a smaller font for one line, so this slice does not claim font-size control or introduce an alternate-screen TUI. One-shot output, redirects, injected streams without the role UI, Sessions, Action Audit, and every model-visible contract remain unchanged. Canonical system prompt remains v21 and provider adapter contract remains v24. See [0068: Terminal Message Hierarchy and Hanging Indent](./decisions/0068-terminal-message-hierarchy-and-hanging-indent.md).

## Host Workbench Navigation and Failure Guidance

The REPL Host workbench now provides grouped `/help <session|tools|git|context|provider|input>`, `/session list [count] [open|closed] [model=<name>]`, and `/actions [count] [status=<status>] [tool=<name>]`. Session filtering reads only validated workspace-bound metadata, remains newest-first, and shows current/latest markers plus durable provider/model provenance; resume still requires `latest` or a complete Session ID. Audit filtering uses only strictly replayed lifecycle status and canonical tool name, without parsing result prose or adding repair, retry, or export behavior.

Known context, provider, runtime, authorization, and Session failures share one Host formatter that appends conservative `Next:` guidance. It never retries automatically, claims rollback, or conflates an uncommitted turn with completed side effects retained in Action Audit. The persistent terminal maps typed events to provider preparation, a specific running tool, result processing, compaction, usage, approval, and finalization phases. `ProjectSession` emits the content-free `TurnCommitStarted` only immediately before `SessionWriter.append_turn`, so `Saving Session` identifies the real durable append rather than a post-hoc guess.

Every change is limited to Host queries, ephemeral events, and terminal text. No new Session record is written and nothing enters provider history. The canonical system prompt remains v21, provider adapter contract remains v24, and the 21-tool catalog, Effective Context identities, and all Session and Action Audit schemas remain unchanged. See [0069: Host Workbench Navigation and Failure Guidance](./decisions/0069-host-workbench-navigation-and-guidance.md).

## Assistant Turn Execution Trace Grouping

The real TTY now presents the complete AgentLoop execution triggered by one user submission as one Assistant Turn, with one blank line between the user message and the turn's first visible output. Provider-authored companion and final text retains the `• ` marker. Host facts such as context preflight, tool lifecycle, approval and its diff, usage, ledger, compaction, and failure output use a `  │ ` rail on every logical line. If the model requests a tool without companion text, presentation starts with the rail rather than inventing an empty `•`. They visually belong to the same turn without being misrepresented as model speech, while existing colors, risk emphasis, and redaction boundaries remain intact.

Conversation separators no longer appear between assistant text, Host traces, and later assistant continuations. The persistent frontend writes one low-intensity short rule only after `TurnFinished`, placing it after final text or failure output and before the next live `›`; slash-command results remain Host blocks outside a model turn. One-shot, redirected, injected streams without the role UI, Sessions, Action Audit, and provider history are unchanged.

This is a Host-only terminal presentation change. Canonical system prompt remains v21, provider adapter contract remains v24, and the 21-tool catalog, ToolArguments v1, ActionIdentity v1, Effective Context identities, and every Session, compaction, and Action Audit schema remain unchanged. See [0070: Assistant Turn Execution Trace Grouping](./decisions/0070-assistant-turn-execution-trace-grouping.md).

## Durable Session Naming and Terminal Identity

New Sessions persist a `New session N` default in `session_header` v2. After the first normal assistant response succeeds but before the turn is committed, title prompt v1 may use the same pinned provider for at most three no-tools requests. Source text is capped at 4096 UTF-8 bytes, output reserve is fixed at 512 tokens to accommodate hidden reasoning that a provider may count against output, and accepted titles remain capped at 48 characters and 160 UTF-8 bytes. Automatic names are checked case-insensitively across the workspace; conflicts enter the next rejected set, while three exhausted attempts or a provider failure use a bounded Host fallback with a stable numeric suffix. Title and ordinary generations share the 24-invocation turn ceiling, so a full ordinary loop issues no title call.

New `turn_committed` v7 stores the first-turn `session_name + source(model|fallback)`, conversation, and all provider usage in one record; failed, cancelled, or uncommitted turns leave no name. Legacy `turn_committed` v1-v6 and `session_header` v1 replay unchanged, and older untitled turns retain a deterministic Host compatibility name without transcript rewriting. `/session rename <name>` appends `session_named` v1 with source `manual`, while `--auto` restores the first-turn automatic title. The complete UUID remains the exact resume identity.

`/session show`, `/session list`, and the TTY toolbar display names and refresh after turns, new, resume, and rename. Slash Host commands now echo their `›` input and write input, Host result, and one short separator as a complete block. Names do not enter ordinary provider history, the canonical Agent system prompt, tool contracts, Action Audit, compaction, or Effective Context. System prompt remains v21, while the dedicated title projection advances the adapter contract to v25. See [0071: Durable Session Naming and Terminal Identity](./decisions/0071-durable-session-naming-and-terminal-identity.md).

## Session Lifecycle Management and Naming Diagnostics

`/session archive` and `/session unarchive` now append a reversible `session_archive_changed` v1 organization marker. Archive is not close, delete, or switch: it changes neither complete history, runtime binding, Effective Context, the `latest` pointer, nor UUID resume identity. An archived Session remains usable and resumable by UUID or `latest`. Setting the current state again is idempotent and appends no redundant record. `/session show`, list summaries, and the TTY toolbar expose the state.

`/session list` gains `active|archived`, exact `model=`, and case-insensitive literal `name=` substring filters. They compose with `open|closed` and the 1-to-100 result bound. Filtering reads only strictly replayed workspace Session metadata, remains newest-first, and does not add fuzzy search, name-based resume, tags, folders, or pinning.

When first-turn automatic naming uses the Host fallback, new `turn_committed` v8 atomically persists one bounded reason with the title and first turn: provider output limit, provider failure, invalid candidate, duplicate title, or exhausted provider-invocation budget. Only `source=fallback` may carry a reason; model titles and ordinary later turns may not. The terminal renders only the safe category, never provider text, exceptions, or title-request content. v1-v7 retain strict replay without rewriting, and v7 titles have no diagnostic field.

These changes are Session metadata, Host queries, and terminal presentation. Canonical system prompt v21, provider adapter contract v25, all 21 model-visible tools, ToolArguments v1, ActionIdentity v1, Action Audit v1, compaction, and Effective Context identity remain unchanged. See [0072: Session Archive, Search, and Title Fallback Diagnostics](./decisions/0072-session-archive-search-and-title-fallback-diagnostics.md).

## Pinned Sessions and Snapshot-based Quick Switching

`/session pin` and `/session unpin` store a reversible pin through append-only `session_pin_changed` v1. Pin is not rename, archive, close, or resume and changes neither history, runtime binding, Effective Context, `latest`, nor UUID identity. Setting the current state again appends no record. Older transcripts with no such record strictly replay as `pinned=false`. Session detail, list summaries, and the TTY toolbar expose pin state, while `/session list pinned|unpinned` composes with lifecycle, archive, model, name, and count filters.

Quick switching does not promote names or numbers into identity. `/session switch` builds a process-local snapshot of at most ten other strictly replayed Sessions in newest-first order; `/session switch list` accepts the same filters and a 1-to-20 bound. Each preview contains a number, name, complete UUID, turn count, lifecycle/archive/pin state, creation time, and durable runtime provenance without reading conversation text. `/session switch <number>` resolves only through the current snapshot to its complete UUID, then clears the snapshot whether or not the number was valid.

The actual switch fully reuses the existing `ProjectSession.switch_session` transaction: target-aware read-only prepare, current-runtime context screening, complete-transcript stale/CAS validation, durable `session_resumed` commit, writer transfer, and `latest` update. Ordinary prompts, new/rename/archive/pin, direct `/resume`, and every picker refresh clear the previous snapshot, so an old number is never silently reinterpreted against a new directory. Known-context rejection, stale conflict, or precommit failure preserves the current Session and runtime; post-commit partial outcomes retain the existing truthful resume semantics.

This is a Host-only Session metadata and navigation change. Canonical system prompt v21, provider adapter contract v25, the 21-tool catalog, ToolArguments v1, ActionIdentity v1, Action Audit v1, `turn_committed` v8, compaction, and Effective Context identity remain unchanged. See [0073: Pinned Sessions and Snapshot-based Quick Switching](./decisions/0073-pinned-sessions-and-snapshot-quick-switching.md).

## Read-only Session Inspection and Bounded Turn Preview

`/session show` still reports the current Session with no argument, while `/session show <latest|complete-UUID>` strictly replays arbitrary target metadata without resuming it. `/session preview <latest|complete-UUID> [1-10]` selects the three most recent committed complete turns by default and at most ten; standalone `session preview [selector] --limit N` exposes the same projection. REPL selectors accept only `latest` or a canonical lowercase UUID4. Names, picker numbers, and paths are not preview identities.

Preview projects only each turn's final user and assistant text. It does not repeat tool companion text, tool results, Action Audit, usage, or compaction summaries. Terminal controls are escaped before rendering, complete output is capped at 32 KiB, and truncation is explicit. Complete tool causality and Host execution facts remain available through `/tools`, `/actions`, and the original transcript; the simplified preview is never presented as a complete audit.

Target reads use existing-only validation, strict replay, and `allow_repair=false`. They create no empty-workspace state, take no writer lease, repair no incomplete tail, append no record, and invoke no provider. Success and failure leave the current Session, `latest`, runtime, history, Effective Context, picker snapshot, and every schema unchanged. Canonical system prompt remains v21, provider adapter contract remains v25, and the 21-tool catalog and Effective Context identity remain unchanged. See [0074: Read-only Session Inspection and Bounded Turn Preview](./decisions/0074-read-only-session-inspection-and-bounded-turn-preview.md).

## Session Search, Turn Navigation, Export, Fork, and Repair

Cross-Session search applies a case-sensitive literal query independently to each complete turn's final user and assistant logical lines. Results carry the complete UUID, 1-based turn, role, line, and bounded excerpt. One call scans at most 10,000 directory entries, selects 100 transcripts in stable UUID order, reads 16 MiB, returns 100 matches, and renders 32 KiB. Candidate, read, match, and rendering truncation are explicit, so no-match applies only to the scanned set. `/session turns` uses an independent 1-based start and a one-to-ten count to inspect a search location without turning recent-preview syntax into an ambiguous offset.

Conversation export writes Markdown or export-local JSON v1 to stdout with only Session identity and every final user/assistant turn. Selection is capped at 1,000 turns and 1 MiB of text, while complete rendering is capped at 2 MiB; overflow fails as a whole rather than silently truncating. Tool companion text, ToolUse/Result, ledgers, Action Audit, usage, failures, compaction, and raw records remain outside this readable projection, while internal JSONL remains the complete audit source.

Fork accepts only a positive complete-turn boundary from a strict source snapshot. The child receives a new UUID and writes `session_forked` v1 immediately after its header with the parent UUID, copied-turn count, and exact source transcript SHA-256. Selected turns retain complete provider-neutral items and ToolUse/Result causality. Current ledgers are copied, while legacy pre-ledger turns derive a minimal consistent ledger from requests and results. Copied provider usage is empty; parent Action Audit, failures, runtime events, names, archive/pin state, and compaction are not copied. A final runtime record installs the caller's current binding, the REPL selects the child, and parent bytes remain unchanged. Failure before `latest` replacement durably removes the new child and lock, and cleanup failure is not hidden; if replacement occurred but directory durability is unknown, the possibly referenced child is retained. If ProjectSession cannot construct the child AgentLoop, it releases the candidate writer lease and keeps the current in-memory Session unchanged.

Doctor uses a no-follow descriptor and only reports `valid | repairable_tail | invalid`. Repairable means a strictly replayable newline-terminated complete prefix followed by an invalid UTF-8/JSON final fragment. Empty data, middle or complete-line corruption, and complete JSON missing only its final newline remain invalid. Explicit repair acquires the target writer lease and existing directory lock, rechecks descriptor/path identity, durably creates a private backup named by the complete source SHA-256, then truncates only the fragment and appends/fsyncs the existing `recovery` v1 record. It does not resume, update `latest`, switch runtime/current Session, or repair an active writer.

All five stages are Host management features. Only fork adds the record-local `session_forked` v1 type; existing schemas do not advance. Canonical system prompt remains v21, provider adapter contract remains v25, and the 21-tool catalog, ToolArguments v1, ActionIdentity v1, `turn_committed` v8, Action Audit, compaction, and `ctx-v3`/`ctx-v4` identities remain unchanged. See [0075](./decisions/0075-bounded-cross-session-final-text-search.md), [0076](./decisions/0076-bounded-session-turn-range-inspection.md), [0077](./decisions/0077-bounded-conversation-export.md), [0078](./decisions/0078-provenance-linked-session-forking.md), and [0079](./decisions/0079-explicit-session-diagnosis-and-tail-repair.md).

## Foundation 1D: Bounded Literal Grep and Versioned Tool Arguments

The model-visible read-only surface now has the fixed `read_file, glob, grep` order. `grep(query, include)` uses the same portable workspace-relative selector as glob to choose non-symlink regular files, then performs case-sensitive literal substring search within strict UTF-8 logical lines. Each matching source line produces one compact JSONL record containing a POSIX relative path, 1-based line number, and complete line text. Regex, indexing, Unicode normalization, `.gitignore`, multiple patterns, and context windows remain unsupported.

Grep has explicit hard bounds: at most 1,000 candidates, 1 MiB per file, 16 MiB aggregate reads, 200 matching lines, and 32 KiB model-visible output, in addition to the selector's entry, directory, and depth limits. An unreadable, oversized, NUL-bearing, or invalid-UTF-8 selected file is a safe whole-call error. Only match/output caps return a stable prefix of complete JSON records followed by a `{"truncated":true}` sentinel. Empty success means the bounded candidate set was searched completely. Reads recheck regular/non-symlink descriptor identity while retaining the documented local single-user TOCTOU boundary.

To represent grep's two fields, in-memory `ToolUse` gained immutable canonical-JSON `ToolArguments` v1. At Foundation 1D, new `turn_committed` records used record-local schema v2 with `arguments_version + arguments`; legacy schema-v1 read/glob items converted to the same generic in-memory representation during replay without rewriting old JSONL, and resume appended only v2 at that stage. Assistant-tool-text persistence, multi-tool batching, and the tool outcome ledger later advanced the writer through v3/v4/v5; provider usage audit advanced it to v6, atomic first-turn Session titles advanced it to v7, and current title-fallback diagnostics advance it to v8 while retaining v1-v7 readers. New `turn_failed` records use v2, and new `context_compacted` records use v4 while retaining v2/v3 compatibility. Current Effective Context uses ctx-v3/v4 while replaying legacy ctx-v1/v2 checkpoints.

All three tools continue to share three sequential executions per user turn, while AgentLoop and ProjectSession retain explicit composition and dispatch rather than a dynamic registry. Anthropic and OpenAI-compatible ordinary count/create requests project the same exact three-schema catalog, compact summaries remain no-tools, and parallel calls remain disabled. The adapter contract advances to v5; canonical model system prompt v4 declares literal grep, correct empty/truncated interpretation, and still-unavailable write/Bash/regex capabilities. Generic arguments, prompt, and catalog intentionally change current-binary context IDs without rewriting historical checkpoints.

See [0021: Foundation 1D Bounded Literal Grep](./decisions/0021-foundation-1d-bounded-literal-grep.md) for the complete design.

## Foundation 1C: Bounded Workspace Glob

The model-visible read-only surface now contains the fixed ordered `read_file` and `glob(pattern)` tools. `glob` uses workspace-relative portable `/`-separated patterns with component `*`, `?`, bracket classes, and whole-component `**`. Bare patterns do not become recursive implicitly, hidden components require an explicit leading dot, and `.gitignore` is not read. Results contain only non-symlink regular files as POSIX relative paths in deterministic UTF-8 lexical order; directories, special files, and every symlink are neither returned nor traversed.

Search has several hard bounds: at most 4096 pattern characters/bytes and 64 components, 200 matches, 32 KiB output, 10,000 scanned entries, 1,000 directories, and depth 32. Match/output caps return a stable prefix plus `[truncated]`; traversal or depth bounds return a safe error because completeness cannot be established, without exposing an absolute workspace or raw OS failure. The implementation uses only standard-library `os.scandir` and component `fnmatchcase`, with no shell or new dependency, and states its local single-user TOCTOU boundary honestly.

Both tools share three sequential executions per user turn. AgentLoop still dispatches explicitly, unknown tools and limits become structured results, and provider or durable-commit failure leaves the candidate turn uncommitted. A narrow canonical catalog fixes `read_file, glob` order and drives Effective Context identity plus both Anthropic and OpenAI-compatible ordinary count/create schemas. Compact summaries remain no-tools and parallel calls remain disabled.

Foundation 1C originally preserved append-only compatibility by using schema-v1 `ToolUse.path` as the read/glob single-string seam and projecting `{"path":...}` or `{"pattern":...}` natively. That allowed old read-only Sessions and mixed glob/read turns to resume and compact without rewriting. Foundation 1D has now replaced this temporary seam with `ToolArguments` and record-local turn schema v2, while the legacy v1 decoder remains compatible. Foundation 1C's adapter v4, prompt v3, and two-tool context identity remain documented as historical design facts.

See [0020: Foundation 1C Bounded Workspace Glob](./decisions/0020-foundation-1c-bounded-workspace-glob.md).

## Foundation 1B: deterministic bounded read_file tool loop

The REPL and `prompt` command complete this minimal, testable path:

```text
terminal input → AgentLoop (one pinned canonical system-prompt snapshot + ordered causal context)
  → ScriptedFakeProvider → optional read_file within the current workspace
  → structured tool result → ScriptedFakeProvider → final text output
```

A provider response is either final assistant text or one `read_file` request. The loop returns final text only after the provider finishes, and commits the whole attempted turn—user input, any tool request/result, and final assistant text—only after that success.

Each user turn permits at most three file reads. A further request receives a structured limit error; another tool request after it stops deterministically.

`read_file` accepts only a relative path whose resolved target remains inside the current workspace. It rejects absolute paths, `..` or symlink escapes, missing paths, directories, unreadable files, and invalid UTF-8. It returns at most 32 KiB of UTF-8 text with a truncation marker. It cannot write, rename, delete, execute commands, search, or access the network.

The default `ScriptedFakeProvider` retains visible echo behavior and does not request tools by itself. Its scripted form provides deterministic tool-loop evidence in tests, while `demo-read <path>` exposes the same fixed cycle for manual terminal verification.

The `prompt` command remains one-shot, but every successful turn is auto-saved. Within one REPL, `/history <count>` shows only completed user/final-assistant turns from the current Session, never internal tool data.

Foundation 1B originally proved only process-local atomic history. Foundation 3D now persists each complete turn to workspace JSONL. A bare `coquo` invocation in a noninteractive terminal explains that automation should use `coquo prompt "..."` and exits nonzero, avoiding accidental hangs in pipes or CI.

See [0001: single-turn loop](./decisions/0001-foundation-0-single-turn-loop.md), [0002: deterministic REPL](./decisions/0002-foundation-0-deterministic-repl.md), [0003: in-memory text history](./decisions/0003-foundation-1a-in-memory-text-history.md), and [0004: bounded read_file tool loop](./decisions/0004-foundation-1b-bounded-read-file-tool-loop.md) for the detailed decisions.

## Foundation 3H: Pre-turn Automatic Context Compaction

Ordinary one-shot and REPL prompts now assess the exact initial request before sending a new turn: current Effective Context plus the pending user message and requested output reserve. A known `FITS` at `(input + reserve) * 100 >= window * 80` gets at most one proactive `high_water` compaction attempt; known `CONTEXT_EXCEEDED` gets at most one mandatory `overflow` attempt. `UNKNOWN` is not guessed and generates no summary, fake mode remains request-free and quiet, and `MODEL_OUTPUT_EXCEEDED` is rejected directly because compaction cannot repair the reserve.

`PreparedAgentTurn` pins exactly one pending `UserMessage` and the committed context snapshot before any history mutation. The pending item participates in source and candidate assessment so the decision covers the request that would really be sent; it never enters the summary source, checkpoint, context identity, or durable history. After a successful checkpoint the prepared turn rebases only its committed snapshot, sends the same pending tuple once, and persists it only if the complete ordinary turn succeeds.

Automatic and manual `/compact` share the 3F-2 prepare → runtime work → revalidate/commit/install transaction: at least four complete effective turns, retain the latest two, summarize earlier complete turns, require comparable known counts and a known-`FITS` candidate that strictly reduces pending-inclusive input, and install memory only after checkpoint append and fsync. One `provider_for_turn()` lease pins provider, route, capability, status, and generation across initial assessment, summary, candidate assessment, and the complete tool loop while blocking switches, another turn, manual compaction, resume transition, and close.

Each prompt gets only one automatic attempt: no recursive compaction and no retry after a tool continuation or provider error. If a proactive failure remains a safe precommit failure and the original request is a known `FITS`, a warning is emitted and the original turn continues. A mandatory failure preserves the original overflow rejection and sends no ordinary generation. A stale source or uncertain checkpoint durability cannot continue the old request; if the checkpoint committed durably before later generation failed, the checkpoint remains while the pending turn does not commit.

New `context_compacted` records use closed schema v3 with `trigger = manual | high_water | overflow`; only `high_water` carries the fixed `high_water_percent = 80`. Schema-v2 checkpoints continue to replay as legacy manual provenance. Trigger data is audit-only and appears in `/context`; it does not enter `ctx-v2` identity, and token counts, fit reports, and pending prompts are not persisted. Typed prompt events contain only safe count evidence, context IDs, turn counts, checkpoint sequence, and reason codes. One-shot events go to stderr so stdout remains the model response alone.

The canonical model system prompt was reviewed: automatic timing remains entirely Host-controlled, the model still cannot request compaction, and the existing untrusted Host-summary framing already covers post-compaction input. Version 2, exact text, and fingerprint therefore remain unchanged. See [0019: Pre-turn Automatic Context Compaction](./decisions/0019-pre-turn-automatic-context-compaction.md).

## Foundation 3G: Target-aware Resume Prepare/Commit

Startup `--resume` and REPL `/resume` now prepare the target, build its candidate Effective Context, and screen it against the current runtime before durable commit. Known context/model-output overflow is rejected before any resume audit, tail recovery, or latest-pointer write. `UNKNOWN` fails open with a warning; fake mode explicitly reports screening unavailable and sends no provider request. Resume still restores Session state only and never reconstructs the runtime from historical bindings.

`SessionStore.prepare_resume()` is a physically read-only, single-use exclusive lease. It requires the existing root, directory lock, target lock, latest metadata, and transcript; replays through a retained `O_NOFOLLOW` descriptor; and records an incomplete final crash tail only as pending recovery. The transcript stale token includes device/inode/size/mtime/ctime plus exact-byte SHA-256, while the `latest` selector also captures a pointer token. Before its first write, commit revalidates the transcript, pathname, target lock, and latest CAS. Appends, same-size replacement, inode/symlink/lock swaps, and a latest-pointer move during counting therefore become retryable conflicts. Explicit UUID/path selection ignores unrelated latest moves, while a same-current selector returns a write-free no-op.

Commit candidate-replays the proposed records before applying `Recovery` when needed, then `SessionResumed`, then the atomic latest update. `Recovery` may follow `SessionClosed` but keeps the state closed until `SessionResumed`. The prepared descriptor/lock transfers to `SessionWriter` after success; ordinary appends also use that descriptor and verify pathname identity, eliminating the revalidate/reopen TOCTOU gap.

The fsync of `SessionResumed` is the semantic commit point. Typed outcomes distinguish precommit/stale failure, recovery-only durability, unknown transcript durability, applied resume with an unchanged failed latest pointer, and replaced latest with unknown directory-fsync durability. Errors after the commit point no longer claim that everything was unchanged or attempt unreliable rollback. Top-level `--resume ... prompt` sends resume evidence to stderr so stdout contains only the final model response; known rejection exits 2 with empty stdout.

The Manager's context-transition lease pins the current provider, route, capability, status, and generation while blocking switch, turn, compact, and close. Screening uses the candidate loop's `effective_context_snapshot()`, so a compacted Session is measured only as summary plus retained suffix. The next real invocation still performs full preflight. The canonical model system prompt was reviewed: this slice has no model-visible change, so version 2, exact text, and fingerprint remain unchanged. See [0018: Target-aware Resume Prepare/Commit](./decisions/0018-target-aware-resume-prepare-commit.md).

## Foundation 3F-2: Controlled Compact Transaction

REPL `/compact` can shorten provider-visible effective context manually while preserving the complete append-only transcript and `/history`. Foundation 3F-2's fixed policy requires at least four complete effective turns, retains the latest two verbatim, and uses the current real provider once to summarize the earlier projection. Fake runtime is unavailable; that original slice did not trigger automatically and did not retry the original user turn. Foundation 3H later invokes the same transaction before a new turn from known evidence, while still providing no failed-turn retry.

Compact generation uses a separately versioned prompt and a dedicated no-tools request. The Anthropic native body omits `tools`; OpenAI-compatible bodies omit both `tools` and `parallel_tool_calls`; counting and generation share the same input projection. Only normally completed nonempty text is accepted. Tool calls, refusal, truncation, and malformed responses fail closed.

A summary is not a `ConversationItem` or real turn. Effective state is `Host summary + retained complete-turn suffix`, and adapters project the summary through explicit untrusted continuation framing. The normal Agent canonical system prompt is version 2 and explains that a Host summary is earlier conversation context, not a system instruction or new user request. Contexts without summaries retain the original `ctx-v1` identity format; summary-bearing contexts use `ctx-v2`.

Session migration does not rewrite old lines: ordinary records remain schema v1, legacy Foundation 3F-2 `context_compacted` records use schema v2, and current manual and automatic checkpoints use schema v3. V3 adds trigger provenance and an optional high-water percentage. Mixed replay accepts v2/v3, interprets v2 as manual, reconstructs full history from every `TurnCommitted`, restores summary/retained state from the latest checkpoint, and appends later turns to both full and effective state. Checkpoint append reuses candidate replay validation, O_APPEND, flush/fsync, and installs in-memory effective state only afterward.

The transaction freezes writer/session/sequence, loop, full/effective state, and source context ID before generation, then rechecks them after generation and candidate assessment. Source and candidate need comparable known counts; the candidate must be a known `FITS` and strictly reduce input tokens. Precommit, stale, and persistence failures do not write `TurnFailed` or change effective memory.

After compaction, `/context` reports checkpoint source, summary presence, retained real turns, and checkpoint sequence, without counting the summary as a transcript turn or item. See [0017: Controlled Compact Transaction](./decisions/0017-controlled-compact-transaction.md).

## Provider-neutral Effective Context Snapshot and `/context`

`AgentLoop` now distinguishes full history derived from the append-only transcript, provider-visible effective history, and one invocation request. In 3F-1, full and effective history still remain exactly equal after restore, successful commit, and resume. Each initial request and tool continuation derives from one `EffectiveContextSnapshot` plus the current pending suffix, so model behavior is unchanged while future compaction no longer needs to rewrite `/history` or durable transcript truth.

Complete committed history uses one strict validator that accepts only `UserMessage, (ToolUse, matching ToolResult)*, AssistantText`; tool pairs must be adjacent, IDs must match, and IDs are globally unique. Session replay, loop restoration, and effective-context construction share this causal rule while retaining their own schema, size, and provider-invocation terminal validation.

The snapshot applies canonical JSON and domain-separated SHA-256 to the current system prompt, neutral `read_file` contract, and complete effective turns, producing a stable `ctx-v1-...` content identity. The identity excludes Session/runtime/provider/audit/token metadata, is not persisted in JSONL, and is not presented as proof of transcript tamper resistance.

REPL `/context` freezes context and target under the `ProjectSession` facade lock and shows source, context ID, full/effective turn and item counts, exact/estimated/unknown input, reserve, both model limits, fit, and known remaining capacity. It invokes no generation or tool, writes no transcript or audit record, and changes no history or runtime. Fake mode is explicitly unavailable; OpenAI-compatible routes use a local estimate; exact inspection on the official Anthropic route may call count-only `messages.count_tokens` but never `messages.create`.

Session schema remains v1 and persists no effective context, checkpoint, or count. See [0016: provider-neutral Effective Context Snapshot](./decisions/0016-provider-neutral-effective-context-snapshot.md). The canonical model system prompt was reviewed: Host-only inspection and full-history passthrough add no model-visible capability, so version 1 and its fingerprint remain unchanged.

## Target-aware runtime switch UX

The long-lived runtime now screens the current committed conversation context against a destination before `/provider use`, `/model`, or the matching `ProjectSession` API commits its candidate. `AgentLoop` builds a read-only snapshot from the current canonical system prompt and exact committed causal history. An empty Session remains `history=()`; no synthetic user message is invented for counting.

Adapter counting accepts empty history or complete committed history ending in `AssistantText`, while actual `respond()` remains strict about invocation history ending in `UserMessage` or `ToolResult`. Anthropic and OpenAI-compatible counting therefore continue to share the same native projection as create without weakening send-time causal validation.

The Manager screens the same provider/route/capability candidate it already prepared:

- known context or model-output overflow raises `RuntimeSwitchContextError` before the active selection or client changes, closes the candidate, and preserves the old runtime, selection, and generation;
- `FITS` commits and returns the count method/value, reserve, and window;
- `UNKNOWN` fails open, but the REPL warns that compatibility is not confirmed, no history was deleted, and the next real invocation still runs full preflight;
- a fake destination needs no compatibility report.

Under its facade lock, `ProjectSession` freezes history, screens and commits, and then appends the existing schema-v1 `RuntimeChanged` record. If the runtime changed but audit append fails, `RuntimeSwitchAuditError` carries the applied result rather than falsely claiming that the switch did not happen or attempting an unreliable rollback. Transcript bindings now preserve the real runtime generation. A rejected switch writes no conversation, `TurnFailed`, or runtime-change record.

The original Foundation 3E slice did not pre-screen `/resume` or startup `--resume`; Foundation 3G now closes that boundary with read-only preparation, current-runtime screening, and a durable commit transaction. Runtime switching itself still does not compact, delete history, or create a new Session automatically.

See [0015: target-aware runtime switch UX](./decisions/0015-target-aware-runtime-switch-ux.md). The canonical model system prompt was reviewed; this remains Host-side runtime control, so version 1 and its fingerprint remain unchanged.

## Target-specific request counting and per-invocation preflight

The runtime now pins the provider client, exact route, context/model-output capability, and redacted status in one immutable turn snapshot. That snapshot is the only provider-invocation entry point, so the initial request, every `read_file` continuation, and the final invocation after the tool limit are all preflighted again.

The decision keeps three concepts distinct: context window, model maximum output, and the current route's requested output reserve. `input + reserve == window` is allowed; a known `>` is rejected locally before sending; if any required fact is unknown, the Host does not guess and lets the provider remain the final authority. A rejected turn commits no conversation history and appends only a safe `TurnFailed` audit record.

For the official Anthropic endpoint, the official SDK's `messages.count_tokens` counts the same model/system/messages/tools projection shared with create. A failure safely degrades to a compact UTF-8 JSON `ceil(bytes / 4)` estimate. OpenAI-compatible Chat Completions always uses the matching local estimate rather than calling a count endpoint belonging to a different protocol.

Provider-profile schema v4 adds a `model_max_output_tokens` override, while private discovery-cache schema v2 can store positive context and model-output limits independently. `route`, `/status`, and `/provider current` show both limits and the requested reserve, but no successful last-request token meter is persisted. Foundation 3H now consumes the fit report before a new turn to decide whether to compact; per-invocation preflight remains the final gate for every real request.

See [0014: target-specific request counting and preflight](./decisions/0014-target-specific-request-counting-and-preflight.md). The canonical model system prompt was reviewed: this slice adds Host-side send control without changing model-visible capabilities, so prompt version 1 and its fingerprint remain unchanged.

## Provider-owned model context capability

The runtime can resolve the current exact endpoint/model context window without fabricating unknown limits. Resolution follows a fixed precedence:

1. the named profile's exact override;
2. an exact official provider/endpoint/model built-in entry;
3. a fresh private XDG discovery cache entry;
4. provider-owned live discovery;
5. `unknown`.

The official Anthropic endpoint reuses the same official SDK client for the Models API. Generic OpenAI-compatible `/models` responses do not share a context-metadata contract and are therefore not probed blindly.

```bash
uv run coquo provider add local-qwen \
  --provider custom \
  --model Qwen/Qwen3.5 \
  --protocol openai-compatible \
  --base-url http://127.0.0.1:11434 \
  --context-window-tokens 131072
uv run coquo provider show local-qwen
uv run coquo --profile local-qwen route
```

`provider show` labels user configuration as a `context window override`; offline `route` and runtime `/status` show the resolved value and source. Successful discovery is stored only at:

```text
${XDG_CACHE_HOME:-~/.cache}/coquo/model-context-capabilities.json
```

The cache contains no credential value, raw provider body, or Session content. Profile-registry schema v3 reads v1/v2/v3, upgrades only the layer written, and supports explicit `provider migrate`.

This slice establishes capacity facts only. It does not count current request tokens, reject oversized requests, or compact history. See [0013: provider-owned model context capability](./decisions/0013-provider-owned-model-context-capabilities.md) for the detailed design.

## Resume Runtime Binding and the First Tool Action

Session resume always keeps the caller's current runtime and never reconstructs a provider from transcript history. Legacy `session_resumed` v1 reopened lifecycle state without updating replay's current binding. A resumed text-only turn therefore switched binding naturally at its final `turn_committed`, but a provider that requested a tool first reached strict `action_requested` validation before commit and safely failed when the current runtime differed from historical provenance.

New `session_resumed` records use record-local schema v2 and capture the context-screened current redacted `BindingSnapshot` at the same append-and-fsync semantic commit point. Both startup `--resume` and live REPL Session switching pass their pinned context-transition runtime. Candidate replay installs that binding before the first resumed Action while binding equality and Action lease validation remain strict. Low-level `SessionStore.open` retains the replayed binding when no explicit binding is supplied.

Legacy v1 remains readable without rewriting; v1 rejects a binding field and v2 requires one complete valid binding. `SessionResumed` fsync semantics, latest-pointer partial outcomes, resume CAS, model history, and Effective Context identity remain unchanged. Canonical system prompt stays at v25, provider adapter stays at v26, and all 21 tool schemas, ToolArguments, Action Audit, Task records, and context representations remain unchanged. See [0091: Resume Runtime Binding at the Durable Commit Point](./decisions/0091-resume-runtime-binding-at-the-durable-commit-point.md).

## Adaptive Foreground Task Orchestration

Tasks now project bounded Host/reviewer checks for the current completion proposal into the next Stage. A new `reflection` Stage strictly disables tools and may only use `TASK_REFLECTION_JSON` to recommend `continue`, `correction`, `revise-plan`, `needs-human`, or `fail`; the Host appends `task_reflection_recorded` only after the Stage's ordinary Session Turn commits. Reflection cannot execute, accept, authorize, or complete a Task.

A `correction` Stage continues through the ordinary AgentLoop, PermissionGate, approval, Action Audit, tool budgets, cancellation, and atomic Session commit. A correction's new completion proposal makes checks and verifications for the old proposal non-current while preserving history. New plan proposals use schema v2 to identify their direct predecessor, revision reason, and optional reflection provenance. Legacy v1 remains replayable, and every revised plan still requires explicit acceptance.

`/task drive <id> [1-16]` implements a bounded, cancellable, foreground-only state machine. It can propose an initial plan, run accepted steps, execute deterministic Host checks, reflect after failure and run one advised correction/continuation, or propose a revised plan. It stops accurately at pause, recovery requirement, budget, Stage limit, pending/exhausted plan, human evidence, independent review, manual completion, or reflection escalation. The Driver never invokes a token/API-cost-bearing independent reviewer automatically; `/task next` previews the next decision and cost boundary without mutation.

`task_pause_changed` blocks only automatic driving while explicit Stage and management commands remain available. `task_context_checkpoint` stores source sequence, checkpoint chain, accepted-plan progress, current completion Stage, unresolved criterion indices, and latest reflection ID. It stores no dialogue, tool arguments, or complete output, becomes current only after candidate replay and append+fsync, and never deletes or rewrites the complete Task transcript. Task framing may use a checkpoint plus a shorter recent-Stage suffix.

The canonical system prompt advances to v26. Provider adapter v26, all 21 tool schemas and order, ToolArguments v1, ordinary Turn budgets, Session and Action Audit schemas, and `ctx-v5`/`ctx-v6` representation versions remain unchanged. The no-project-instructions empty full-context ID becomes `ctx-v5-4f33f80622dd368a51b4046c5292951f2dd42fdb05b3d9be798dfa6b5f2457a4`. See [0092: Adaptive Foreground Task Orchestration](./decisions/0092-adaptive-foreground-task-orchestration.md).

## Task Proposal Control Boundary

The `/task` family remains the human/operator command surface and the foreground Driver remains Host-owned. Future model Task interfaces enter a separate proposal adapter: they do not generate slash commands and cannot operate `TaskStore` directly. `ConversationRequest` and `PreparedAgentTurn` can now pin one exact tool-name subset; Anthropic count/create and OpenAI-compatible estimate/create project that same subset in global canonical order. A provider request for an unexposed tool fails before any dispatch.

AgentLoop gains a Task-control dispatch seam separate from `ActionDispatcher`. A control call must be the only tool call in its assistant response and closes tools after handling so the next invocation is text-only. It still enters ordinary ToolUse/ToolResult causality, the shared Turn budget, Session transcript, and Host tool ledger, but a proposal receives no Action lease and creates no Action Audit merely for requesting coordination.

The internal `TaskControlProposal` binds proposal kind, Task/Stage identity, pinned Effective Context ID, tool-use ID, and bounded ToolArguments. A successful dispatch must carry the matching proposal, and AgentLoop invokes the Host proposal sink only after the complete Session Turn commits successfully. Recovery trusts neither assistant prose nor ToolResult content: the committed Turn must contain exactly one matching control call and the Host ledger must record the same ID and tool name as `succeeded`. No concrete Task coordination tool is public yet, so ordinary model behavior, the 21-tool catalog, and system prompt v26 are unchanged. Exact subset projection advances the provider adapter contract to v27; `ctx-v5`/`ctx-v6`, Session/Task/Action Audit schemas, and 8/32/24 budgets remain unchanged. See [0094: Task Proposal Control Boundary](./decisions/0094-task-proposal-control-boundary.md).

## Model-visible Task Coordination Tools

The complete canonical catalog now appends `task_propose_plan`, `task_report_reflection`, `task_report_blocker`, and `task_propose_completion` after the original 21 ordinary tools. Ordinary prompts still expose only those 21 tools. Planning receives bounded read/Git observation plus plan/blocker; reflection receives only reflection/blocker; execution and correction receive the 21 ordinary tools plus completion/blocker. A provider request outside the exact current-Stage subset fails before dispatch.

The four tools submit proposals only and are not filesystem Actions. They receive no Action lease, do not pass through PermissionGate, and create no Action Audit, while still participating in ToolUse/ToolResult causality, the shared Turn budget, Session transcript, and Host tool ledger. A control call must be response-exclusive and permits only text finalization afterward. A blocker uses a closed category, moves the current Task to blocked, and stops the Driver as `model-blocked`, but cannot grant permission, supply evidence, terminate, or complete the Task.

The proposal sink retains only an immutable value bound to Task, Stage, context, tool-use ID, and canonical arguments. Durable order is `stage_started -> Session Turn append+fsync -> stage_committed -> Task proposal record`: a Session or Stage commit failure writes no Task proposal, while failure of the final Task append can recover from the exact committed Turn and successful Host ledger. Recovery of the same tool-use ID and canonical payload is idempotent; changed arguments, a reused ID across the Task, or another proposal for the same Stage is rejected. New plan, completion, reflection, and blocker records use schemas v3, v2, v2, and v1. Legacy plan v1/v2, completion v1, and reflection v1 remain readable without rewriting. `TASK_PLAN_JSON`, `TASK_REFLECTION_JSON`, and `TASK_COMPLETION_PROPOSAL` remain only for historical Stage recovery compatibility.

The canonical system prompt advances to v27 and the provider adapter contract advances to v28. Effective Context representations remain `ctx-v5`/`ctx-v6`; the catalog change updates the no-project-instructions empty full-context ID to `ctx-v5-63362449120e69a39d2a03b22c8c1937ee66d2fd67d065d4e3ccfd3466d88aa7`. ToolArguments v1, Session/Action Audit/compaction/Task Stage schemas, the 8/32/24 budgets, acceptance policy, and workspace hard boundaries remain unchanged. See [0095: Model-visible Task Coordination Tools](./decisions/0095-model-visible-task-coordination-tools.md).

## Model-proposed Task Admission

An ordinary Prompt now exposes `task_propose_start(objective, reason, acceptance_criteria)` after the original 21 tools, allowing the model to explain why work needs multiple bounded Stages. The call creates only a proposal: it does not create or accept a Task, execute a Stage, acquire an Action lease, pass through PermissionGate, or create Action Audit. It must be the only call in its assistant tool response, shares the 8/32/24 budgets, and forces text-only finalization after its receipt. The four existing Stage coordination tools remain unavailable in ordinary Prompts, and Task Stages never receive `task_propose_start`.

Immutable `TaskAdmissionProposal` binds the objective, reason, one to sixteen acceptance criteria, pinned Effective Context ID, and tool-use ID into a deterministic `tap-v1-...` identity. Pending state gets no duplicate record and is derived only from a durable committed Turn: replay requires the exact ToolUse, matching non-error ToolResult receipt, and a Host-ledger `succeeded` fact for the same ID and name. Assistant prose, an uncommitted Turn, or a failed ledger cannot manufacture a proposal.

The user manages only current-Session proposals through `/task proposals [pending|accepted|rejected|all]` and `/task proposal show|accept|reject|drive`. The first `accept` call only previews the canonical name, budget, completion policy, prepared criteria, and configuration/confirmation SHA-256 values. Only a second confirmation carrying the digest creates a Task with `task_admission_origin` v1 and appends `task_admission_resolved` v1 when the candidate still matches. The origin persists both digests. If Task creation succeeds but the Session append fails, an exact retry finds the same Task and adds the missing resolution, while changed configuration or confirmation fails. Acceptance invokes no provider; `drive` separately hands an accepted proposal to the existing bounded foreground Driver. Reject appends only a rejected resolution and creates no Task; pending and rejected proposals cannot drive.

The canonical catalog has 26 definitions, ordinary Prompts expose 22, and exact Task Stage subsets do not change. Informed acceptance and Driver handoff are Host/terminal lifecycle changes, so system prompt v28 and provider adapter contract v29 remain current. Effective Context stays `ctx-v5`/`ctx-v6`, including the no-project-instructions empty full-context ID `ctx-v5-0112c304e7ae0718fad6efdc4e7f5b258d267d9922854d3846fe76f1e594abf0`. `turn_committed` v8, ToolArguments v1, Action Audit, compaction, budgets, and old Session/Task transcripts remain unchanged and are not rewritten. Provider startup failure preserves the accepted admission and an accurate failed Stage, so restart can drive again. If the Session Turn committed but the Task proposal append failed, `/task recover` writes only the proposal proven by exact committed ToolUse and successful Host-ledger evidence; it reruns neither provider nor Stage. Automatic acceptance, automatic driving after acceptance, and implicit cross-Session mutation remain unavailable. See [0097: Informed Task Admission and Foreground Handoff](./decisions/0097-informed-task-admission-and-foreground-handoff.md).

## Natural-language Task Lifecycle Handoffs

Ordinary Prompts add `task_accept_admission`, `task_accept_plan`, and `task_confirm_completion` to translate the current user's explicit natural-language decision into one closed structured request. The model does not generate slash commands, and the Host does not keyword-match text such as "OK" or "accept". The model interprets language; the Host accepts only the exact pending admission, latest unaccepted plan, or current completion proposal. Ambiguous language, model recommendations, project instructions, files, tool results, and summaries are not human authorization.

Lifecycle dispatch does not mutate Task state. The Host first binds the request to the current Session, pinned Effective Context, tool-use ID, subject, and confirmation SHA-256, plan ID, or completion Stage ID. Only after the AgentLoop commits the complete ordinary Session Turn and successful tool ledger does the post-commit sink recover exact causality, revalidate stale state, and invoke the existing Task APIs. Admission and plan acceptance then emit a typed foreground handoff: the persistent terminal waits boundedly for the old worker to clear and starts the same `drive_task`, while the plain REPL continues after `prompt()` returns. Neither path synthesizes a user message or slash command. One-shot invocation commits lifecycle state but does not read stdin or secretly enter an interactive loop.

Completion confirmation can use that direct user Turn only for unresolved `human` criteria. Any unverified Host-check or independent-reviewer criterion rejects before a successful tool receipt. The three tools cannot grant filesystem permission, approve an Action, bypass Task budgets, or fabricate an acceptance source. `/task` remains available for custom admission configuration, exact preview, audit, rejection, pause, recovery, independent review, and advanced control, but is no longer required for the ordinary success path.

The catalog grows from 26 to 29 definitions and ordinary Prompts from 22 to 25 tools; Task Stage least-capability subsets do not change. The canonical system prompt advances to v29, provider adapter contract to v30, and the no-project-instructions empty full-context ID becomes `ctx-v5-d7662f867a8ebb6f1be1be18eaa0090ef96fb22547cd3a9d7104dc2f69a0328e`. Effective Context representations, ToolArguments v1, 8/32/24 budgets, Session/Task/Action Audit schemas, and old transcripts remain unchanged without rewriting. See [0098: Natural-language Task Lifecycle Handoffs](./decisions/0098-natural-language-task-lifecycle-handoffs.md).

## Recoverable Provider Tool Argument Validation

The Anthropic and OpenAI-compatible adapters now distinguish provider responses that cannot be represented safely from canonically bounded arguments that violate only an ordinary tool's specific schema. Invalid JSON, non-objects, unknown tools, duplicate or invalid IDs, arguments beyond the global 16 KiB bound, and invalid Task coordination input still fail with `response_invalid` and commit no Turn. A bounded ordinary-tool call is instead frozen as ToolArguments and passed to the existing Host tool boundary, which returns a matching error ToolResult. The next continuation replays that exact ToolUse/ToolResult causality so the model can shorten or correct its request within the same Turn.

No execution boundary is relaxed. The provider schema for `write_file` now adds `maxLength: 4096` guidance, while the Host still enforces both 4,096 characters and 4,096 UTF-8 bytes; PermissionGate and approval cannot increase either bound. Task proposal and lifecycle tools retain complete adapter-side validation and cannot approach the durable Task state machine with invalid arguments.

The provider adapter contract advances to v31, while canonical system prompt v29 remains unchanged. Catalog count and order do not change, but the exact `write_file` schema updates the no-project-instructions empty full-context ID to `ctx-v5-e681ce5f35a3bd5b4d0591912d49119c767e97ad87b9ecad6806777c3a6caecd`. Effective Context representations, ToolArguments v1, Session/Task/Action Audit schemas, the 8/32/24 budgets, and old transcripts remain unchanged without rewriting. See [0099: Recoverable Provider Tool Argument Validation](./decisions/0099-recoverable-provider-tool-argument-validation.md).

## TTY Host Wrapping and Process-local Command History

The persistent TTY now converts dim Host blocks and in-Turn `  │ ` traces into bounded visual lines at the current display width before applying the same indentation or rail to every line. Long context, tool, usage, failure, and slash-result lines therefore no longer depend on terminal edge wrapping that returns continuations to column zero. Non-TTY and redirected output, assistant Markdown, and internal approval styling retain their existing paths.

Prompt history still starts from at most 1,000 committed user prompts in the current Session, but every ordinary prompt or single-line slash command accepted by the current process immediately enters the same bounded in-memory history for Up/Down and Ctrl-R recall. Slash history never enters the Session transcript, Action Audit, or provider history and disappears when the process exits. A Session switch replaces the ordinary-prompt source with the target Session history while retaining the slash command that triggered the switch. Canonical system prompt v26, provider adapter v26, Effective Context identity, and every durable schema remain unchanged. See [0093: TTY Host Wrapping and Process-local Command History](./decisions/0093-tty-host-wrapping-and-process-local-command-history.md).

## Persistent Activity Indicator and Task Output Alignment

The persistent TTY now places one bounded activity row above the prompt editor. Ordinary turns begin with `Preparing turn`, Task workers begin with `Preparing Task Stage`, and typed frontend events then update the label for provider preparation, model response, a specific running tool, approval, compaction, Session persistence, or Task lifecycle work. The row is text-only, contains no symbol or animation, and disappears on `Ready`. Host-owned labels contain no file content, complete argv, provider payload, or Task body.

Complete assistant responses now always reuse the `• ` role marker and two-space hanging indent, including the defensive stream-mismatch fallback exercised by Task orchestration. Plain streaming also restores the continuation prefix after explicit model newlines. Markdown, dim Host blocks, and `  │ ` traces retain their existing display-width wrapping paths. The activity row is ephemeral prompt-toolkit UI and enters neither Session or Task transcripts, Action Audit, provider history, compaction, Effective Context, nor Eval evidence. Canonical system prompt remains v29, provider adapter contract remains v31, and every model-visible, budget, and durable-schema contract remains unchanged. See [0100: Persistent Activity Indicator and Task Output Alignment](./decisions/0100-persistent-activity-indicator-and-task-output-alignment.md).

## `turn_committed` v5 Inherited Content Compatibility

`turn_committed` v3 introduced ordinary `tool_use.assistant_text`, v4 introduced atomic `assistant_tool_batch`, and v5 added the Host tool ledger on top of those existing capabilities. Historical v5 writers therefore persisted inherited fields together with the ledger, including an explicit `assistant_text: null` when no companion text existed. The current codec accidentally omitted v5 from manually enumerated item-version sets, so strict replay reported a legal field as unknown and could block a new turn while first-turn Session title conflict checking scanned historical Sessions.

The item codec now expresses inheritance at each capability's introduction boundary: every supported schema at v3 or later reads and writes ordinary assistant companion text, and every supported schema at v4 or later reads and writes assistant tool batches. The supported set remains closed to v1 through v8 and does not admit unknown future versions. Strict v1/v2 rejection, v5 ledger validation, complete causality, and historical transcript bytes remain unchanged. Canonical system prompt remains v29, provider adapter contract remains v31, and Effective Context, Session version numbers, and every other durable contract remain unchanged. See [0101: turn_committed v5 Inherited Assistant Content Replay](./decisions/0101-turn-committed-v5-inherited-assistant-content-replay.md).

## Bounded Independent Brave/Tavily Web Search

`web_search(query, max_results)` adds Coquo's first Host-owned public-web search path. The model supplies only a unified query and result count; the Host selects a fixed Brave or Tavily Search API. It accepts no model-selected endpoint and does not read result pages, so its causality and provenance remain distinct from future provider-native search, MCP search, and general `web_fetch`. The tool is available to ordinary Prompts and Task planning, execution, and correction Stages, but not reflection Stages.

Brave uses a fixed GET request and subscription-token header. Tavily uses a fixed Bearer-authenticated POST with basic search, one chunk per source, and automatic parameters, generated answers, raw content, and images disabled; Tavily documents this as one basic-search credit. The Host limits queries to 512 characters/2 KiB and results to 1 through 10, with a 15-second timeout, 256 KiB response, 32 KiB JSONL output, and at most 100 parsed raw results. Both responses normalize into provider-ordered title, URL, snippet, domain, and explicit backend fields after unsafe or duplicate URLs are filtered. Third-party results remain untrusted data.

Search uses the new `network-read` action: `read-only` and `workspace-write` deny it, while only `danger-full-access` proceeds under `ask | auto`. The low-level `WebSearchTool` can resolve one valid credential and use `COQUO_WEB_SEARCH_BACKEND` to disambiguate two, but ADR 0103 later requires every ordinary ProjectSession to start with independent sources disabled. Only explicit REPL `/search use brave|tavily` activates one. `/search status|sources` inspects state and `/search reset` restores the Provider-native default or disables every source. The first active source is the only currently executed primary; additional sources establish the future fan-out interface but are not requested or billed. Command configuration is process-local, does not write Session state, and invokes no provider. Ask displays the complete query, count, actual backend, and backend-specific quota disclosure. The query and a credential-free backend-configuration fingerprint participate in exact ActionIdentity, approval binding, and durable Action Audit, while routine live and `/actions` views redact query text. Credentials never enter model arguments, ActionIdentity, ToolResult, Session, or audit. Timeout or transport uncertainty returns `partial` and prohibits automatic retry.

The catalog now contains 22 ordinary tools and 30 definitions total. Canonical system prompt advances to v30, provider adapter contract advances to v32, and the empty full-context identity becomes `ctx-v5-468d2b764f1b20902080a07d4a00f027eb531ea5651cc90c74b681956bbc80b9`; ToolArguments v1, ActionIdentity v1, `ctx-v5`/`ctx-v6` representations, and all Session, Task, and Action Audit schemas remain unchanged, and old transcripts are not rewritten. Non-persistent ApprovalPreview advances to v2 to carry the selected backend; ActionPrecondition adds a credential-free configuration SHA-256 kind without changing ActionIdentity's version. Deterministic injected-transport tests cover both wire protocols, selection, policy, approval, audit, truncation, malformed responses, and uncertain failures without real network access or API quota. See [0102: Bounded Independent Web Search](./decisions/0102-bounded-independent-web-search.md).

## Provider-native Web Search

Provider preset, message protocol, and native-search adapter are now modeled separately. Profile schema advances to v5 and can select `auto`, `none`, one implemented adapter, or an imported `custom-manifest-v1`. The catalog now presets Anthropic, OpenAI, xAI, DashScope, OpenRouter, DeepSeek, Zhipu, Moonshot, Ark, Hunyuan, Qianfan, Ollama, and local routes. Anthropic, DashScope, and OpenRouter declare native search; OpenAI declares it only for model names containing `search-preview`; other presets and custom profiles default to unavailable. Custom profiles may select either OpenAI-compatible or Anthropic-messages protocol. Old Profiles need not be retained and may be rebuilt under v5, although the store keeps low-cost legacy-schema reading.

At Session startup, a route with declared native search activates `provider`; otherwise no source is active. Brave and Tavily always remain disabled even when credentials exist. `/search use provider|brave|tavily [...]` explicitly changes process-local ordered sources, `/search reset` restores the Provider default, and Provider/model switches also reset to the new route default. Only the first primary executes. Provider-native search is part of provider generation rather than a Coquo ToolUse: it consumes no ordinary tool request and enters no PermissionGate, Action lease, or Action Audit. Selecting an independent source disables Provider search and re-exposes Host `web_search`.

Fixed adapters project an Anthropic server tool, OpenAI `web_search_options`, DashScope `extra_body.enable_search`, and an OpenRouter server tool. Supported citations append as a bounded Markdown `Sources:` section in final assistant text and therefore persist through ordinary Session history. To avoid misclassifying vendor server-tool stream events as Host calls, native search currently uses a buffered provider invocation followed by one complete terminal text delta. A custom manifest allows only bounded `extra_body`, one non-function server tool, and a predefined citation format. It rejects protected request and credential-shaped fields, endpoints, headers, code, custom parsers, and oversized structures. The CLI reads it only while creating or replacing a Profile, stores canonical content, and does not retain the source path.

Canonical system prompt advances to v31, provider adapter contract advances to v33, Profile fingerprinting advances to v4, and the empty full-context identity becomes `ctx-v5-9ec8e77ded21f83ef65f66cb8c54d0e1c79e64d19bbfaa988e9a7d919b1d1e80`. ToolArguments, ActionIdentity, Session, Task, Action Audit, compaction, and provider-usage schemas remain unchanged. See [0103: Provider-native Web Search](./decisions/0103-provider-native-web-search.md).

## OpenAI Responses Protocol and Provider-owned History

Coquo now treats `openai_responses` as a first-class wire protocol beside Anthropic Messages and OpenAI Chat Completions. Built-in OpenAI routes use Responses. DeepSeek selects by model: `deepseek-v4-flash` uses Responses and declares official Provider-native `web_search`, while other DeepSeek models retain Chat Completions without inferred search. Custom Profiles may also select `openai-responses`. Existing V4 Flash Chat Profiles remain readable with their Chat semantics and do not silently gain native search.

The Responses adapter sends stateless full history with system policy separated into `instructions`, fixes `store=false`, and projects Host function tools beside optional Provider `web_search`. Host ToolUse and ToolResult become `function_call` and `function_call_output` with one matching `call_id`. Provider `reasoning` and `web_search_call` instead become bounded `ProviderOwnedItem` values carried by `ProviderResponseEnvelope`: AgentLoop persists and returns them unchanged on later turns but never sends them through Host dispatch, PermissionGate, tool budgets, or Action Audit. Unknown hosted tools, duplicate IDs, incomplete items, and malformed shapes fail closed.

Responses streaming treats the semantic terminal response object as authoritative, emits `response.output_text.delta`, and adds normalized citations at completion. Output-limit `response.incomplete` retains usage and partial-observation facts but commits no Turn. No-tools Session-title and compaction invocations may parse and discard their independent reasoning but reject Provider tool calls. New `turn_committed` records use schema v9 for Provider-owned items; v1-v8 remain readable without rewriting. Effective Context advances to `ctx-v7`/`ctx-v8`, with empty full-context ID `ctx-v7-a9178c934e67352a98ba3641b927acc250d800c1af8d9d1de1bfaa2f2028a6e7`. The provider adapter contract advances to v34 while canonical system prompt remains v31. See [0104: OpenAI Responses Protocol and Provider-owned History](./decisions/0104-openai-responses-protocol-and-provider-owned-history.md).

## Provider Search Resilience, Controls, and Observability

A real DeepSeek Responses invocation may finish as `response.completed` while retaining a `web_search_call` with `status=failed`, such as a Provider-owned `open_page` blocked by `SSRF_BLOCKED`. Compatible relays may return optional `annotations` as null, one object, or nested `url_citation`. Coquo now preserves a failed search call as a valid terminal Provider-owned fact while continuing to reject nonterminal states inside a completed Response. Citations accept those bounded shapes; one malformed or unsafe citation is discarded with a content-free warning instead of invalidating valid assistant text. Unknown hosted tools, duplicate IDs, malformed required content, and incomplete Responses remain fail closed.

Process-local Provider search adds `auto|required`, up to 20 canonical allowed domains, and optional `low|medium|high` context size. OpenAI Responses supports all three; Anthropic supports domains and OpenAI Chat search supports context size. Unsupported adapter/option combinations reject explicitly. `/search mode|domains|context` changes only the current runtime and resets with search reset or a Provider/model switch. Independent low-intensity `Provider search:` traces show lifecycle and terminal call, failure, action-type, source, accepted-citation, and discarded-citation counts. `/session preview` and `/session turns` derive the same body-free summary from existing v9 Provider-owned items without exposing queries, URLs, page bodies, or reasoning.

Ordered sources now mean a primary plus explicit model-mediated fallback. `/search use provider tavily` keeps Provider search primary while exposing Tavily-backed Host `web_search` as fallback. The model may request it only after the same history shows Provider failure or unavailable structured citations. The Host never guesses a query, calls a fallback automatically, runs fan-out, or bypasses `network-read`, PermissionGate, approval, Action Audit, quota disclosure, or ordinary budgets. Canonical system prompt advances to v32 and the adapter contract to v35. Effective Context remains `ctx-v7`/`ctx-v8`, while the current empty full-context ID changes to `ctx-v7-3ac4ba4e6ffa39c1184cfff6cc4200eb30607553fdf886451c0d967765ff0432`. Other durable schemas remain unchanged. See [0105: Provider Search Resilience, Controls, and Observability](./decisions/0105-provider-search-resilience-controls-and-observability.md).

## Bounded Fetch, Structured Read, and Controlled Transfer Tools

The ordinary surface adds `web_fetch`, `compare_files`, `git_blame`, `git_refs`, `json_query`, `checksum_file`, `archive_list`, `move_directory`, and `download_file`. Provider adapters no longer map Task tools through drift-prone catalog indexes; they select schemas by canonical name. Anthropic Messages, OpenAI Chat Completions, and OpenAI Responses continue to project the same provider-neutral definitions. The six local observation tools are all `workspace-read`: they respectively bound UTF-8 diff, current-HEAD blame, local refs, strict JSON Pointer, streaming SHA-256 up to 256 MiB, and ZIP/uncompressed-TAR metadata without arbitrary command execution or archive extraction.

`web_fetch` and `download_file` share a standard-library public-web GET transport. It permits only standard HTTP(S) ports, rejects credential-bearing URLs and any non-public or mixed DNS answer set, pins the connection to a validated IP while preserving Host/TLS hostname checks, and repeats validation on each redirect. It supports no proxy, cookie, authentication, body, custom header, compressed response, or JavaScript. `web_fetch` is `network-read`, with 20-second, 512 KiB body, and 64 KiB output bounds. `download_file` combines remote read and atomic workspace install into one `network-write`, with 30-second and 16 MiB bounds plus target-state checks before and after network I/O. `network-write` is denied under read-only and workspace-write and may ask or auto-continue only under danger-full-access.

`move_directory` uses Linux `renameat2(RENAME_NOREPLACE)` and permits only a same-filesystem move to a missing destination. It rejects symlinked parents, descendant destinations, replacement, stale state, and platforms without atomic no-replace support. The three new action classes use dedicated ApprovalPreview v3 kinds. Routine terminal summaries still hide URLs, queries, pointers, and bodies, while ask approval displays the exact URL/path required for authorization. Transport or durability uncertainty remains partial and forbids automatic retry. Canonical system prompt advances to v33, provider adapter contract to v36, and the empty full-context ID to `ctx-v7-d9d80c3188613943154a2c3f8df40062d52ff14fdb19b3b8628d557e81e13c95`. Effective Context remains `ctx-v7`/`ctx-v8`; all durable schemas and old transcript replay remain unchanged. See [0106: Bounded Fetch, Structured Read, and Controlled Transfer Tools](./decisions/0106-bounded-fetch-structured-read-and-controlled-transfer-tools.md).

## Unified Extension Contract and ToolSet Snapshots

Before MCP integration, every current model-visible tool moves into one immutable `ExtensionToolContract`. Each contract binds the exact provider-neutral schema, `builtin | mcp | extension` source and generation, `host-action | task-*` execution ownership, `direct | deferred` exposure, and allowed `PermissionAction` set, with a content identity covering every field. The generation-one `ToolRegistrySnapshot` becomes the shared source of truth for the catalog, terminal permission labels, and future extension sources. `/tools catalog` can inspect registry, contract, source generation, and exposure instead of relying on a duplicate display table.

Each ordinary Turn freezes an exact `ToolSetSnapshot` from one Registry generation during preparation. Provider count and create project the original definitions in that snapshot; a Provider response naming a tool outside it is rejected before dispatch and commits no Turn. Compaction rebase retains the same snapshot, and the ActionLease binds it through the new Effective Context identity. Before PermissionGate or execution, ProjectSession requires the contract to exist, belong to Host action execution, and permit the executor-derived permission classification. This consistency check does not replace workspace containment, stale-state validation, approval, sandboxing, audit, or tool-specific hard bounds.

Epoch zero accepts only `direct` contracts. The pure `promote()` boundary can add only canonically ordered `deferred` contracts from the same Registry snapshot to a later epoch, and only before an ActionLease is issued. Every current built-in remains direct; this slice adds no MCP transport, server lifecycle, credential handling, model-visible discovery tool, or automatic promotion. Provider-native search remains adapter-owned and Task coordination retains its dedicated dispatcher. Effective Context advances to `ctx-v9`/`ctx-v10`, with empty full-context ID `ctx-v9-6e8bb3a51d3138760bdb6e8ea9db1ab94927599529048ba7bee2d7e792fe2b0e`; the provider adapter contract advances to v37, the system prompt remains v33, other durable schemas remain unchanged, and old Sessions are not rewritten. See [0107: Unified Extension Contract and ToolSet Snapshots](./decisions/0107-unified-extension-contract-and-tool-set-snapshots.md).

## Confined stdio MCP Configuration and Read-only Probe

MCP configuration schema v1 accepts only local `stdio` and `confined-stdio` trust, split between XDG user and workspace project scopes; a same-name cross-scope collision is rejected. Server commands must be absolute POSIX executables, with hard bounds on arguments, workspace-relative cwd, and environment mappings. Configuration stores only `TARGET=SOURCE_ENV_NAME`, never values, and new servers default to disabled. Scope locks, revision CAS, symlink rejection, `0600` temporary files, and atomic replacement implement add/replace, enable/disable, and remove.

Each `mcp probe` starts one temporary process through `LinuxBubblewrapCommandSandbox(workspace_writable=False)`: host root and workspace are read-only, temp/home/config are private, sensitive HOME paths are masked, capabilities are dropped, and seccomp denies sockets. Only after verified sandbox activation does the Host send `initialize`, `notifications/initialized`, and bounded paginated `tools/list`. Success, protocol errors, timeouts, and cancellation all close stdin and reclaim the process by exit, process-group terminate, then kill; incomplete cleanup is a distinct error.

Stdio uses strict newline-delimited JSON-RPC and rejects duplicate keys, non-finite numbers, wrong IDs, server-to-client requests, unknown protocols, repeated cursors or tools, oversized or excessive messages/pages/tools, and overly deep or broad JSON. Server instructions, descriptions, schemas, annotations, JSON-RPC error bodies, and stderr contents are not rendered. The terminal shows only sanitized identity, capability/tool names, schema byte counts, pages, duration, stderr byte counts, and cleanup status. Standalone commands provide `mcp add|list|show|enable|disable|remove|probe`; the REPL provides only Host-side `/mcp list|status|show|probe`.

This stage has no `tools/call`, persistent server manager, HTTP/SSE/OAuth, resources/prompts/sampling, or import into Extension Contracts, Registry, ToolSet, Provider requests, PermissionGate, Action Audit, or Session history. The canonical system prompt stays v33, adapter contract stays v37, Effective Context stays `ctx-v9`/`ctx-v10`, and all existing durable schemas remain unchanged. See [0108: Confined stdio MCP Configuration and Inspection](./decisions/0108-confined-stdio-mcp-configuration-and-inspection.md).

## MCP Tool Normalization and Quarantine Catalog

Enabled confined stdio servers can now be normalized into a content-addressed `McpQuarantineCatalog` without entering the initial ToolSet. Every listed tool receives a qualified name of at most 64 characters derived from configured server, remote name, and a hash, and binds user/project scope, configuration revision, negotiated protocol, schema fingerprint, and stable disposition. Schema normalization accepts only an object root and a closed recursive keyword/type subset. Unsupported references, keywords, composition, required sets, or additional-properties forms remain rejected candidates with sanitized reason codes. A failed server probe remains a sanitized source issue without server error or stderr bodies.

An accepted candidate becomes an `ExtensionToolContract` with `source=mcp`, `execution=mcp-remote`, `exposure=deferred`, and no PermissionAction. Its source name binds scope, server, and protocol, while source generation binds configuration revision. The schema and bounded description enter the exact definition, but the description is explicitly marked as untrusted server data. MCP annotations and output schemas do not enter the contract, so `readOnlyHint` and similar hints cannot grant workspace-read or any other permission. Catalog order is canonical by qualified name and its identity covers complete accepted and rejected facts; the Session-local service caches only while the credential-free configuration identity is unchanged.

Standalone `mcp catalog` and Host-only `/mcp catalog` explicitly refresh and show only catalog ID, counts, qualified names, scope/server, revision, protocol, schema fingerprints, and reason codes. They hide descriptions, schemas, annotations, arguments, credentials, server errors, and stderr, invoke no Provider, and write no Session or Action Audit. The built-in source and Registry advance to generation 2 for Slice 4's fixed discovery contracts; a Registry combined with MCP uses generation 3. See [0109: MCP Tool Normalization and Quarantine Catalog](./decisions/0109-mcp-tool-normalization-and-quarantine-catalog.md).

## Progressive MCP Discovery and ToolSet Epoch Transition

The model initially sees two fixed direct contracts, `tool_search(query,max_results)` and `tool_promote(names)`, rather than every MCP schema. Search performs bounded case-insensitive literal-term matching only against MCP deferred contracts in the Registry frozen for the current Turn and returns at most eight candidates. Promotion accepts at most eight exact qualified names actually returned by an earlier same-Turn search. Each discovery call must be the only call in its assistant tool response, enters neither PermissionGate nor Action Audit, and cannot activate a candidate from annotations, guesses, or another extension source.

Promotion still uses canonical `ToolSetSnapshot.promote()` against the same Registry snapshot and remains idempotent for already visible names; a real addition creates the next epoch. For a ProjectSession that already holds an ActionLease, the Host revalidates Session, runtime generation, old context, old ToolSet, and current MCP Registry identity, retires the old lease, constructs an Effective Context bound to the new ToolSet ID, and issues a fresh non-recreatable lease. The next Provider count and create paths use the same new definitions. Old approvals and ActionIdentity values cannot cross epochs; stale configuration or Registry state rejects the transition without committing the candidate Turn.

`tools/call` remains unimplemented. A model request for a promoted MCP contract receives only an `mcp_execution_unavailable` ToolResult and never enters a built-in executor, PermissionGate, approval, Action Audit, or MCP process. The canonical system prompt advances to v34 and provider adapter contract to v38. Effective Context representations remain `ctx-v9`/`ctx-v10`, while the empty full-context ID becomes `ctx-v9-2f737163e792a16fbae49a629f54afc5cf43d49b75f1afe47b12ff5ed4e60d3e`; durable Session, Task, Action Audit, and related schemas remain unchanged. See [0110: Progressive MCP Discovery and ToolSet Epochs](./decisions/0110-progressive-mcp-discovery-and-toolset-epochs.md).

## Audited MCP Execution, Result Normalization, and Process Lifecycle

Promoted `mcp-remote` contracts now receive the Host-assigned `dangerous` PermissionAction; server `readOnlyHint`, `destructiveHint`, and other annotations remain untrusted. MCP calls require `danger-full-access` and pass through the existing PermissionGate under ask or auto. An ask preview shows only the exact qualified tool and redacts arguments. ActionIdentity binds the ToolSet and context through the current lease, while an expected-configuration precondition additionally binds the catalog candidate, server scope/revision, protocol, schema, and catalog ID. Arguments are validated against the frozen supported JSON Schema subset before Action Audit; `pattern`, references, and other unsupported keywords are quarantined instead of executing server regular expressions or schema code inside the Host; permission denial or approval rejection starts no reusable process.

`McpProcessManager` lazily starts confined stdio processes keyed by scope, server, configuration revision, protocol, and catalog ID, then repeats initialize, tools/list, and remote-name/schema-fingerprint verification before the first call. A healthy process accepts sequential calls only. The current process retains at most eight servers and each generation accepts at most 128 completed calls, with deterministic LRU retirement at capacity. Configuration/catalog change, process exit, protocol failure, cancellation, call limit, Session close, or live-schema mismatch retires the generation; status inspection reconciles current enabled configuration, catalog refresh retires generations from another catalog, and a cleanup failure remains manager-owned for another bounded cleanup attempt; no request is automatically retried after dispatch. `/mcp status` exposes only server, scope, revision, protocol, generation, completed-call count, alive state, and stderr byte metadata.

`tools/call` uses a 30-second timeout and the existing strict newline-delimited JSON-RPC boundary. Cancellation attempts `notifications/cancelled` and then reclaims the process. Results accept only a closed CallToolResult shape, at most 64 content blocks, and optional structured content. Text enters a total 64 KiB model result; image, audio, and blob payloads retain only base64-validated type, MIME metadata, and byte count; resource links and embedded resources receive bounded structural validation; `_meta` and annotations are discarded. Normal results succeed, `isError` and JSON-RPC errors are known failures, while timeout, cancellation, post-delivery transport/protocol errors, malformed results, truncation, or incomplete cleanup remain partial or outcome-uncertain. All outcomes use the existing Action Audit and ToolResult causality and forbid automatic retry.

The canonical system prompt advances to v35 with fingerprint `v35-8537a2ef36ba8aa29068cc93f9b09231c0ea4e51a534fdb473e591408a7b5dca`, and the empty full-context ID becomes `ctx-v9-8e257b8889c2794ab1deef575bf96a22a9394cdac71e54234cb769adeaafadc7`; Effective Context remains `ctx-v9`/`ctx-v10`. ApprovalPreview advances to v4. The Provider adapter contract remains v38 because wire projection and parsing do not change. Extension, Registry, ToolSet, ToolArguments, ActionIdentity, Session, Task, Action Audit, Profile, and compaction schemas remain unchanged, and old records are not rewritten. See [0111: Audited MCP Execution and Process Lifecycle](./decisions/0111-audited-mcp-execution-and-process-lifecycle.md).

## Bounded MCP Notifications and Exact Local Tool Policy

MCP JSON-RPC now strictly recognizes `notifications/progress`, `notifications/message`, and `notifications/tools/list_changed`, while unknown notifications continue to consume the same per-request limit. The Host retains only per-class counts and emits at most one content-free activity event per recognized class; progress messages, tokens, logging data, and other server content are never retained or rendered. A malformed recognized notification or flood after call delivery fails with outcome-uncertain semantics while preserving the content-free notification observation.

`tools/list_changed` never mutates the active Turn's frozen ToolSet and never retries the current call. After the call terminates, the Host retires that process generation and invalidates the quarantine-catalog cache. A later catalog-dependent operation probes again; changed schema or other identity causes the existing contract and lease checks to reject stale execution. Terminal tool details add only redacted counts and a catalog-invalidated fact. See [0112: Bounded MCP Notifications and Catalog Invalidation](./decisions/0112-bounded-mcp-notifications-and-catalog-invalidation.md).

An independent schema-v1 MCP tool-policy store has XDG user and workspace project scopes with scope locks, revision CAS, symlink rejection, mode `0600`, and atomic replacement. Each rule exactly binds qualified name, server scope/name/revision, remote name, protocol, input-schema fingerprint, action, and policy revision; only `workspace-read` and `dangerous` are accepted. A complete match is `applied`, absence is `default`, and any mismatch is `stale` with a `dangerous` fallback. Annotations, descriptions, notifications, and results cannot grant authority.

Policy identity enters catalog configuration identity; disposition, effective action, and revision enter candidate identity; and the effective action continues into the contract, Registry, ToolSet, Effective Context, ActionIdentity precondition, and process generation. `mcp policy list|show|set|clear` manages local rules. `set` must first resolve an exact candidate from a live accepted catalog and verify the caller-supplied schema fingerprint, so it cannot authorize guessed, rejected, or stale tools. The system prompt advances to v36 with fingerprint `v36-0ab649c44e73ce244ef761512272188dd4540f46ed5243bcd61c2bbf63d9815d`; the empty full-context ID becomes `ctx-v9-97c4e14f393e36bfc0f7b17f6715ca84a0dde30771a46fd81da434b08f538693`. Provider adapter remains v38 and durable schemas remain unchanged. See [0113: Fingerprint-bound Local MCP Tool Policy](./decisions/0113-fingerprint-bound-local-mcp-tool-policy.md).

## Remote MCP, OAuth, and Extended Capabilities

MCP configuration advances to v2 while continuing to read legacy-v1 stdio files. A `streamable-http` server uses a credential-free HTTPS endpoint and `remote-https` trust. The Host pins public DNS/IP, verifies the TLS hostname, rejects redirects and compression, bounds JSON/SSE responses, and validates `MCP-Session-Id` in memory. Static bearer values are read only by environment name. Every remote tool remains `dangerous` and continues through the quarantine catalog, frozen ToolSet, PermissionGate, approval, ActionIdentity, result normalization, and Action Audit. See [0114](./decisions/0114-streamable-http-and-remote-network-trust.md).

User-level `mcp-oauth.json` retains revision-bound OAuth 2.1 PKCE pending state and tokens using a private directory, mode `0600`, and atomic replacement. `mcp oauth begin|complete|status|logout` implements HTTPS metadata discovery, S256, a loopback redirect, state validation, code exchange, expiry, and one refresh. Token values never enter project configuration, terminal status, Session, Action Audit, or model data. See [0115](./decisions/0115-local-oauth-21-pkce-lifecycle.md).

`mcp resources list|read|subscribe|unsubscribe` provides bounded Resource reads and revisioned subscription restoration; binary content retains only validated byte metadata, and resource notifications retain counts only. `mcp prompts list|get` accepts bounded text messages and labels them as untrusted non-authoritative templates that never enter Effective Context automatically. Roots remain hidden unless one server explicitly enables the single workspace URI. See [0116](./decisions/0116-bounded-mcp-resources-prompts-subscriptions-and-roots.md).

Sampling and Elicitation use a separate `McpReverseRequestCoordinator` with reverse-request, nesting, message, token, schema, and output limits. Sampling requires both Host authorization and a no-tools sampling callback; Elicitation requires an explicit interaction callback. The normal runtime installs neither callback, so denial remains the secure default. See [0117](./decisions/0117-bounded-mcp-sampling-and-elicitation.md).

`mcp doctor` runs one redacted live conformance probe and reports transport, protocol, known/unknown capabilities, tool count, and cleanup without server prose, credentials, or session IDs. Failed initialization attempts remote-session cleanup, status distinguishes stdio from Streamable HTTP generations, and legacy HTTP/SSE remains unsupported. One SSE `data:` line may carry one complete bounded MCP message instead of being constrained by an unrelated 64 KiB line limit; the decoded message and complete HTTP response remain independently capped at 1 MiB, with event and JSON-structure bounds unchanged. Tool schemas accept a known Draft 7 `$schema` declaration only at the root; direct root string properties may also carry a bounded `x-mcp-header` routing hint. Raw metadata remains in the schema fingerprint but is removed before Provider projection, and only safe arguments become corresponding `MCP-Param-*` headers after live schema revalidation. Unknown dialects, nested or duplicate header hints, and other unsupported keywords remain quarantined. The system prompt is v37 with fingerprint `v37-d7ad600e357ae981d083683cbe35580475da88854a0edbe933ce4106bae11c66`; the empty full-context ID is `ctx-v9-febbf229c7b658d6fd2b4f31dc6129cfd7a91487e5f723ef6bf9aafa5969a7b4`; Provider adapter remains v38. See [0118](./decisions/0118-mcp-interoperability-and-production-hardening.md).

## MCP Approval and Read-only Operator Diagnostics

MCP ask approval now resolves the current configured transport from the prepared candidate's exact server scope and name. `stdio` describes only the confined local executable, read-only Host/workspace, private temporary paths, and socket denial. `streamable-http` instead states that arguments travel over HTTPS to a remote service outside the local command sandbox and that external side effects cannot be rolled back. Both forms continue to hide arguments, endpoints, headers, and credentials. ApprovalPreview advances to v5 with a required `stdio|streamable-http` transport field that prevents a false boundary description; it remains a non-durable Host UI representation.

Known MCP ToolResult failure codes now produce conservative `Next:` guidance for narrowing or paginating a request, refreshing the catalog and submitting a new Turn, checking redacted OAuth/server state, inspecting cleanup/runtime status, avoiding automatic replay after uncertain transport outcomes, or revising arguments from a bounded server error. `mcp catalog explain <reason-code>` explains only Host-owned quarantine codes from a closed static table and never displays server schema, prose, or errors.

`mcp policy stale` classifies rules as `stale` only when a successful current catalog proves the mismatch, and as `unresolved` when a source probe failed or the catalog is incomplete; exact active rules are omitted. `mcp policy prune --dry-run` expresses no mutation intent and changes no policy file. It prints existing `clear` commands with exact scope and `--if-revision` only for confirmed stale rules and explicitly excludes unresolved rules. The system prompt, Provider adapter, ToolSet, Effective Context, and every durable schema remain unchanged. See [0120](./decisions/0120-transport-aware-mcp-approval-and-policy-diagnostics.md).

## Early Session Naming and Terminal Rendering Safety

An unnamed first turn now runs the existing bounded no-tools title request after the first ordinary provider response completes and before any tool dispatch. Title calls remain inside the same 24-invocation turn budget. The candidate appears immediately as process-local TTY toolbar state, but only a successful final turn atomically persists it with conversation, source, fallback reason, and usage in `turn_committed`. Failure or cancellation restores the prior name, while commit still rechecks duplicates and uses a stable numbered fallback.

The real TTY reserves the physical final column and caps automatically selected content width at 100 display cells, so a wide terminal emits real newlines with continuation prefixes instead of relying on a narrower IDE or copied-text viewport to soft-wrap again. Startup runtime/Session blocks, assistant Markdown, plain text, Host traces, slash blocks, and approval use that width. The renderer refreshes it before each visible activity event and final response while retaining an unfinished streaming suffix. Startup details use secondary Host indentation, and the banner switches to a vertical layout on narrow screens. Approval is safely wrapped as ANSI-free text before the outer trace applies warning color, preventing literal `\x1b[...]` leakage. System prompt, adapter, tool contracts, Effective Context, and every durable schema remain unchanged. See [0119](./decisions/0119-early-session-title-and-terminal-rendering-safety.md).

## Bounded declarative Skills and ToolSet restriction

Skill v1 uses one strict `<name>/SKILL.md` package whose frontmatter accepts only `manifest-version`, `name`, `description`, and optional `allowed-tools`; exact bounded metadata and body share one stable fingerprint. The Host scans only the workspace-local `.coquo/skills`, project-shared `.agents/skills`, and XDG user `coquo/skills` roots, in that priority order, while retaining shadowed and invalid diagnostics. Symlinks, non-UTF-8, CRLF, unknown fields, YAML errors, read drift, and all size bounds fail closed. `skills list|show|doctor` are read-only inspections with no provider, Session, or Action Audit effects.

Each ordinary Turn pins one SkillInventorySnapshot whose identity enters Effective Context and ActionLease identity. The model first calls `skill_search` in an isolated response over frozen active metadata, then calls `skill_load` in another isolated response with the exact same-Turn name and fingerprint. The Host reloads inventory before returning the body and stale-rejects any change. A successful ToolResult contains complete bounded instructions without an absolute path; Skill guidance remains untrusted procedure, not system authority, permission, approval, tool implementation, or execution evidence.

Optional `allowed-tools` only intersects existing Host or MCP action tools: omission inherits, an empty list removes every ordinary action, and a nonempty list still cannot add or promote a tool; Task, lifecycle, and discovery controls remain. Loading creates a later immutable ToolSet epoch and replacement lease. Cross-Turn restriction is reconstructed only from a complete successful Host `skill_load` pair still retained in Effective Context; once compaction removes that pair, Skill text in a summary cannot reactivate it. The system prompt is v38, provider adapter is v39, and full/compacted Effective Context advances to v11/v12 with `skill_inventory_id` while legacy versions remain valid. See [0121](./decisions/0121-bounded-declarative-skills-and-toolset-restriction.md).

## Bounded Skill resources, composition budgets, and REPL observability

Skill inventory v2 indexes bounded package regular files outside `SKILL.md`: at most 64 resources, 128 directories, 64 KiB per file, 256 KiB total, and 256 characters per relative path. Directory enumeration and file reads use no-follow descriptors; symlinks, non-regular files, escape, overflow, and read drift fail closed. The index contains only relative path, byte count, strict-UTF-8 readability, and a path-and-content fingerprint. Binary resources may be indexed but cannot enter model context.

`skill_load` now returns complete instructions and the resource index. `skill_read_resource` requires the exact active Skill pair still retained in Effective Context and binds the Skill fingerprint, resource path, resource fingerprint, and Turn-pinned inventory identity. After reloading and verification, the Host returns only one complete bounded UTF-8 ToolResult; it does not execute resources, install dependencies, write Action Audit, or expand the ToolSet.

At most four distinct Skills may be active, at most four `skill_load` attempts are accepted per Turn, and active instruction bodies total at most 65536 bytes; a duplicate active name is rejected. Multiple Skills compose in successful load-pair causal order, intersect `allowed-tools` sequentially, and replay only from complete Host results retained in Effective Context. `/skills|active|list|show|doctor` provides current activation and catalog inspection without provider, Session mutation, or Action Audit. Inventory advances to `skills-v2`, full/compacted Effective Context to v13/v14 while strictly validating legacy v11/v12, built-in Registry to generation 4, system prompt to v39, and provider adapter to v40. See [0122](./decisions/0122-bounded-skill-resources-composition-and-observability.md).

## Skill authoring, local import, Task audit, and execution boundary

`/compact preview` now projects active Skills from both the current Effective Context and the exact retained verbatim turns, showing before/after state, names that will deactivate, and the post-compaction action tools. It does not call the provider, generate a summary, or mutate the Session. A Skill remains active only while its complete successful `skill_load` pair remains retained; summary prose cannot restore activation.

Standalone `skills init|check|search|conflicts` provides provider-free and Session-free template creation, canonical validation, deterministic metadata search, and source-shadow diagnostics; the REPL adds read-only `/skills search` and `/skills conflicts`. `skills import <local-directory>` copies only an explicit local directory and never uses the network, clones, or installs dependencies. The source first passes the same no-follow loader; destination and lock are exclusive creates, and the copied package is reloaded to detect source drift, target inode replacement, shadowing, and every fingerprint mismatch. Lock v1 lives in the scope's `skill-locks/` rather than its scanned `skills/` root and records only scope, name, manifest/resource fingerprints, and necessary resource metadata, never source paths, credentials, bodies, or timestamps. `skills lock show|verify` inspects it.

SessionStore can read-only project `skill_load` request identities, Host ledger outcomes, and safe source/fingerprint facts from strictly replayed committed Turns without returning instructions. `task skills <task-id>` selects only the exact `turn_record_sequence` values durably bound to Task Stages; ordinary Task inspection remains independent of Session health. The offline Eval advances to `host-baseline-v3`, whose Task execution Stage uses the ordinary Skill discovery and load path.

A package script remains only a resource: there is no `skill_run_script`, dynamic import, or implicit subprocess. Reading still uses `skill_read_resource`; execution requires an explicit existing `run_command` request and therefore remains under dangerous PermissionGate classification, approval, Action Audit, the Linux sandbox, timeout, cancellation, and output bounds. These changes are Host commands, read-only projections, or stronger local boundaries, so registry generation 4, system prompt v39, provider adapter v40, Skill inventory v2, Effective Context v13/v14, and all Session/Task schemas remain unchanged. See [0123](./decisions/0123-skill-authoring-import-audit-and-execution-boundary.md).

## Explicit Skill internalization and quarantined remote install

Ordinary Prompts add two isolated commit-coupled coordination tools. The model may call `skill_propose_create` with one complete bounded declaration only when the current user explicitly asks to preserve a workflow as a Skill; the Host writes an inactive candidate only after final assistant text and the Session Turn commit. The model may call `skill_accept_create` only after direct user approval of that exact candidate. The Host then recovers successful ToolUse/ToolResult/ledger causality from the committed Turn, rechecks owner Session, pending status, fingerprint, fixed scope, and non-read-only mode, and reuses `import_skill()` plus its lock. The system does not learn Skills automatically from incidental success, repetition, or experience.

Generated and downloaded candidates live under `.coquo/skill-candidates/v1/`, outside inventory scanning, with a private immutable package, closed metadata, and an append-only `created -> installed|rejected` event sequence. `skills fetch` and `/skills fetch` reuse PinnedWebGetTransport and accept only query-free public HTTPS raw `SKILL.md` or bounded ZIP content. Every redirect retains public-address validation. ZIP validation rejects traversal, duplicate or case-fold-colliding paths, multiple package roots, symlink/special/encrypted entries, and count, size, expanded-size, or compression-ratio overflow. Download never installs or activates; candidate list/show exposes complete bounded instructions and resources before explicit install or reject, and installation still creates an exact import lock.

Each prepared Turn still freezes its SkillInventorySnapshot. Installation cannot hot-mutate that Turn's ToolSet and becomes discoverable only in a later Turn. The Registry advances to generation 5, system prompt to v40, provider adapter to v41, and full/compacted Effective Context to v15/v16 while retaining legacy v13/v14 reads. Skill inventory remains v2, and Session, Task, Action Audit, and import-lock schemas do not change. See [0124](./decisions/0124-explicit-skill-authoring-and-quarantined-remote-install.md).

## Frozen Declarative Preauthorization Hooks

Hook schema v1 supports only the `before_action_authorization` event and the side-effect-free `continue|deny|require_ask|advisory` effects. Rules use bounded deterministic matching over exact Tool names, PermissionAction values, canonical workspace-relative path prefixes, and `builtin|mcp` sources; they accept no regex, shell, HTTP, model call, argument mutation, credential, or arbitrary expression. User and project configuration uses strict revisioned JSON, cross-scope unique IDs, disabled-by-default rules, revision CAS, and private atomic writes. Standalone `hooks ...` commands manage configuration, while `/hooks` commands inspect current state read-only.

Every Turn freezes the complete `HookSetSnapshot`, including disabled rules, and includes its `hooks-v1` identity in Effective Context and ActionLease validation; configuration changes during execution affect only a later Turn. Evaluation occurs after Tool hard preparation, PermissionAction classification, Extension Contract validation, and ActionIdentity construction, but before ActionCoordinator and Action Audit admission. `deny` returns a bounded model-visible error, `require_ask` can only tighten `auto` to `ask`, and `advisory` only appends to a normal ToolResult. No effect can turn a PermissionGate denial into an allow, promote MCP policy, or bypass sandboxing or Tool hard constraints. Damaged configuration fails closed before a provider call.

The system prompt advances to v41, provider adapter to v42, and full/compacted Effective Context to v17/v18 while retaining v15/v16 as legacy Skill-v3 representations. Hooks add no model-visible Tool schema, so the Registry remains generation 5; Session, Task, Action Audit, ToolSet, and Skill inventory schemas do not change. See [0125](./decisions/0125-frozen-declarative-preauthorization-hooks.md).

## Durable Hook Observation and Audit

Hook configuration and HookSetSnapshot advance to v2 while retaining strict v1 configuration reads. In addition to `before_action_authorization`, the runtime supports `after_action`, Turn committed/failed, and Task Stage started/committed/failed, blocked, and terminated events. Only preauthorization may use `deny|require_ask`; all other events permit only `continue|advisory`. Lifecycle events reject action matchers, while `after_action` may match a closed terminal outcome. Shell, HTTP, model, background, and other executable handlers remain unsupported.

Each evaluation creates one bounded content-free `HookAuditEntry` containing only the event, exact HookSet ID, subject, matched Hook IDs/effects, aggregate result, and safe action metadata. Hook messages, Tool arguments, file content, and credentials are excluded. Action and Turn-terminal evaluations are atomically persisted in `turn_committed` v10 or `turn_failed` v3. Task evaluations are persisted in `stage_started|committed|failed` v3 and `task_blocker_recorded|task_terminated` v2. Older records remain readable and cannot carry the new ledger. If a Turn fails after actions ran, the earlier action Hook evaluations are retained in the failed record.

Lifecycle advisories use typed transient terminal events and are not copied into the audit ledger. An after-action advisory may enter the normal ToolResult without changing the actual Tool or Action Audit outcome. Standalone `hooks evaluations [session]` and `hooks task <task-id>`, plus REPL `/hooks evaluations [count]` and `/hooks task <task-id> [count]`, perform only strict replay and bounded content-free projection without a Provider call or state mutation. The system prompt is v42, provider adapter v43, HookSet identity `hooks-v2`, and Effective Context v19/v20 with v17/v18 retained as legacy Hook-v1 representations; Registry remains generation 5. See [0126](./decisions/0126-durable-hook-observation-and-audit.md).

## Audited Pinned Local Hook Handlers

Hook configuration and HookSetSnapshot advance to v3 while strictly reading v1/v2 and writing only v3. A rule may optionally configure one fixed direct executable, up to 16 fixed arguments within a 6 KiB budget, a 1-30 second timeout, and the executable SHA-256; a handler rule itself must use `continue` with an empty message, while the runtime result supplies the effective effect. Workspace-relative executables reject symlinks and absolute paths resolve to the actual file; the target must be regular, executable, and no larger than 16 MiB. The Host appends one closed JSON event envelope containing no messages, Tool arguments, file content, credentials, or arbitrary result data to fixed argv and never performs shell parsing.

Every handler invocation is a synthetic dangerous Action named `hook_handler` that reuses the existing ActionLease, PermissionGate, ask/auto approval, Action Audit, cancellation, RunCommandTool, and Linux bubblewrap. Read-only and workspace-write deny handlers; enabling a Hook is not execution authorization. The execution phase rechecks the pinned fingerprint, and a stale file becomes a closed failed Action without starting a process. The sandbox keeps the Host root read-only, workspace writable, temp/home private, sockets denied, environment allowlisted, output bounded, timeout enforced, and process groups cleaned up; raw handler stdout/stderr enters neither Action Audit nor model history.

A successful handler accepts only one closed JSON stdout result whose effect is `continue|deny|require_ask|advisory`. Only preauthorization may return deny/require_ask; observation events accept continue/advisory only. Preauthorization preparation, permission, approval, execution, timeout, stale, or protocol failures fail closed, while after-action and lifecycle failures become advisories and cannot rewrite authoritative action, Turn, or Task outcomes. At most four handlers run per event and twelve per ordinary Turn; recursion, automatic retry, and rollback are prohibited. Turn and Task lifecycle handlers run only after the corresponding authoritative record commits, and their runtime facts enter Session Action Audit.

Standalone commands add `hooks fingerprint`, handler-aware `hooks add`, `hooks template local-handler`, strict workspace-local `hooks import`, readiness-aware `hooks doctor`, and `hooks runs [session]`; the REPL adds `/hooks runs [count]`. Import always creates disabled revision 1, and enablement requires the pinned fingerprint to match. Approval preview advances to v6, system prompt to v43, provider adapter to v44, HookSet identity to `hooks-v3`, and Effective Context to v21/v22 with v19/v20 retained as legacy Hook-v2; Registry and Session/Task record schemas remain unchanged. See [0127](./decisions/0127-audited-pinned-local-hook-handlers.md).

## B6: Writable Team Roles, Host-owned Linked Worktree, and Parent-only Integration

The role and isolated-worktree boundaries are conceptually informed by the
MewCode and Claw-Code study materials under `learning-submodules`; Coquo does
not copy their implementation or prompts and does not depend on those projects
at runtime.

B6.1–B6.4 separate the authority workspace from the Child execution root and
constrain writable Team members to two fixed contracts. `isolated-workspace-
writer-v1` receives bounded file write/edit capability; `isolated-coder-v1`
additionally receives `run_command` only when the parent has
`danger-full-access`, the existing Linux command sandbox is ready, and every
Host boundary remains satisfied. Neither role receives Team, Task, Child
delegation, network, MCP, Skill, or integration controls; parent capability is
the ceiling and Team approval cannot raise it. Each assignment uses a
Host-owned linked worktree under `.coquo/worktrees/<workspace-fingerprint>/`,
bound to the authority repository, base ref/HEAD, member, assignment, and
capability snapshot. The worktree is the execution root; the authority
workspace remains the only integration target. `.coquo/worktrees` is sensitive
local runtime state, not a security boundary.

When a Child reaches a reviewable terminal state, the Host performs one bounded
seal: it checks HEAD stability, containment, symlinks, special files, Git
metadata, gitlinks, size/count limits, and concurrent snapshot consistency, then
records manifest/patch summaries and SHA-256 digests in the worktree ledger.
Patch bytes live in a neighboring immutable `0600` local artifact and never in
Session prose, approval previews, or model context. Terminal text, handoffs,
and artifacts remain untrusted evidence. Tampering, source drift, target
conflict or dirtiness, non-ancestral targets, unknown process outcomes, and
unverifiable effects fail closed without automatic retry, reset, cleanup, or a
success claim.

B6.5 adds the parent-Session-only `team_worktree_integrate` Action. It is not a
Team-control shortcut and apply is not completion: the Host first uses the
PermissionGate and exact approval preview to bind a single-use Action identity,
then rechecks assignment ownership, sealed terminal evidence, source/target
ref and HEAD, ancestry, cleanliness, digests, active operations, and
`git apply --check` before executing fixed-argv `git apply`. A successful apply
leaves the authority workspace uncommitted; it never auto-merges, rebases,
commits, retries, or retires. A later integration requires the user to restore
the target to an explicitly clean state. Conflicts or failed checks do not
mutate the target. If execution begins but its effect cannot be proven, the
Host records `integration_unknown`, forbids retry, and requires inspection.

B6.6 migrates the model-visible contract atomically to Registry generation 9,
catalog 62, ordinary parent ToolSet 58, system prompt v48, Provider adapter
v48, and Effective Context `ctx-v27`/`ctx-v28`; older Team/Child/Context
versions remain replayable under the existing compatibility policy. CLI and
REPL expose member `--role` plus `team worktree
status|diff|recover|retire --confirm`, but never implicitly integrate, commit,
push, or delete a worktree. Team close does not retire worktrees; only an
explicit, reviewable Host retirement can remove an eligible worktree and its
artifact.

B6.7 verifies the boundaries with two isolated writable Children, artifact
tampering, source/target drift, conflicts, provision/seal/integration unknown
outcomes, cancellation with retained worktrees, Provider-free recovery, the
separation of apply from work completion, legacy replay, CLI/REPL behavior, and
fake smoke tests. The final offline release gate remains full pytest, Ruff
check/format, `uv lock --check`, and `git diff --check`; real Providers,
network, credentials, and API cost are outside that gate. See [0144](./decisions/0144-host-owned-linked-worktree-lifecycle.md) and [0145](./decisions/0145-authority-execution-scope-and-child-actions.md) for the complete rationale and status.

## Task–Child–Team Unified Orchestration Bridge

Task, Child Run, Team assignment, and Team schedule each remain authoritative
for their own append-only ledger. The Host-only `TaskOrchestrationService`
connects them by binding exact identities to a Task Stage, reusing the existing
ProjectSession and Team service execution paths, verifying terminal evidence,
and appending one normalized external Stage terminal. External Stages use the
record-local schema-v1 `stage_delegated`, `stage_external_committed`, and
`stage_external_failed` records; existing Task, Session, Child, Team, and
schedule transcripts are not rewritten, and ordinary foreground Stage terminals
remain distinct.

The bridge order is fixed: validate Task owner/workspace/objective/identity,
admit the external target, durably append delegation, reuse existing execution
and supervision, observe cross-ledger terminal state, verify evidence digests,
and append the Task terminal. A Child requires a published `ChildHandoff`; a
Team assignment requires `observe_terminal`; a schedule may have a lazy empty
roster, but before commit the bridge reads the final assignment roster from the
exact schedule ledger, aggregates every assignment handoff, and produces a
canonical digest. Cancellation, failure, interruption, missing handoff,
owner/workspace/identity mismatch, ledger inconsistency, unknown process
outcomes, and durability uncertainty fail closed and require recovery. The
bridge never retries, cleans an orphan, invents an ID, or claims success; repeat
observation does not append another Task terminal record.

Permission/approval, budget, and workspace boundaries propagate only downward
from the parent ceiling. They cannot be elevated by Team or Child admission and
do not bypass the PermissionGate, sandbox, timeout, output, edit-conflict,
causality, or audit boundaries. The bridge adds no model-visible tool and does
not change the Registry, system prompt, Provider adapter, or Effective Context
versions. Children still receive no Task, Team, recursive-delegation, MCP,
Skill, or integration controls. Team schedule resume re-acquires only the exact
nonterminal schedule lease, appends no second start record, and never rebuilds a
schedule from Task state. See [0146: Task–Child–Team Unified Orchestration Bridge](./decisions/0146-task-child-team-orchestration-bridge.md).

## Bounded Recursive Read-only Child and Host-owned Workflow Orchestration

This slice permits one conservative recursive capability: the Host may admit a
depth-one read-only Child, and only when the fixed `read-only-explorer-v1`
capability is explicitly enabled may that Child create at most one depth-two
Grandchild. The Grandchild cannot delegate again. The Host owns admission,
permission ceilings, budgets, cancellation, execution leases, durable Child
ledgers, handoff delivery, and recovery. Recursive Children receive no Task,
Team, Skill, Hook, MCP, write, shell, network, or integration controls.

The recursive Child has a distinct role-prompt contract and fingerprint. A
depth-one recursive Child receives the fixed read-only ToolSet plus the Host's
explicit Child controls; a depth-two read-only Grandchild receives no Child
controls. Depth-two delegation uses record-local schema v2 and records
`parent_child_run_id` and `root_child_run_id`; legacy depth-one records remain
replayable. Identity, capability, owner, prompt-fingerprint, and schema
mismatches fail closed.

The Host-owned workflow skeleton is Architect → Explorer → Executor → Reviewer
→ Integrator. It reuses exact identities and execution boundaries from Task and
the Task–Child–Team bridge without duplicating Provider, Child, Team workers, or
ledgers. The Host advances phases and persists bounded state; Explorer,
Executor, and Reviewer results are always `untrusted evidence`. Reviewer
`passed` enters integration, `rejected` enters rework, and `unknown` enters
`recovery-required`. Accept is an explicit Host decision only: the workflow
does not write files, merge, commit, push, invoke a Provider, or silently
create a Task, Child, or Team.

Each external Explorer/Executor stage now persists a bounded projection of the
exact stage Task ID, target, Child/Team/assignment/schedule identity, handoff
observation, status, and evidence digest; the external ledger remains
authoritative. Explorer uses the workflow root Task, while Executor receives a
separately derived stage Task so unknown external usage remains fail-closed and
the binding is visible as `Stage Task` in the CLI. Admission, execution,
observation, and recovery are explicit Host operations. The provider-free
`workflow start|show|advance|explore-start|execute-start|recover|review|integrate-preflight|integrate|accept|rework|recover-integration` commands show
the external IDs and `evidence: untrusted`, and reject provider/profile
selection. Recovery only re-observes the recorded identity: cancellation,
missing handoff, unknown process outcome, lease conflict, or uncertain
durability becomes recovery-required, with no retry, replacement, cleanup,
merge, commit, or push. The persistent Child worker still uses one workspace
lease and at most four concurrent threads with durable queue/execution leases,
heartbeats, terminal events, and orphan recovery; deterministic tests prove
two-Child parallelism, exclusive leases, cancellation recovery, and identity
preservation across reload. See [0148](./decisions/0148-bounded-recursive-child-and-host-workflow-orchestration.md)
and [0149](./decisions/0149-durable-workflow-stage-observation-and-recovery.md).

## ADR index

128. [0128: Coquo Product Identity Migration](./decisions/0128-coquo-product-identity-migration.md)
129. [0129: Shared Agent Runtime Assembly Boundary](./decisions/0129-shared-agent-runtime-assembly-boundary.md)
130. [0130: Durable Child Run Identity and State](./decisions/0130-durable-child-run-identity-and-state.md)
131. [0131: Child Admission and Detached Session Binding](./decisions/0131-child-admission-and-detached-session.md)
132. [0132: One-Shot Child Foreground Execution](./decisions/0132-child-foreground-execution.md)
133. [0133: Process-Local Child Run Supervision](./decisions/0133-child-process-local-supervision.md)
134. [0134: Child Cancellation, Bounded Wait, and Restart Recovery](./decisions/0134-child-cancellation-wait-and-restart-recovery.md)
135. [0135: Evidence-Backed Child Handoff and Parent Delivery](./decisions/0135-evidence-backed-child-handoff-and-parent-delivery.md)
136. [0136: Model Child Delegation Controls](./decisions/0136-model-child-delegation-controls.md)
137. [0137: Command Sandbox Capability Readiness](./decisions/0137-command-sandbox-capability-readiness.md)
138. [0138: Durable Team Identity and Member Registry](./decisions/0138-durable-team-identity-and-members.md)
139. [0139: Recoverable Team Member Child Assignments](./decisions/0139-recoverable-team-member-child-assignments.md)
140. [0140: Durable Team Mailbox and Assignment Delivery](./decisions/0140-durable-team-mailbox-and-assignment-delivery.md)
141. [0141: Durable Team Work Board and Manual Review](./decisions/0141-durable-team-work-board-and-manual-review.md)
142. [0142: Bounded Team Scheduler and Recovery](./decisions/0142-bounded-team-scheduler-and-recovery.md)
143. [0143: Parent-Owned Team Control Approval and Reply Evidence](./decisions/0143-model-visible-team-controls.md)
144. [0144: Host-Owned Linked Worktree Lifecycle and Bounded Change Sealing](./decisions/0144-host-owned-linked-worktree-lifecycle.md)
145. [0145: Authority/Execution Scope and Restricted Child Actions](./decisions/0145-authority-execution-scope-and-child-actions.md)
146. [0146: Task–Child–Team Unified Orchestration Bridge](./decisions/0146-task-child-team-orchestration-bridge.md)
147. [0147: Persistent Child Background Runtime](./decisions/0147-persistent-child-background-runtime.md)
147. [0147: Persistent Child Background Runtime](./decisions/0147-persistent-child-background-runtime.md)

## Durable Team Identity and Member Registry

B1 now provides workspace-bound durable Team and member identities without
mistaking identity for a thread, model context, or capability. Each Team has an
independent append-only transcript at
`.coquo/teams/<workspace-fingerprint>/<team-id>.jsonl`; its header binds an
immutable owner Session and its lifecycle is irreversible `open -> closed`.
Strict closed schema-v1 replay, contiguous sequences, workspace/fingerprint/
path checks, bounded records and directories, no-follow handling, atomic
creation, append+fsync, an exclusive writer, and durability-uncertainty
poisoning make corruption or partial writes fail closed.

Member records retain a fixed `read-only-investigator-v1` role and immutable
UUID/name identity. Names are casefold-unique across the Team's complete
history, with at most 64 members. The only transitions are
`active <-> disabled -> left`; disabling blocks future assignments only, while
`left` preserves historical identity and cannot rejoin. `coquo team ...` and
REPL `/team ...` are Host-only creation, listing, inspection, close, and member
lifecycle controls: they make no Provider call, create no Child, write no owner
Session turn, change no `latest` pointer, and alter no Effective Context. REPL
mutations require the current Session to be the immutable Team owner; standalone
commands address an exact Team without silently switching a Session.

B1 intentionally excluded assignments, handoffs, mailboxes, messaging, shared task
boards, scheduling, write permissions, recursive delegation, long-lived workers,
and model-visible Team tools. B2 now binds every assignment to a fresh Child Run
and detached Session. B3 builds on that ledger with an append-only owner/member
mailbox: before a new Team Child is admitted, the Host freezes at most eight inbox
messages and preallocates delivery and reply IDs; only an exact committed Child
Turn and published handoff can create one member reply and mark the inbox
delivered. B4 adds an append-only work-item board: dependencies point only
backward, Host assignment moves ready work through the existing Child saga,
terminal Child evidence enters review, and explicit Host completion or release
controls the work result. Team close requires terminal work, assignment, mailbox,
and reply-read gates. Reading never deletes evidence, failed or interrupted work
does not consume pending messages, and generic or already-admitted v1 Children
keep their old contract. B5 adds append-only schedule waves, schema-v3 assignment
provenance, one OS lifetime lease per Team, deterministic bounded local selection,
cancellation, and Provider-free abandoned-run recovery. Assignment submission uses
the persistent Child runtime by default; an explicitly injected supervisor retains
the process-local worker path. A schedule only moves Child outcomes into review and
never retries, auto-completes, or runs as a permanent daemon. B7/B8 add a dedicated `TEAM_CONTROL`
runtime path and eleven controls visible only to an ordinary parent Session. Rejection,
ownership, budget, and approval failures return bounded ToolResults, while an accepted
effect remains durable after a later parent-Turn failure. Registry generation 8,
system prompt v47, Provider contract v47, and Effective Context v25/v26 migrate
together with v23/v24 replay support; Children, Task Stages, and compact summaries
never receive Team controls. See [0138: Durable Team Identity and Member Registry](./decisions/0138-durable-team-identity-and-members.md),
[0139: Recoverable Team Member Child Assignments](./decisions/0139-recoverable-team-member-child-assignments.md),
[0140: Durable Team Mailbox and Assignment Delivery](./decisions/0140-durable-team-mailbox-and-assignment-delivery.md),
[0141: Durable Team Work Board and Manual Review](./decisions/0141-durable-team-work-board-and-manual-review.md),
[0142: Bounded Team Scheduler and Recovery](./decisions/0142-bounded-team-scheduler-and-recovery.md),
and [0143: Parent-Owned Team Control Approval and Reply Evidence](./decisions/0143-model-visible-team-controls.md).

## Shared Agent Runtime and Durable Child Run Foundation

`AgentRuntimeFactory -> AgentRuntime -> AgentLoop` is now the unified assembly
and one-turn orchestration path for parent Sessions. The runtime owns one loop
and one volatile turn state; permissions, Action Audit, Session/Task
persistence, hooks, titles, and compaction remain Host-owned by
`ProjectSession` through explicit callbacks. Resume, switch, new, and fork
install writer/runtime pairs together, with no worker threads or parallel
Provider use added.

Child Runs now have an independent workspace-bound JSONL ledger. The Host can
create, inspect, list, cancel, and prepare a bounded objective under an existing
Session. `child prepare` freezes a redacted read-only execution envelope,
creates a detached Child Session without changing `latest`, and derives
`ready`; it invokes no Provider and writes no parent Session record. Preparation
failures are bounded and durable, and exact partial creation states are safe to
retry.

An ordinary parent Turn now sees `child_spawn`, `child_status`, `child_wait`, and `child_cancel` at the fixed catalog tail. Each control must be the only call in its assistant response, but success does not force final text; the parent may continue useful work or observe another Child in a later response. A Turn may successfully spawn at most four Children, and waits reserve at most 30 seconds per request and 60 seconds cumulatively. Every Child retains the A3 fixed read-only tools, one Turn, and depth one, with no write, command, network, MCP, Skill/Task control, or recursive delegation.

Delegation approval is separate from the Action PermissionGate. Under `ask`, the Host displays the exact objective, redacted route/model, tools, budgets, process-local limitation, and additional Provider-cost warning before creation; `auto` removes only the interaction. The parent Session first persists a content-free `child_delegation_decided`; after acceptance, the Child ledger is atomically created with its header and `child_run_delegated` before normal admission. Rejection or cancellation creates no Child or detached Session. Status, wait, and cancel validate durable parent ownership each time; a terminal wait delivers the A7 handoff through a normal ToolResult, and a later parent Turn failure does not roll back real Child effects.

At the A7 Child slice, the model contract advanced to Registry generation 7,
system prompt v45, Provider adapter v46, and Effective Context v23/v24, while
v21/v22 remained strict legacy representations. B8.1 later migrated the current
ordinary-parent contract atomically to generation 8, system prompt v47, Provider
v47, and Effective Context v25/v26; older versions remain replayable. Child,
Task Stage, and compact-summary requests never receive parent Child or Team
controls. A deterministic fake path spawns two real Children in one parent Turn,
continues parent tool work, waits for and delivers both handoffs, and strictly
replays the parent and both detached Child Sessions. See [0136](./decisions/0136-model-child-delegation-controls.md).

`child run <id>` now acquires an independent execution lease for a `ready`
Child, reconstructs its redacted Provider route, and runs one read-only Turn
through the same `AgentRuntimeFactory -> AgentRuntime -> AgentLoop` path. A
durable Child Session Turn commit precedes `completed`; route, construction,
and execution failures derive bounded `failed` evidence. The detached Child
Session never updates `latest`, and the parent Session/runtime remain unchanged.

The REPL now exposes `/child start <id>` to submit a `ready` Child to a
workspace-bound durable queue. `child start` then attempts to launch the
restartable local `python -m coquo.background_worker` process; the queue keeps
at most 32 pending items, one worker process uses at most four Child execution
threads, and an idle worker exits after a bounded interval rather than becoming
an unlimited daemon. Each worker calls the same A4 executor without sharing the
parent writer, Provider manager, or runtime state. The parent can commit its own
prompt while Children run. Queue events are bounded `queued`, `claimed`,
`heartbeat`, `requeued`, and `terminal` observations; the Child ledger remains
the only authority for execution and terminal state. Queue, worker lease,
heartbeat, and worker-state durability failures are fail-closed, and worker
failures publish only bounded diagnostics.

Running cancellation is durably recorded before cooperative signaling; a blocked
Provider leaves the run `cancelling`. `wait` replays the durable Child ledger
without depending on the submitting process. A claimed `ready` Child is
requeued only after a fresh execution lease proves that no executor owns it;
claimed `running`/`cancelling` work can only be marked `interrupted` after a
recovery lease and is never automatically retried. `child status`,
`/child status`, and `child recover` expose worker identity, heartbeat, active
submissions, queue state, orphan candidates, and bounded diagnostics. Explicitly
injected process-local supervisors remain available for deterministic tests and
callers that intentionally need in-process execution; otherwise ProjectSession
and Team assignments use this persistent runtime by default. Legacy v1
sentinels cannot prove owner death and remain fail-closed for human
investigation. See [0147: Persistent Child Background Runtime](./decisions/0147-persistent-child-background-runtime.md).

Every terminal Child may now append one schema-v1 `child_run_handoff_published` record. A completed handoff uses `committed_turn()` and `turn_evidence()` to bind the exact Child Turn sequence, raw-record SHA-256, and assistant-text digest. Its body is bounded by both 32 KiB characters and UTF-8 bytes with an explicit truncation marker. Failed, cancelled, interrupted, and preparation-failed Children generate only a Host summary containing the stable outcome and result code; objective text, Provider errors, and tracebacks are not copied. An identical publication is idempotent, a different one conflicts, and reading an already-published completed handoff revalidates its Child Session evidence.

`child_handoff_delivered` is a content-free schema-v1 audit receipt in the parent Session containing only parent/Child identity, terminal sequence, handoff digest, source, and optional ToolUse ID. The Host durably commits the receipt before rendering the body; a standalone audit writer adds no `session_resumed` and does not update `latest`. Receipts enter neither history, Effective Context, usage, tool ledgers, compaction, export, nor fork, and workers never hold the parent writer. An uncertain Child or Session fsync poisons that writer and requires inspection instead of blind retry. This Host-only A7 capability changes no Tool Registry, system prompt, Provider contract, or Effective Context version; model delegation controls, messaging, and Teams remain later slices. See [ADR 0129](./decisions/0129-shared-agent-runtime-assembly-boundary.md),
[ADR 0130](./decisions/0130-durable-child-run-identity-and-state.md), and
[ADR 0131](./decisions/0131-child-admission-and-detached-session.md),
[ADR 0132](./decisions/0132-child-foreground-execution.md),
[ADR 0133](./decisions/0133-child-process-local-supervision.md),
[ADR 0134](./decisions/0134-child-cancellation-wait-and-restart-recovery.md), and
[ADR 0135](./decisions/0135-evidence-backed-child-handoff-and-parent-delivery.md).

1. [0001: Foundation 0 single-turn loop](./decisions/0001-foundation-0-single-turn-loop.md)
2. [0002: Foundation 0 deterministic REPL](./decisions/0002-foundation-0-deterministic-repl.md)
3. [0003: Foundation 1A in-memory text history](./decisions/0003-foundation-1a-in-memory-text-history.md)
4. [0004: Foundation 1B bounded read_file tool loop](./decisions/0004-foundation-1b-bounded-read-file-tool-loop.md)
5. [0005: Foundation 2A provider-neutral model routing](./decisions/0005-foundation-2a-provider-neutral-model-routing.md)
6. [0006: Foundation 2B adapter-owned compatibility policy](./decisions/0006-foundation-2b-adapter-owned-compatibility-policy.md)
7. [0007: Foundation 3A non-streaming Anthropic adapter](./decisions/0007-foundation-3a-anthropic-non-streaming-adapter.md)
8. [0008: Foundation 3B local multi-provider runtime](./decisions/0008-foundation-3b-local-multi-provider-runtime.md)
9. [0009: Foundation 3C named provider profiles and runtime manager](./decisions/0009-foundation-3c-named-provider-profiles-and-runtime-manager.md)
10. [0010: Foundation 3D stable profile identity and durable Sessions](./decisions/0010-foundation-3d-stable-profile-identity-and-durable-sessions.md)
11. [0011: decoupled REPL presentation and slash dispatch](./decisions/0011-decoupled-repl-presentation-and-slash-dispatch.md)
12. [0012: first canonical model system prompt](./decisions/0012-first-canonical-model-system-prompt.md)
13. [0013: provider-owned model context capability](./decisions/0013-provider-owned-model-context-capabilities.md)
14. [0014: target-specific request counting and per-invocation preflight](./decisions/0014-target-specific-request-counting-and-preflight.md)
15. [0015: target-aware runtime switch UX](./decisions/0015-target-aware-runtime-switch-ux.md)
16. [0016: provider-neutral Effective Context Snapshot](./decisions/0016-provider-neutral-effective-context-snapshot.md)
17. [0017: Controlled Compact Transaction](./decisions/0017-controlled-compact-transaction.md)
18. [0018: Target-aware Resume Prepare/Commit](./decisions/0018-target-aware-resume-prepare-commit.md)
19. [0019: Pre-turn Automatic Context Compaction](./decisions/0019-pre-turn-automatic-context-compaction.md)
20. [0020: Foundation 1C Bounded Workspace Glob](./decisions/0020-foundation-1c-bounded-workspace-glob.md)
21. [0021: Foundation 1D Bounded Literal Grep](./decisions/0021-foundation-1d-bounded-literal-grep.md)
22. [0022: Foundation 4A Permission Policy Contract](./decisions/0022-foundation-4a-permission-policy-contract.md)
23. [0023: Foundation 4A Exact Action Identity, Single-use Approval Grant, and Durable Action Audit](./decisions/0023-foundation-4a-exact-action-identity-and-durable-audit.md)
24. [0024: Foundation 4A Approval Coordination, Runtime Integration, and Controlled `write_file`](./decisions/0024-foundation-4a-approval-coordination-and-controlled-write.md)
25. [0025: Foundation 4A Action Audit Observability](./decisions/0025-foundation-4a-action-audit-observability.md)
26. [0026: Foundation 4B Exact Edit Preparation, Execution, and Authorization Composition](./decisions/0026-foundation-4b-exact-edit-preparation-execution-and-authorization.md)
27. [0027: Foundation 4B Model-visible Exact Edit Integration](./decisions/0027-foundation-4b-model-visible-exact-edit-integration.md)
28. [0028: Foundation 4C Controlled Command Contract and Side-effect-free Preparation](./decisions/0028-foundation-4c-controlled-command-contract-and-preparation.md)
29. [0029: Foundation 4C Bounded Command Execution and Process-group Cleanup](./decisions/0029-foundation-4c-bounded-command-execution-and-process-cleanup.md)
30. [0030: Foundation 4C Durable Model-visible Command Integration](./decisions/0030-foundation-4c-durable-model-visible-command-integration.md)
31. [0031: Foundation 4D Controlled Single-directory Creation](./decisions/0031-foundation-4d-controlled-single-directory-creation.md)
32. [0032: Foundation 4E Controlled No-overwrite File Move](./decisions/0032-foundation-4e-controlled-no-overwrite-file-move.md)
33. [0033: Foundation 4F Controlled Regular-file Deletion](./decisions/0033-foundation-4f-controlled-regular-file-deletion.md)
34. [0034: Foundation 4G Controlled Empty-directory Deletion](./decisions/0034-foundation-4g-controlled-empty-directory-deletion.md)
35. [0035: Foundation 1E Bounded One-level Directory Listing](./decisions/0035-foundation-1e-bounded-directory-listing.md)
36. [0036: Foundation 4H Controlled Bounded Regular-file Copy](./decisions/0036-foundation-4h-controlled-bounded-file-copy.md)
37. [0037: Tool Batch A Bounded Workspace Navigation](./decisions/0037-batch-a-bounded-workspace-navigation.md)
38. [0038: Tool Batch B Process-isolated Regex Grep](./decisions/0038-batch-b-process-isolated-regex-grep.md)
39. [0039: Tool Batch C Structured Exact Multi-edit Patch](./decisions/0039-batch-c-structured-exact-multi-edit-patch.md)
40. [0040: Shared Six-call Tool Budget](./decisions/0040-shared-six-call-tool-budget.md)
41. [0041: Live Redacted Tool Activity Events](./decisions/0041-live-redacted-tool-activity-events.md)
42. [0042: Provider-neutral Assistant Tool Text Representation](./decisions/0042-provider-neutral-assistant-tool-text-representation.md)
43. [0043: Provider Mixed-response Inbound Normalization](./decisions/0043-provider-mixed-response-inbound-normalization.md)
44. [0044: `turn_committed` v3 Assistant Tool Text Persistence](./decisions/0044-turn-committed-v3-assistant-tool-text-persistence.md)
45. [0045: Provider Mixed-response History Projection](./decisions/0045-provider-mixed-response-history-projection.md)
46. [0046: AgentLoop and Terminal Assistant Tool Text Integration](./decisions/0046-agent-loop-and-terminal-assistant-tool-text-integration.md)
47. [0047: Provider-neutral Synchronous Response Streaming](./decisions/0047-provider-neutral-synchronous-response-streaming.md)
48. [0048: OpenAI-compatible Chat Completions Streaming](./decisions/0048-openai-compatible-chat-completions-streaming.md)
49. [0049: Anthropic Messages Streaming](./decisions/0049-anthropic-messages-streaming.md)
50. [0050: AgentLoop, Runtime, and Terminal Streaming Integration](./decisions/0050-agentloop-runtime-and-terminal-streaming-integration.md)
51. [0051: TTY Markdown Rendering](./decisions/0051-tty-markdown-rendering.md)
52. [0052: Exact Bounded Informed Approval Previews](./decisions/0052-exact-bounded-informed-approval-previews.md)
53. [0053: TTY Prompt Editor and Interaction Feedback](./decisions/0053-tty-multiline-prompt-editor.md)
54. [0054: Sequential Tool-call Budget Hardening](./decisions/0054-sequential-tool-call-budget-hardening.md)
55. [0055: Bounded Multi-tool Response Batches](./decisions/0055-bounded-multi-tool-response-batches.md)
56. [0056: Structured Tool Outcome Ledger](./decisions/0056-structured-tool-outcome-ledger.md)
57. [0057: Durable Tool Ledger Inspection](./decisions/0057-durable-tool-ledger-inspection.md)
58. [0058: Runtime Context Meter and Provider Token Usage](./decisions/0058-runtime-context-meter-and-provider-token-usage.md)
59. [0059: Context and Compaction Observability](./decisions/0059-context-and-compaction-observability.md)
60. [0060: Provider Output-limit and Compaction Failure Diagnostics](./decisions/0060-provider-output-limit-and-compaction-failure-diagnostics.md)
61. [0061: Process-local Runtime Output Budget Control](./decisions/0061-process-local-runtime-output-budget-control.md)
62. [0062: Durable Session Provider Usage Audit](./decisions/0062-durable-session-provider-usage-audit.md)
63. [0063: Bounded Read-only Git Change Observation](./decisions/0063-bounded-read-only-git-change-observation.md)
64. [0064: Bounded Reachable Git History Observation](./decisions/0064-bounded-reachable-git-history-observation.md)
65. [0065: Opt-in Bounded Live Tool Details](./decisions/0065-opt-in-bounded-live-tool-details.md)
66. [0066: Trusted Command Result Observability](./decisions/0066-trusted-command-result-observability.md)
67. [0067: Persistent Inline Terminal Frontend](./decisions/0067-persistent-inline-terminal-frontend.md)
68. [0068: Terminal Message Hierarchy and Hanging Indent](./decisions/0068-terminal-message-hierarchy-and-hanging-indent.md)
69. [0069: Host Workbench Navigation and Failure Guidance](./decisions/0069-host-workbench-navigation-and-guidance.md)
70. [0070: Assistant Turn Execution Trace Grouping](./decisions/0070-assistant-turn-execution-trace-grouping.md)
71. [0071: Durable Session Naming and Terminal Identity](./decisions/0071-durable-session-naming-and-terminal-identity.md)
72. [0072: Session Archive, Search, and Title Fallback Diagnostics](./decisions/0072-session-archive-search-and-title-fallback-diagnostics.md)
73. [0073: Pinned Sessions and Snapshot-based Quick Switching](./decisions/0073-pinned-sessions-and-snapshot-quick-switching.md)
74. [0074: Read-only Session Inspection and Bounded Turn Preview](./decisions/0074-read-only-session-inspection-and-bounded-turn-preview.md)
75. [0075: Bounded Cross-Session Final-text Search](./decisions/0075-bounded-cross-session-final-text-search.md)
76. [0076: Bounded Session Turn-range Inspection](./decisions/0076-bounded-session-turn-range-inspection.md)
77. [0077: Bounded Conversation-only Session Export](./decisions/0077-bounded-conversation-export.md)
78. [0078: Provenance-linked Session Forking](./decisions/0078-provenance-linked-session-forking.md)
79. [0079: Explicit Session Diagnosis and Tail Repair](./decisions/0079-explicit-session-diagnosis-and-tail-repair.md)
80. [0080: Fail-closed Linux Command Sandbox](./decisions/0080-fail-closed-linux-command-sandbox.md)
81. [0081: Host Workbench Diagnostics and Prompt History Search](./decisions/0081-host-workbench-diagnostics-and-prompt-history-search.md)
82. [0082: Host Policy and Tool Discoverability](./decisions/0082-host-policy-and-tool-discoverability.md)
83. [0083: Foundation 5A Root AGENTS.md Project Instructions](./decisions/0083-foundation-5a-root-agents-project-instructions.md)
84. [0084: Deterministic Offline Host Eval Baseline](./decisions/0084-deterministic-offline-host-eval-baseline.md)
85. [0085: Actual Coding Task Eval](./decisions/0085-actual-coding-task-eval.md)
86. [0086: Durable Task Identity and Host Management](./decisions/0086-durable-task-identity-and-host-management.md)
87. [0087: Durable Stage Lifecycle and Turn Evidence](./decisions/0087-durable-stage-lifecycle-and-turn-evidence.md)
88. [0088: Foreground Task Stage Execution and Recovery](./decisions/0088-foreground-task-stage-execution-and-recovery.md)
89. [0089: Task Planning, Acceptance, Budgets, and Management](./decisions/0089-task-planning-acceptance-budgets-and-management.md)
90. [0090: Structured Task Acceptance and Independent Review](./decisions/0090-structured-task-acceptance-and-independent-review.md)
91. [0091: Resume Runtime Binding at the Durable Commit Point](./decisions/0091-resume-runtime-binding-at-the-durable-commit-point.md)
92. [0092: Adaptive Foreground Task Orchestration](./decisions/0092-adaptive-foreground-task-orchestration.md)
93. [0093: TTY Host Wrapping and Process-local Command History](./decisions/0093-tty-host-wrapping-and-process-local-command-history.md)
94. [0094: Task Proposal Control Boundary](./decisions/0094-task-proposal-control-boundary.md)
95. [0095: Model-visible Task Coordination Tools](./decisions/0095-model-visible-task-coordination-tools.md)
96. [0096: Model-proposed Task Admission](./decisions/0096-model-proposed-task-admission.md)
97. [0097: Informed Task Admission and Foreground Handoff](./decisions/0097-informed-task-admission-and-foreground-handoff.md)
98. [0098: Natural-language Task Lifecycle Handoffs](./decisions/0098-natural-language-task-lifecycle-handoffs.md)
99. [0099: Recoverable Provider Tool Argument Validation](./decisions/0099-recoverable-provider-tool-argument-validation.md)
100. [0100: Persistent Activity Indicator and Task Output Alignment](./decisions/0100-persistent-activity-indicator-and-task-output-alignment.md)
101. [0101: turn_committed v5 Inherited Assistant Content Replay](./decisions/0101-turn-committed-v5-inherited-assistant-content-replay.md)
102. [0102: Bounded Independent Web Search](./decisions/0102-bounded-independent-web-search.md)
103. [0103: Provider-native Web Search](./decisions/0103-provider-native-web-search.md)
104. [0104: OpenAI Responses Protocol and Provider-owned History](./decisions/0104-openai-responses-protocol-and-provider-owned-history.md)
105. [0105: Provider Search Resilience, Controls, and Observability](./decisions/0105-provider-search-resilience-controls-and-observability.md)
106. [0106: Bounded Fetch, Structured Read, and Controlled Transfer Tools](./decisions/0106-bounded-fetch-structured-read-and-controlled-transfer-tools.md)
107. [0107: Unified Extension Contract and ToolSet Snapshots](./decisions/0107-unified-extension-contract-and-tool-set-snapshots.md)
108. [0108: Confined stdio MCP Configuration and Inspection](./decisions/0108-confined-stdio-mcp-configuration-and-inspection.md)
109. [0109: MCP Tool Normalization and Quarantine Catalog](./decisions/0109-mcp-tool-normalization-and-quarantine-catalog.md)
110. [0110: Progressive MCP Discovery and ToolSet Epochs](./decisions/0110-progressive-mcp-discovery-and-toolset-epochs.md)
111. [0111: Audited MCP Execution and Process Lifecycle](./decisions/0111-audited-mcp-execution-and-process-lifecycle.md)
112. [0112: Bounded MCP Notifications and Catalog Invalidation](./decisions/0112-bounded-mcp-notifications-and-catalog-invalidation.md)
113. [0113: Fingerprint-bound Local MCP Tool Policy](./decisions/0113-fingerprint-bound-local-mcp-tool-policy.md)
114. [0114: Streamable HTTP and Remote Network Trust](./decisions/0114-streamable-http-and-remote-network-trust.md)
115. [0115: Local OAuth 2.1 PKCE Lifecycle](./decisions/0115-local-oauth-21-pkce-lifecycle.md)
116. [0116: Bounded MCP Resources, Prompts, Subscriptions, and Roots](./decisions/0116-bounded-mcp-resources-prompts-subscriptions-and-roots.md)
117. [0117: Bounded MCP Sampling and Elicitation](./decisions/0117-bounded-mcp-sampling-and-elicitation.md)
118. [0118: MCP Interoperability and Production Hardening](./decisions/0118-mcp-interoperability-and-production-hardening.md)
119. [0119: Early Session Title Preparation and Terminal Rendering Safety](./decisions/0119-early-session-title-and-terminal-rendering-safety.md)
120. [0120: Transport-aware MCP Approval and Policy Diagnostics](./decisions/0120-transport-aware-mcp-approval-and-policy-diagnostics.md)
121. [0121: Bounded Declarative Skills and ToolSet Restriction](./decisions/0121-bounded-declarative-skills-and-toolset-restriction.md)
122. [0122: Bounded Skill Resources, Composition, and Observability](./decisions/0122-bounded-skill-resources-composition-and-observability.md)
123. [0123: Skill Authoring, Local Import, Task Audit, and Execution Boundary](./decisions/0123-skill-authoring-import-audit-and-execution-boundary.md)
124. [0124: Explicit Skill Authoring and Quarantined Remote Install](./decisions/0124-explicit-skill-authoring-and-quarantined-remote-install.md)
125. [0125: Frozen Declarative Preauthorization Hooks](./decisions/0125-frozen-declarative-preauthorization-hooks.md)
126. [0126: Durable Hook Observation and Audit](./decisions/0126-durable-hook-observation-and-audit.md)
127. [0127: Audited Pinned Local Hook Handlers](./decisions/0127-audited-pinned-local-hook-handlers.md)
128. [0148: Bounded Recursive Read-only Child and Host-owned Workflow Orchestration](./decisions/0148-bounded-recursive-child-and-host-workflow-orchestration.md)
129. [0149: Durable Workflow Stage Observation and Recovery](./decisions/0149-durable-workflow-stage-observation-and-recovery.md)
130. [0150: Upstream Provider API Error Facts and Safe Display](./decisions/0150-upstream-provider-error-facts-and-safe-display.md)
131. [0151: Unified Read-only Observation Timeline](./decisions/0151-unified-read-only-observation-timeline.md)
132. [0152: Observation Stream, Diagnosis, and Retention](./decisions/0152-observation-stream-diagnosis-and-retention.md)
133. [0153: Provider Reasoning Effort Modes](./decisions/0153-provider-reasoning-effort-modes.md)
134. [0154: Child/Team Recovery Boundaries and Provider Effort Matrix](./decisions/0154-child-team-recovery-and-effort-matrix.md)
135. [0155: Live Provider Round and Tool Timeline](./decisions/0155-live-provider-round-and-tool-timeline.md)
136. [0156: Long-Term Memory Contract and Local Store](./decisions/0156-long-term-memory-contract-and-local-store.md)
137. [0157: Real Provider Acceptance and Stream Diagnostics](./decisions/0157-real-provider-acceptance-and-stream-diagnostics.md)
138. [0158: Live Observation Event Output](./decisions/0158-live-observation-event-output.md)
139. [0159: Background Effect Confidence and Terminal Idempotency](./decisions/0159-background-effect-confidence.md)
140. [0160: Bounded Memory Retrieval Index Cache](./decisions/0160-bounded-memory-retrieval-index-cache.md)
141. [0161: Bounded Provider Reliability and Workflow Driver](./decisions/0161-bounded-provider-reliability-and-workflow-driver.md)
142. [0162: Fixed Command Resource Limits](./decisions/0162-fixed-command-resource-limits.md)
143. [0163: Bounded Self-Evolution Controller](./decisions/0163-bounded-self-evolution-controller.md)
144. [0164: Automatic Workflow-to-Skill Evolution Pipeline](./decisions/0164-automatic-skill-evolution-pipeline.md)
145. [0165: Memory, Strategy, Eval, and Provider Stability Loop](./decisions/0165-memory-strategy-eval-provider-stability.md)
146. [0166: Host-gated Browser Actions in the AgentLoop](./decisions/0166-browser-action-agentloop-integration.md)

## Upstream Provider API Error Facts and Safe Display

The OpenAI-compatible (including Responses) and Anthropic adapters now retain a
bounded set of upstream facts in the shared `ProviderFailure`: HTTP status
codes (100–599), standard `error.type`, `error.code`, upstream message, request
ID, and a safely parsed numeric `Retry-After`. Coquo's own `kind`,
`diagnostic_code`, `retryable`, and Host message remain authoritative, so
upstream facts do not replace Host classification, retry, or stop semantics.
For example, a 3xx, 4xx, 429, or 5xx response still displays its actual status
even when the SDK maps it to a generic failure.

CLI Provider failure output renders these fields on separate lines so an
operator can distinguish authentication, authorization, request, rate-limit,
model, service, and transport failures. Adapter boundaries enforce bounded
length, printable characters, and valid status codes; non-JSON or unknown
bodies are not copied wholesale, and headers, raw bodies, credentials, and
tokens never enter the failure object, Session, or terminal. This slice only
improves fact retention and display; it does not add automatic retry, fallback,
waiting, resend, or telemetry. See [0150: Upstream Provider API Error Facts and
Safe Display](./decisions/0150-upstream-provider-error-facts-and-safe-display.md).

## Unified Read-only Observation Timeline

O1–O3 add a Host-only `ObservationEvent` projection contract and the
`observe timeline` command. Existing Session, Task, Child Run, and Team durable
ledgers are projected into one bounded text or JSONL timeline without creating
a second log, copying conversation/tool/message/handoff bodies, or migrating
old schemas. Events retain stable identities, sequence, timestamp, phase/status,
evidence level, parent event, and selected related IDs. Strictly replayed
Session/Task/Team facts are `host-verified`; Child lifecycle facts are
`host-observed`; Child handoffs are `untrusted`. Workspace-wide merging links
roots through existing Task admission, Stage delegation, Child parent
delegation, Team control, and assignment IDs; missing relations remain
unparented instead of being guessed from time or prose. Each in-process Agent
Turn carries volatile `trace_id`/`turn_id`/`session_id` metadata, and an
in-process derived context may retain that trace. Detached Child workers do not
receive an unpersisted parent trace across a restart; they establish their own
volatile Turn context and correlate through durable parent/session/tool/stage/
assignment identities. This does not change Session, Provider, system-prompt,
or model-visible tool contracts. After a restart, historical trace context is
reconstructed only from existing durable identities. See [0151: Unified
Read-only Observation Timeline](./decisions/0151-unified-read-only-observation-timeline.md).

## Observation Stream, Diagnosis, and Retention

O4–O9 extend the O1–O3 Host-only contract with a bounded process-local
`ObservationStream`. Existing PromptEvents are projected as content-free live
events carrying the current volatile trace/turn correlation; the original
terminal event sink remains independent, and stream failures cannot change
Agent causality. Provider lifecycle, context preflight, compaction, usage,
tool, permission, Task, Child, Team, and worker activity share the same event
shape. Background queue snapshots become bounded `background_*`
Host-observed events containing stable IDs, state, timestamps, and worker/lease
references; the Child ledger remains execution authority.

`observe timeline` now supports bounded trace, status, evidence, record-type,
and ISO-8601 time-window filters. `observe diagnose` reports missing parent
links, untrusted handoffs, failed/unknown outcomes, and stale background
claims, with manual recovery guidance only. It never retries, recovers,
approves, or mutates a ledger. Process-local events are retained by count and
optional age without deleting Session, Task, Child, Team, or queue records.
Prompts, model/tool/handoff bodies, headers, credentials, and tokens are not
retained, and no remote telemetry is introduced. See [0152: Observation
Stream, Diagnosis, and Retention](./decisions/0152-observation-stream-diagnosis-and-retention.md).

## Provider Reasoning Effort Modes

Runtime now exposes the broad Host-owned process-local reasoning union
`none|minimal|low|medium|high|xhigh|max`. It is separate from
`max_output_tokens`: effort selects a provider reasoning policy, while the
output budget limits visible response size. One-shot and interactive
invocations accept all seven levels; the REPL uses `/effort`,
`/effort <level>`, and `/effort reset`. Changes are only allowed between turns
and are recorded as redacted `runtime_changed` binding audit; no reasoning text
is persisted.

Profiles declare the native kind, supported native level names, explicit
Host-to-native mappings, and an optional default. OpenAI-compatible Chat
Completions and Responses send the mapped native effort field. Anthropic
Messages uses the string adaptive contract (`thinking` plus
`output_config.effort`); legacy numeric `budget_tokens` is unsupported. Missing
mappings fail closed and there is no implicit `max -> high` conversion. Legacy
bindings without the field replay as unset. Children inherit only redacted
route provenance and gain no additional permissions, tools, or budgets from a
higher effort mode; effort does not alter concurrency, Child count, or loop
limits. See [0153: Provider Reasoning Effort Modes](./decisions/0153-provider-reasoning-effort-modes.md).

## Child/Team Recovery Boundaries and Provider Effort Matrix

Concurrent Child replay now uses a bounded stable read snapshot, so a
transient append-size change is retried instead of being reported as transcript
corruption; persistent drift still fails closed. Supervisor recovery follows the
durable execution lease: a live worker is reported as `still_owned`, and only
after it releases the lease may recovery append `interrupted`. It never retries,
resumes, or executes a READY Child.

Task-to-Child and Task-to-Team observation converges only after terminal evidence
and an identity-matching handoff are available. Failed, cancelled, and
interrupted Children produce one idempotent failed Task stage; missing or
inconsistent handoffs remain recovery errors. The Provider effort regression
matrix covers every Host level across OpenAI Chat/Responses and Anthropic
adaptive string mappings; unmapped levels fail closed and numeric
`budget_tokens` remains unsupported. See [0154: Child/Team Recovery Boundaries
and Provider Effort Matrix](./decisions/0154-child-team-recovery-and-effort-matrix.md).

## Live Provider Round and Tool Timeline

One user prompt remains one durable Session Turn, while each model request
inside its Agent loop is now presented through paired Host-only
`ProviderInvocationStarted/Finished` events. The start precedes preflight or
Provider I/O; the finish retains only the invocation index, limit, `turn` or
`session-title` purpose, `final-text`, `tool-request`, `failed`, or `cancelled`
outcome, a bounded tool count, and bounded Host-measured elapsed time. It does
not retain model responses, reasoning, or raw tool input. The automatic
first-Turn Session-title request is also displayed at its real shared-budget
index, so round 1 no longer appears to jump inexplicably to round 3. Existing
tool lifecycle safe summaries and assistant deltas are reused.

The persistent TTY now shows the logical Turn start, each model round, tool
execution, streamed final text, and Provider elapsed wait. The dynamic wait
line is ephemeral, while each permanent completion line retains elapsed time
so copied scrollback distinguishes a slow Provider response from terminal
delivery delay. `Turn committed` appears only after the Session prompt returns
from its durable commit path.
When a Provider round remains open for more than five seconds, the TTY also
adds a low-frequency Host-only `still waiting` heartbeat every five seconds,
showing the current round and accumulated wait without exposing a Provider
response, request body, or tool arguments.
Lifecycle events remain FIFO and non-droppable, while assistant text deltas
are preserved as separate events for independent flushing. Successful non-TTY prompt and eval output keeps its
quiet stdout/stderr compatibility contract; the public NDJSON event format is
defined by the separate versioned 0158 design. This slice borrows Claw-Code's conceptual
separation of runs, assistant rounds, and tool results without copying its TUI,
prompts, wire format, or implementation. See [0155: Live Provider Round and
Tool Timeline](./decisions/0155-live-provider-round-and-tool-timeline.md).

## Long-Term Memory Contract and Local Store

New long-term-memory configuration writes use schema v3 while the reader keeps
the original v1 shape and transitional v2 shape compatible without rewriting
them. Legacy configurations resolve to `retrieval=text` and
`capture=explicit` when those fields are absent and advance only after an
explicit update. Corrupt or unreadable configuration fails before Provider
invocation instead of silently masquerading as disabled memory. Recall
queries only confirmed records, never touch candidate/stale matches, deduplicate
all derived-query results, and append at most one `recalled` event for each item
that actually enters a Prepared Turn. Event count is independent from record
count and is checked before append, so the store cannot write an event that
makes its own log exceed the replay bound.

Explicit `remember:` extraction still follows durable `turn_committed`, but it
now passes through the existing PermissionGate and durable Action Audit:
read-only denies it, `approval=ask` acceptance/rejection/cancellation remains
authoritative, and `write=auto` is not an approval bypass. Team memory
grant/revoke is also a Host Action exposed only through `/team memory
grant|revoke <team-id>`; resume restores a successful grant only
after the Team still exists and the Session is still its owner. Model
`memory_add` records trusted Session/turn provenance, while bounded update and
delete reasons remain in the append-only event log.

Consolidation validates every duplicate before its first append and reports a
truthful `partial` observation if lower-level I/O fails after an event becomes
durable. Automatic capacity eviction also emits a content-free observation.
Tests for Anthropic, OpenAI Chat Completions, and OpenAI Responses fix identical
count/create ordering: an optional compacted summary first, multiple
`[UNTRUSTED MEMORY EVIDENCE]` user-data items next, and current committed
history last.

The implementation exposes `retrieval=text|semantic`; `semantic` uses the
deterministic, local, feature-hashed `semantic-local-v1` strategy and does not
contact a model or network service. It is a bounded replaceable retrieval
backend, not a learned embedding model. Configuration also exposes
`capture=explicit|conservative`. Explicit capture accepts only `remember:`
markers, while conservative capture accepts a small allow-list of preference
and project-rule sentence forms. Both operate only after a successful
`turn_committed`; explicit `write=auto` may confirm its candidate, but an
implicit conservative candidate always remains `candidate` until a later human
confirmation. Arbitrary model output is never extracted. Deduplication,
consolidation, conflict enumeration, reinforcement, stale review, and capacity
eviction append events with bounded reasons, and confirmed conflicts are never
silently overwritten.

Access is controlled by a Host-owned `MemoryAccessContext`, not by model text.
The ordinary Host receives the current workspace scope; an active Host Task may
add its Host-derived Task scope; a Team scope appears only after an explicit
revocable Host grant. Child runtimes receive no read or write scopes and do not
extract memory; unresolved scope is fail-closed. Memory remains untrusted
evidence and cannot grant tools, permissions, approvals, or execution
authority. Model-visible memory tools are enabled only under the two explicit
Host switches and reuse PermissionGate, Action Audit, and untrusted ToolResult;
remote Providers remain a later backend option.

The first long-term semantic-memory slice keeps project instructions, Session
history, context compaction, and Task/Child/Team execution ledgers separate.
`MemoryRecord` uses explicit `user|workspace|task|team|child` scopes, a
candidate/confirmed/stale/deleted/evicted lifecycle, confidence, source
Session/turn provenance, and bounded timestamps. Confirmation requires an
explicit confirmation time; deletion and eviction are terminal. DeerFlow is the
fact-governance reference and Hermes is the provider-lifecycle reference; this
slice adds no remote backend and never extracts arbitrary model output. It
accepts only explicit markers or the opt-in conservative sentence allow-list,
always after a durable turn commit.

The local backend is an append-only `.coquo/memory/events.jsonl` event log with
an exclusive lock. Each event stores the complete current record; append and
fsync happen before the replay view changes. Unknown fields, malformed schema,
duplicate creation, post-terminal changes, oversized events, and path/symlink
violations are rejected. Host configuration is separate from Provider profiles:
`.coquo/memory/config.json` defaults to `enabled=false` and provides
`recall=off|on`, `write=off|propose|auto`, `tools`, and the fixed `local`
provider. With the master switch disabled, effective recall, write, and tool
exposure are all disabled. The first runtime slice now performs bounded
workspace recall during turn preparation: only confirmed records in the current
workspace scope are selected when both `enabled=true` and `recall=on` are
effective. The selected records are frozen in the Prepared Turn and sent as a
separate `[UNTRUSTED MEMORY EVIDENCE]` user-data block; they are not appended to
the Session transcript and cannot affect permissions or Action Audit. Isolated
Child runtimes receive an empty recall provider. `coquo memory
status|configure|enable|disable` and explicit
`add|list|show|search|confirm|update|stale|delete` commands manage the local
ledger only; they do not invoke a Provider, mutate a Session, or add
model-visible tools. Candidate extraction runs only after durable
`turn_committed`; Child/Team sharing is Host-authorized and fail-closed. Remote
Providers remain a later backend option. See [0156: Long-Term Memory Contract
and Local Store](./decisions/0156-long-term-memory-contract-and-local-store.md).

Semantic recall now reuses a bounded process-local feature index. Each cached
entry is bound to the memory ID, content, status, update timestamp, and scope
version; a changed or disappeared record is recomputed or removed. Cache state
is never durable, shared with Children, or treated as evidence, and retrieval
results expose content-free candidate and hit/miss diagnostics. The replayed
memory event log remains the only source of truth. See [0160: Bounded Memory
Retrieval Index Cache](./decisions/0160-bounded-memory-retrieval-index-cache.md).

## Bounded Provider Reliability and Workflow Driver

Provider reliability distinguishes one logical invocation from its physical
attempts: the default is one attempt, an explicit policy allows at most three,
and retry is permitted only for rate-limit, timeout, transport, or provider-
unavailable failures observed before any text delta. Backoff, `Retry-After`,
elapsed time, input/output token budgets, known-usage requirements, and
cancellation are checked before another attempt; visible text is never replayed.
Every physical attempt contributes usage or an explicit unknown fact to the
existing Host tracker. Lifecycle events retain only bounded attempt and stream
diagnostics and never request/response bodies, credentials, or headers.

The Workflow Driver composes the existing Task, Child, and Team ledgers on the
Host side. It may advance recorded Architect, Explorer, and Executor stages in
order, bounded by four stages and an elapsed-time limit, and returns an explicit
`review-ready`, `pending-stage`, `recovery-required`, `stage-failed`,
`stage-limit`, `elapsed-limit`, `cancelled`, or `blocked` stop reason. It never
automatically reviews, integrates, accepts, retries, commits, pushes, or creates
hidden Provider/tool work. Background mode stops at a durable pending stage;
failure and recovery states require a fresh observation of the authoritative
ledger. See [0161: Bounded Provider Reliability and Workflow Driver](./decisions/0161-bounded-provider-reliability-and-workflow-driver.md).

## Fixed Command Resource Limits

Before releasing the Linux sandbox activation gate and user argv,
`run_command` applies fixed `resource.prlimit` ceilings: CPU seconds equal the
requested timeout, address space is 2 GiB, individual file size is 256 MiB, and
open file descriptors are limited to 1024. These Host limits cannot be raised
by model arguments or approval mode and layer with bubblewrap/seccomp, workspace,
environment, output, timeout, and process-group cleanup boundaries. If any limit
cannot be installed, execution fails closed with
`command_resource_limits_unavailable` and `resource-limits-rejected`, explicitly
stating that the command was not started; there is no unsandboxed fallback. See
[0162: Fixed Command Resource Limits](./decisions/0162-fixed-command-resource-limits.md).

## Real Provider Acceptance and Stream Diagnostics

`scripts/real_provider_acceptance.py` is a manual-only acceptance harness. It
uses a temporary workspace by default and exercises a final response, a
bounded read-only tool call, and long-term-memory recall. A caller-provided
workspace must not already contain the exclusive fixture; the fixture is
created atomically and cleaned up afterward. The harness requires
`COQUO_REAL_PROVIDER_ACCEPT=1` plus independent network, credential, and cost
acknowledgements, refuses to start a Provider when any gate is missing, bounds
subprocess time and output, and never stores credential values or a report file.

`ProviderInvocationFinished` now carries optional Host-measured stream facts:
elapsed duration, text-delta count, time to first delta, and maximum inter-delta
gap. The TTY renders these facts only when deltas were observed. They contain
no response text or request data and do not affect retry, failure, or commit
semantics. A large first-delta time points to upstream latency; a large
inter-delta gap points to provider/transport buffering; a low-gap stream with
late terminal output points to the Host presentation path. Deterministic tests
cover the metrics and all acceptance safety gates; CI never invokes a real
endpoint. See [0157](./decisions/0157-real-provider-acceptance-and-stream-diagnostics.md).

## Live Observation Event Output

`ObservationStream` now exposes a process-local subscription boundary that
delivers each Host observation event in FIFO order. Subscriber failures are
isolated and cannot change Agent causality. `coquo --events ndjson prompt
"Inspect this workspace"` flushes the same bounded, content-free projection as
one JSON object per line to stderr, while stdout retains the final response.
Stream deltas expose only character and UTF-8 byte counts, never response text,
prompts, tool arguments, headers, credentials, reasoning, or tokens; the
default `--events none` keeps the existing output contract. This is a local
diagnostic stream, not a claim of provider-side or terminal exactly-once
rendering. See [0158](./decisions/0158-live-observation-event-output.md).

Interactive TTYs additionally use an `immediate_streaming` presentation path:
each received delta is terminal-escaped, written, and flushed without waiting
for a complete Markdown paragraph or fenced code block. Incomplete Markdown is
not reinterpreted in this path; the Agent loop still validates the complete
response against the received deltas. This removes Host-side Markdown buffering
from latency diagnosis, but cannot create chunks that the Provider, SDK, or
network has not delivered.

## Bounded Self-Evolution Controller

The Host-owned `EvolutionController` now provides a bounded self-evolution control plane, defaulting to `off` with `propose` and `supervised` candidate-generation modes. It writes bounded traces, grader facts, repeated patterns, Memory/Skill/Prompt/Workflow candidates, safety checks, independent validation/test metric comparisons, human approval, versioned activation, live observations, rollback, and archival to the separate `.coquo/evolution/events.jsonl` log; every candidate retains provenance and remains untrusted task data. `coquo evolution` performs only local audit and state transitions: it does not invoke a Provider, execute tools, or modify PermissionGate, sandbox, ToolSet, Child/Team, or AgentLoop policy. Only a safety-passed candidate that passes Eval and receives explicit approval can activate, and rollback restores the newest prior stable candidate when one exists. See [0163: Bounded Self-Evolution Controller](./decisions/0163-bounded-self-evolution-controller.md).

## Automatic Workflow-to-Skill Evolution Loop

On top of that control plane, `SkillEvolutionService` implements the bounded
nine-step Skill loop: successful committed Host workflow Trace, at least three
repeated successes, a bounded Evolution candidate, a declarative `SKILL.md`
package, quarantine under `.coquo/skill-candidates/v1/`, safety checks and
independent validation/test metric Eval, explicit human approval, discovery by
the next Turn's Skill inventory after Host installation, and bounded usage
observation, exact rollback, and archival. Candidates are never hot-loaded into
the frozen current Turn, and generic `skills install` cannot bypass the
Evolution approval gate.

The generated Skill is untrusted declarative guidance. Its `allowed-tools`
metadata only intersects the current ToolSet; it cannot grant writes, commands,
network, MCP, Child, Team, PermissionGate, sandbox, or AgentLoop authority.
This loop is not automatic evolution of Memory, the system prompt, or
high-privilege policy. Those targets require separate design and human release.
See [0164: Automatic Workflow-to-Skill Evolution Pipeline](./decisions/0164-automatic-skill-evolution-pipeline.md).

## Background Effect Confidence and Terminal Idempotency

Background queue items now carry `not-started`, `in-flight`, `confirmed`, or
`unknown` side-effect confidence. New records use queue schema v2; legacy v1
records remain replayable with an event-derived state. A normally observed Child
terminal record is `confirmed`; an orphaned RUNNING/CANCELLING execution is
recovered as `interrupted` with `unknown`, requiring human inspection of
possible external effects. Terminal writes are idempotent only when status and
confidence match; conflicting observations fail closed instead of overwriting
evidence. The queue remains an observation ledger, does not automatically retry,
and makes no exactly-once claim. See [0159](./decisions/0159-background-effect-confidence.md).

## Memory, Strategy, Eval, and Provider Stability Loop

`MemoryEvolutionService` now mines an untrusted experience candidate from at
least three repeated successful Memory traces and links it to the local Memory
record through an append-only `memory_link` event. The candidate remains in
quarantine until static safety checks, independent validation/test metrics, and
explicit human approval pass. Activation first makes the Evolution candidate
active and then confirms the Memory record; a failure attempts a rollback.
Usage observations retain only bounded counts and optional reinforcement facts;
rollback marks a confirmed record stale without changing the original Session
commit.

`StrategyEvolutionService` provides the same mining, evaluation, approval,
activation, observation, rollback, and archival lifecycle for Prompt and
Workflow strategy candidates. A committed Host Turn records independent
Workflow, Memory, and Prompt traces. Generated text remains untrusted
declarative data and cannot change system-prompt authority, ToolSet,
PermissionGate, sandbox, AgentLoop, Provider, or Child/Team policy. With
evolution disabled, traces are retained but no candidates are generated.

`EvalPlatform` adds a bounded versioned dataset registry, disjoint
validation/test splits, closed `EvalGrade` facts, durable run records, and
baseline/candidate comparison on top of the existing offline Host Eval. Runs
store only bounded check names, scores, statuses, and timestamps under
`.coquo/evals/runs.jsonl`; prompts, responses, paths, and credentials are not
stored. Missing cases, per-case regressions, or a lower suite pass rate or mean
score fail the regression gate. Inspect it with `coquo eval platform
datasets|run|compare`.

Long-running Provider acceptance now supports up to eight explicit soak
repetitions. `StreamSample` classifies observations as not streamed, first-delta
wait, excessive inter-delta gap, or healthy, reporting Host measurements without
guessing a vendor cause. The TTY event queue exposes enqueue, drain,
high-watermark, and blocked-put counts and emits one warning when presentation
backpressure is observed. This distinguishes missing upstream chunks, Provider
buffering, and Host queue delay. Existing cancellation, retry, no-replay-after-
delta, and atomic Turn commit boundaries remain unchanged. See [0165: Memory,
Strategy, Eval, and Provider Stability Loop](./decisions/0165-memory-strategy-eval-provider-stability.md).

## Host-gated Browser Actions in the AgentLoop

Browser automation now enters the normal Host-owned
`ProjectSession → AgentLoop → ActionCoordinator → Action Audit` path through an
explicitly injected `BrowserAutomation` runtime. Only a Session with that
runtime receives the closed `browser_action` contract; ordinary Sessions and
all Child Sessions keep it out of their ToolSet. Each request performs one
bounded action—`navigate`, `click`, `fill`, `extract_text`, or `screenshot`—and
uses action-specific catalog validation.

Browser actions are `network-read` Host Actions and continue through the
PermissionGate, approval, Hook, lease, cancellation, timeout, output-limit, and
audit boundaries. The Browser runtime enforces credential-free URLs, the origin
allowlist, selectors, step limits, and backend constraints. Page text,
screenshots, URLs, and backend claims return only as bounded ToolResult data
marked `evidence: "untrusted"`; they cannot grant permission, run commands,
access MCP or Skills, delegate work, or modify the workspace. A missing or
closed runtime and backend failures fail closed or are reported truthfully; no
automatic retry is performed.

This slice uses explicit Host/API injection, adds no CLI switch, and has no
implicit Playwright dependency. Session shutdown closes the injected runtime.
See [0166: Host-gated Browser Actions in the
AgentLoop](./decisions/0166-browser-action-agentloop-integration.md).
