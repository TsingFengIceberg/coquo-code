<div align="center">

<img src="./docs/assets/coquo-mark.png" alt="Coquo COQ mark" width="240">

# Coquo

English | [中文](./README.md)

[![Python](https://img.shields.io/badge/Python-3.12%E2%80%933.13-3776AB?logo=python&logoColor=white)](./pyproject.toml)
[![uv](https://img.shields.io/badge/uv-managed-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![pytest](https://img.shields.io/badge/pytest-8%2B-0A9EDC?logo=pytest&logoColor=white)](./pyproject.toml)
[![Ruff](https://img.shields.io/badge/Ruff-0.9%2B-D7FF64?logo=ruff&logoColor=black)](./pyproject.toml)

</div>

Coquo is a learning-first coding-agent CLI prototype for local, single-user use. The model makes decisions, the host executes controlled tools within an explicit workspace boundary, and structured results return to the model.

The name comes from Latin *coquō*, “I cook”: requirements, context, tools, and model decisions are prepared into verified software changes.

> **Current status:** named Provider Profiles, real and offline runtimes, resumable Sessions, foreground multi-Stage Tasks, both Eval layers, and 35 ordinary bounded tools are implemented across local coding, Git observation, web search and fetch, structured reads, controlled file transfer, audited MCP tool execution, and declarative Skill loading. See [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) for exact capabilities and security boundaries.

## Contents

- [Quick start](#quick-start)
- [Main commands](#main-commands)
  - [Run tasks and start the REPL](#run-tasks-and-start-the-repl)
  - [Configure providers](#configure-providers)
  - [Inspect routes and context windows](#inspect-routes-and-context-windows)
  - [Manage Sessions](#manage-sessions)
- [Manage Tasks](#manage-tasks)
- [Manage Child Runs](#manage-child-runs)
  - [REPL commands](#repl-commands)
- [Configuration and local state](#configuration-and-local-state)
- [Development and verification](#development-and-verification)
- [Detailed documentation](#detailed-documentation)
- [Current scope and next step](#current-scope-and-next-step)

## Quick start

Coquo requires Python 3.12 or 3.13, the latest stable [uv](https://docs.astral.sh/uv/), and Git. The project uses `uv.lock` for a reproducible environment. Model use of `run_command` additionally requires Linux, `/usr/bin/bwrap`, and `libseccomp.so.2`; independent web search requires `BRAVE_SEARCH_API_KEY` or `TAVILY_API_KEY` in the process environment. Other features remain available when these optional prerequisites are missing, and the corresponding tool fails closed.

```bash
cd coquo
uv sync
uv run coquo
```

A bare invocation starts the REPL in a real terminal. Without a selected real provider, it uses the deterministic fake provider and performs no network access:

```text
›

  fake · ~/Projects/coquo
```

The formal command is `coquo`; `coquo` is a shorthand. A module entry point is also available:

```bash
uv run coquo --version
uv run python -m coquo --help
```

## Main commands

The command's own help is always the authoritative parameter reference:

```bash
uv run coquo --help
uv run coquo provider --help
uv run coquo session --help
uv run coquo task --help
```

### Run tasks and start the REPL

| Purpose | Command |
| --- | --- |
| Start a REPL with a new Session | `uv run coquo` |
| Resume the workspace's latest Session | `uv run coquo --resume latest` |
| Run one prompt | `uv run coquo prompt "Explain this workspace"` |
| Run in another workspace | `uv run coquo -C ../project prompt "Explain the project structure"` |
| Use a named profile | `uv run coquo --profile work prompt "Explain the README"` |
| Temporarily override this process's output budget | `uv run coquo --profile work --max-output-tokens 8192 prompt "Generate a detailed report"` |
| Override a profile's model temporarily | `uv run coquo --profile work --model model-v2 prompt "Continue"` |
| Use a direct model route | `uv run coquo --model anthropic/claude-opus-4-8 prompt "Explain the README"` |
| Approve workspace writes interactively in the REPL | `uv run coquo --permission-mode workspace-write --approval ask` |
| Allow automatic workspace writes in one-shot mode | `uv run coquo --permission-mode workspace-write --approval auto prompt "Create note.txt"` |
| Approve local commands interactively in the REPL | `uv run coquo --permission-mode danger-full-access --approval ask` |
| Allow approved commands automatically in one-shot mode | `uv run coquo --permission-mode danger-full-access --approval auto prompt "Run the project tests"` |
| Start a REPL that can explicitly select Tavily | `TAVILY_API_KEY=... uv run coquo --permission-mode danger-full-access --approval ask`, then run `/search use tavily` |
| Use Profile-declared Provider-native search | `uv run coquo --profile search-provider prompt "Search for the official Python 3.14 release notes and list the sources"` |
| Show the version | `uv run coquo --version` |

Common permission modes:

```bash
uv run coquo                                      # read-only REPL
uv run coquo --permission-mode workspace-write --approval ask
uv run coquo --permission-mode danger-full-access --approval ask
uv run coquo --permission-mode workspace-write --approval auto prompt "Modify and verify the project"
```

### Configure providers

A built-in provider gets its protocol, default endpoint, and credential environment-variable name from the catalog:

```bash
export ANTHROPIC_API_KEY='...'
uv run coquo provider add work \
  --provider anthropic \
  --model claude-opus-4-8
```

A custom OpenAI-compatible endpoint requires an explicit protocol and base URL. A profile stores only the credential environment-variable name, never the key value:

```bash
export VENDOR_API_KEY='...'
uv run coquo provider add vendor \
  --provider custom \
  --model vendor/model \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY \
  --context-window-tokens 1000000
```

Official DeepSeek V4 Flash automatically selects Responses and enables Provider-native search by default; other DeepSeek models retain Chat Completions:

```bash
uv run coquo provider add deepseek-flash \
  --provider deepseek \
  --model deepseek-v4-flash \
  --api-key-env DEEPSEEK_API_KEY \
  --max-output-tokens 16384
```

A custom endpoint that implements Responses can select `openai-responses` explicitly:

```bash
uv run coquo provider add responses-gateway \
  --provider custom \
  --model vendor/model \
  --protocol openai-responses \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY
```

Provider preset, message protocol, and native-search adapter are independent. Built-in Profiles default to `auto` and enable only catalog-declared capability; custom Profiles default to `none`. A compatible endpoint can explicitly select an implemented adapter:

```bash
uv run coquo provider add search-provider \
  --provider custom \
  --model vendor/search-model \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY \
  --native-search-adapter openai-chat-web-search-options-v1
```

Future vendor extensions can be imported with `--native-search-manifest path/to/search.json`:

```json
{
  "schema_version": 1,
  "id": "future-vendor-search-v1",
  "request": {
    "extra_body": {"enable_search": true},
    "server_tool": null
  },
  "response": {"citation_format": "openai-url-annotations"}
}
```

The bounded declarative manifest permits only restricted `extra_body`, a non-function server tool, and a predefined citation format. It cannot define an endpoint, header, credential, executable code, or custom parser. The CLI validates it once while creating or replacing the Profile and stores canonical data rather than the source path. Old Profiles need not be migrated; remove and rebuild them under the new schema. `provider show` and `route` report the resolved capability, adapter, source, and manifest digest.

Common profile-management commands:

```bash
uv run coquo provider list
uv run coquo provider show vendor
uv run coquo provider use vendor              # workspace scope
uv run coquo provider use vendor --scope user
uv run coquo provider clear --scope project
uv run coquo provider rename vendor vendor-new --if-revision 1
uv run coquo provider remove vendor-new
uv run coquo provider migrate
```

Selection precedence is explicit `--profile` → explicit direct `--model` → workspace active → user active → fake/offline. `provider use` prepares the candidate route, credential, and client before atomically switching; failure preserves the old configuration and client.

### Inspect routes and context windows

`route` is an offline diagnostic command. It constructs no provider client, reads no key value, and sends no network request.

```bash
uv run coquo --profile vendor route
uv run coquo --model openai/gpt-5 route
```

A named profile can configure the context window for its exact endpoint/model:

```bash
uv run coquo provider replace vendor \
  --provider custom \
  --model vendor/model \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY \
  --context-window-tokens 1000000 \
  --if-revision 1

uv run coquo provider show vendor
uv run coquo --profile vendor route
```

Use `route` for offline resolution results and `/status` plus `/context` in the REPL for the current runtime and context state. See [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) for complete capability-resolution, request-preflight, automatic-compaction, and pre-switch screening rules.

### Manage Sessions

```bash
uv run coquo prompt "First turn"
uv run coquo session list
uv run coquo session show latest
uv run coquo session actions latest
uv run coquo session tools latest
uv run coquo session tools latest --limit 5 --details
uv run coquo --resume latest prompt "Continue the previous turn"
uv run coquo --resume <session-uuid>
```

A Session is workspace-bound and stores successful turns in append-only JSONL. New turns also persist a per-request Host tool ledger for actual successes, errors, skips, and budget rejections without relying on model self-reporting. Use the `session` and `--resume` commands above to inspect, audit, and restore it; see [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) for complete replay, screening, and durability semantics.

### Manage Tasks

```bash
uv run coquo task create "Implement resumable multi-stage work" \
  --name "Task runtime" \
  --accept "A human confirms the change meets the objective" \
  --criterion '{"kind":"path-exists","description":"The test file exists","path":"tests/test_app.py","path_type":"file"}' \
  --completion-policy auto-verified \
  --max-stages 12 --max-provider-invocations 288 --max-tool-requests 384
uv run coquo task list --status ready --archive active --name runtime
uv run coquo task show <task-uuid>
uv run coquo task timeline <task-uuid>
```

Tasks manage recoverable foreground multi-stage work. They can begin through natural-language interaction or be inspected and controlled through `task` and `/task`; see [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) and the Task ADRs for the complete lifecycle, acceptance, and recovery boundaries.

### Manage Child Runs

```bash
uv run coquo child create "Inspect the workspace" --parent-session latest
uv run coquo child list --status queued
uv run coquo child show <child-run-uuid>
uv run coquo child prepare <child-run-uuid>
uv run coquo child run <child-run-uuid>
uv run coquo child cancel <child-run-uuid> "Defer execution"
uv run coquo child wait <child-run-uuid> --timeout 30
uv run coquo child recover [<child-run-uuid>]
uv run coquo child handoff <child-run-uuid>
uv run coquo child deliver <child-run-uuid>
```

`child prepare` freezes a bounded redacted read-only execution envelope and creates a detached Child Session without changing `latest`; `child run` then executes one foreground read-only Turn through the shared Agent runtime. In the REPL, `/child start <id>` submits a ready Child to up to four daemon workers in the current process. `child cancel` durably records a request before signaling a cooperative token; `child wait` observes durable state only, and `child recover` marks abandoned `running`/`cancelling` work `interrupted` only after acquiring its v2 OS execution lock. `child handoff` publishes a bounded untrusted result tied to exact terminal evidence; `child deliver` commits a content-free receipt in the parent Session before Host rendering and never injects Child output into parent history or Effective Context. Legacy v1 leases remain fail-closed, and process exit never auto-restarts a Child.

### REPL commands

| Command | Purpose |
| --- | --- |
| `/help [session\|task\|child\|tools\|git\|context\|provider\|search\|mcp\|skills\|hooks\|policy\|input]` | Show Host controls by category; `task` shows durable Task entry points and `child` shows Child Run metadata entry points |
| `/history <count>` | Show recent complete turns in the current Session |
| `/actions last`, `/actions [count] [status=<status>] [tool=<name>]` | Show the latest action quickly, or filter redacted current-Session Action Audits by status and tool name |
| `/tools catalog [tool-name]` | Show permission and Prompt/Stage availability for all 39 canonical tools, or one tool's argument schema and major hard boundaries |
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
| `/search status`, `/search sources` | Inspect Provider/Brave/Tavily availability, ordered activation, and primary without displaying keys |
| `/search use <source> [source...]` | Set the primary; `provider brave|tavily` adds one explicit model-mediated fallback, without fan-out or fusion |
| `/search mode <auto\|required>` | Let the model decide whether to use Provider search or require it for every request |
| `/search domains <domain> [domain...]`, `/search domains reset` | Set or clear adapter-supported Provider domain restrictions |
| `/search context <low\|medium\|high\|reset>` | Set or restore adapter-supported Provider search context size |
| `/search reset` | Clear the REPL override; restore Provider-native search when available, otherwise disable every source |
| `/hooks [active\|list\|show <id>\|doctor\|evaluations [count]\|runs [count]\|task <task-id> [count]]` | Inspect current Hook configuration, durable evaluations, and audited handler runs read-only without a model call; use standalone `hooks` commands to edit it |
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
| `/task start <objective>` | Create a durable Task owned by the current Session without invoking the model or executing a Stage |
| `/task proposals [pending\|accepted\|rejected\|all]` | Read-only list model-submitted Task admission proposals in the current Session |
| `/task proposal show <admission-id>` | Inspect one proposal's objective, reason, criteria, provenance, and resolution status |
| `/task proposal accept <admission-id> [<config-json>]` | Read-only preview the canonical Task configuration, prepared criteria, and exact confirmation SHA-256 without creating a Task |
| `/task proposal accept <admission-id> confirm <sha256> [<config-json>]` | Revalidate the same candidate and idempotently create one sourced Task without invoking a provider or executing a Stage |
| `/task proposal reject <admission-id> [reason]` | Durably reject a pending proposal without creating a Task |
| `/task proposal drive <admission-id> [1-16]` | Start the existing bounded foreground Driver only for an accepted proposal; reject pending or rejected proposals |
| `/task list [1-100] [status=<status>] [active\|archived] [name=<text>]` | Read-only filter durable Tasks in the current workspace |
| `/task show <task-id>`, `/task timeline <task-id>` | Strictly replay Task details or the complete Stage timeline without dialogue or tool bodies |
| `/task continue <task-id> <Stage objective>`, `/task recover <task-id>` | Execute one ordinary Turn, or reconcile committed Session evidence without rerunning a provider or tool |
| `/task plan <task-id>`, `/task plan accept <task-id>`, `/task run <task-id> [1-16]` | Propose, explicitly accept, and serially execute bounded foreground Stages; run reports its stop reason |
| `/task reflect <task-id>`, `/task correct <task-id> [objective]`, `/task revise <task-id>` | Reflect without tools on current failed acceptance, execute one controlled correction Stage, or propose a provenance-linked replacement plan |
| `/task drive <task-id> [1-16]`, `/task next <task-id>` | Advance the bounded adaptive foreground state machine, or preview its next decision read-only; reviewer criteria stop with a token/API-cost notice |
| `/task checkpoint <task-id>`, `/task pause <task-id> [reason]`, `/task resume <task-id>` | Append a bounded derived checkpoint, or pause/resume only automatic driving while retaining explicit Stage controls |
| `/task verify <task-id> <criterion> <evidence>`, `/task verify host <task-id>` | Submit evidence for human criteria, or run deterministic path, digest, command, and Action Audit checks |
| `/task review <task-id>`, `/task complete <task-id>` | Use the current provider for an independent no-tools review; complete manually or by policy only after all criteria pass |
| `/task cancel <task-id> <reason>`, `/task fail <task-id> <reason>` | Commit an explicit cancelled or failed terminal outcome |
| `/task rename <task-id> <name>`, `/task archive <task-id>`, `/task unarchive <task-id>` | Manage display name and reversible archive state |
| `/task derive <parent-task-id> <objective>` | Create an independently governed Task with immutable parent provenance |
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
/mcp list
/mcp status
/mcp show <server-name>
/mcp probe <server-name>
/mcp catalog
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

The table above is the usage reference for REPL input, presentation, Session management, Task control, context inspection, and Git observation. See [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) and the [architecture decision records](./docs/decisions/) for implementation and persistence details.

For a deterministic view of the bounded tool loop:

```bash
uv run coquo demo-read README.md
uv run coquo demo-read ../outside.txt   # verify workspace-escape rejection
```

`demo-read` is not a real model interface. It does not write files, execute shell commands, or access the network.

MCP tools are `dangerous` by default. Run `mcp catalog` to obtain the exact qualified name and schema fingerprint, then use `mcp policy set <qualified-name> --schema-fingerprint <fingerprint> --action workspace-read` to declare one exact local stdio version read-only; remote tools always remain `dangerous`. `mcp catalog explain <reason-code>` explains quarantine reasons, `mcp policy stale` inspects invalid or currently unresolved policies, and `mcp policy prune --dry-run` only previews revision-bound cleanup commands. `mcp add-http`, `mcp oauth`, `mcp resources`, `mcp prompts`, and `mcp doctor` cover remote setup, authorization, non-Tool capability inspection, and interoperability diagnostics.

Declarative Skills may live under workspace `.coquo/skills` or `.agents/skills`, or the XDG user configuration directory. Use `skills init|check|search|import|lock` for local packages, `skills fetch|candidate|install` to quarantine, inspect, and install public raw or ZIP packages, or `/skills` in the REPL for current activation and candidates. The model proposes preserving a workflow only after an explicit user request and does not learn automatically from experience. Implemented-foundations defines the exact format, budgets, and authority boundary.

Hooks can add local restrictions before the normal PermissionGate and observe terminal actions plus selected Turn or Task lifecycle events. An optional pinned local handler runs only as a separate dangerous Action through approval, Action Audit, and the command sandbox, never as a privileged callback. Use `hooks add|fingerprint|template|import|list|show|doctor|enable|disable|remove|runs` to manage and inspect disabled-by-default user or project rules; implemented-foundations and ADRs 0125-0127 define the detailed boundaries.

## Configuration and local state

| Path | Contents |
| --- | --- |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/providers.json` | user provider profiles and active selection |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/mcp-servers.json` | user MCP definitions; endpoints, client metadata, and environment names, never credential values |
| `<workspace>/.coquo/mcp-servers.json` | project MCP server definitions; new servers default to disabled |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/mcp-tool-policies.json` | exact user MCP tool permission policies |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/mcp-oauth.json` | user remote-MCP OAuth pending state and tokens; private mode-`0600` file |
| `<workspace>/.coquo/mcp-tool-policies.json` | exact project MCP tool permission policies |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/hooks.json` | user declarative Hook configuration; new rules default to disabled |
| `<workspace>/.coquo/hooks.json` | project declarative Hook configuration; applied through frozen Turn snapshots |
| `<workspace>/.coquo/provider.json` | workspace active profile |
| `<workspace>/.coquo/sessions/.../*.jsonl` | Session transcripts |
| `<workspace>/.coquo/tasks/.../*.jsonl` | independent Task transcripts |
| `${XDG_CACHE_HOME:-~/.cache}/coquo/model-context-capabilities.json` | private context-capability discovery cache |

`.coquo/` can contain user input, model responses, source excerpts, and tool results. Add it to the target project's `.gitignore`; do not commit, synchronize, or publish it. Configuration and the capability cache do not store known credential values, but the system cannot detect an unknown secret that appears in user text or source code.

## Development and verification

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
git diff --check
uv run coquo eval list
uv run coquo eval run all
uv run coquo eval run all --format json
uv run coquo eval task list
tmp=$(mktemp -d)
uv run coquo eval task prepare inventory-validation "$tmp/task"
uv run coquo eval task score inventory-validation "$tmp/task"
```

`pytest` verifies functions, modules, and protocol boundaries, while `eval run` sends fixed scripted-fake trajectories through the complete Host path. `eval task prepare/score` instead creates a small code task offline and scores actual outcomes outside the candidate directory with visible and Host-private tests. Only `eval task run` with both `--real-provider` and an explicit profile/model may invoke a real vendor; it runs in a newly created isolated task directory, writes tool events to stderr, and reserves stdout for the stable Host score. After changing dependencies, run `uv lock` before checking the lockfile. Coquo does not install Node, Rust, Java, Docker, databases, or other build environments for a target workspace.

## Detailed documentation

- [Implemented foundations and design evolution](./docs/implemented-foundations_en.md): a consolidated account of the system prompt, tool loop, route policy, multi-provider runtime, profiles, Sessions, context capability, compaction, permission/approval, and controlled writes.
- [Architecture decision records](./docs/decisions/): complete problem statements, trade-offs, boundaries, and verification records for each learning slice.
- [Deterministic Offline Host Eval Baseline](./docs/decisions/0084-deterministic-offline-host-eval-baseline.md): fixed tasks, isolated execution, Host-fact scoring, and the boundary from pytest and real-model evaluation.
- [Actual Coding Task Eval](./docs/decisions/0085-actual-coding-task-eval.md): actual code outcomes, protected files, Host-private tests, explicit real-provider opt-in, and command-sandbox boundaries.
- [Durable Task Identity and Host Management](./docs/decisions/0086-durable-task-identity-and-host-management.md): the Task/Stage/Turn/Action hierarchy, independent durable identity, and Host-only management boundary.
- [Durable Stage Lifecycle and Turn Evidence](./docs/decisions/0087-durable-stage-lifecycle-and-turn-evidence.md): the Stage start/terminal state machine, exclusive writer, restart interruption semantics, and Session Turn evidence.
- [Foreground Task Stage Execution and Recovery](./docs/decisions/0088-foreground-task-stage-execution-and-recovery.md): ordinary AgentLoop reuse, Task framing, exact crash recovery, failure mapping, and terminal integration.
- [Task Planning, Acceptance, Budgets, and Management](./docs/decisions/0089-task-planning-acceptance-budgets-and-management.md): plan execution, cumulative inter-Stage budgets, completion proposals, human acceptance, and lifecycle management.
- [Structured Task Acceptance and Independent Review](./docs/decisions/0090-structured-task-acceptance-and-independent-review.md): structured criteria, read-only Host verification, independent no-tools review, and automatic completion policy.
- [Adaptive Foreground Task Orchestration](./docs/decisions/0092-adaptive-foreground-task-orchestration.md): acceptance feedback, Reflection/Correction, plan revision, bounded driving, Task checkpoints, and human controls.
- [Resume Runtime Binding at the Durable Commit Point](./docs/decisions/0091-resume-runtime-binding-at-the-durable-commit-point.md): records the current runtime binding in the same durable resume commit so the first resumed tool action still passes strict Action Audit binding checks.
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
- [TTY Host Wrapping and Process-local Command History](./docs/decisions/0093-tty-host-wrapping-and-process-local-command-history.md): visual continuation indentation for dim Host output, slash-command recall, and the non-persistence boundary.
- [Task Proposal Control Boundary](./docs/decisions/0094-task-proposal-control-boundary.md): operator commands, model proposals, and Host Driver authority; exact tool subsets; terminal control calls; and post-commit publication/recovery.
- [Model-visible Task Coordination Tools](./docs/decisions/0095-model-visible-task-coordination-tools.md): four structured Task proposal tools, least-capability Stage exposure, commit-coupled ledger writes, idempotent recovery, and legacy compatibility.
- [Model-proposed Task Admission](./docs/decisions/0096-model-proposed-task-admission.md): ordinary-Prompt Task creation proposals, explicit user resolution, Session-derived pending state, and idempotent acceptance across Task/Session commit boundaries.
- [Informed Task Admission and Foreground Handoff](./docs/decisions/0097-informed-task-admission-and-foreground-handoff.md): canonical configuration preview, exact SHA-256 confirmation, separate foreground Driver handoff, restart recovery, and complete offline Eval coverage.
- [Persistent Activity Indicator and Task Output Alignment](./docs/decisions/0100-persistent-activity-indicator-and-task-output-alignment.md): ephemeral phase text above the editor, unified hanging indentation for Task and ordinary replies, and the non-persistence boundary.
- [`turn_committed` v5 Inherited Assistant Content Replay](./docs/decisions/0101-turn-committed-v5-inherited-assistant-content-replay.md): replay of inherited companion text and tool batches, strict ledger validation, and unchanged legacy transcript bytes.
- [Bounded Independent Web Search](./docs/decisions/0102-bounded-independent-web-search.md): the Brave/Tavily Host tool, `network-read` approval, credential isolation, and bounded result normalization.
- [Provider-native Web Search](./docs/decisions/0103-provider-native-web-search.md): Profile-declared search dialects, default activation, source selection, and Provider/Host ownership boundaries.
- [OpenAI Responses Protocol and Provider-owned History](./docs/decisions/0104-openai-responses-protocol-and-provider-owned-history.md): Responses routing, semantic streaming, and durable replay of Provider-owned items.
- [Provider Search Resilience, Controls, and Observability](./docs/decisions/0105-provider-search-resilience-controls-and-observability.md): failed-action compatibility, citation degradation, process-local controls, redacted observation, and explicit fallback.
- [Bounded Fetch, Structured Read, and Controlled Transfer Tools](./docs/decisions/0106-bounded-fetch-structured-read-and-controlled-transfer-tools.md): public-page fetch, structured read-only observation, directory movement, controlled downloads, and the `network-write` boundary.
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
- [Claw-Code prompt study map](./docs/references/claw-code-prompts/README.md): read-only reference structure and Coquo-specific differences.
- [Harness-study](https://github.com/TsingFengIceberg/Harness-study): related Harness reading and learning notes.

## Current scope and next step

Coquo currently provides 35 ordinary bounded tools for workspace reads and writes, command verification, Git observation, web search and fetch, structured reads, controlled downloads, progressive MCP discovery, and declarative Skill loading, plus durable Task coordination tools. Named Provider Profiles, Session resume, context and compaction, PermissionGate and Action Audit, foreground multi-Stage Tasks, the terminal REPL, and offline Evals are integrated.

The project remains a local single-user CLI prototype. MCP supports confined stdio, Streamable HTTP, and extended capabilities; Skills currently provide bounded local packages, progressive discovery, context lifetime, and ToolSet restriction. An ordinary parent Agent can now use four model tools to delegate to at most four independent Children and observe, wait for, or cooperatively cancel them; each Child remains read-only, one-Turn, depth-one, and process-local, and its handoff returns as an untrusted ToolResult. Messaging, shared tasks, and Teams are not implemented; executable Skills, a marketplace, and browser automation are also deferred. Exact tool contracts, versions, compatibility rules, and security boundaries live in [Implemented Foundations and Design Evolution](./docs/implemented-foundations_en.md) and the [architecture decision records](./docs/decisions/).
