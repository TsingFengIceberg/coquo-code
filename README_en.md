<div align="center">

<img src="./docs/assets/leo-mark.png" alt="LEO mark" width="240">

# Leonervis Code

English | [中文](./README.md)

[![Python](https://img.shields.io/badge/Python-3.12%E2%80%933.13-3776AB?logo=python&logoColor=white)](./pyproject.toml)
[![uv](https://img.shields.io/badge/uv-managed-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![pytest](https://img.shields.io/badge/pytest-8%2B-0A9EDC?logo=pytest&logoColor=white)](./pyproject.toml)
[![Ruff](https://img.shields.io/badge/Ruff-0.9%2B-D7FF64?logo=ruff&logoColor=black)](./pyproject.toml)

</div>

Leonervis Code is a learning-first coding-agent CLI prototype for local, single-user use. The model makes decisions, the host executes controlled tools within an explicit workspace boundary, and structured results return to the model.

> **Current status:** named provider profiles, real/offline runtimes, resumable Sessions, workspace-root `AGENTS.md` project instructions, and 21 bounded tools are implemented. On Linux, `run_command` requires bubblewrap and seccomp isolation: the workspace is the only Host-persistent writable area and network sockets are denied; an unavailable sandbox never degrades to direct Host execution. Read-only Git observation distinguishes staged, unstaged, and untracked state, returns bounded tracked patches, and reads recent current-HEAD history or one complete-ID reachable commit. Anthropic and OpenAI-compatible providers normalize ordered calls from one provider response into a neutral batch; after complete validation, the Host still sends each action through PermissionGate, approval, and Action Audit without parallel execution. Persisted tool ledgers, compaction checkpoints, and provider usage audits are inspectable, while context pressure and current-process token usage remain immediately visible. The three-layer budget is eight calls per response, 32 tool requests per user turn, and 24 provider invocations with a final text-only invocation.

## Contents

- [Quick start](#quick-start)
- [Main commands](#main-commands)
  - [Run tasks and start the REPL](#run-tasks-and-start-the-repl)
  - [Configure providers](#configure-providers)
  - [Inspect routes and context windows](#inspect-routes-and-context-windows)
  - [Manage Sessions](#manage-sessions)
  - [REPL commands](#repl-commands)
- [Configuration and local state](#configuration-and-local-state)
- [Development and verification](#development-and-verification)
- [Detailed documentation](#detailed-documentation)
- [Current scope and next step](#current-scope-and-next-step)

## Quick start

Leonervis Code requires Python 3.12 or 3.13, the latest stable [uv](https://docs.astral.sh/uv/), and Git. The project uses `uv.lock` for a reproducible environment. Model use of `run_command` additionally requires Linux, `/usr/bin/bwrap`, and `libseccomp.so.2`; other features remain available when one is missing, but commands fail closed.

```bash
cd leonervis-code
uv sync
uv run leonervis-code
```

A bare invocation starts the REPL in a real terminal. Without a selected real provider, it uses the deterministic fake provider and performs no network access:

```text
›

  fake · ~/Projects/leonervis-code
```

The formal command is `leonervis-code`; `leonervis` is a shorthand. A module entry point is also available:

```bash
uv run leonervis --version
uv run python -m leonervis_code --help
```

## Main commands

The command's own help is always the authoritative parameter reference:

```bash
uv run leonervis-code --help
uv run leonervis-code provider --help
uv run leonervis-code session --help
```

### Run tasks and start the REPL

| Purpose | Command |
| --- | --- |
| Start a REPL with a new Session | `uv run leonervis-code` |
| Resume the workspace's latest Session | `uv run leonervis-code --resume latest` |
| Run one prompt | `uv run leonervis-code prompt "Explain this workspace"` |
| Run in another workspace | `uv run leonervis-code -C ../project prompt "Explain the project structure"` |
| Use a named profile | `uv run leonervis-code --profile work prompt "Explain the README"` |
| Temporarily override this process's output budget | `uv run leonervis-code --profile work --max-output-tokens 8192 prompt "Generate a detailed report"` |
| Override a profile's model temporarily | `uv run leonervis-code --profile work --model model-v2 prompt "Continue"` |
| Use a direct model route | `uv run leonervis-code --model anthropic/claude-opus-4-8 prompt "Explain the README"` |
| Approve workspace writes interactively in the REPL | `uv run leonervis-code --permission-mode workspace-write --approval ask` |
| Allow automatic workspace writes in one-shot mode | `uv run leonervis-code --permission-mode workspace-write --approval auto prompt "Create note.txt"` |
| Approve local commands interactively in the REPL | `uv run leonervis-code --permission-mode danger-full-access --approval ask` |
| Allow approved commands automatically in one-shot mode | `uv run leonervis-code --permission-mode danger-full-access --approval auto prompt "Run the project tests"` |
| Show the version | `uv run leonervis-code --version` |

Use `prompt` for scripts and one-shot tasks; use the bare command for a stateful multi-turn REPL. Successful turns are saved automatically, and tool execution shows redacted `[tool 1/32] ...` status lines.

Common permission modes:

```bash
uv run leonervis-code                                      # read-only REPL
uv run leonervis-code --permission-mode workspace-write --approval ask
uv run leonervis-code --permission-mode danger-full-access --approval ask
uv run leonervis-code --permission-mode workspace-write --approval auto prompt "Modify and verify the project"
```

REPL `ask` approval shows a bounded candidate diff before `write_file`, `edit_file`, and `patch_file`, and essential risk facts for copy, move, delete, mkdir, and command actions; workspace changes after approval still cause stale rejection, and approval never disables the command sandbox. The sandbox makes the Host root read-only, remounts the workspace read-write, provides private `/tmp`, masks known HOME-sensitive paths, and denies sockets. It does not provide rollback, resource quotas, or hostile-concurrency transactions. One-shot tool status goes to stderr while the final answer goes to stdout; use `/actions` in the REPL for durable Action Audit. See [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) and the [architecture decision records](./docs/decisions/) for all 21 tools, permissions, workspace/symlink rules, timeouts, stale-state checks, and durability boundaries.

### Configure providers

A built-in provider gets its protocol, default endpoint, and credential environment-variable name from the catalog:

```bash
export ANTHROPIC_API_KEY='...'
uv run leonervis-code provider add work \
  --provider anthropic \
  --model claude-opus-4-8
```

A custom OpenAI-compatible endpoint requires an explicit protocol and base URL. A profile stores only the credential environment-variable name, never the key value:

```bash
export VENDOR_API_KEY='...'
uv run leonervis-code provider add vendor \
  --provider custom \
  --model vendor/model \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY \
  --context-window-tokens 1000000
```

Common profile-management commands:

```bash
uv run leonervis-code provider list
uv run leonervis-code provider show vendor
uv run leonervis-code provider use vendor              # workspace scope
uv run leonervis-code provider use vendor --scope user
uv run leonervis-code provider clear --scope project
uv run leonervis-code provider rename vendor vendor-new --if-revision 1
uv run leonervis-code provider remove vendor-new
uv run leonervis-code provider migrate
```

Selection precedence is explicit `--profile` → explicit direct `--model` → workspace active → user active → fake/offline. `provider use` prepares the candidate route, credential, and client before atomically switching; failure preserves the old configuration and client.

### Inspect routes and context windows

`route` is an offline diagnostic command. It constructs no provider client, reads no key value, and sends no network request.

```bash
uv run leonervis-code --profile vendor route
uv run leonervis-code --model openai/gpt-5 route
```

A named profile can configure the context window for its exact endpoint/model:

```bash
uv run leonervis-code provider replace vendor \
  --provider custom \
  --model vendor/model \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY \
  --context-window-tokens 1000000 \
  --if-revision 1

uv run leonervis-code provider show vendor
uv run leonervis-code --profile vendor route
```

Use `route` for offline resolution results and `/status` plus `/context` in the REPL for the current runtime and context state. See [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) for complete capability-resolution, request-preflight, automatic-compaction, and pre-switch screening rules.

### Manage Sessions

```bash
uv run leonervis-code prompt "First turn"
uv run leonervis-code session list
uv run leonervis-code session show latest
uv run leonervis-code session actions latest
uv run leonervis-code session tools latest
uv run leonervis-code session tools latest --limit 5 --details
uv run leonervis-code --resume latest prompt "Continue the previous turn"
uv run leonervis-code --resume <session-uuid>
```

A Session is workspace-bound and stores successful turns in append-only JSONL. New turns also persist a per-request Host tool ledger for actual successes, errors, skips, and budget rejections without relying on model self-reporting. Use the `session` and `--resume` commands above to inspect, audit, and restore it; see [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) for complete replay, screening, and durability semantics.

### REPL commands

| Command | Purpose |
| --- | --- |
| `/help [session\|tools\|git\|context\|provider\|policy\|input]` | Show Host controls by category; `policy` consolidates permission, approval, and command-sandbox guidance |
| `/history <count>` | Show recent complete turns in the current Session |
| `/actions last`, `/actions [count] [status=<status>] [tool=<name>]` | Show the latest action quickly, or filter redacted current-Session Action Audits by status and tool name |
| `/tools catalog [tool-name]` | Show permission and availability for all 21 canonical tools, or one tool's argument schema and major hard boundaries |
| `/tools [count]` | Show durable tool-ledger summaries for recent turns, default 5 and maximum 20 |
| `/tools details [count]` | Expand per-request tool names, outcomes, and safe result codes with a 32 KiB total output bound |
| `/tool-details [compact\|full]` | Inspect or switch process-local live tool detail; compact is default and full shows bounded structured command argv |
| `/changes` | Show staged, unstaged, and untracked Git path states without invoking the model |
| `/changes unstaged` | Show a bounded tracked patch from index to worktree without invoking the model |
| `/changes staged` | Show a bounded tracked patch from HEAD to index without invoking the model |
| `/commits [count] [path]` | Show recent commits reachable from current HEAD without invoking the model; default 10 and maximum 50 |
| `/commit <full-id> [path]` | Show one bounded message and tracked patch for a current-HEAD-reachable commit without invoking the model |
| `/status` | Summarize the current Session, permission/approval, latest context pressure, tool budgets, sandbox dependencies, and redacted runtime |
| `/permissions [permission-mode [approval-mode]]` | Show the current PermissionGate matrix or preview another mode/approval pair without changing runtime policy |
| `/sandbox check` | Verify Linux, bubblewrap, seccomp, and real sandbox activation with fixed `/usr/bin/true`, without a model call or Session write |
| `/context` | Read-only inspection of Effective Context, content ID, count, and target fit |
| `/instructions` | Inspect root `AGENTS.md` presence, UTF-8 byte count, and content fingerprint without showing its text |
| `/usage` | Show actual provider-token usage for the latest invocation, latest turn, and current process-local profile |
| `/usage session` | Show durable turn, failure, and compaction usage across Session restarts |
| `/usage turns` | Show durable usage for the ten most recent committed or failed turns |
| `/output [tokens\|reset]` | Inspect, temporarily change, or restore the current runtime output-token budget |
| `/compact preview` | Preview fixed compaction selection and current context pressure without generating a summary or modifying the Session |
| `/compact` | Use the current real provider to summarize older complete turns and persist an effective-context checkpoint |
| `/compactions [count]` | Show recent durable compaction checkpoints, default 5 and maximum 20 |
| `/provider list` | List named profiles |
| `/provider current` | Show the current profile/provider/model |
| `/provider use <name>` | Atomically switch the workspace's active profile |
| `/model <model>` | Override this process's model without editing the profile |
| `/session show [latest\|id]` | Show strictly replayed metadata for the current or selected Session without switching |
| `/session preview <latest\|id> [1-10]` | Read the selected Session's recent complete user/final-assistant turns, default 3 and maximum 10 |
| `/session turns <latest\|id> <start> [1-10]` | Read from one explicit 1-based complete turn, default count 3 |
| `/session search <text>` | Literally search final user/assistant text within at most 100 Sessions and 16 MiB of transcripts |
| `/session export <latest\|id> [markdown\|json]` | Write a bounded complete conversation view to the terminal/stdout without tool or audit bodies |
| `/session fork <latest\|id> <turn>` | Copy complete model causality through one turn into a provenance-linked new Session |
| `/session doctor <latest\|id>` | Read-only classify a transcript as valid, repairable-tail, or invalid |
| `/session repair <latest\|id>` | Durably back up, then repair only a provably incomplete final record |
| `/session list [count] [open\|closed] [active\|archived] [pinned\|unpinned] [model=<name>] [name=<text>]` | Combine lifecycle, archive, pin, exact-model, and literal-name filters for workspace Sessions |
| `/session switch`, `/session switch list [filters]` | Build a process-local numbered snapshot of at most 20 Sessions, then switch once with `/session switch <number>` |
| `/session new` | Start an empty Session while preserving the runtime |
| `/session rename <name>` | Durably rename the current Session; use `--auto` to restore the title derived from its first successful turn |
| `/session archive`, `/session unarchive` | Reversibly archive or unarchive the current Session without changing history, runtime, latest, or resume identity |
| `/session pin`, `/session unpin` | Reversibly pin or unpin the current Session without changing history, runtime, latest, or resume identity |
| `/resume <latest\|id>` | Switch Sessions while preserving the runtime |
| `/clear` | Clear only the current terminal screen without changing Session or history |
| `/exit`, `/quit` | Exit normally |

Common REPL operations:

```text
/status
/sandbox check
/context
/compact preview
/compactions 5
/usage
/usage session
/usage turns
/actions last
/tools catalog
/tools catalog run_command
/permissions
/permissions workspace-write auto
/tools details 3
/tool-details full
/changes
/changes unstaged
/changes staged
/commits 10
/commit <full-commit-id>
/compact
/session show
/session show latest
/session preview latest 3
/session turns latest 1 3
/session search provider adapter
/session export latest markdown
/session doctor latest
/session list 10
/session list 20 archived name=provider
/session pin
/session list pinned
/session switch list 10 active pinned
/session switch 1
/session rename Provider adapter review
/session archive
/session unarchive
/resume latest
/history 5
```

A real TTY now uses one persistent inline `prompt_toolkit.Application` for its input area and toolbar. Submission immediately leaves a blank prompt at the bottom while model and tool output appears between the submitted prompt and the new draft. One draft remains editable while busy, but Enter neither queues nor inserts a second message. Ctrl-R opens a separate case-insensitive reverse-search field over the current Session's latest 1,000 committed prompts. Slash completion continues through tool names, permission modes, Action Audit status/tool filters, and common subcommands, while an unambiguous close spelling produces a suggestion and never automatic execution. Ctrl-C cooperatively cancels the active turn; Ctrl-D cancels first and waits for provider, tool, and Action Audit cleanup before exit. Approval temporarily owns input and restores the draft afterward. `/clear` resets only the display and does not change Session, history, or transcript state.

Typed Host events drive the toolbar phase, distinguishing turn/provider preparation, action planning, a specific running tool, tool-result processing, approval, compaction, provider-usage recording, the actual durable Session append, finalization, and cancellation. Known context, provider, runtime, authorization, Session, and `run_command` result codes include a conservative `Next:` suggestion without automatic retry, rollback claims, or hiding completed tool side effects. `/status`, `/sandbox check`, `/tools catalog`, `/actions last`, and other slash inspections never enter model history. Command approval now states the actual read-only Host, writable workspace, and socket-denial sandbox boundary.

Submitted user messages consistently start with `› ` and assistant bodies with `• `. Explicit newlines and terminal wrapping continue from the body column after the marker. One blank line separates the user message from the first visible output of its turn; if the model requests a tool without companion text, presentation starts with the `  │ ` Host trace and does not invent an empty `•`. Within one user turn, Host execution facts such as context, tools, approval, usage, and failures use that rail inside the same Assistant Turn without masquerading as model speech; slash results remain Host blocks outside a turn and form complete interaction blocks containing the echoed `›` input, Host result, and one low-intensity rule. Switching between assistant text and Host traces no longer inserts a separator, and one low-intensity short rule appears only after the complete turn before the next `›`. Warnings, approvals, and errors remain prominent; `NO_COLOR=1` removes color and dim styling while preserving roles, rails, separators, indentation, and layout.

A real TTY uses a `›` input marker and `model · Session name · context · workspace` status line. A new Session first persists `New session N`; before its first successful turn is committed, the same provider may receive up to three dedicated no-tools title requests. Conflicting automatic titles are retried, then the Host adds a stable numeric suffix if needed. Title calls share the 24-provider-invocation turn ceiling and are atomically stored with the first turn and usage in `turn_committed` v8. When provider output limits, provider failures, invalid or duplicate titles, or the invocation budget require a Host fallback, its safe reason is persisted and displayed. Failed or uncommitted turns leave no name behind. `/session rename <name>` sets a manual name and `/session rename --auto` restores the first-turn automatic title. `/session archive` adds only a reversible organization marker; archived Sessions remain resumable and usable by UUID or `latest`. Names and archive state refresh immediately, while the UUID remains the exact resume identity. Before every real provider invocation it shows a block context meter; afterward it shows the provider's actual input/output tokens. Tool continuations are measured independently, then the turn and current-profile totals are summarized. Live tool lines remain redacted and compact by default; `/tool-details full` expands bounded structured command argv, cwd, timeout, and direct-versus-shell interpretation guidance for the current process, while warning that argv may contain sensitive values and continuing to hide file/edit/patch/search content. Command completion lines show trusted exit/status, duration, and stdout/stderr byte counts; full mode expands signal, per-stream truncation, and cleanup completeness, but neither mode displays raw stdout/stderr. The `/changes` family runs fixed read-only Git observation directly: it does not invoke a provider, consume model-tool budget, or write Session or Action Audit, and untracked entries expose paths rather than content. `/context` and `/compact preview` label normal, approaching 80%, auto-compact, near-full, or unknown pressure; `/usage` also shows the latest compaction generation in the current runtime. `/usage session` and `/usage turns` read cross-restart usage from strictly replayed Session terminal records; legacy records show unavailable, while absent metadata remains explicitly unknown rather than zero. When a provider exhausts its output limit, the terminal shows the requested limit and usable actual usage; the incomplete response does not become a final answer or committed turn, and completed tool side effects are not rolled back. `/output` shows the effective budget, configured default, and known model maximum; `/output 8192` changes only the current process and `/output reset` restores the profile or direct-route default. An update screens the current Effective Context for known overflow before rebuilding the provider route, while profile files, Session history, and accumulated usage remain unchanged. A model switch preserves and re-screens the temporary budget, while a new profile switch clears it. A non-reducing `/compact` failure shows source and candidate input measurements while leaving checkpoints and Effective Context unchanged, and durably records the failed invocation usage audit. Process-local totals still reset after a successful `/provider use` or `/model` switch; Session totals persist, but no cost is calculated. Enter submits and Alt+Enter inserts a newline; if the terminal intercepts Alt, press Esc and then Enter. Assistant output begins with `•`, and tool turns also show a Host-generated `Tool summary:`. A TTY renders assistant Markdown, while pipes and redirects retain raw Markdown. `NO_COLOR=1` disables color but preserves Markdown layout. See [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) for complete boundaries.

`/session pin` stores a pin through append-only metadata, and `/session list pinned` composes with the other filters. `/session switch` keeps only one process-local snapshot from picker numbers to complete UUIDs and shows name, turn count, lifecycle/archive/pin state, model, and creation time. `/session switch <number>` consumes and immediately clears that snapshot. Any ordinary prompt, Session metadata mutation, direct resume, or failed refresh also discards it. The actual switch still runs the existing prepare, context screening, stale/CAS validation, and durable resume commit; failure preserves the current Session.

`/session show <id>` and `/session preview <id> [1-10]` inspect another Session without switching. Preview uses strict replay to select recent complete turns and exposes only final user/assistant text, not tool companion text, tool results, or Action Audit. It escapes terminal controls and caps output at 32 KiB. Neither command calls a provider, takes a writer lease, repairs a tail, appends a record, or changes the current Session, `latest`, runtime, history, Effective Context, or picker snapshot. Scripts can obtain the same projection with `leonervis-code -C <workspace> session preview [latest|id] --limit 3`.

`/session search` performs bounded case-sensitive literal matching only over final dialogue and returns complete UUIDs plus turn numbers; `/session turns` inspects the matching location. `/session export` writes only a Markdown or JSON conversation view to stdout and never presents internal Action Audit, tool results, usage, or compaction summaries as shareable dialogue.

`/session fork` creates a new UUID from one strict source snapshot at a complete-turn boundary. It copies complete ToolUse/ToolResult causality but not the parent's Action Audit, provider usage, failures, compaction, archive, or pin state; `session_forked` v1 stores the parent UUID, boundary, and source transcript SHA-256. `/session doctor` is always read-only. `/session repair` accepts only a valid complete prefix plus an incomplete final JSON fragment, obtains the writer lease, durably stores a digest-named private backup, then truncates the fragment and appends/fsyncs the existing `recovery` v1 record. Middle corruption, complete JSON missing a newline, and actively written Sessions are never repaired.

`/commits` and `/commit` reuse the same fixed read-only Git runner: history is limited to current-`HEAD` reachability, while `git_show` accepts only complete lowercase 40/64-hex commit IDs. Subjects, messages, and patches are bounded with explicit truncation, and terminal control characters are escaped.

For a deterministic view of the bounded tool loop:

```bash
uv run leonervis-code demo-read README.md
uv run leonervis-code demo-read ../outside.txt   # verify workspace-escape rejection
```

`demo-read` is not a real model interface. It does not write files, execute shell commands, or access the network.

## Configuration and local state

| Path | Contents |
| --- | --- |
| `${XDG_CONFIG_HOME:-~/.config}/leonervis-code/providers.json` | user provider profiles and active selection |
| `<workspace>/.leonervis-code/provider.json` | workspace active profile |
| `<workspace>/.leonervis-code/sessions/.../*.jsonl` | Session transcripts |
| `${XDG_CACHE_HOME:-~/.cache}/leonervis-code/model-context-capabilities.json` | private context-capability discovery cache |

`.leonervis-code/` can contain user input, model responses, source excerpts, and tool results. Add it to the target project's `.gitignore`; do not commit, synchronize, or publish it. Configuration and the capability cache do not store known credential values, but the system cannot detect an unknown secret that appears in user text or source code.

## Development and verification

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
git diff --check
```

After changing dependencies, run `uv lock` before checking the lockfile. Leonervis Code does not install Node, Rust, Java, Docker, databases, or other build environments for a target workspace.

## Detailed documentation

- [Implemented foundations and design evolution](./docs/implemented-foundations_en.md): a consolidated account of the system prompt, tool loop, route policy, multi-provider runtime, profiles, Sessions, context capability, compaction, permission/approval, and controlled writes.
- [Architecture decision records](./docs/decisions/): complete problem statements, trade-offs, boundaries, and verification records for each learning slice.
- [AgentLoop and Terminal Assistant Tool Text Integration](./docs/decisions/0046-agent-loop-and-terminal-assistant-tool-text-integration.md): mixed-response execution order, live presentation, failure atomicity, and Session recovery.
- [AgentLoop, Runtime, and Terminal Streaming Integration](./docs/decisions/0050-agentloop-runtime-and-terminal-streaming-integration.md): stream preflight, complete tool assembly, live REPL display, and durable final confirmation.
- [TTY Markdown Rendering](./docs/decisions/0051-tty-markdown-rendering.md): safe-block streaming, TTY layout, raw redirects, and terminal-control boundaries.
- [Exact Bounded Informed Approval Previews](./docs/decisions/0052-exact-bounded-informed-approval-previews.md): prepared candidate diffs, exact-action binding, risk summaries, terminal safety, and stale revalidation.
- [TTY Prompt Editor and Interaction Feedback](./docs/decisions/0053-tty-multiline-prompt-editor.md): exact multiline input, Session-derived history, slash completion, screen clearing, and ephemeral assistant status.
- [Sequential Tool-call Budget Hardening](./docs/decisions/0054-sequential-tool-call-budget-hardening.md): staged over-budget work, precise multiple-call diagnostics, and unchanged sequential execution boundaries.
- [Bounded Multi-tool Response Batches](./docs/decisions/0055-bounded-multi-tool-response-batches.md): provider batch extraction, sequential Host execution, the 8/32/24 budget, Session/context compatibility, and failure atomicity.
- [Structured Tool Outcome Ledger](./docs/decisions/0056-structured-tool-outcome-ledger.md): per-request Host accounting, authoritative forced text-only summaries, Session v5, and post-commit terminal summaries.
- [Durable Tool Ledger Inspection](./docs/decisions/0057-durable-tool-ledger-inspection.md): bounded post-replay ledger queries, `session tools`, `/tools`, and explicit legacy availability.
- [Runtime Context Meter and Provider Token Usage](./docs/decisions/0058-runtime-context-meter-and-provider-token-usage.md): per-invocation context progress, provider usage normalization, process-local turn/profile totals, and explicit unknown semantics.
- [Context and Compaction Observability](./docs/decisions/0059-context-and-compaction-observability.md): read-only compact preview, durable checkpoint history, context-risk levels, and latest compaction usage.
- [Provider Output-limit and Compaction Failure Diagnostics](./docs/decisions/0060-provider-output-limit-and-compaction-failure-diagnostics.md): structured truncation failures, failed-invocation usage, uncommitted-state guidance, and non-reducing compaction evidence.
- [Process-local Runtime Output Budget Control](./docs/decisions/0061-process-local-runtime-output-budget-control.md): CLI/REPL temporary budgets, target-aware screening, switch semantics, and usage continuity.
- [Durable Session Provider Usage Audit](./docs/decisions/0062-durable-session-provider-usage-audit.md): successful/failed terminal usage, cross-resume totals, legacy unavailability, and Host-only boundaries.
- [Bounded Read-only Git Change Observation](./docs/decisions/0063-bounded-read-only-git-change-observation.md): fixed Git status/diff, repository-metadata boundaries, `/changes`, and the 19-tool contract.
- [Bounded Reachable Git History Observation](./docs/decisions/0064-bounded-reachable-git-history-observation.md): current-HEAD history, complete reachable commit IDs, `/commits`, `/commit`, and the 21-tool contract.
- [Opt-in Bounded Live Tool Details](./docs/decisions/0065-opt-in-bounded-live-tool-details.md): process-local compact/full switching, bounded structured command argv, shell interpretation guidance, and terminal-safety boundaries.
- [Trusted Command Result Observability](./docs/decisions/0066-trusted-command-result-observability.md): trusted content-free command outcomes, compact/full completion presentation, and raw stdout/stderr non-disclosure.
- [Persistent Inline Terminal Frontend](./docs/decisions/0067-persistent-inline-terminal-frontend.md): persistent inline input, one worker, the UI approval broker, cooperative cancellation, and plain-path compatibility.
- [Terminal Message Hierarchy and Hanging Indent](./docs/decisions/0068-terminal-message-hierarchy-and-hanging-indent.md): stable role markers, body-column continuation, reduced-intensity Host information, and prominent risk states.
- [Host Workbench Navigation and Failure Guidance](./docs/decisions/0069-host-workbench-navigation-and-guidance.md): Session and audit filtering, categorized help, known-failure next steps, and truthful persistence activity.
- [Assistant Turn Execution Trace Grouping](./docs/decisions/0070-assistant-turn-execution-trace-grouping.md): same-turn grouping of model text and Host execution traces, authority boundaries, and turn-end separation.
- [Durable Session Naming and Terminal Identity](./docs/decisions/0071-durable-session-naming-and-terminal-identity.md): no-tools model titles, atomic first-turn naming, collision fallback, legacy replay, exact resume identity, and slash/toolbar boundaries.
- [Session Archive, Search, and Title Fallback Diagnostics](./docs/decisions/0072-session-archive-search-and-title-fallback-diagnostics.md): reversible archive metadata, combined list filtering, fallback reasons, v8 compatibility, and unchanged resume semantics.
- [Pinned Sessions and Snapshot-based Quick Switching](./docs/decisions/0073-pinned-sessions-and-snapshot-quick-switching.md): reversible pins, combined filtering, one-use numbered snapshots, and failure-atomic reuse of the existing resume transaction.
- [Read-only Session Inspection and Bounded Turn Preview](./docs/decisions/0074-read-only-session-inspection-and-bounded-turn-preview.md): strict target metadata inspection, final-dialogue preview without switching, and a 32 KiB terminal bound.
- [Bounded Cross-Session Final-text Search](./docs/decisions/0075-bounded-cross-session-final-text-search.md): final-dialogue literal search with candidate, read, match, and completeness bounds.
- [Bounded Session Turn-range Inspection](./docs/decisions/0076-bounded-session-turn-range-inspection.md): explicit 1-based turn ranges, strict replay, and 32 KiB read-only rendering.
- [Bounded Conversation-only Session Export](./docs/decisions/0077-bounded-conversation-export.md): Markdown/JSON stdout conversation export separated from complete audit data.
- [Provenance-linked Session Forking](./docs/decisions/0078-provenance-linked-session-forking.md): complete-turn causality copying, `session_forked` v1 provenance, and parent immutability.
- [Explicit Session Diagnosis and Tail Repair](./docs/decisions/0079-explicit-session-diagnosis-and-tail-repair.md): read-only doctor, private backup, and explicit incomplete-final-record repair.
- [Fail-closed Linux Command Sandbox](./docs/decisions/0080-fail-closed-linux-command-sandbox.md): read-only Host view, writable workspace mount, seccomp network denial, sensitive-path masking, and no unsandboxed fallback.
- [Host Workbench Diagnostics and Prompt History Search](./docs/decisions/0081-host-workbench-diagnostics-and-prompt-history-search.md): consolidated status, sandbox probing, tool catalog, latest audit, command guidance, and Ctrl-R history search.
- [Host Policy and Tool Discoverability](./docs/decisions/0082-host-policy-and-tool-discoverability.md): per-tool schema and hard-bound inspection, read-only PermissionGate previews, contextual completion, and non-executing spelling suggestions.
- [Provider Mixed-response History Projection](./docs/decisions/0045-provider-mixed-response-history-projection.md): exact native projection for Anthropic and OpenAI-compatible continuation history.
- [`turn_committed` v3 Assistant Tool Text Persistence](./docs/decisions/0044-turn-committed-v3-assistant-tool-text-persistence.md): nullable companion text, v1/v2 replay compatibility, and unchanged legacy prefixes.
- [Provider Mixed-response Inbound Normalization](./docs/decisions/0043-provider-mixed-response-inbound-normalization.md): strict conversion from both provider-native mixed shapes to one neutral `ToolUse`.
- [Provider-neutral Assistant Tool Text Representation](./docs/decisions/0042-provider-neutral-assistant-tool-text-representation.md): the atomic internal companion-text representation, bounds, and context identity.
- [Live Redacted Tool Activity Events](./docs/decisions/0041-live-redacted-tool-activity-events.md): typed tool lifecycles, terminal stream boundaries, redacted summaries, sink-failure isolation, and the unchanged model/Session contracts.
- [Bounded One-level Directory Listing](./docs/decisions/0035-foundation-1e-bounded-directory-listing.md): one-level observation, entry types, no-follow paths, scan/output bounds, and empty/truncated semantics.
- [Controlled Empty-directory Deletion](./docs/decisions/0034-foundation-4g-controlled-empty-directory-deletion.md): workspace-delete approval, empty-state and identity revalidation, the atomic rmdir empty condition, and parent durability.
- [Controlled Regular-file Deletion](./docs/decisions/0033-foundation-4f-controlled-regular-file-deletion.md): workspace-delete approval, target and parent identity, unlink durability, and non-retryable partial outcomes.
- [Controlled No-overwrite File Move](./docs/decisions/0032-foundation-4e-controlled-no-overwrite-file-move.md): dual-path identity, workspace-move approval, no-overwrite hard-link/unlink, stale checks, and truthful partial outcomes.
- [Controlled Single-directory Creation](./docs/decisions/0031-foundation-4d-controlled-single-directory-creation.md): the single-directory path contract, workspace-create approval, stale checks, fsync, and partial durability.
- [Durable Model-visible Command Integration](./docs/decisions/0030-foundation-4c-durable-model-visible-command-integration.md): the durable pre-spawn commit point, CLI approval/audit, six-tool order, provider adapter v8, system prompt v7, and compatibility.
- [Bounded Command Execution and Process-group Cleanup](./docs/decisions/0029-foundation-4c-bounded-command-execution-and-process-cleanup.md): direct argv, closed environment, bounded output, UTF-8/base64, timeout/cancellation, and TERM-to-KILL cleanup.
- [Controlled Command Contract and Side-effect-free Preparation](./docs/decisions/0028-foundation-4c-controlled-command-contract-and-preparation.md): argv/cwd/timeout bounds, `dangerous` permission binding, the environment allowlist, and exact approval identity.
- [Model-visible Exact Edit Integration](./docs/decisions/0027-foundation-4b-model-visible-exact-edit-integration.md): fifth-tool schema/order, provider parity, system prompt v6, Effective Context identity, and ProjectSession dispatch.
- [Exact Edit Preparation, Execution, and Authorization Composition](./docs/decisions/0026-foundation-4b-exact-edit-preparation-execution-and-authorization.md): unique exact replacement, side-effect-free preparation, atomic replacement, stale checks, and why Slices 0–3 leave the model contract unchanged.
- [Action Audit Observability](./docs/decisions/0025-foundation-4a-action-audit-observability.md): standalone and REPL read-only inspection, redacted fields, count bounds, and the unchanged model contract.
- [Approval Coordination and Controlled `write_file`](./docs/decisions/0024-foundation-4a-approval-coordination-and-controlled-write.md): coordinator ordering, prepared-turn leases, CLI approval UX, create/overwrite hard bounds, and partial-outcome semantics.
- [Exact Action Identity and Durable Action Audit](./docs/decisions/0023-foundation-4a-exact-action-identity-and-durable-audit.md): the exact manifest/digest, prepared-turn lease, single-use grant, append-only lifecycle, and crash/recovery semantics.
- [Permission Policy Contract](./docs/decisions/0022-foundation-4a-permission-policy-contract.md): orthogonal permission/approval semantics, action classes, the deterministic decision matrix, stable reasons, and the pure policy boundary.
- [Bounded Literal Grep](./docs/decisions/0021-foundation-1d-bounded-literal-grep.md): literal/include semantics, JSONL line results, content/file bounds, generic arguments, and mixed turn-schema replay.
- [Bounded Workspace Glob](./docs/decisions/0020-foundation-1c-bounded-workspace-glob.md): portable patterns, hidden/symlink policy, deterministic bounds, the shared tool budget, and the legacy schema-v1 seam.
- [Pre-turn Automatic Context Compaction](./docs/decisions/0019-pre-turn-automatic-context-compaction.md): the 80% high-water mark, pending-turn isolation, one-attempt policy, shared runtime lease, and schema-v3 trigger provenance.
- [Target-aware Resume Prepare/Commit](./docs/decisions/0018-target-aware-resume-prepare-commit.md): read-only preparation, current-runtime screening, exact stale/CAS checks, and durable partial outcomes.
- [Controlled Compact Transaction](./docs/decisions/0017-controlled-compact-transaction.md): manual `/compact`, no-tools summary generation, mixed Session schema, and persist-before-memory atomicity.
- [Provider-neutral Effective Context Snapshot](./docs/decisions/0016-provider-neutral-effective-context-snapshot.md): full/effective context boundaries, stable `ctx-v1` identity, and read-only `/context`.
- [Target-aware runtime switch UX](./docs/decisions/0015-target-aware-runtime-switch-ux.md): committed-context screening before switches, known-reject/unknown-allow behavior, and atomic audit semantics.
- [Target-specific request counting and preflight](./docs/decisions/0014-target-specific-request-counting-and-preflight.md): native-input counting, two distinct limits, and typed local rejection before every provider invocation.
- [Provider-owned model context capability](./docs/decisions/0013-provider-owned-model-context-capabilities.md): context/model-output limit resolution and cache design.
- [Canonical model system prompt](./docs/decisions/0012-first-canonical-model-system-prompt.md): model-visible contract, version, and fingerprint.
- [Stable profile identity and durable Sessions](./docs/decisions/0010-foundation-3d-stable-profile-identity-and-durable-sessions.md): profile UUID/revision and Session persistence.
- [Claw-Code prompt study map](./docs/references/claw-code-prompts/README.md): read-only reference structure and Leonervis-specific differences.
- [Harness-study](https://github.com/TsingFengIceberg/Harness-study): related Harness reading and learning notes.

## Current scope and next step

The fixed model-visible order is `read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file, delete_directory, list_directory, copy_file, read_file_lines, stat_path, list_tree, grep_regex, patch_file, git_status, git_diff, git_log, git_show`. One provider response may contain up to eight ordered tool calls; one user turn admits at most 32 tool requests and 24 provider invocations, with the final invocation restricted to text. The Host parses and budget-checks the complete batch before sequential execution. A non-successful action explicitly skips later calls in that batch, and a batch that cannot fit the remaining budget gets zero execution. Every model tool still passes separately through permission, approval, execution, and Action Audit.

Foundation 5A reads only the canonical workspace-root name `AGENTS.md`: missing means no project instructions; an existing entry must be a non-symlink strict-UTF-8 regular file without NUL and no larger than 32 KiB. The Host reads and freezes it once while preparing each user turn, reuses that snapshot for every tool continuation, and reloads only on the next turn. It does not search parents or subdirectories and never automatically loads `CLAUDE.md` or `LEONERVIS.md`. Project instructions use a dedicated provider block and participate in token counting and Effective Context identity, but are not written to the Session transcript. They remain subordinate to canonical Host policy and the current direct user request and cannot relax permission, approval, workspace, symlink, budget, audit, sandbox, or durability boundaries. `/instructions` displays metadata only and neither invokes a provider nor mutates the Session.

Provider batching, the structured tool outcome ledger and durable inspection, default-redacted live activity with explicit command-argv expansion and trusted command-result statistics, mixed responses, streaming, TTY Markdown rendering, process-local output-budget control, Session naming/archive/pinning/filtering/quick switching/preview/search/turn navigation/export/fork/doctor/repair/provider-usage audit, read-only Git change/history observation, the fail-closed Linux command sandbox, and Foundation 5A project instruction loading are complete. Current versions are canonical system prompt v23, provider adapter contract v26, ToolArguments v1, ActionIdentity v1, `session_header` v1/v2 replay with new records using v2, `session_named` v1, `session_archive_changed` v1, `session_pin_changed` v1, `session_forked` v1, `turn_committed` schema v8 with v1-v7 replay, `turn_failed` schema v2, Action Audit schema v1, `context_compacted` v2/v3 replay with new records using v4, and current `ctx-v5`/`ctx-v6` representations. Legacy Sessions and `ctx-v1` through `ctx-v4` identities/checkpoints remain compatible; the empty full-context identity without project instructions is `ctx-v5-0700acbf613c3896f65ea82d5fa78f7139406f50e9b5227bcabedf223708d39b`. Linked worktrees, arbitrary Git argv, abbreviated/arbitrary revisions, refs, unreachable object reads, untracked patches, recursive project-instruction inheritance, recursive copying/deletion, ignore-aware or indexed search, fuzzy/free-form patching, directory movement, non-empty deletion, recursive mkdir, shell source strings, interactive PTYs, network tools, network allowlists, resource quotas, Host sandbox bypass, Session merge/import/remote sync, general automatic retry, parallel tools, multiple agents, and remote services remain unavailable. See [ADR 0083](./docs/decisions/0083-foundation-5a-root-agents-project-instructions.md) for the project-instruction design.
