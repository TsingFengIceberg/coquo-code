# Implemented Foundations and Design Evolution

> This document preserves the implementation narrative for Leonervis Code's completed learning slices. The README is intentionally limited to primary commands and usage entry points. The ADRs under [`docs/decisions/`](./decisions/) remain the authoritative records for each slice's rationale, boundaries, and verification evidence.
>
> [中文](./implemented-foundations.md) | English

## Contents

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
- [Process-local Runtime Output Budget Control](#process-local-runtime-output-budget-control)
- [Durable Session Provider Usage Audit](#durable-session-provider-usage-audit)
- [Bounded Read-only Git Change Observation](#bounded-read-only-git-change-observation)
- [Bounded Reachable Git History Observation](#bounded-reachable-git-history-observation)
- [Opt-in Bounded Live Tool Details](#opt-in-bounded-live-tool-details)
- [Trusted Command Result Observability](#trusted-command-result-observability)
- [Host Workbench Navigation and Failure Guidance](#host-workbench-navigation-and-failure-guidance)
- [Assistant Turn Execution Trace Grouping](#assistant-turn-execution-trace-grouping)
- [Durable Session Naming and Terminal Identity](#durable-session-naming-and-terminal-identity)
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
- [ADR index](#adr-index)

## Canonical model system prompt

Leonervis Code builds a provider-neutral `SystemPromptSnapshot` from `src/leonervis_code/system_prompt.py`. The snapshot contains an explicit version, normalized text, and a domain-separated SHA-256 fingerprint. It is built once at the beginning of each user turn and remains pinned across every provider/tool continuation in that turn:

```text
SystemPromptSnapshot + neutral conversation history
  -> Anthropic Messages: top-level system + messages
  -> OpenAI-compatible: one leading system role + messages
  -> Scripted fake: record the same request snapshot
```

The canonical model system prompt is now version 21. It permits brief companion text for a whole response batch and states that the Host completely validates up to eight ordered calls before sequential execution. One user turn admits at most 32 tool requests and 24 provider invocations, with the last invocation restricted to text. During forced text-only finalization, the model must use the `Host tool ledger:` counts in the last real Tool result: `unused_admission_slots` is only unused capacity, while `tool_requests_closed=true` means no further call is possible even when a slot remains. The ordinary Agent still cannot initiate compaction. The current 21 model-visible tools include bounded `git_status`, `git_diff`, `git_log`, and `git_show`; PermissionGate, approval, Action Audit, and every per-tool hard bound remain Host-enforced, and a multi-call response never grants parallel execution.

It explicitly does not claim recursive copying/deletion, ignore-aware or indexed search, fuzzy/free-form patching, non-empty directory deletion, directory movement, recursive mkdir, shell source strings, interactive PTYs, OS/network sandboxing, compaction initiation, project-instruction loading, or multi-agent capabilities. Prompt instructions also do not replace the Host's hard workspace, symlink, encoding, size, exact-state conflict, timeout/process cleanup, causality, audit, and durability constraints.

The system prompt is not a `ConversationItem`, so `/history`, `ProjectSession.history`, and append-only Session JSONL contain only real user/assistant/tool causal chains. A new turn after resume uses the current binary's canonical prompt; schema-v2/v3 compact checkpoints store only compact-prompt, summary-framing, and trigger provenance without inserting the normal system prompt into conversation history.

The **model system prompt** and the terminal's `›` input marker plus `model · workspace` status line are different interfaces: the former is a model-visible contract, while the latter is only human-facing interaction and status presentation.

See [0012: first canonical model system prompt](./decisions/0012-first-canonical-model-system-prompt.md) for the detailed decision and [references/claw-code-prompts](./references/claw-code-prompts/README.md) for the Claw-Code prompt-structure study map.

## Foundation 3D: stable profile identity and durable Sessions

Profile-registry schema v3 uses an immutable UUID as reference identity, while each name remains a readable, mutable alias and each revision supports update-conflict checks. Schema v3 also adds an optional exact-model `context_window_tokens` override.

Legacy schema-v1 profiles deterministically map their original names to UUIDs. The reader accepts mixed v1, v2, and v3 user/project files, and a write upgrades only the file it actually changes:

```bash
uv run leonervis-code provider show vendor
uv run leonervis-code provider list --show-ids
uv run leonervis-code provider rename vendor vendor-new --if-revision 1
uv run leonervis-code provider replace vendor-new \
  --provider custom \
  --model vendor/model-v2 \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --if-revision 2
uv run leonervis-code provider migrate
```

Every `prompt` or REPL invocation creates or opens:

```text
<workspace>/.leonervis-code/sessions/<workspace-fingerprint>/<session-id>.jsonl
```

A Session uses append-only JSONL. A successful turn's user message, tool-use/result pairs, and final assistant text are written and fsynced as one complete commit record before in-memory history changes. Each open Session holds an exclusive writer lock.

Corrupt middle records, unknown schemas, and invalid tool pairing fail closed. Only an incomplete, unterminated crash tail can be truncated under controlled recovery, which also appends a recovery record.

```bash
uv run leonervis-code prompt "First turn"
uv run leonervis-code session list
uv run leonervis-code session show latest
uv run leonervis-code --resume latest prompt "Continue the previous turn"
uv run leonervis-code -C ../another-workspace --resume latest
```

A bare launch creates a new Session, while `--resume latest` continues the workspace's latest pointer. Inside the REPL, `/session new` starts empty history without changing the current runtime provider, and `/resume <id>` switches to existing history. `[current]` marks the destination of the next REPL prompt, `[latest]` marks the current `latest.json` target, and `open/closed` describes transcript lifecycle rather than lock ownership; a closed Session remains resumable.

Sessions and runtime providers are decoupled. The transcript records the profile ID/revision, provider/protocol, model, endpoint, and non-secret fingerprints actually used for each historical turn solely as audit provenance. After resume, the working provider still comes from this invocation's `--profile`/`--model`, workspace active selection, user active selection, or fake fallback. The runtime never reconstructs a client from historical binding metadata, and later profile rename, replacement, or deletion does not block resume.

Sending old history to a newly selected provider is an explicit runtime choice. If the current adapter rejects that history, the failed turn is not committed.

A local Session can contain user input, model responses, source excerpts, and tool results, so `.leonervis-code/` is sensitive runtime state and should not be committed, synchronized, or published. Known configured credential values are never written as binding data, but the system cannot generally detect an unknown secret that appears in user text or a file read by a tool.

`ProjectSession` exposes `session_id`, `transcript_path`, `session_info()`, `list_sessions()`, `new_session()`, `switch_session()`, and `resume=`. Switching Sessions replaces only durable history and preserves the current provider client.

See [0010: stable profile identity and durable Sessions](./decisions/0010-foundation-3d-stable-profile-identity-and-durable-sessions.md) for the detailed decision.

## Foundation 3C: named provider profiles and a real multi-turn REPL

Profile definitions live at:

```text
${XDG_CONFIG_HOME:-~/.config}/leonervis-code/providers.json
```

A workspace stores only its active profile ID in `.leonervis-code/provider.json`. Neither JSON file stores key values. The workspace directory is local runtime state and should be added to the target project's `.gitignore`.

```bash
# Built-in provider: protocol, default endpoint, and credential env come from the catalog
uv run leonervis-code provider add work-openai \
  --provider openai \
  --model gpt-5

# Controlled custom OpenAI-compatible endpoint; store only the key's env-variable name
uv run leonervis-code provider add local-qwen \
  --provider custom \
  --model Qwen/Qwen3.5 \
  --protocol openai-compatible \
  --base-url http://127.0.0.1:11434

uv run leonervis-code provider add vendor \
  --provider custom \
  --model vendor/model \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY

uv run leonervis-code provider list
uv run leonervis-code provider show vendor
uv run leonervis-code provider use local-qwen
uv run leonervis-code provider use work-openai --scope user
uv run leonervis-code provider clear --scope project
uv run leonervis-code provider remove vendor
```

Selection precedence is explicit `--profile` → explicit direct `--model` → workspace active → user active → fake/offline. `--profile NAME --model MODEL` uses a process-local model override on that profile endpoint without rewriting the profile:

```bash
uv run leonervis-code --profile work-openai --model gpt-5-mini \
  prompt "Explain this workspace"
uv run leonervis-code --profile work-openai
```

Both `provider use` and REPL `/provider use` resolve the route, validate the credential, and construct a candidate SDK client before writing active configuration and swapping the current client. On failure, the old active selection and client remain intact. `/model` is likewise atomic and allowed only between turns.

Complete neutral history and tool-use/result pairs survive a provider switch. If the new provider rejects old history, the failed turn is not committed.

Other project modules can use the public facade directly:

```python
from pathlib import Path
from leonervis_code import ProjectSession

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
uv run leonervis-code --model anthropic/claude-opus-4-8 \
  prompt "Explain this workspace"

export OPENAI_API_KEY='...'
uv run leonervis-code --model openai/gpt-5 \
  prompt "Explain this workspace"

export XAI_API_KEY='...'
uv run leonervis-code --model xai/grok-3 \
  prompt "Explain this workspace"

export DASHSCOPE_API_KEY='...'
uv run leonervis-code --model dashscope/qwen-plus \
  prompt "Explain this workspace"

uv run leonervis-code --model ollama/qwen3:8b \
  prompt "Explain this workspace"

export OPENROUTER_API_KEY='...'
uv run leonervis-code --model openrouter/anthropic/claude-opus-4-8 \
  prompt "Explain this workspace"
```

The Anthropic path uses the official `anthropic` SDK. Every other built-in route reuses the official `openai` SDK through the Chat Completions wire adapter. Both clients are synchronous, non-streaming, and configured with `max_retries=0`.

Adapters currently declare the ordered `read_file(path)`, `glob(pattern)`, and `grep(query, include)` schemas. The three local tools jointly enforce workspace, UTF-8, files-only no-symlink, and bounded output/read constraints while sharing the per-turn budget.

A one-shot controlled OpenAI-compatible endpoint can also be supplied without persisting a provider or key:

```bash
export VENDOR_API_KEY='...'
uv run leonervis-code \
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
uv run leonervis-code --model openai/gpt-5 route
```

The fake fallback remains unchanged. If a workspace/user active profile exists, `prompt` and the bare REPL use that real profile even without an explicit selector:

```bash
uv run leonervis-code provider clear --scope project
uv run leonervis-code provider clear --scope user
uv run leonervis-code prompt "Hello"   # fake with no active profile; no network
uv run leonervis-code                   # fake REPL with no active profile; no network
```

See [0007: non-streaming Anthropic adapter](./decisions/0007-foundation-3a-anthropic-non-streaming-adapter.md) and [0008: local multi-provider runtime](./decisions/0008-foundation-3b-local-multi-provider-runtime.md) for the detailed decisions. Run live smoke checks only when the user explicitly chooses their own credentials, endpoints, and API budget.

## Foundation 2B: offline adapter-owned compatibility policy

`route` is a deterministic diagnostic surface for the control-plane and adapter-policy boundary:

```bash
uv run leonervis-code route

uv run leonervis-code route \
  --model beta \
  --max-output-tokens 32 \
  --fallback-model default

uv run leonervis-code route \
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

The Host executor now passes prepared argv directly to `subprocess.Popen`, fixes `shell=False` and `stdin=DEVNULL`, and creates a separate process session/group for every command. Leonervis does not parse pipes, redirects, wildcards, variable expansion, or command substitution. An approved executable may itself be a shell and interpret its arguments, so direct argv is still not a sandbox. The executor rechecks every workspace-root/cwd component immediately next to the spawn boundary and returns `command_cwd_invalid` without starting a process if that boundary has become invalid. Ordinary path APIs cannot eliminate the remaining local TOCTOU window completely, so this is not a hostile-concurrency guarantee.

Commands inherit only a closed Host environment allowlist, with `PWD` replaced by the actual cwd; provider API keys and arbitrary project variables are not forwarded automatically. Independent readers continuously drain stdout and stderr to EOF, retain only the first 32 KiB of each, and record captured/total byte counts plus truncation. Valid UTF-8 returns as text; other bytes return as base64, avoiding locale-dependent decoding and pipe-buffer deadlocks.

Timeout or `KeyboardInterrupt` triggers bounded TERM-to-KILL process-group cleanup. The same path cleans a lingering group when the main process exits normally but a background child still holds the pipes, preventing the normal-return path from waiting indefinitely. Success, nonzero exit, invalid cwd, missing executable, signal, timeout, cancellation, and incomplete cleanup have stable JSON status/result codes. Timeout, cancellation, signal, or any cleanup uncertainty is partial even when the main process returned nonzero, because the process may already have produced irreversible side effects; the system neither retries automatically nor claims filesystem, network, credential, or resource isolation.

The executor still owns no permission, Session, or CLI behavior. See [0029: Foundation 4C Bounded Command Execution and Process-group Cleanup](./decisions/0029-foundation-4c-bounded-command-execution-and-process-cleanup.md).

## Foundation 4C Slices 7–9: Durable Model-visible Command Integration

`run_command` is now connected to ProjectSession and ActionCoordinator. The exact request is prepared and fixed as `dangerous`, then follows `action_requested → permission_decided → optional approval_resolved → revalidate/grant consume → action_execution_started → spawn/execute → action_execution_finished`. `Popen` is allowed only after `action_execution_started` append+fsync succeeds. Any earlier audit, approval, lease, or revalidation failure prevents spawn; if finish persistence fails after spawn, the turn stays uncommitted and replay truthfully derives `outcome-unknown` from started-without-finish.

The REPL's `approval=ask` shows argv, relative cwd, and timeout; one-shot ask still cancels safely without reading stdin. `session actions` and `/actions` show only the executable, extra-argument count, cwd, timeout, permission/approval, and lifecycle/result code, not full argv in the ordinary audit summary. The Session still stores exact ActionIdentity to validate the request, workspace fingerprint, prepared-turn lease, runtime generation, and Effective Context binding.

The canonical tool order is now `read_file, glob, grep, write_file, edit_file, run_command`, and all six still share at most three sequential executions per user turn. Anthropic and OpenAI-compatible ordinary count/create requests project the same sixth closed schema, compact-summary requests remain tool-free, and parallel calls remain disabled. The provider adapter contract advances to v8; the canonical system prompt advances to v7; the empty full-context golden becomes `ctx-v1-e6b5274ea57642fd614842c58dfa74def0b6f0c1319b2c312b7c54d61b834ce3`.

ToolArguments remains v1, new `turn_committed` remains schema v2, ActionIdentity and Action Audit remain v1, ordinary Session records remain v1, `context_compacted` continues v2/v3 replay, and Effective Context representations remain `ctx-v1`/`ctx-v2`. Old transcripts/checkpoints are not rewritten, and resume or compaction never reruns a command. See [0030: Foundation 4C Durable Model-visible Command Integration](./decisions/0030-foundation-4c-durable-model-visible-command-integration.md).

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

Leonervis can now accurately represent "assistant text plus one tool call" internally. The existing immutable `ToolUse` has an optional `assistant_text` that atomically binds exact text to the same tool ID, name, and arguments. `None` remains the existing pure tool call; non-empty text is bounded to 32 KiB characters and 32 KiB of UTF-8 and is neither trimmed nor normalized. Effective Context identity and compact source preserve the text, while the tool-use/result causal pair remains indivisible.

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

Leonervis now accepts a bounded ordered batch of tool calls in one provider response. The neutral `AssistantToolBatch` stores response-wide companion text and multiple `ToolUse` values with unique IDs; one call retains the legacy `ToolUse` shape. The OpenAI-compatible adapter assembles calls from `tool_calls[]` or independent stream indexes, while Anthropic assembles content blocks. The complete response must pass count, ID, JSON, closed-schema, and causal validation before any action in that batch can run; one invalid call rejects the whole batch.

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

A closed `TerminalViewState`, pure reducer, and bounded local queue move assistant, tool, context, usage, compaction, and failure events from one background worker to the sole TTY renderer. Only consecutive assistant deltas may be coalesced; tool, approval, failure, and durable-final facts cannot be lost. Renderer and terminal-sink failures remain best-effort and cannot affect execution, Action Audit, or turn commit. One-shot, redirect, injected-stream, and non-TTY paths remain synchronous.

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

## Foundation 1D: Bounded Literal Grep and Versioned Tool Arguments

The model-visible read-only surface now has the fixed `read_file, glob, grep` order. `grep(query, include)` uses the same portable workspace-relative selector as glob to choose non-symlink regular files, then performs case-sensitive literal substring search within strict UTF-8 logical lines. Each matching source line produces one compact JSONL record containing a POSIX relative path, 1-based line number, and complete line text. Regex, indexing, Unicode normalization, `.gitignore`, multiple patterns, and context windows remain unsupported.

Grep has explicit hard bounds: at most 1,000 candidates, 1 MiB per file, 16 MiB aggregate reads, 200 matching lines, and 32 KiB model-visible output, in addition to the selector's entry, directory, and depth limits. An unreadable, oversized, NUL-bearing, or invalid-UTF-8 selected file is a safe whole-call error. Only match/output caps return a stable prefix of complete JSON records followed by a `{"truncated":true}` sentinel. Empty success means the bounded candidate set was searched completely. Reads recheck regular/non-symlink descriptor identity while retaining the documented local single-user TOCTOU boundary.

To represent grep's two fields, in-memory `ToolUse` gained immutable canonical-JSON `ToolArguments` v1. At Foundation 1D, new `turn_committed` records used record-local schema v2 with `arguments_version + arguments`; legacy schema-v1 read/glob items converted to the same generic in-memory representation during replay without rewriting old JSONL, and resume appended only v2 at that stage. Assistant-tool-text persistence, multi-tool batching, and the tool outcome ledger later advanced the writer through v3/v4/v5; provider usage audit advanced it to v6, and the current atomic first-turn Session title advances it to v7 while retaining v1-v6 readers. New `turn_failed` records use v2, and new `context_compacted` records use v4 while retaining v2/v3 compatibility. Current Effective Context uses ctx-v3/v4 while replaying legacy ctx-v1/v2 checkpoints.

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

Foundation 1B originally proved only process-local atomic history. Foundation 3D now persists each complete turn to workspace JSONL. A bare `leonervis-code` invocation in a noninteractive terminal explains that automation should use `leonervis-code prompt "..."` and exits nonzero, avoiding accidental hangs in pipes or CI.

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
uv run leonervis-code provider add local-qwen \
  --provider custom \
  --model Qwen/Qwen3.5 \
  --protocol openai-compatible \
  --base-url http://127.0.0.1:11434 \
  --context-window-tokens 131072
uv run leonervis-code provider show local-qwen
uv run leonervis-code --profile local-qwen route
```

`provider show` labels user configuration as a `context window override`; offline `route` and runtime `/status` show the resolved value and source. Successful discovery is stored only at:

```text
${XDG_CACHE_HOME:-~/.cache}/leonervis-code/model-context-capabilities.json
```

The cache contains no credential value, raw provider body, or Session content. Profile-registry schema v3 reads v1/v2/v3, upgrades only the layer written, and supports explicit `provider migrate`.

This slice establishes capacity facts only. It does not count current request tokens, reject oversized requests, or compact history. See [0013: provider-owned model context capability](./decisions/0013-provider-owned-model-context-capabilities.md) for the detailed design.

## ADR index

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
