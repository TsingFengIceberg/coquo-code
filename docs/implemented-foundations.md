# 已实现 Foundation 与设计演进

> 本文集中保存 Coquo 已完成学习切片的实现说明。README 只保留主要命令和使用入口；每个切片的决策依据、边界与验证细节仍以 [`docs/decisions/`](./decisions/) 下的 ADR 为准。
>
> 中文 | [English](./implemented-foundations_en.md)

## 文档导航

- [Coquo 产品身份迁移](#coquo-产品身份迁移)
- [Canonical model system prompt](#canonical-model-system-prompt)
- [Foundation 3D：稳定 Profile Identity 与可恢复 Session](#foundation-3d稳定-profile-identity-与可恢复-session)
- [Foundation 3C：命名 Provider Profile 与真实多轮 REPL](#foundation-3c命名-provider-profile-与真实多轮-repl)
- [Foundation 3B：本地多 Provider 真实模型路径](#foundation-3b本地多-provider-真实模型路径)
- [Foundation 2B：离线 adapter-owned compatibility policy](#foundation-2b离线-adapter-owned-compatibility-policy)
- [Foundation 4A：Permission Policy Contract](#foundation-4apermission-policy-contract)
- [Foundation 4A Slice 3–4：Exact Action Identity与Durable Action Audit](#foundation-4a-slice-34exact-action-identity与durable-action-audit)
- [Foundation 4A Slice 5–9：Approval Coordination与Controlled `write_file`](#foundation-4a-slice-59approval-coordination与controlled-write_file)
- [Foundation 4A Slice 10：Action Audit Observability](#foundation-4a-slice-10action-audit-observability)
- [Foundation 4B Slice 0–3：Exact Edit Preparation、Execution与Authorization Composition](#foundation-4b-slice-03exact-edit-preparationexecution与authorization-composition)
- [Foundation 4B Slice 4：Model-visible Exact Edit Integration](#foundation-4b-slice-4model-visible-exact-edit-integration)
- [Foundation 4C Slice 0–3：Controlled Command Contract与Side-effect-free Preparation](#foundation-4c-slice-03controlled-command-contract与side-effect-free-preparation)
- [Foundation 4C Slice 4–6：Bounded Command Execution与Process-group Cleanup](#foundation-4c-slice-46bounded-command-execution与process-group-cleanup)
- [Foundation 4C Slice 7–9：Durable Model-visible Command Integration](#foundation-4c-slice-79durable-model-visible-command-integration)
- [Fail-closed Linux `run_command` Sandbox](#fail-closed-linux-run_command-sandbox)
- [Host Workbench Diagnostics 与 Prompt History Search](#host-workbench-diagnostics-与-prompt-history-search)
- [Host Policy 与 Tool Discoverability](#host-policy-与-tool-discoverability)
- [Foundation 5A：根 AGENTS.md 项目指令](#foundation-5a根-agentsmd-项目指令)
- [确定性离线 Host Eval 基线](#确定性离线-host-eval-基线)
- [Actual Coding Task Eval](#actual-coding-task-eval)
- [Durable Task Identity 与 Host Management](#durable-task-identity-与-host-management)
- [Durable Stage Lifecycle 与 Turn Evidence](#durable-stage-lifecycle-与-turn-evidence)
- [Foreground Task Stage Execution 与 Recovery](#foreground-task-stage-execution-与-recovery)
- [Task Planning、Acceptance、Budgets 与 Management](#task-planningacceptancebudgets-与-management)
- [Structured Task Acceptance 与 Independent Review](#structured-task-acceptance-与-independent-review)
- [Task Proposal Control Boundary](#task-proposal-control-boundary)
- [自然语言 Task 生命周期交接](#自然语言-task-生命周期交接)
- [可恢复的 Provider Tool 参数校验](#可恢复的-provider-tool-参数校验)
- [有界独立 Brave/Tavily 网页搜索](#有界独立-bravetavily-网页搜索)
- [Provider 原生网页搜索](#provider-原生网页搜索)
- [OpenAI Responses protocol 与 Provider-owned history](#openai-responses-protocol-与-provider-owned-history)
- [Provider Search Resilience、Controls 与 Observability](#provider-search-resiliencecontrols-与-observability)
- [Foundation 4D Slice 0–4：Controlled Single-directory Creation](#foundation-4d-slice-04controlled-single-directory-creation)
- [Foundation 4E Slice 0–9：Controlled No-overwrite File Move](#foundation-4e-slice-09controlled-no-overwrite-file-move)
- [Foundation 4F Slice 0–6：Controlled Regular-file Deletion](#foundation-4f-slice-06controlled-regular-file-deletion)
- [Foundation 4G Slice 0–6：Controlled Empty-directory Deletion](#foundation-4g-slice-06controlled-empty-directory-deletion)
- [工具批次 A：Bounded Workspace Navigation](#工具批次-abounded-workspace-navigation)
- [工具批次 B：Process-isolated Regex Grep](#工具批次-bprocess-isolated-regex-grep)
- [工具批次 C：Structured Exact Multi-edit Patch](#工具批次-cstructured-exact-multi-edit-patch)
- [Shared Six-call Tool Budget](#shared-six-call-tool-budget)
- [Live Redacted Tool Activity](#live-redacted-tool-activity)
- [Provider-neutral Assistant Tool Text Representation](#provider-neutral-assistant-tool-text-representation)
- [Provider Mixed-response Inbound Normalization](#provider-mixed-response-inbound-normalization)
- [`turn_committed` v3 Assistant Tool Text Persistence](#turn_committed-v3-assistant-tool-text-persistence)
- [Provider Mixed-response History Projection](#provider-mixed-response-history-projection)
- [AgentLoop 与 Terminal Assistant Tool Text Integration](#agentloop-与-terminal-assistant-tool-text-integration)
- [Provider Streaming 与 Terminal Failure Atomicity](#provider-streaming-与-terminal-failure-atomicity)
- [TTY Markdown Rendering](#tty-markdown-rendering)
- [Exact Bounded Informed Approval](#exact-bounded-informed-approval)
- [Sequential Tool-call Budget Hardening](#sequential-tool-call-budget-hardening)
- [Bounded Multi-tool Response Batches](#bounded-multi-tool-response-batches)
- [Structured Tool Outcome Ledger](#structured-tool-outcome-ledger)
- [Durable Tool Ledger Inspection](#durable-tool-ledger-inspection)
- [Runtime Context Meter 与 Provider Token Usage](#runtime-context-meter-与-provider-token-usage)
- [Context 与 Compaction Observability](#context-与-compaction-observability)
- [Provider Output-limit 与 Compaction Failure Diagnostics](#provider-output-limit-与-compaction-failure-diagnostics)
- [上游 Provider API 错误事实与安全展示](#上游-provider-api-错误事实与安全展示)
- [统一只读可观察性时间线](#统一只读可观察性时间线)
- [Process-local Runtime Output Budget Control](#process-local-runtime-output-budget-control)
- [Durable Session Provider Usage Audit](#durable-session-provider-usage-audit)
- [Bounded Read-only Git Change Observation](#bounded-read-only-git-change-observation)
- [Bounded Reachable Git History Observation](#bounded-reachable-git-history-observation)
- [Opt-in Bounded Live Tool Details](#opt-in-bounded-live-tool-details)
- [Trusted Command Result Observability](#trusted-command-result-observability)
- [Host Workbench Navigation 与 Failure Guidance](#host-workbench-navigation-与-failure-guidance)
- [Assistant Turn Execution Trace Grouping](#assistant-turn-execution-trace-grouping)
- [Durable Session Naming 与 Terminal Identity](#durable-session-naming-与-terminal-identity)
- [Session Lifecycle Management 与 Naming Diagnostics](#session-lifecycle-management-与-naming-diagnostics)
- [Pinned Sessions 与 Snapshot-based Quick Switching](#pinned-sessions-与-snapshot-based-quick-switching)
- [Read-only Session Inspection 与 Bounded Turn Preview](#read-only-session-inspection-与-bounded-turn-preview)
- [Session Search、Turn Navigation、Export、Fork 与 Repair](#session-searchturn-navigationexportfork-与-repair)
- [Foundation 1D：Bounded Literal Grep](#foundation-1dbounded-literal-grep-与-versioned-tool-arguments)
- [Foundation 1C：Bounded Workspace Glob](#foundation-1cbounded-workspace-glob)
- [Foundation 1B：确定性的受限 read_file 工具循环](#foundation-1b确定性的受限-read_file-工具循环)
- [Foundation 3H：Pre-turn Automatic Context Compaction](#foundation-3hpre-turn-automatic-context-compaction)
- [Foundation 3G：Target-aware Resume Prepare/Commit](#foundation-3gtarget-aware-resume-preparecommit)
- [Foundation 3F-2：Controlled Compact Transaction](#foundation-3f-2controlled-compact-transaction)
- [Provider-neutral Effective Context Snapshot 与 `/context`](#provider-neutral-effective-context-snapshot-与-context)
- [Target-aware runtime switch UX](#target-aware-runtime-switch-ux)
- [Target-specific request counting 与 per-invocation preflight](#target-specific-request-counting-与-per-invocation-preflight)
- [Provider-owned model context capability](#provider-owned-model-context-capability)
- [Frozen Declarative Preauthorization Hooks](#frozen-declarative-preauthorization-hooks)
- [Durable Hook Observation 与 Audit](#durable-hook-observation-与-audit)
- [Audited Pinned Local Hook Handlers](#audited-pinned-local-hook-handlers)
- [B6：Writable Team Roles、Host-owned Linked Worktree 与 Parent-only Integration](#b6writable-team-roleshost-owned-linked-worktree-与-parent-only-integration)
- [Task–Child–Team 统一编排桥接](#taskchildteam-统一编排桥接)
- [Shared Agent Runtime 与 Durable Child Run Foundation](#shared-agent-runtime-与-durable-child-run-foundation)
- [ADR 索引](#adr-索引)

## Coquo 产品身份迁移

项目在`0.1.0` Pre-Alpha阶段将公开身份从Leonervis Code一次性迁移为Coquo。Python distribution、import package、唯一CLI与module入口统一为`coquo`；workspace状态改为`.coquo/`，XDG配置与cache改为`coquo/`，产品自有环境变量使用`COQUO_`前缀。MCP client metadata、HTTP User-Agent、临时文件、终端标题和模型可见角色同步采用新身份；LEO图形替换为确定性的COQ字标。

迁移不提供旧CLI、旧import package或旧运行数据的自动兼容。Coquo不读取、不搬运也不删除`.leonervis-code/`及旧XDG目录；Git ignore、command sandbox和independent reviewer继续保护旧、新两个状态目录，避免遗留敏感数据因改名暴露。产品自有fingerprint domain改为`coquo-*`，因此旧Session、Task、Action、ToolSet、Skill、Hook和Effective Context identity不属于新runtime兼容合同。结构化record算法没有变化；model system prompt升级到v44，provider adapter contract升级到v45，builtin Tool Registry与source generation升级到6。历史ADR保持原名，完整决策见[ADR 0128](./decisions/0128-coquo-product-identity-migration.md)。

## Canonical model system prompt

Coquo 从 `src/coquo/system_prompt.py` 构建 provider-neutral `SystemPromptSnapshot`。Snapshot 包含显式版本、规范化文本和 domain-separated SHA-256 fingerprint；每个 user turn 开始时只构建一次，并在该 turn 的全部provider/tool continuation中固定不变：

```text
SystemPromptSnapshot + neutral conversation history
  -> Anthropic Messages: top-level system + messages
  -> OpenAI-compatible: one leading system role + messages
  -> Scripted fake: record the same request snapshot
```

Canonical model system prompt当前为version 22。它允许一个response携带属于整批的brief companion text，并说明Host会先完整验证最多8个有序calls，再逐个执行；每个user turn最多接纳32个工具请求和24次provider invocation，最后一次只允许文字。强制text-only收尾时，模型必须以最后一个真实Tool result中的`Host tool ledger:`计数为准；`unused_admission_slots`只表示未使用容量，`tool_requests_closed=true`表示即使尚有空位也不能继续调用。普通Agent仍不能主动compact。当前21个model-visible tools包含有界`git_status`、`git_diff`、`git_log`与`git_show`；PermissionGate、approval、Action Audit及各工具hard bounds继续由Host强制，多call response不获得并行执行许可。

它明确说明`run_command`必须经过Linux bubblewrap与seccomp沙箱，同时不声称具备recursive copy/delete、ignore-aware或indexed search、fuzzy/free-form patch、non-empty directory delete、directory move、recursive mkdir、shell source string、interactive PTY、网络allowlist、资源配额、主动compact、项目指令加载或多 Agent 能力。Prompt指令也不替代Host对workspace、symlink、编码、大小、exact-state conflict、timeout/process cleanup、causality、audit、sandbox和durability的硬约束。

System prompt 不属于 `ConversationItem`，所以 `/history`、`ProjectSession.history` 和 append-only Session JSONL 只保存真实 user/assistant/tool 因果链。恢复旧 Session 后，新 turn 使用当前 binary 的 canonical prompt；schema-v2/v3 compact checkpoint只保存compact prompt、summary-framing与trigger provenance，不把正常system prompt写进conversation history。

这里的 **model system prompt** 与终端中的`›`输入标记和`model · workspace`状态栏是两个不同界面：前者是模型可见契约，后者只是人类终端交互与状态提示。

详细决策见 [0012：第一版 canonical model system prompt](./decisions/0012-first-canonical-model-system-prompt.md)，Claw-Code prompt 结构学习入口见 [references/claw-code-prompts](./references/claw-code-prompts/README.md)。

## Foundation 3D：稳定 Profile Identity 与可恢复 Session

Profile registry schema v3 使用不可变 UUID 作为引用身份，名称只作为可读、可修改的别名；revision 用于更新冲突检查。Schema v3 还增加可选的 exact-model `context_window_tokens` override。

旧 schema v1 profile 会由原始名称确定性映射到 UUID。Reader 支持 user/project v1、v2、v3 混合状态，写操作只升级实际写入的文件：

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

每次 `prompt` 或 REPL 会创建或打开：

```text
<workspace>/.coquo/sessions/<workspace-fingerprint>/<session-id>.jsonl
```

Session 使用 append-only JSONL。成功 turn 的 user message、tool use/result 和最终 assistant text 会作为一条完整 commit record 写入并 fsync，成功后才更新内存历史。每个打开的 Session 持有独占 writer lock。

损坏的中间 record、未知 schema 和错误 tool pairing 都 fail closed；只有进程崩溃形成的无换行不完整尾部可以受控截断，并追加 recovery record。

```bash
uv run coquo prompt "第一轮"
uv run coquo session list
uv run coquo session show latest
uv run coquo --resume latest prompt "继续上一轮"
uv run coquo -C ../another-workspace --resume latest
```

裸启动会创建新 Session，`--resume latest` 会继续该 workspace 的 latest 指针。REPL 中，`/session new` 保留当前 runtime provider 并开始空白历史，`/resume <id>` 切换到已有历史。列表中的 `[current]` 表示下一条 REPL prompt 的写入目标，`[latest]` 表示 `latest.json` 当前指向；`open/closed` 是 transcript 生命周期记录，不代表当前锁状态，closed Session 仍可恢复。

Session 与 runtime provider 解耦。Transcript 记录每个历史 turn 当时实际使用的 profile ID/revision、provider/protocol、model、endpoint 和非敏感 fingerprint，仅供审计。恢复后真正工作的 provider 继续由本次 `--profile`/`--model`、workspace active、user active 或 fake fallback 决定；runtime 不按历史 binding 重建 client，也不会因 profile 后来改名、修改或删除而阻止恢复。

把旧历史发送给新的当前 provider 属于显式运行选择。若当前 adapter 拒绝这段历史，失败 turn 不会提交。

本地 Session 可能包含用户输入、模型回答、源码片段和工具结果，属于敏感运行状态；`.coquo/` 不应提交、同步或公开。系统保证已知配置 credential value 不作为 binding 写入，但无法通用识别用户文本或被读取文件中自行包含的未知 secret。

`ProjectSession` 对外提供 `session_id`、`transcript_path`、`session_info()`、`list_sessions()`、`new_session()`、`switch_session()` 和 `resume=`。Session 切换只替换 durable history，保持当前 provider client。

详细决策见 [0010：稳定 Profile Identity 与可恢复 Session](./decisions/0010-foundation-3d-stable-profile-identity-and-durable-sessions.md)。

## Foundation 3C：命名 Provider Profile 与真实多轮 REPL

Profile 定义保存在：

```text
${XDG_CONFIG_HOME:-~/.config}/coquo/providers.json
```

Workspace 只在 `.coquo/provider.json` 保存 active profile ID。两个 JSON 都不保存 key value；workspace 目录是本地运行状态，应加入目标项目的 `.gitignore`。

```bash
# 内置 provider：protocol、默认 endpoint 与默认 credential env 由 catalog 提供
uv run coquo provider add work-openai \
  --provider openai \
  --model gpt-5

# 受控 custom OpenAI-compatible endpoint：只保存 key 的环境变量名
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

选择优先级为：显式 `--profile` → 显式 direct `--model` → workspace active → user active → fake/offline。`--profile NAME --model MODEL` 在该 profile endpoint 上使用当前进程的 model override，不改写 profile：

```bash
uv run coquo --profile work-openai --model gpt-5-mini \
  prompt "解释这个 workspace"
uv run coquo --profile work-openai
```

`provider use` 和 REPL `/provider use` 都先解析 route、检查 credential、构造候选 SDK client，再写 active 配置并交换当前 client；失败时旧 active 和旧 client 不变。`/model` 同样只在两个 turn 之间原子切换。

完整 neutral history 与 tool use/result 配对跨 provider 保留。新 provider 若拒绝旧历史，失败 turn 不会提交。

项目其他模块可使用公开 facade：

```python
from pathlib import Path
from coquo import ProjectSession

with ProjectSession.open(Path.cwd(), profile="work-openai") as session:
    first = session.prompt("先解释 README")
    session.set_model("gpt-5-mini")
    second = session.prompt("继续")
```

`ProjectSession` 还提供 `list_profiles()`、`use_profile()`、`use_profile_id()`、`clear_active()`、`status()`、`history` 和 `turns`。

详细决策见 [0009：命名 Provider Profile 与常驻 Runtime](./decisions/0009-foundation-3c-named-provider-profiles-and-runtime-manager.md)。

## Foundation 3B：本地多 Provider 真实模型路径

提供全局 `--model` 时，`prompt` 通过统一 resolver/factory 选择真实 adapter：

```bash
export ANTHROPIC_API_KEY='...'
uv run coquo --model anthropic/claude-opus-4-8 \
  prompt "解释这个 workspace"

export OPENAI_API_KEY='...'
uv run coquo --model openai/gpt-5 \
  prompt "解释这个 workspace"

export XAI_API_KEY='...'
uv run coquo --model xai/grok-3 \
  prompt "解释这个 workspace"

export DASHSCOPE_API_KEY='...'
uv run coquo --model dashscope/qwen-plus \
  prompt "解释这个 workspace"

uv run coquo --model ollama/qwen3:8b \
  prompt "解释这个 workspace"

export OPENROUTER_API_KEY='...'
uv run coquo --model openrouter/anthropic/claude-opus-4-8 \
  prompt "解释这个 workspace"
```

Anthropic 路径使用官方 `anthropic` SDK；其他内置路径复用官方 `openai` SDK 的 Chat Completions wire adapter。两个 SDK 都是同步非流式调用并固定 `max_retries=0`。

Adapter当前声明固定顺序的`read_file(path)`、`glob(pattern)`与`grep(query, include)` schema。本地三个Tool共同强制workspace、UTF-8、files-only/no-symlink与bounded output/read约束，并共享每turn预算。

也可显式调用临时 OpenAI-compatible endpoint，不持久化 provider 或 key：

```bash
export VENDOR_API_KEY='...'
uv run coquo \
  --model vendor/model \
  --provider-protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY \
  prompt "解释这个 workspace"
```

显式 provider namespace 优先。只有已登记的 `claude-*`、`gpt-*`、`grok-*`、`qwen-*`、`kimi-*` bare family 会被确定性识别；未知 bare model 不依据现有 credential 猜测。

Route 与 adapter config 不保存 secret value；key 只在 factory 构造所选 SDK client 时读取。当前不读取 `.env`、OAuth 或 keyring，也不实现 streaming、自动 retry/backoff、fallback execution、request token preflight、compact、并行工具或跨 workspace Session 恢复。

真实 route 可在不构造 client、不访问网络的情况下预览：

```bash
uv run coquo --model openai/gpt-5 route
```

默认 fake fallback 保持不变；若 workspace/user 已有 active profile，未带显式 selector 的 `prompt` 与裸 REPL 会使用该真实 profile：

```bash
uv run coquo provider clear --scope project
uv run coquo provider clear --scope user
uv run coquo prompt "Hello"   # 无 active 时 fake，不联网
uv run coquo                   # 无 active 时 fake REPL，不联网
```

详细决策见 [0007：Anthropic 非流式 Adapter](./decisions/0007-foundation-3a-anthropic-non-streaming-adapter.md) 与 [0008：本地多 Provider Runtime](./decisions/0008-foundation-3b-local-multi-provider-runtime.md)。真实 smoke test 只应在用户明确愿意使用自己的 credential、endpoint 和 API 费用时手动运行。

## Foundation 2B：离线 adapter-owned compatibility policy

`route` 是确定性的 control-plane 与 adapter-policy 边界诊断入口：

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

Route resolver 负责**硬**准入规则：有效 provider/model 选择、enabled 状态、所需 tool-use/streaming capability、canonical option 类型与范围、fallback 有效性，以及 Harness-owned field 保护。

选定 adapter 负责 provider-native wire name 和有文档依据的**软**兼容行为。Fake `beta` model 用于证明这种区别：请求的 `temperature` 会作为已知 fixed-sampling incompatibility 被省略，`route` 显示该决定，而不是静默改变请求或错误 hard fail。

Provider-specific extension 当前只有受控 Python API 路径；它不能覆盖 `model`、messages、tools、streaming、token-limit fields 或 adapter-generated parameter fields。CLI 暂不接受任意 JSON body override。

`route` 的 Foundation 2B 子命令形式完全离线：不构造 provider client、不读取环境变量、不访问网络，也不显示 credential reference/value。带全局 `--model` 的 route 使用真实 resolver 展示 provider、protocol、wire model、base URL 来源和 `configured/missing/not required` 状态，但仍不构造 client 或发送请求。成功 preview 不代表远端 provider 必然接受请求。

详细决策见 [0005：Provider-neutral Model Routing](./decisions/0005-foundation-2a-provider-neutral-model-routing.md) 与 [0006：Adapter-owned Compatibility Policy](./decisions/0006-foundation-2b-adapter-owned-compatibility-policy.md)。

## Foundation 4A：Permission Policy Contract

在暴露写工具前，Host先建立无状态、无I/O的纯`PermissionGate` policy kernel。能力上限固定为`read-only | workspace-write | danger-full-access`，交互模式固定为`ask | auto`，两者正交；结果固定为`allow | ask | deny`并携带stable machine-readable reason。Policy action class是`workspace-read | workspace-create | workspace-overwrite | dangerous | unknown`，其中unknown在所有配置下fail closed。

`read_file`、`glob`与`grep`归类为`workspace-read`，在所有mode/approval组合下allow且不要求terminal confirmation。Workspace create/overwrite在`read-only`下deny，在更高能力模式下由`ask | auto`决定ask或allow；dangerous action只有`danger-full-access`可进入ask/allow。PermissionGate不读取CLI、Session、provider、credential或filesystem，不执行Tool，不创建approval token，也不能绕过workspace、symlink、size、timeout、conflict、causality或durability hard bounds。

作为该边界的前置修复，`read_file`拒绝最终和中间的所有symlink component，包括指向workspace内部的link与broken link；普通nested UTF-8读取和32 KiB bound保持不变。当前local single-user v0仍不声称消除检查与open之间的hostile concurrent TOCTOU。

该policy slice当时没有model-visible变化，因此canonical system prompt保持v4、adapter contract保持v5，现有Session/context representation不升级。后续Slice 3–9在不改变纯gate职责的前提下接入exact identity、audit、runtime approval与controlled write。

完整决策见[0022：Foundation 4A Permission Policy Contract](./decisions/0022-foundation-4a-permission-policy-contract.md)。

## Foundation 4A Slice 3–4：Exact Action Identity与Durable Action Audit

PermissionGate之后，Host为一个resolved action建立不可替换的`ActionIdentity` v1。Identity包含Host生成的request UUID、provider `tool_use_id`、exact tool name、immutable `ToolArguments`、trusted action classification、workspace fingerprint、prepared-turn lease与execution precondition；sorted compact JSON经domain-separated SHA-256形成`act-v1-...` digest。Lease固定Session ID、不可重建的lease UUID、runtime generation与`ctx-v1 | ctx-v2` Effective Context ID，因此resume、runtime switch或prepared-turn replacement不能继续使用旧approval。

Precondition采用closed identity：`none | path-absent | expected-state-sha256`。Single-use `ApprovalGrant`只可为PermissionGate确定性返回的`ask`签发；它是Host内存对象，不是model-visible bearer token。消费必须匹配完整identity、lease与precondition，并通过lock保证并发消费最多一次成功；mismatch、stale lease、stale precondition与replay都有stable rejection code。

Session新增五种append-only schema-v1 audit records：`action_requested`、`permission_decided`、`approval_resolved`、`action_execution_started`与`action_execution_finished`。Replay重算policy、验证exact references与authorization，并重建lifecycle；后续write slice把terminal finish扩展为`succeeded | failed | partial`。当前sequential Harness最多有一个unresolved action；`turn_committed`、`runtime_changed`、`context_compacted`和clean `session_closed`不得跨越它。Action audit保存在`ReplayState.action_audits`，但永不进入full/effective model history，也不被compaction删除或总结。

`action_execution_started`使用append+fsync作为副作用前durable barrier。若resume或`turn_failed`遇到尚未start的action，replay派生`abandoned`；若已有durable start但没有finish，则派生`outcome-unknown`。Executor返回后如果finish audit失败，typed `ActionOutcomeAuditError`保留known outcome与storage cause；Host不得误报未执行或重试副作用补审计。

该Slice 3–4在落地时仍是Host-only contract，因此当时system prompt v4、三只读工具顺序与adapter contract v5保持不变；Slice 5–9随后完成runtime integration。完整决策见[0023：Foundation 4A Exact Action Identity、Single-use Approval Grant与Durable Action Audit](./decisions/0023-foundation-4a-exact-action-identity-and-durable-audit.md)。

## Foundation 4A Slice 5–9：Approval Coordination与Controlled `write_file`

Central `ActionCoordinator`现在严格编排`action_requested -> permission_decided -> optional human resolution -> durable action_execution_started -> executor -> action_execution_finished`。Deny不询问、不执行；ask的accept签发并消费exact single-use grant，reject/cancel返回structured tool error；executor只有在start record append+fsync成功后才能产生副作用。普通executor失败被安全归因，但副作用后final audit失败必须传播，不能伪装rollback或重试。

`PreparedAgentTurn`在automatic compaction完成后绑定一个`ActionLease`，同一turn的provider continuation固定同一Session、runtime generation、Effective Context与system prompt snapshot。ProjectSession lock覆盖完整provider/approval turn，所以approval期间不能切换runtime、resume或替换context。Stale auto identity或accepted grant会终止turn并追加`turn_failed`，而不是作为普通ToolResult继续调用provider。

CLI新增`--permission-mode read-only|workspace-write|danger-full-access`和`--approval ask|auto`，默认`read-only + ask`。One-shot ask安全取消且不读取stdin；REPL只展示trusted action class、相对path与UTF-8 byte count，支持accept/reject/cancel以及EOF/Ctrl-C fail-safe。Capability ceiling与interaction mode保持正交，auto approval不能绕过任何executor hard bound。

Model-visible顺序现在固定为`read_file, glob, grep, write_file`，共享每个user turn最多三次resolved/executed calls；第四次请求只得到limit result且不创建action lifecycle。`write_file(path, content)`只接受一个portable workspace-relative path与完整UTF-8 content，模型不能传overwrite flag、expected hash、mkdir、delete、patch或approval字段。Host观察真实目标：absent target分类为`workspace-create + path-absent`，现有UTF-8 regular file分类为`workspace-overwrite + expected-state-sha256`。Malformed或hard-rejected write在permission eligibility前返回error ToolResult、消耗预算但不产生action audit。

Path拒绝absolute、Windows drive、backslash、`.`/`..`、空component、重复/尾随`/`和所有intermediate/final symlink；parent必须已存在，不自动mkdir。Content同时限制4096 characters与4096 UTF-8 bytes；overwrite source最多1 MiB、必须是UTF-8普通文件，并绑定digest/device/inode/mode。Create使用same-directory temp、file fsync、hard-link到仍不存在的target、cleanup与parent fsync；overwrite使用preserved-mode temp、file fsync、exact digest/inode recheck、`os.replace`与parent fsync。成功返回包含`bytes_written`、`operation`和relative `path`的deterministic JSON。

目标已经可见但temporary cleanup或directory fsync失败时，result与audit使用`partial`，明确要求inspect workspace且禁止自动retry；它不同于缺少finish record的`outcome-unknown`。Provider continuation或turn commit在写后失败时，已发生的file effect和action audit保留，candidate turn不提交并记录`turn_failed`。

这一model-visible变化把canonical system prompt升级到v5、adapter contract升级到v6；Anthropic与OpenAI-compatible ordinary count/create projection暴露相同四工具closed schema/order，compact-summary仍无tools，parallel calls仍关闭。`ToolArguments`保持v1，new `turn_committed`保持schema v2，action audit records保持schema v1，`context_compacted`继续v2/v3 replay，Effective Context representation继续`ctx-v1`/`ctx-v2`；新的prompt/tool snapshot只自然改变current-binary context ID，不重写历史checkpoint。

完整决策见[0024：Foundation 4A Approval Coordination、Runtime Integration与Controlled `write_file`](./decisions/0024-foundation-4a-approval-coordination-and-controlled-write.md)。Bash、patch/edit、delete、mkdir、parallel actions与portable full filesystem CAS仍明确不在当前范围。

## Foundation 4A Slice 10：Action Audit Observability

此前durable action audit只存在于Host transcript replay state中。现在standalone CLI可用`session actions [latest|id] [--limit N]`查看指定Session，REPL可用`/actions [count]`查看当前Session。默认显示最近20条，显式数量范围为1到100；截断后仍按原始时间顺序展示，空状态也有明确结果。

输出只保留人类审计所需摘要：request sequence、tool、trusted action class、workspace-relative path、permission decision/reason、approval outcome和derived final status/result code。完整write content、executor message、absolute workspace、request/tool-use/grant/lease ID、digest、workspace fingerprint与precondition hash都不会显示；path和persisted result code会转义control characters，避免破坏终端结构。

Standalone路径只验证已存在的Session root并以`allow_repair=False`严格重放，不创建目录、不拿writer lease、不修复tail、不更新latest pointer或追加record。REPL路径在当前Session lock下读取已重放state，不调用provider，也不进入模型history。损坏或unsafe transcript继续fail closed。

这是Host-only observability变化。Canonical system prompt审阅后保持v5，四工具顺序与共享三次预算不变，adapter contract保持v6；ToolArguments v1、`turn_committed` schema v2、action audit schema v1、`context_compacted` v2/v3 replay和`ctx-v1`/`ctx-v2` representation均不变。完整决策见[0025：Foundation 4A Action Audit Observability](./decisions/0025-foundation-4a-action-audit-observability.md)。JSON export、filters、repair/retry、完整forensic dump与remote audit仍不在范围。

## Foundation 4B Slice 0–3：Exact Edit Preparation、Execution与Authorization Composition

在Slice 0–3阶段，Host先建立了一个尚未model-visible的内部`edit_file(path, old_text, new_text)`引擎。它只接受已存在、最多1 MiB、strict UTF-8且无symlink的普通文件；`old_text`必须非空并恰好出现一次，重叠出现也计为多匹配，`new_text`可为空。Old/new各自限制为4096 characters和4096 UTF-8 bytes，结果仍不得超过1 MiB。Prepare只读取、验证并构造完整candidate bytes，不创建temporary、不修改目标，也不产生action audit。

执行复用controlled overwrite的same-directory temporary、mode preservation、file fsync、digest/device/inode复查、atomic `os.replace`和parent-directory fsync。Stale或replace前失败返回`edit_not_applied`且不改目标；replace已可见但directory durability未知时如实返回`partial / edited_durability_unknown`。成功结果包含result byte count、`operation: edited`、relative path和一次replacement。

Exact edit固定映射到现有`workspace-overwrite`，继续使用原始canonical arguments、`expected-state-sha256` precondition、prepared-turn lease、single-use approval grant与append-only Action Audit。独立组合测试已证明read-only deny、ask accept/reject/cancel、auto allow、approval等待期间source变化后的stale rejection，以及audit strict replay和CLI redaction；Action Audit只显示path，不显示old/new文本。

Slice 0–3故意不修改tool catalog、provider projection、AgentLoop或ProjectSession dispatch。Canonical system prompt保持v5，adapter contract保持v6，model-visible顺序仍为`read_file, glob, grep, write_file`并共享三次预算；ToolArguments v1、`turn_committed` schema v2、action audit schema v1、`context_compacted` v2/v3 replay与`ctx-v1`/`ctx-v2` representation均不变。这一步把schema/order、provider parity、system prompt、Effective Context identity goldens和runtime dispatch留给后续Slice 4统一接通；Slice 4现已完成该接入。完整决策见[0026：Foundation 4B Exact Edit Preparation、Execution与Authorization Composition](./decisions/0026-foundation-4b-exact-edit-preparation-execution-and-authorization.md)。

## Foundation 4B Slice 4：Model-visible Exact Edit Integration

Slice 4把已验证的内部exact-edit engine完整接入普通Agent路径。Canonical工具顺序现在固定为`read_file, glob, grep, write_file, edit_file`，五者继续共享每个user turn最多三次顺序execution预算。公开schema只允许`path`、`old_text`和`new_text`三个string字段：`path`与`old_text`不能为空，`old_text`可以是纯空白，`new_text`可以为空以做精确删除；catalog继续对每个string施加4096 characters和4096 UTF-8 bytes边界。

Anthropic与OpenAI-compatible普通count/create projection现在按相同顺序暴露第五个closed schema并还原为同一immutable `ToolArguments`；compact-summary请求仍不携带tools，parallel calls仍关闭。Provider adapter contract升级为v7。Canonical system prompt升级为v6，明确区分`edit_file`的小型唯一锚定修改与`write_file`的create/完整内容替换，并要求模型服从permission、approval、stale-state和visible-partial结果。

`ProjectSession`在permission eligibility前prepare edit，因此missing target、零匹配、多匹配、no-op、symlink、invalid UTF-8或size错误只产生structured Tool error，不创建Action Audit。合法edit固定映射到`workspace-overwrite`，复用source SHA-256 precondition、prepared-turn lease、single-use approval grant、durable execution start、原子replace与known-outcome audit。成功记录`succeeded / edited`；replace前失败记录`failed / edit_not_applied`；replace已可见但directory durability未知记录`partial / edited_durability_unknown`。

这次tool/prompt snapshot变化会自然改变current-binary Effective Context ID，但representation仍为`ctx-v1` full history与`ctx-v2` compacted context。ToolArguments保持v1，new `turn_committed`保持schema v2，Action Audit records保持schema v1，`context_compacted`继续支持v2/v3 replay；旧transcript和checkpoint不重写。完整决策见[0027：Foundation 4B Model-visible Exact Edit Integration](./decisions/0027-foundation-4b-model-visible-exact-edit-integration.md)。在Foundation 4B阶段，regex/fuzzy/hunk/multi-replacement patch、create/delete/rename/mkdir、多文件事务与Bash/test仍明确不在范围；后续Foundation 4C已单独加入受控command执行。

## Foundation 4C Slice 0–3：Controlled Command Contract与Side-effect-free Preparation

这一阶段建立了尚未model-visible的内部`run_command(argv, cwd, timeout_seconds)`准备边界，主要用途是未来运行测试、lint、format check与build verification。合同故意不命名为`run_test`：测试文件本身仍是本地程序，可以访问workspace外、credential和network，也可以修改多个文件或启动子进程；没有OS sandbox时不能暗示它只读、安全或可回滚。

请求必须恰好包含argv数组、workspace-relative cwd与timeout。Argv为1–64个UTF-8 strings，首项是nonblank executable，每项最多1024 characters/bytes，aggregate最多8192 bytes；NUL拒绝，而pipe、wildcard或command-substitution字符只作为literal argument保留。Cwd只能是`.`或最多64 components、4096 characters/bytes的portable `/`路径，prepare以`lstat`检查workspace root并逐段拒绝missing、file及任何symlink。Timeout固定为1–300秒，future stdout/stderr cap各32 KiB；模型不能提供environment override或提高这些Host bounds；future executor只复制closed non-secret-oriented环境allowlist，不自动转发provider API key。

`RunCommandTool.prepare()`只读取immutable ToolArguments并验证workspace root/cwd，不解析PATH、不查找executable、不写Session/Action Audit，也不导入或启动subprocess。Frozen `PreparedRunCommand`保存exact request、argv tuple、canonical cwd、timeout、`dangerous`分类与`ActionPrecondition.none()`；revalidation可在未来spawn前再次拒绝workspace root/cwd变成missing、file或symlink。

Command复用既有`PermissionAction.DANGEROUS`而不新增会错误暗示workspace containment的`workspace-execute`。因此read-only与workspace-write均deny，只有danger-full-access按ask/auto产生approval或allow。现有ActionIdentity v1已经把canonical argv/cwd/timeout、workspace fingerprint、prepared-turn lease与Effective Context绑定；任一request、runtime generation或context变化都会改变digest。它不尝试hash executable、测试代码或整个project tree，也不伪装成portable filesystem CAS。

Slices 0–3当时故意不增加executor、process group、CLI presentation、durable command audit、catalog、provider projection或AgentLoop/ProjectSession dispatch，所以该阶段普通prompt还不能请求或运行command。Canonical model system prompt保持v6，provider adapter contract保持v7，五个model-visible tools及共享三次预算不变；ToolArguments v1、ActionIdentity v1、Session与Action Audit schema、`context_compacted` v2/v3 replay及`ctx-v1`/`ctx-v2` representation均不变。后续Slice 4–9现已完成执行、清理、持久协调与模型接入；本阶段原始边界见[0028：Foundation 4C Controlled Command Contract与Side-effect-free Preparation](./decisions/0028-foundation-4c-controlled-command-contract-and-preparation.md)。


## Foundation 4C Slice 4–6：Bounded Command Execution与Process-group Cleanup

Host executor现在使用`subprocess.Popen`直接执行prepared argv，固定`shell=False`、`stdin=DEVNULL`并为每次命令建立独立process session/group。Coquo不解析pipe、redirect、wildcard、variable expansion或command substitution；如果获批的executable自身是shell，它仍可自行解释参数，因此direct argv并不等于sandbox。Executor在紧贴spawn的边界再次逐段检查workspace root/cwd，失效时以`command_cwd_invalid`拒绝启动；普通path API无法完全消除剩余local TOCTOU窗口，因此不声称hostile-concurrency安全。

Command只继承closed Host environment allowlist，并按实际cwd覆盖`PWD`；provider API key与任意project环境变量不自动转发。Stdout和stderr由独立reader持续drain到EOF，各只保留前32 KiB，同时记录captured/total bytes与truncated。合法UTF-8以text返回，其他bytes以base64返回，避免locale-dependent decode和pipe buffer deadlock。

Timeout或`KeyboardInterrupt`会触发有界TERM→KILL process-group cleanup；若主进程已正常退出但background child仍持有pipes，同一路径也会清理残留group，避免普通返回无限等待。Success、nonzero、invalid cwd、missing executable、signal、timeout、cancel与cleanup incomplete均有稳定JSON status/result code。Timeout、cancel、signal或无法确认清理完整时记录partial，即使主进程已返回nonzero，因为进程可能已经产生不可回滚副作用；系统不自动retry，也不声称filesystem、network、credential或resource isolation。

Executor仍不拥有permission、Session或CLI。完整执行边界见[0029：Foundation 4C Bounded Command Execution与Process-group Cleanup](./decisions/0029-foundation-4c-bounded-command-execution-and-process-cleanup.md)。

## Foundation 4C Slice 7–9：Durable Model-visible Command Integration

`run_command`现已接入ProjectSession与ActionCoordinator。Exact request先prepare并固定为`dangerous`，随后按`action_requested → permission_decided → optional approval_resolved → revalidate/grant consume → action_execution_started → spawn/execute → action_execution_finished`执行。只有`action_execution_started` append+fsync成功后才允许`Popen`；其前失败绝不spawn，其后finish持久化失败则turn不提交，replay将started-without-finish诚实导出为`outcome-unknown`。

REPL的`approval=ask`显示argv、relative cwd与timeout；one-shot ask继续安全cancel且不读stdin。`session actions`和`/actions`只显示executable、额外参数数量、cwd、timeout、permission/approval及lifecycle/result code，不在普通审计摘要中回显完整argv。Session仍保存exact ActionIdentity以验证request、workspace fingerprint、prepared-turn lease、runtime generation与Effective Context绑定。

Canonical tool order现为`read_file, glob, grep, write_file, edit_file, run_command`，六者继续共享每个user turn最多三次顺序执行。Anthropic与OpenAI-compatible ordinary count/create投影相同第六个closed schema，compact-summary请求仍无tools，parallel calls仍关闭。Provider adapter contract升级到v8；canonical system prompt升级到v7，empty full-context golden更新为`ctx-v1-e6b5274ea57642fd614842c58dfa74def0b6f0c1319b2c312b7c54d61b834ce3`。

ToolArguments保持v1，new `turn_committed`保持schema v2，ActionIdentity与Action Audit保持v1，普通Session records保持v1，`context_compacted`继续v2/v3 replay，Effective Context representation继续`ctx-v1`/`ctx-v2`；旧transcript/checkpoint不重写，resume和compaction也不会重跑command。完整决策见[0030：Foundation 4C Durable Model-visible Command Integration](./decisions/0030-foundation-4c-durable-model-visible-command-integration.md)。

## Fail-closed Linux `run_command` Sandbox

生产`run_command`现在固定通过`/usr/bin/bwrap`执行。Host root以只读方式呈现，当前workspace在原绝对路径重新挂载为读写；`/tmp`为私有tmpfs，`/dev`最小化，Host `/proc`、`/sys`和`/run`被空的私有视图遮蔽。Command可见的HOME、TMP、UV cache与XDG路径指向私有`/tmp`，原HOME存在时还会遮蔽已知credential、Git、cloud、container及Agent状态路径。Workspace仍可能被命令不可逆修改，沙箱不提供rollback、resource quota或hostile-concurrency transaction。

本机无法可靠创建network namespace，因此Host用`libseccomp.so.2`生成BPF，在bubblewrap完成mount/namespace setup后禁止`socket`、可用时的`socketcall`及`io_uring_setup`。这同时拒绝Internet与Unix-domain socket创建。Bubblewrap必须通过私有`--info-fd`提供activation evidence，`--block-fd`会在Host验证并放行前阻止请求argv启动；Linux、固定bwrap、libseccomp、filter、spawn或activation任一步不可用都返回`command_sandbox_unavailable`，绝不把原argv降级为Host直接执行。

依赖readiness现在还会以closed environment、2秒timeout和64 KiB输出上限读取固定`/usr/bin/bwrap --help`，并要求ADR 0080使用的`--disable-userns`、`--block-fd`、`--info-fd`与`--seccomp`全部存在。缺少任何必需能力的旧版bwrap在执行用户命令前即报告unavailable；生产launch参数不降级，真实沙箱测试仅在同一生产activation probe可用时运行。这个Host-only修正不改变tool、permission、Session、prompt、provider或Effective Context合同。见[0137：Command Sandbox Capability Readiness](./decisions/0137-command-sandbox-capability-readiness.md)。

PermissionGate保持正交：`run_command`仍只在`danger-full-access`范围内按ask/auto继续，approval不关闭沙箱。Direct argv、`shell=False`、closed stdin/environment、1–300秒timeout、stdout/stderr各32 KiB retention、持续drain、取消与TERM到KILL process-group cleanup均保持。工具名、顺序、schema、provider projection、adapter contract v25、ToolArguments v1、ActionIdentity v1、Action Audit与Session schema均不变；模型可见保证使system prompt升级到v22，current empty full-context identity更新为`ctx-v3-a28664ae5f5143fac7e7b5936d78cb59c31643eb1a07eb7f41d73167625d67f8`。完整决策见[0080：Fail-closed Linux Command Sandbox](./decisions/0080-fail-closed-linux-command-sandbox.md)。

## Host Workbench Diagnostics 与 Prompt History Search

`/status`现在组合一个纯本地`ProjectStatus`快照，显示当前Session、permission/approval、最近一次已观察context压力、三层tool预算、沙箱依赖和既有脱敏runtime信息。它不发起provider count/generation、不执行用户命令、不修改Session或Action Audit。`/sandbox check`另行使用生产`RunCommandTool`的同一activation路径执行固定`/usr/bin/true`，验证Linux、固定bubblewrap、seccomp filter和activation gate；probe没有用户argv、模型调用或持久审计，也不授权后续命令，失败仍保持无Host fallback。

既有`/tools`继续表示持久工具账本；新增`/tools catalog`按规范21-tool顺序显示权限分类与当前mode可用性，避免破坏旧命令语义。`/actions last`只复用严格replay后的Action Audit视图选择最新一条。Command approval修正为显示真实的只读Host、可写workspace与socket拒绝边界；可信`run_command` result code会附加保守`Next:`建议，timeout、signal、cancel或cleanup uncertainty均不会触发自动retry或rollback声称。

常驻TTY为Ctrl-R接入独立prompt_toolkit SearchToolbar，对当前Session最近1000条已提交user prompt做大小写不敏感反向搜索。搜索接受后只恢复一份可编辑draft，仍需再次Enter才提交；Session切换沿用既有history replacement，不跨Session搜索。已有`/clear`获得真实frontend回归覆盖，确认只写terminal reset sequence，不调用模型或修改Session/history/transcript。该切片只改变Host workbench与输入呈现：canonical system prompt保持v22、adapter contract保持v25、21-tool schema/order、Effective Context identity及全部Session/Action Audit schema不变。详见[0081：Host Workbench Diagnostics and Prompt History Search](./decisions/0081-host-workbench-diagnostics-and-prompt-history-search.md)。

## Host Policy 与 Tool Discoverability

`/tools catalog <tool-name>`从当前规范`TOOL_CATALOG`读取顺序和input schema，显示参数形状、required状态、权限分类、当前policy可用性与一条Host维护的主要硬边界摘要；它不调用工具，也不意味着跳过执行时的workspace、symlink、size、conflict、timeout、output或durability复查。原有无参数catalog和持久`/tools`账本保持兼容。

`/permissions`用真实纯函数`PermissionGate`显示当前六类action的`allow | ask | deny`与稳定reason，同时单独报告沙箱依赖状态，避免把“policy允许”误解为“执行一定可用”；可选mode和approval只计算“未应用的policy preview”，不会改变进程配置、授权任何action或写Session。`/help policy`集中说明permission、approval和command sandbox的正交关系。补全候选扩展到规范tool names、permission modes、Action Audit status/tool筛选值和常用子命令；未知top-level、provider、session或tool name只在单个高相似候选时显示`Did you mean`，从不改写输入或自动dispatch。完整性测试要求每个规范工具都有hard-bound摘要，真实ProjectSession回归还证明这些发现性命令不调用provider、不改变transcript、history、usage、Action Audit或Session metadata。该Host-only切片保持system prompt v22、adapter contract v25、21-tool schema/order、Effective Context identity和全部持久schema不变。详见[0082：Host Policy and Tool Discoverability](./decisions/0082-host-policy-and-tool-discoverability.md)。

## Foundation 5A：根 `AGENTS.md` 项目指令

Coquo现在只识别workspace根目录的`AGENTS.md`。Missing表示没有项目指令；现有entry必须经root directory descriptor以no-follow方式打开，并保持non-symlink regular-file identity。内容使用strict UTF-8，允许空文件并保留原始LF或CRLF bytes，不允许NUL，characters和UTF-8 bytes均最多32 KiB。它不向parent、child directory或Git root搜索，不合并层级，也不自动读取`CLAUDE.md`、`COQUO.md`或其他兼容名称。Invalid existing file会在普通provider调用前明确失败，而不是静默当作missing。

AgentLoop在每个user turn准备时读取一次`ProjectInstructionsSnapshot`，并和system prompt、tool catalog及committed history一起冻结。该turn内所有provider continuation、preflight和ActionLease复查都复用同一快照；即使工具在回合中重写`AGENTS.md`，当前回合仍完成于旧快照，下一回合才读取新内容。Manual/automatic compaction的source和candidate使用同一快照，变更中的manual compaction会按既有CAS规则冲突；resume和Session切换不会恢复历史指令副本，而是用当前workspace文件筛查下一次Effective Context。指令正文不写transcript、checkpoint、Action Audit或Session record。

Provider-neutral `ConversationRequest`保留独立project-instructions字段。Anthropic将其投影为canonical system prompt后的第二个system text block，OpenAI-compatible投影为第二条system message；count/create/stream共享相同构造，因此内容准确参与preflight与token计量。Compact-summary与Session-title专用请求不暴露项目指令。Canonical prompt明确项目指导从属于Host policy、工具硬边界与当前直接user request，不能授权、放宽permission/approval、把普通file/tool output提升为指令或证明执行。`/instructions`只显示presence、相对path、UTF-8 byte count、representation与fingerprint，不显示正文、不调用provider、不消耗工具预算或修改Session。

该模型可见变化把system prompt升级到v23，fingerprint为`v23-3858281d3354288e15dd51569d896fe22c6e4842d8c8b5192dc4a2e296792a55`；provider wire projection使adapter contract升级到v26。Effective Context current full/compacted representation升级为`ctx-v5`/`ctx-v6`并把exact指令snapshot或明确absent纳入identity；无项目指令时empty full-context identity为`ctx-v5-0700acbf613c3896f65ea82d5fa78f7139406f50e9b5227bcabedf223708d39b`。旧`ctx-v1`至`ctx-v4`仍可校验与replay，Session与Action Audit schema均不升级，也不重写旧JSONL。完整决策见[0083：Foundation 5A Root AGENTS.md Project Instructions](./decisions/0083-foundation-5a-root-agents-project-instructions.md)。

## 确定性离线 Host Eval 基线

`coquo eval`当前提供版本化考试集`host-baseline-v3`。前四个内置案例覆盖受限读取、auto policy受控创建、read-only写入拒绝和同批首动作失败后跳过后续动作；第五个案例覆盖模型提议Task、用户精确确认、前台planning/execution、执行Stage中的普通`skill_search`与`skill_load`、人工验收与完成。每个案例固定prompt、初始UTF-8文件、scripted fake provider回复、permission/approval模式及预期Host事实；runner总是在新的temporary workspace和独立provider配置路径中启动真实`ProjectSession`，因此会经过普通AgentLoop、PermissionGate、工具执行、Session commit和Action Audit，但不会读取用户credential、真实provider配置或网络。

评分发生在Session关闭后：runner通过`SessionStore`严格replay并比较committed turn数量、完整workspace entry与文件bytes摘要、跨全部相关Turn按时间排序的durable tool ledger和Action Audit lifecycle。Task案例还验证accepted admission、唯一且来源匹配的Task、最终状态及Stage kind/outcome。最终assistant文字也按UTF-8 byte count与SHA-256 identity精确比较，但不能覆盖workspace事实；测试明确证明模型即使声称“已创建”，缺失目标文件仍会让案例失败。文本报告只展开失败check，稳定JSON报告不含temporary path、时间戳、随机UUID或原文内容，适合本地回归与后续CI比较。`eval list`列出案例，`eval run <id>`执行单例，`eval run all --format json`执行完整机器可读基线。

这是Host correctness baseline，不是pytest替代品，也不是对真实模型规划质量、随机性或泛化能力的评估。它不运行credential、网络、API费用、command sandbox、性能benchmark、排行榜或外部fixture；scripted trajectory通过只说明固定Harness路径仍符合已声明不变量。该Host-only入口没有改变model-visible tool、system prompt v23、adapter contract v26、Effective Context、Session或Action Audit schema。详见[0084：Deterministic Offline Host Eval Baseline](./decisions/0084-deterministic-offline-host-eval-baseline.md)。

## Actual Coding Task Eval

`coding-task-v1`新增`inventory-validation`与`slug-normalization`两道固定小型Python任务。`eval task prepare TASK OUTPUT`只物化README、一个待修生产文件和可见`unittest`，不复制Host私有测试；`eval task score TASK WORKSPACE`对现有候选执行只读评分。Scorer先检查完整entry shape与protected README/test SHA-256，再通过逐层no-follow读取把声明文件复制到新的temporary scoring workspace，最后才注入私有测试。候选目录不会被写入隐藏文件或测试产物，额外entry、protected修改、symlink、special file、单文件超过1 MiB、总量超过4 MiB或扫描超过100 entries都会失败或fail closed。

可见测试和私有测试都使用固定`/usr/bin/python3 -m unittest discover ...`命令，并经过生产`RunCommandTool`的bubblewrap/seccomp沙箱；不存在“为了Eval直接subprocess”的旁路。`eval task run TASK --real-provider`还要求显式`--profile`、`--profile-id`或`--model`，固定在新建任务目录中以`danger-full-access + auto`运行普通ProjectSession/AgentLoop/PermissionGate/tool/Action Audit链。无`--output`时目录在评分后删除，有`--output`时保留供人工检查；工具生命周期写stderr，stdout只输出不含workspace path、provider正文或随机ID的稳定评分。Host按agent turn、committed turn、action certainty、workspace shape、protected files、visible tests和hidden tests评分，不依赖模型最终文字。

普通command sandbox会只读挂载Host根目录，因此real-task Eval额外在bubblewrap中遮蔽当前Coquo源码checkout；安装态至少遮蔽evaluator模块与bytecode cache，然后再挂载任务workspace。隐藏测试也只在agent Session关闭后的独立评分目录中生成，模型工具无法读取其正文。该切片没有改变21个model-visible tools、system prompt v23、adapter contract v26、`ctx-v5`/`ctx-v6`、ToolArguments、Session、Action Audit或compaction schema；它不是任意benchmark loader、模型排行榜、重试框架或无授权的provider smoke。详见[0085：Actual Coding Task Eval](./decisions/0085-actual-coding-task-eval.md)。

## Durable Task Identity 与 Host Management

Coquo现在明确区分`Task -> Stage -> Turn -> Action`：Task表示可跨重启继续的用户目标；未来每个Stage只推进一个有界步骤并继续使用普通Turn的8/32/24预算；每个Action仍经过PermissionGate、approval、工具硬边界与Action Audit。当前第一阶段只实现最外层Task身份，不实现Stage执行。

每个Task以独立`task_header` schema v1保存在`<workspace>/.coquo/tasks/<workspace-fingerprint>/<task-id>.jsonl`，包含canonical UUID4、workspace identity、一个已有owner Session、受限目标、最多16条验收条件和UTC创建时间，当前派生状态为`ready`。TaskStore执行no-follow普通文件读取、closed schema、严格完整行replay、有界扫描，以及temporary file fsync后exclusive hard-link安装和directory fsync；最终名称已可见但durability不确定时不会误报为完全未创建。List/inspect不创建或修复状态。

Standalone提供`task create/list/show`，REPL提供`/task start/list/show`；REPL创建固定绑定当时的current Session。它们都是Host-only命令，不调用provider或工具、不消耗Turn预算、不写Session transcript或Action Audit，也不把Task目标提升为system authority或Action授权。System prompt保持v23、adapter contract保持v26，21个model-visible tools、ToolArguments v1、`ctx-v5`/`ctx-v6`以及Session/compaction/Action Audit schema均不变。ADR 0087随后增加Stage record与writer lease；`/task continue`和恢复执行仍是后续slice。详见[0086：Durable Task Identity and Host Management](./decisions/0086-durable-task-identity-and-host-management.md)。

## Durable Stage Lifecycle 与 Turn Evidence

Task transcript新增closed schema-v1 `stage_started`、`stage_committed`和`stage_failed`。Replay要求header之后严格start/terminal交替、record sequence准确、Stage编号从1连续递增、Stage UUID唯一、identity与owner Session匹配且时间不倒退。没有Stage为`ready`；commit后为`paused`；失败后为`blocked`；未终结start在普通inspection中固定为`interrupted`，只有持有live writer的当前进程可显示`stage-in-progress`。

`TaskStore.open()`提供非阻塞独占`TaskWriter`。每次append先candidate replay，再检查pathname/inode与transcript上限，完整写入并fsync后才更新内存。写入开始后发生I/O或fsync失败会返回“record可能可见”的typed错误并poison当前writer，调用方必须release后strict inspect，不能自动重试。`SessionStore.turn_evidence()`只接受真实`turn_committed` record，返回Session ID、Turn编号、record sequence、时间与原始newline-terminated JSONL行SHA-256，不暴露对话或工具正文；Stage commit由Host自己获取证据，调用方不能自报hash。

`task list`现在显示Stage数，`task show`和`/task show`显示最近Stage目标、结果、commit Turn evidence、failure reason或interrupted recovery提示。这仍不是执行入口：当前没有`/task continue`、provider调用、completion proposal、累计Task预算或自动恢复。System prompt v23、adapter contract v26、21个工具、8/32/24预算、ToolArguments v1、`ctx-v5`/`ctx-v6`以及Session/compaction/Action Audit schema均不变。详见[0087：Durable Stage Lifecycle and Turn Evidence](./decisions/0087-durable-stage-lifecycle-and-turn-evidence.md)。

## Foreground Task Stage Execution 与 Recovery

`/task continue <task-id> <stage-objective>`现在把一个Task Stage映射为一个真实`ProjectSession.prompt()`，因此直接复用普通AgentLoop、8/32/24 Turn预算、PermissionGate、逐Action approval、tool hard bounds、command sandbox、Action Audit和atomic Session commit，不存在第二套“长任务工具循环”。执行前必须切换到Task的owner Session；Task本身不是权限或approval。

Host先生成以`[Coquo durable Task Stage]`开头的有界UserMessage，其中canonical JSON只携带Task目标、验收条件、accepted plan、最近16个脱敏Stage摘要、当前Stage、累计usage、总预算和剩余额度。新`stage_started`与`stage_committed` schema v2分别在provider调用前保存Session baseline和完整prompt SHA-256，并在真实Turn提交后复制provider/token/tool ledger计数；正常失败的`stage_failed` schema v2也保存不含内容的provider/tool-attempt计数。它们都不复制对话、参数、结果或审计正文；旧Stage v1继续replay并明确显示accounting unavailable。

`/task recover`不调用provider或工具：它只在durable baseline之后查找user-message digest精确匹配的committed Turn。零匹配会把Stage记为`interrupted`失败；唯一匹配绑定真实Turn；多匹配保持原Task并fail closed。它还能补上“Stage已经commit、但plan/completion proposal尚未append”窗口中的协议记录。Provider failure、cooperative cancellation、未提交Turn和Host failure分别落入closed Stage failure reason；若异常发生前Turn已经提交，则先绑定证据再报错，绝不盲目重放副作用。

Canonical system prompt升级为v24并明确Task framing是不可信数据，execution最终协议为`TASK_COMPLETION_PROPOSAL: yes|no`，planning为`TASK_PLAN_JSON:`，两者都必须是最终非空行。协议行原样保留在Session transcript供恢复，但从有效Task结果和streaming终端显示中移除；`/task run`还会显示准确的Stage数量与停止原因。Adapter contract保持v26，21个工具及顺序/schema不变，Effective Context representation仍为`ctx-v5`/`ctx-v6`；因为prompt exact content参与identity，无项目指令的current empty full-context ID更新为`ctx-v5-bd663ddc5d94403891caac9f91d76a319200967331a18163859e203cd6bbb116`。详见[0088：Foreground Task Stage Execution and Recovery](./decisions/0088-foreground-task-stage-execution-and-recovery.md)。

## Task Planning、Acceptance、Budgets 与 Management

`/task plan`用一个planning Stage生成1至32个有界步骤；`/task plan accept`只写人工接受记录，不执行Action。`/task run`一次最多前台顺序执行16个accepted步骤，每步获得新的普通Turn；只有objective按accepted plan顺序精确匹配且已committed的execution Stage才推进progress，手动插入的无关Stage不会跳过计划工作。Run会在命令上限、plan耗尽、完成提议、预算耗尽、interrupted或terminal状态停止。

Task默认Stage/provider/tool累计额度为32/768/1024，并可配置input/output token ceiling。这些是Stage之间的admission ceiling：已准入Stage保留完整普通Turn边界，不会被动态截短；committed Stage与正常失败尝试都计入Host观察到的实际用量，达到或超过ceiling后拒绝下一Stage。Legacy或崩溃恢复Stage若无法证明用量，会同时阻止后续provider/tool admission以及已配置token ceiling，而不是把未知工作当成零。

模型`yes`只追加`completion-proposed`，不等于完成。`/task verify`把人工证据绑定到当前proposal对应的Stage和验收条件；之后若继续工作使proposal失效，旧证据不会复用。`/task complete`要求current proposal及全部条件通过。Task另外支持completed/cancelled/failed三种closed终态、rename、可逆archive、完整timeline、list过滤和带immutable parent provenance的独立derive；这些Host管理命令不进入普通模型对话。

新增configuration、plan proposal/acceptance、completion proposal、acceptance verification、terminal、rename和archive record均使用各自schema v1，不改Session、Action Audit、provider projection或compaction schema。当前仍不支持background worker、scheduler、SubAgent、team、worktree orchestration、并行Stage或Task级blanket approval。详见[0089：Task Planning, Acceptance, Budgets, and Management](./decisions/0089-task-planning-acceptance-budgets-and-management.md)。

## Structured Task Acceptance 与 Independent Review

新Task可在首个Stage前追加schema-v1 `task_acceptance_contract`，把最多16条条件分为`human`、`path-exists`、`path-unchanged`、`command-succeeds`、`action-audit-certain`或`independent-reviewer`，并选择`manual`或`auto-verified`完成策略。旧header-only Task不重写，replay时继续解释为人工验收与手动完成。每种条件固定一种可信来源：人工证据、确定性Host检查或independent reviewer，错误来源不能满足条件。

`/task verify host`不调用模型：它执行no-follow路径类型、创建时文件SHA-256基线、owner Session Action Audit certainty或受限命令检查。命令检查复用生产`RunCommandTool`的bubblewrap、seccomp、环境、timeout、输出与cleanup边界，但workspace改为只读挂载；沙箱不可用时fail closed。每次Host/reviewer尝试写入schema-v1 `task_acceptance_checked`，只有`passed`才写匹配source的acceptance verification。

`/task review`复用current provider/API/model route，但构造独立、无工具、没有Executor Session history的request。Reviewer只能看到Task显式声明的普通文件快照与有界Host事实，`.git`、`.coquo`和任意`.env*` component被拒绝；返回必须是覆盖全部目标条件的严格JSON verdict。Review用量与普通Turn/compaction分开计量，response或错误不进入Executor Session transcript。

`auto-verified`也只有在current committed execution Stage已有model completion proposal，且当前proposal的全部条件都由规定来源验证后，Host才追加`completed`。后续Stage会让旧proposal、check与verification失去完成效力，但不会删除历史。Canonical system prompt升级为v25；provider adapter保持v26，21个tool schema、Session、Action Audit与Effective Context representation不变。无项目指令的empty full-context ID更新为`ctx-v5-7fefaa42ca4226a17e7312fc723ecb3add2b6e8c96a0ac02671e69048156d401`。详见[0090：Structured Task Acceptance and Independent Review](./decisions/0090-structured-task-acceptance-and-independent-review.md)。

## Foundation 4D Slice 0–4：Controlled Single-directory Creation

新增第七个model-visible工具`mkdir(path)`，用于创建一个缺失的workspace相对目录。Path采用portable `/`分隔格式并同时限制character、UTF-8 byte、component数量和单component bytes；绝对路径、Windows drive、反斜杠、空组件、`.`、`..`、NUL、缺失parent、非目录parent和任何已观察到的symlink都会在permission之前拒绝。目标若已经是任何filesystem entry也属于hard rejection，不生成Action Audit；prepare只返回immutable path、`workspace-create`分类和`path_absent`前置条件，不产生副作用。

`read-only`拒绝mkdir；`workspace-write`与`danger-full-access`按现有ask/auto策略处理。Approval绑定exact ActionIdentity与目标absence，等待期间目标若出现会以stale拒绝。只有`action_execution_started`成功append+fsync后才执行一次非递归目录创建；executor再次验证路径和目标，成功后fsync新目录与parent。创建前失败记录`failed / directory_not_created`，目录已可见但durability无法确认则记录`partial / directory_created_durability_unknown`，系统不自动重试或声称回滚。

Provider continuation或turn commit失败不会删除已创建目录；durable Action Audit保留真实结果，candidate turn仍保持未提交。CLI approval显示`workspace-create mkdir path='...'`，`session actions`与`/actions`只显示相对路径、permission/approval和result。普通路径API仍不构成OS sandbox或敌对并发下的完整portable filesystem transaction；本工具与现有本地单用户workspace边界一致，不授权workspace外副作用。

Canonical tool order现在是`read_file, glob, grep, write_file, edit_file, run_command, mkdir`，七个工具继续共享每个user turn三次顺序预算。Anthropic与OpenAI-compatible ordinary count/create投影相同closed schema，compact-summary不暴露工具。Provider adapter contract升级为v9，canonical system prompt升级为v8，empty full-context golden更新为`ctx-v1-12b7d8f648ac4909132c0176de74297f8d00805b887e190d51767b6fc1e2c986`；ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、`context_compacted` v2/v3 replay和`ctx-v1`/`ctx-v2`representation均不升级。详见[0031：Foundation 4D Controlled Single-directory Creation](./decisions/0031-foundation-4d-controlled-single-directory-creation.md)。

## Foundation 4E Slice 0–9：Controlled No-overwrite File Move

新增第八个model-visible工具`move_file(source, destination)`，只把一个现有的non-symlink regular file移动到一个完全不存在的workspace相对目标。Source与destination都使用有界portable `/`路径；两端parent必须已存在、是真实目录且整条路径不得包含symlink，source与destination parent还必须位于同一filesystem。Directory source、相同路径、缺失parent、cross-filesystem和任何已存在destination entry都会在permission之前hard reject，不创建Action Audit，也不会自动创建parent或覆盖目标。

Prepare无副作用，并把source的device/inode/mode/size/mtime/ctime/link count、两端parent identity和destination absence组合为`expected-state-sha256`。移动使用独立的`workspace-move`权限分类：`read-only`拒绝，`workspace-write`与`danger-full-access`按ask/auto处理。Approval绑定exact ActionIdentity与一次性grant；等待期间source、destination或任一parent变化都会在执行前以stale precondition拒绝。CLI approval与Action Audit只展示source/destination两个workspace相对路径和生命周期结果，不泄露绝对路径、fingerprint或内部ID。

只有`action_execution_started`成功append+fsync后才允许产生filesystem副作用。Executor再次通过directory descriptor复查两端parent、source identity与destination absence，然后执行`exclusive hard-link destination → fsync destination parent → unlink source → fsync source parent`。使用exclusive hard link而不是普通`rename()`，确保并发出现的destination不会被覆盖，也不读取文件内容，因此binary或较大的普通文件同样可以移动。

Hard-link加unlink不是单步filesystem transaction。Destination link创建后若durability确认或source unlink失败，系统会如实返回partial：可能两个名称同时存在，也可能文件已移动但source removal durability未知；模型必须先检查两个路径，不能自动重试。Provider continuation或turn commit失败不会撤销真实文件效果，durable Action Audit保留已知结果，candidate turn仍不提交；若`action_execution_started`持久化失败，则destination绝不能出现。

Canonical tool order现在是`read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file`，八个工具继续共享每个user turn最多三次顺序执行。Anthropic与OpenAI-compatible ordinary count/create投影相同closed schema，compact-summary不暴露工具。Provider adapter contract升级为v10，canonical system prompt升级为v9，empty full-context golden更新为`ctx-v1-b18f599515bec3196b10a2bf877d39f1da19f6a9eb3b4f1e123ccc3cd16da760`；ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、普通Session schema、`context_compacted` v2/v3 replay和`ctx-v1`/`ctx-v2`representation均不升级。目录移动、覆盖目标、cross-filesystem copy/delete fallback、file delete与recursive delete仍不在范围；详见[0032：Foundation 4E Controlled No-overwrite File Move](./decisions/0032-foundation-4e-controlled-no-overwrite-file-move.md)。

## Foundation 4F Slice 0–6：Controlled Regular-file Deletion

新增第九个model-visible工具`delete_file(path)`，只永久删除一个现有的non-symlink regular file。Path使用有界portable workspace-relative语法；parent必须已存在、为真实目录且整条路径不得包含symlink。Missing target、directory、symlink、无效path或不安全parent都会在permission之前hard reject，不创建Action Audit。工具不读取内容，因此binary和较大的普通文件也可删除；directory、glob、batch、recursive、trash、backup与undo都不在范围。

Prepare无副作用，并冻结target的device/inode/mode/size/mtime/ctime/link count与parent identity，组合为`expected-state-sha256`。删除使用独立的`workspace-delete`权限分类：`read-only`拒绝，`workspace-write`与`danger-full-access`按ask/auto处理。Approval只展示workspace相对path并绑定exact ActionIdentity、prepared-turn lease与一次性grant；等待期间target或parent变化会在执行前以stale precondition拒绝。

只有`action_execution_started`成功append+fsync后才允许产生filesystem副作用。Executor通过真实parent directory descriptor再次核对parent与target identity，然后unlink名称并fsync parent。POSIX没有本项目可移植使用的conditional-unlink primitive，所以最后一次stat与unlink之间仍存在极小TOCTOU窗口；该合同面向当前本地单用户、受控并发模型，不声称抵抗同workspace中的敌对并发进程。

成功返回`succeeded / file_deleted`与`{"operation":"deleted","path":"..."}`；明确未删除返回`failed / file_not_deleted`；unlink已成功但parent fsync失败则返回`partial / file_deleted_durability_unknown`。Partial表示名称已经消失但持久性未知，模型必须先检查状态且不得自动重试，以免误删后来创建的同名文件。Provider continuation或turn commit失败不会撤销真实删除，durable Action Audit保留而candidate turn不提交；若`action_execution_started`持久化失败，文件绝不能消失。

Canonical tool order现在是`read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file`，九个工具继续共享每个user turn最多三次顺序执行。Anthropic与OpenAI-compatible ordinary count/create投影相同closed schema，compact-summary不暴露工具。Provider adapter contract升级为v11，canonical system prompt升级为v10，empty full-context golden更新为`ctx-v1-42200fbe6c48a76d91ac0dde71e12be0e41674b1ad06c8b82bf82a541e3049e8`；ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、普通Session schema、`context_compacted` v2/v3 replay和`ctx-v1`/`ctx-v2`representation均不升级。下一独立slice只应考虑empty-directory removal并重新证明目录为空、并发child与parent durability边界，不能直接扩展为recursive delete；详见[0033：Foundation 4F Controlled Regular-file Deletion](./decisions/0033-foundation-4f-controlled-regular-file-deletion.md)。

## Foundation 4G Slice 0–6：Controlled Empty-directory Deletion

新增第十个model-visible工具`delete_directory(path)`，只永久删除一个现有的empty non-symlink directory。Path继续使用有界portable workspace-relative语法；parent必须已存在、为真实目录且整条路径不得包含symlink。Missing target、regular file、symlink、non-empty target、无效path或不安全parent都会在permission之前hard reject，不创建Action Audit。空path无法表达workspace root，因此workspace本身不可删除；glob、batch、recursive、trash、backup与undo都不在范围。

Prepare无副作用，并冻结target directory的device/inode/mode/mtime/ctime/link count、parent identity与观察到的empty状态，组合为`expected-state-sha256`。目录删除复用`workspace-delete`：`read-only`拒绝，两个可写mode按ask/auto处理。Approval只展示workspace相对path，并绑定exact ActionIdentity、prepared-turn lease与single-use grant；等待期间target、parent或目录内容变化会以stale precondition fail closed。

Filesystem effect之前必须先append+fsync `action_execution_started`。Executor通过real parent和no-follow target directory descriptor再次核对identity与empty状态，随后调用OS `rmdir`并fsync parent。最后的`rmdir`本身原子要求目标仍为空，因此empty预检后并发出现child会安全失败并保留目录内容；但identity检查与按名称`rmdir`之间仍存在极小TOCTOU窗口，本地单用户合同不声称抵抗敌对并发。

成功返回`succeeded / directory_deleted`和`{"operation":"deleted","path":"..."}`；明确未删除返回`failed / directory_not_deleted`；目录名已删除但parent durability未知返回`partial / directory_deleted_durability_unknown`。Partial后不得自动重试。Provider continuation或turn commit失败不会撤销真实删除，durable Action Audit保留而candidate turn不提交；final audit失败则留下`outcome-unknown`恢复语义。

Canonical tool order现在是`read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file, delete_directory`，十个工具继续共享每个user turn最多三次顺序执行。Provider adapter contract升级为v12，canonical system prompt升级为v11，empty full-context golden更新为`ctx-v1-64ce77996397ddd1f84a27248ddd3e47224948563db506e3bfbda96939799406`。ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、普通Session schema、`context_compacted` v2/v3 replay和`ctx-v1`/`ctx-v2`representation均不升级。递归或非空目录删除仍被明确禁止；详见[0034：Foundation 4G Controlled Empty-directory Deletion](./decisions/0034-foundation-4g-controlled-empty-directory-deletion.md)。

## Foundation 1E：Bounded One-level Directory Listing

新增第十一个model-visible工具`list_directory(path)`，用`.`表示workspace root，其他输入使用有界portable workspace-relative目录语法。Target必须存在、是directory，且整条目标路径不允许symlink component。工具只枚举direct children，不递归、不跟随symlink、不读取文件内容、不应用`.gitignore`；hidden entries也会出现，每项以`file`、`directory`、`symlink`或`other`分类。

结果按完整workspace-relative UTF-8 path稳定排序，并以`{"path":"...","type":"..."}` JSONL返回。一次最多扫描10,000个direct entries；超过扫描上限时whole call报错，避免把filesystem原始枚举顺序误报为stable prefix。完整扫描后最多返回200项且output最多32 KiB，count或byte cap只保留完整records并附加`{"truncated":true}`；未截断空输出表示该次有界扫描观察到empty directory。读取期间entry消失或无法no-follow stat会整体失败，目录并发变化仍不被宣称为原子snapshot。

`list_directory`复用`workspace-read`，所有permission modes均自动allow且不进入人工approval，但仍经过prepared-turn lease、PermissionGate、durable Action Audit、tool-use/result因果配对和atomic turn commit。AgentLoop与ProjectSession继续显式composition/dispatch；十一个工具共享每user turn三次顺序执行，第四次获得structured limit result。新argument-bearing turn仍写`turn_committed` schema v2，旧Session、resume和compaction无需重写或重新执行工具。

Anthropic与OpenAI-compatible ordinary count/create投影相同第十一个closed schema，compact-summary继续no-tools且parallel calls关闭。Provider adapter contract升级到v13，canonical system prompt升级到v12，empty full-context golden更新为`ctx-v1-7776df09d6ace66621cee46719755307b7d816bccde25f61064b4205c689b3b2`；ToolArguments v1、ActionIdentity v1、Session/Action Audit schemas、`context_compacted` v2/v3 replay和`ctx-v1`/`ctx-v2`representation保持不变。Recursive tree、metadata/stat、symlink target与ignore-aware view仍不在范围；详见[0035：Foundation 1E Bounded One-level Directory Listing](./decisions/0035-foundation-1e-bounded-directory-listing.md)。

## Foundation 4H：Controlled Bounded Regular-file Copy

新增第十二个model-visible工具`copy_file(source, destination)`。两条路径都是有界portable workspace-relative file path，双方parent必须已存在且整条路径不得包含symlink；source必须是最多1 MiB的existing regular file，destination必须完全不存在。工具复制原始bytes与source基本`rwx` permission bits，清除setuid/setgid/sticky特殊位且source自身保持不变；不要求UTF-8，也不复制owner、timestamps、ACL、xattr、sparse/reflink或hard-link关系。

Side-effect-free prepare通过`O_NOFOLLOW`有界读取source，冻结source device/inode/mode/size/mtime/ctime/link count、content SHA-256、两侧parent identity和destination absence。Approval后与execute开始时重新验证exact state；source内容/identity、任一parent或destination变化都会stale/conflict fail closed。Prepared bytes是获准复制的snapshot，executor不会重新解释文本或跟随pathname替换。

Filesystem effect前必须先durable append `action_execution_started`。Executor在destination parent内exclusive创建hidden temporary file，写入prepared bytes、设置basic rwx permission bits并fsync file，再以exclusive hard-link安装missing destination，删除temporary name并fsync parent。成功返回`file_copied`和包含source、destination、bytes_copied的compact JSON；destination race永不覆盖。

安装前普通失败且cleanup成功返回`failed / file_not_copied`；若temporary cleanup失败则返回`partial / temporary_cleanup_failed_destination_absent`。Destination安装后的cleanup或durability不确定返回对应partial result，要求inspect workspace且不得自动重试。Provider continuation或turn commit失败不撤销真实copy，Action Audit仍持久记录effect而candidate turn不提交。

Copy复用`workspace-create`，因此`read-only`拒绝，两个可写mode按ask/auto处理。Approval和脱敏Action Audit展示source/destination两个relative paths，不展示source bytes、digest、precondition或absolute path。Canonical tool order追加`copy_file`，十二个工具共享每user turn三次顺序预算。Provider adapter contract升级到v14，canonical system prompt升级到v13，empty full-context golden更新为`ctx-v1-0cd5ddd1c14a00ddcfc01b8879bc83e49a7f8fb5113d5e3d00d98a6f25c413f3`；ToolArguments v1、ActionIdentity v1、Session/Action Audit schemas、`context_compacted` v2/v3 replay和`ctx-v1`/`ctx-v2`representation保持不变。Recursive/directory copy与destination overwrite仍不在范围；详见[0036：Foundation 4H Controlled Bounded Regular-file Copy](./decisions/0036-foundation-4h-controlled-bounded-file-copy.md)。

## 工具批次 A：Bounded Workspace Navigation

批次A在原有`read_file`和`list_directory`之后新增三个只读导航工具。`read_file_lines(path, start_line, line_count)`从最多1 MiB的strict UTF-8普通文件读取1-based logical-line窗口，`start_line`最多1,000,000、一次最多200行且JSONL输出最多32 KiB。`stat_path(path)`允许`.`表示workspace root，以no-follow方式报告type、基本`rwx` mode和nanosecond mtime，普通文件额外报告size；final symlink作为`symlink`观察但不读取target，parent symlink仍拒绝。`list_tree(path, max_depth)`允许1–16层递归，包含hidden entry、不跟随symlink、不读文件内容；10,000 entry/1,000 directory扫描上限整体失败，500 result/32 KiB上限返回完整JSONL records加truncated sentinel。

三者使用共享portable path合同：最多4096 UTF-8 bytes/characters、64 components、每component 255 bytes，拒绝absolute/Windows drive/backslash/NUL/empty/`.`/`..` component，并通过directory descriptors逐层拒绝parent symlink。它们复用`workspace-read`，无需人工approval但仍经过prepared-turn lease、PermissionGate、durable Action Audit、因果配对和atomic turn commit。详见[ADR 0037](./decisions/0037-batch-a-bounded-workspace-navigation.md)。

## 工具批次 B：Process-isolated Regex Grep

`grep_regex(pattern, include)`提供case-sensitive Python `re` logical-line search。它复用literal grep的portable file selector、1,000 candidates、每file 1 MiB、aggregate 16 MiB、200 matches和32 KiB JSONL output边界，selected file必须是strict UTF-8、无NUL的non-symlink regular file。Pattern非空、单行、最多4096 UTF-8 bytes；不提供flags参数、跨行匹配、index或ignore-aware语义。

Selector、读取和regex matching全部在spawn worker process中运行，Host使用固定1秒whole-call timeout。超时后先terminate并有界join，必要时kill并再次join；worker failure、invalid payload和cleanup失败都只返回稳定安全错误，不能卡住Host或泄露traceback。该隔离限制CPU挂死风险，但不是OS sandbox，也不限制worker读取已由workspace selector允许的数据。详见[ADR 0038](./decisions/0038-batch-b-process-isolated-regex-grep.md)。

## 工具批次 C：Structured Exact Multi-edit Patch

`patch_file(path, edits)`接受1–16个`{"old_text":"...","new_text":"..."}` exact edits，每段old/new最多4096 UTF-8 bytes且`old_text`非空，整个arguments仍受ToolArguments v1的16 KiB canonical JSON总上限约束。Target必须是existing non-symlink strict UTF-8 regular file且source/result均最多1 MiB。所有`old_text`都在同一原始snapshot中验证恰好一次，match区间不得重叠；按原始位置排序后一次构造完整candidate，所以不会发生前一个replacement改变后一个anchor含义的顺序依赖。

Patch复用`workspace-overwrite`、source SHA-256 precondition、approval后revalidation、durable `action_execution_started`和`WriteFileTool`的mode-preserving atomic replace。Approval与Action Audit只展示relative path，不展示edits、digest或absolute path。普通failure保持原文件，stale approval失效；replace已发生但directory fsync失败返回`partial / patched_durability_unknown`并禁止自动retry。Provider continuation或turn commit失败保留真实effect和durable audit但不提交candidate turn。详见[ADR 0039](./decisions/0039-batch-c-structured-exact-multi-edit-patch.md)。

批次A/B/C把canonical order扩展到17个工具并保持共享三次顺序预算。Provider adapter contract升级到v15，canonical system prompt升级到v14，empty full-context golden更新为`ctx-v1-ac2b833bb46894c250e2b31370d47911b3464cfa2c71c23ded504f0ea65fd4cf`。ToolArguments v1已经能够规范保存nested JSON edits，因此ToolArguments、ActionIdentity、Session/Action Audit、compaction和`ctx-v1`/`ctx-v2`representation均不升级；旧transcript不重写。Foundation 5A仍暂缓。

## Shared Six-call Tool Budget

随着model-visible surface扩展到17个工具，原共享三次预算不足以在一个普通turn内完成“搜索、读取、修改、验证、复查”。该slice当时把固定Host上限提升为每user turn六次顺序请求，全部工具共用；成功、工具错误、permission denial、approval rejection/cancel与executor failure都消耗已进入normal dispatch的名额且不退款。Approval mode不会改变额度。该历史预算后来由[0055](./decisions/0055-bounded-multi-tool-response-batches.md)的三层预算取代。

前六次照常经过validation、PermissionGate、optional approval、Action Audit和executor。第七次不进入这些边界，只得到与原`tool_use_id`匹配的structured limit result；模型随后必须输出final text，若第八次仍请求工具则candidate turn不提交并确定性停止。新user turn重新获得六次额度，Host不会自动开启turn或继续任务。

Canonical system prompt升级到v15；工具schema、顺序及provider projection逻辑不变，所以provider adapter contract保持v15。Empty full-context golden更新为`ctx-v1-ea0e03265910b48b3cd97e3ace999507379a5e5cf168c6898390870266df051f`；ToolArguments、ActionIdentity、Session/Action Audit、compaction和ctx representation版本均不升级，旧transcript不重写。完整决策见[0040：Shared Six-call Tool Budget](./decisions/0040-shared-six-call-tool-budget.md)。

## Live Redacted Tool Activity

该slice让AgentLoop为每次正常工具dispatch发出typed started/finished事件，并复用当时共享六次预算的index。第七次请求只发limited事件且不进入dispatch；若dispatch异常后effect不能可靠判断，结束状态明确为`outcome-unknown`。ProjectSession从结构化PermissionGate、approval resolution和ActionCoordinator execution metadata映射`error | denied | rejected | cancelled | succeeded | failed | partial`，不解析ToolResult文本。

默认compact摘要按工具类型最小化：显示workspace-relative path、include、byte/edit/argument count、command basename、cwd和timeout；不显示file/edit/query内容、完整argv、absolute path、digest、lease、内部ID或raw result。参数执行校验前出现的absolute path会隐藏为`<absolute>`，控制字符被转义，摘要长度有界。One-shot通过可复用`TerminalEventSink`把事件写到stderr并保持stdout只有最终回答；REPL写到自身stdout。Sink异常被隔离，不能改变工具执行、Action Audit、turn commit或Session state。后续[0065](./decisions/0065-opt-in-bounded-live-tool-details.md)只为REPL增加显式process-local full模式；compact与one-shot边界保持不变。

Live events不写append-only transcript、不参与resume/compaction、不进入model history，也不能替代durable Action Audit。该Host-only slice不改变工具schema/order、system prompt v15、provider adapter v15、empty Effective Context identity或任何Session/Action Audit/context representation version。完整决策见[0041：Live Redacted Tool Activity Events](./decisions/0041-live-redacted-tool-activity-events.md)。

## Provider-neutral Assistant Tool Text Representation

Coquo现在能在内部准确表达“assistant文字与一个tool call同时出现”：既有immutable `ToolUse`新增可选`assistant_text`，把原始文字与同一tool ID、name和arguments原子绑定。`None`仍是既有纯工具调用；非空文字最多32 KiB characters和32 KiB UTF-8 bytes，不做trim或normalization。Effective Context identity与compact source会保留该文字，tool-use/result因果对仍不可拆分。

这一步当时只定义内部表示，尚未让真实provider使用。Anthropic与OpenAI-compatible parser、history serializer、AgentLoop和`turn_committed` schema v2都明确fail closed，确保文字不会在执行、审计或持久化过程中被静默丢弃。后续ADR 0043–0046现已依次完成入站normalization、Session v3、history projection及runtime/terminal接入。

既有纯工具调用的identity payload不变，因此system prompt v15、provider adapter contract v15、tool schema/order、ToolArguments v1、Session/Action Audit schema和`ctx-v1`/`ctx-v2`representation均不升级。完整决策见[0042：Provider-neutral Assistant Tool Text Representation](./decisions/0042-provider-neutral-assistant-tool-text-representation.md)。

## Provider Mixed-response Inbound Normalization

Anthropic Messages与OpenAI-compatible Chat Completions现在会把各自的mixed native response统一解码为`ToolUse.assistant_text`。Anthropic要求`tool_use` stop reason和恰好一个合法tool block，并按wire顺序拼接所有text blocks；OpenAI-compatible要求`tool_calls` finish reason和恰好一个合法function call，并原样保留非空`message.content`。没有companion text时仍产生既有pure `ToolUse`，纯文字response也保持不变。

两个parser继续拒绝多工具、错误stop/finish reason、unknown tool、malformed arguments、unsupported content以及empty、invalid或超限companion text。该slice当时只接到入站边界；后续ADR 0044–0046现已完成Session持久化、history projection与runtime执行展示。

Provider response contract升级使adapter contract变为v16并自然改变route fingerprint。System prompt保持v15且仍要求模型只返回tool call；tool schema/order、Effective Context golden、ToolArguments v1及Session/context representation均不变。完整决策见[0043：Provider Mixed-response Inbound Normalization](./decisions/0043-provider-mixed-response-inbound-normalization.md)。

## `turn_committed` v3 Assistant Tool Text Persistence

新Session turn现在使用record-local schema v3。它沿用v2的generic `arguments_version + arguments`，并要求每个`tool_use`保存nullable `assistant_text`：mixed response保存exact text，pure tool call保存`null`。Companion text继续受non-empty、valid UTF-8、32 KiB character/byte、no-NUL及Session record总大小限制，malformed或unknown字段在append/replay前fail closed。

Reader继续兼容v1 single-path与v2 generic-arguments turn；二者在内存中解释为没有companion text。旧transcript不迁移、不重写，resume只在原prefix之后追加`session_resumed`和v3 turn。V3 replay会把文字恢复到原`ToolUse`，保持tool-use/result原子因果；full history完整保留，compact checkpoint后的retained suffix也能精确恢复mixed pair。

该slice当时仍让provider history serializer与AgentLoop拒绝mixed runtime；后续ADR 0045–0046现已接通普通CLI。其record-local兼容结论保持不变：ToolArguments v1、Action Audit、`context_compacted` v2/v3与`ctx-v1`/`ctx-v2`representation未因Session v3升级。完整决策见[0044：`turn_committed` v3 Assistant Tool Text Persistence](./decisions/0044-turn-committed-v3-assistant-tool-text-persistence.md)。

## Provider Mixed-response History Projection

两个真实adapter现在能把provider-neutral mixed history无损投影回各自wire protocol。Anthropic在同一个assistant message中按`text block -> tool_use block`顺序发送，OpenAI-compatible在同一个assistant message中同时设置exact `content`与唯一`tool_calls`；匹配`ToolResult`仍紧随其后。Pure tool call保持原有wire shape，companion text不会被拆成final answer、复制到result或获得新的tool ID。

在该slice边界，Serializer继续复用closed tool schema与完整因果校验，malformed text、unknown tool、multiple calls和broken pairing都fail closed。Ordinary count/create共享同一projection，compact summary仍无tools。该变化把adapter contract升级到v17；tool catalog/order、六次预算、ToolArguments v1、`turn_committed` v3、Action Audit、compaction与context representation均不变。完整决策见[0045：Provider Mixed-response History Projection](./decisions/0045-provider-mixed-response-history-projection.md)。

## AgentLoop 与 Terminal Assistant Tool Text Integration

普通runtime现已完整接通mixed response。AgentLoop先发出exact companion-text事件，再发tool started/finished并照常执行；`ToolUse -> ToolResult`立即进入provider continuation，只有后续纯assistant text才结束并durably commit整个turn。One-shot把companion text和tool events写到stderr、stdout只保留final answer；REPL按相同顺序写到自身输出流。已有结尾换行不会被重复追加，terminal sink失败不会改变执行、Action Audit或commit。

Live companion event不持久化，也不作为执行证明；durable truth仍是`turn_committed` v3、完整transcript和Action Audit。Provider continuation或turn commit失败不会提交candidate history，但已发生的工具副作用与audit不能回滚。第七次mixed请求只能得到limit result，第八次仍请求工具会停止且不提交，因此文字不能绕过六次共享预算。ProjectSession确定性场景证明mixed turn可执行、显示、close/resume并在恢复后的provider请求中原样回传。

Canonical system prompt升级为v16，empty full-context golden变为`ctx-v1-bc29d5392990da88d9a0641d78cfc051d0d9e92b9f3452e90b1259ae16df2b58`；adapter contract保持v17，ToolArguments v1、`turn_committed` v3、Action Audit、`context_compacted` v2/v3和`ctx-v1`/`ctx-v2`representation不升级，旧transcript不重写。完整决策见[0046：AgentLoop 与 Terminal Assistant Tool Text Integration](./decisions/0046-agent-loop-and-terminal-assistant-tool-text-integration.md)。

## Provider Streaming 与 Terminal Failure Atomicity

该streaming阶段让Anthropic Messages与OpenAI-compatible Chat Completions可在同步provider调用中流式返回assistant文字。两个adapter各自严格组装native event/chunk、finish reason和fragmented tool JSON；在该阶段，只有形成完整且通过known-tool schema验证的单个neutral `ToolUse`后才允许AgentLoop执行，multiple calls仍fail closed。0055随后把完整有界batch纳入同一“先完整解析、再执行”边界。

REPL即时显示文字delta；tool companion完整解析后才接tool activity，final text只有在Session turn append+fsync成功后才得到committed确认，因此不会重复打印。Provider中断、Ctrl-C或commit失败会明确说明visible partial text未提交。One-shot因stream结束前不能知道文字是final还是tool companion而先buffer：companion与tool activity写stderr，stdout仍只在durable commit后输出一次final answer。Runtime在第一个delta前完成context preflight，并在完整同步stream期间保持原turn lease。

Delta不持久化，也不能作为tool execution或turn commit证明；durable truth仍是`turn_committed` v3、Action Audit和完整transcript。Adapter contract最终升级为v19。Canonical system prompt经审阅保持v16，tool schema/order、六次预算、ToolArguments v1、ActionIdentity v1、Session/compaction schemas、empty context golden与`ctx-v1`/`ctx-v2`representation不变。完整设计见[0047](./decisions/0047-provider-neutral-synchronous-response-streaming.md)至[0050](./decisions/0050-agentloop-runtime-and-terminal-streaming-integration.md)。

## TTY Markdown Rendering

REPL与TTY one-shot现在使用锁定的Rich renderer展示assistant Markdown：标题、强调、列表、表格与fenced code转成terminal layout和可选ANSI syntax styling。Streaming按blank line或closed fence等safe boundary输出完整block，避免用incomplete fragment误解析代码围栏；tool companion完整分类后flush，final suffix只在durable turn commit后flush。

非TTY stdout/stderr、pipe和redirect继续输出原始Markdown，`NO_COLOR`只关闭ANSI而保留Markdown布局。Provider返回的ESC、CR、NUL和其他terminal controls在TTY副本中变成visible escape text，Rich markup、emoji和terminal hyperlink均关闭。Session、provider continuation和Effective Context始终使用exact原始文字，因此renderer failure或版本变化不改变恢复和context identity。

该Host-only presentation slice新增Rich runtime dependency，但canonical system prompt保持v16、adapter contract保持v19、`turn_committed`保持v3，其他tool、permission、audit、compaction和context契约均不变。完整决策见[0051：TTY Markdown Rendering](./decisions/0051-tty-markdown-rendering.md)。

## Exact Bounded Informed Approval

REPL的逐次`ask`现在会在用户回答前展示prepared action事实。`write_file`、`edit_file`和`patch_file`使用准备阶段冻结的原始UTF-8 snapshot与完整candidate生成unified diff；CLI不会为了展示再读取一次workspace。Create、overwrite、empty file、内容相同但仍执行的overwrite、missing final newline以及truncated preview都有明确标识。Diff最多160行、24 KiB，单行最多4096 bytes；截断只影响展示，approval仍绑定完整candidate。

Copy、move和file delete显示prepared byte count，directory create/delete与command显示destination absence、永久删除、不可自动回滚以及command无OS/filesystem/network sandbox等关键事实。Preview携带exact ActionIdentity digest和closed tool-kind，mismatch在任何Action Audit写入前fail closed。Terminal副本转义C0/C1、Unicode format和line/paragraph separator controls，并可安全着色；普通live tool line与`/actions`继续脱敏。

Preview只存在于REPL `ask`调用，不持久化、不进入provider history、Session、resume、compaction或Effective Context。One-shot ask仍不读取stdin并取消，auto不展示preview。用户接受后原有precondition refresh、single-use grant和stale rejection继续执行，因此展示diff不扩大permission或hard execution边界。该Host-only变化保持canonical system prompt v16、adapter contract v19、17工具schema/order、六次预算、ToolArguments v1、ActionIdentity v1、Action Audit v1、`turn_committed` v3、`context_compacted` v2/v3及`ctx-v1`/`ctx-v2`representation不变。完整决策见[0052：Exact Bounded Informed Approval Previews](./decisions/0052-exact-bounded-informed-approval-previews.md)。

## TTY Prompt Editor 与交互反馈

REPL现在通过独立`PromptEditor`边界读取输入。真实TTY使用锁定的prompt-toolkit和简洁的`›`输入标记，续行以两个空格对齐，下方显示bounded、control-safe的`model · workspace`状态栏。Enter提交，Alt+Enter插入LF；若terminal拦截Alt组合，Esc后Enter是等价后备。Bracketed paste把多行粘贴作为一个buffer。提交文本不做整体`strip()`，因此模型、Session与resume保留原始缩进、换行和结尾换行；只有全空白buffer仍由REPL忽略。

每次显示输入框前，editor从当前Session完整turns重建最多1000条、合计最多4 MiB的已提交user prompt历史，因此Up/Ctrl-R支持跨进程resume，并在`/resume`或`/session new`后切换来源；slash command、取消草稿及失败未提交turn不会残留。Tab completion显示顶层命令说明，并补全`/provider list|current|use`和`/session show|list|new`。`/clear`只发出清屏序列，不调用模型、不写transcript、不改变Session或Effective Context。

Ctrl-C取消非空草稿并继续REPL，空buffer时退出；Ctrl-D只在空buffer退出。输入最多256 KiB characters和256 KiB UTF-8 bytes，并拒绝NUL。非TTY、pipe、redirect及注入stream继续使用确定性单行fallback。多行slash前缀是普通模型文本，Host slash command仍必须是单行。

真实TTY提交后显示临时`• Working...`，首个可见assistant或tool lifecycle事件会清除它；assistant companion与final输出都以`•`标记。One-shot、pipe、redirect和注入stream不增加这些反馈，既有stdout/stderr合同不变。反馈事件不持久化，失败清理仍以durable Session和Action Audit为事实源。

这是Host-only输入与presentation变化。Canonical system prompt保持v16，adapter contract保持v19，17工具schema/order与六次预算、ToolArguments v1、ActionIdentity v1、Action Audit v1、`turn_committed` v3、`context_compacted` v2/v3和`ctx-v1`/`ctx-v2`representation均不变。Exact多行文本会自然参与具体turn与context identity，但identity representation不升级。完整决策见[0053：TTY Prompt Editor 与交互反馈](./decisions/0053-tty-multiline-prompt-editor.md)。

## Sequential Tool-call Budget Hardening

一次DeepSeek-compatible真实观察要求empty workspace创建三个目录和八个文件，仅mutation就至少需要11次工具调用，无法装入六次turn预算。成功执行只读`list_directory`后，provider在下一response发送nonzero stream tool-call index，表示同一assistant response里还包含后续call。旧adapter在任何该response工具执行前fail closed，但错误只显示invalid index；Action Audit确认没有发生mkdir或write。

OpenAI-compatible parser现在把positive tool-call index准确分类为unsupported multiple calls，同时继续拒绝malformed index、multiple delta entries、变化ID和其他不完整shape。它不会只取第一个call、缓存后续call或自动retry。System prompt v17明确要求预算不足时只使用剩余顺序额度，随后报告已完成与待完成工作并等待later user turn，绝不能为了完成batch而把calls打包。

Host仍强制每turn六次调用，17工具schema/order、`parallel_tool_calls=false` projection、permission/approval、Action Audit、Session schema和causality不变。Accepted provider shape不变，因此adapter contract保持v19；new prompt fingerprint为`v17-1c66b2e9cf6b622477408f99106294b2cdab14a9983a7fb6b4d628218307b851`，empty full-context identity为`ctx-v1-4bcd666498bd96b3af1aa59a1d6793b31cdcdcff1dc274db80c6f051f1e8b6da`，representation仍为`ctx-v1`/`ctx-v2`。完整决策见[0054：Sequential Tool-call Budget Hardening](./decisions/0054-sequential-tool-call-budget-hardening.md)。

## Bounded Multi-tool Response Batches

Coquo现在接受provider一次回复中的有界有序工具batch。统一内部`AssistantToolBatch`保存整份回复的companion text和多个唯一ID的`ToolUse`；单调用仍沿用旧`ToolUse`。OpenAI-compatible parser按`tool_calls[]`或stream index分别组装每个call，Anthropic按content blocks组装。整份回复必须先通过数量、ID、JSON、closed schema和因果校验，任一call无效都会在该batch任何动作前整体拒绝。

Host仍不并行。可接纳batch按provider顺序逐项进入PermissionGate、approval、Action Audit和executor；一个动作非成功会让同批后续项返回明确skipped error而不执行。三层预算为每response最多8个calls、每user turn最多32个admitted requests、最多24次provider invocations且最后一次text-only。无法装入剩余请求预算的整批零执行并返回匹配的budget errors。已成功副作用不因后项失败回滚，最终assistant text和durable turn commit前candidate history仍不提交。

OpenAI-compatible history投影一个assistant `tool_calls[]`后接ordered tool messages；Anthropic投影一个assistant tool block sequence后接一个包含ordered results的user message。Count/create共享projection，text-only invocation不暴露tools。Canonical system prompt升级为v18，adapter contract升级为v20，`turn_committed` current schema升级为v4，Effective Context current full/compacted representation升级为`ctx-v3`/`ctx-v4`。ToolArguments v1、ActionIdentity/Action Audit、ordinary Session records及`context_compacted` record schema不变；Session v1/v2/v3与旧`ctx-v1`/`ctx-v2`checkpoint继续replay且不重写。Fingerprint为`v18-6ddfaa8302427bbe25c1ee28cee6b1e5975949da111a96876baa8e834cd86f8c`，empty full-context identity为`ctx-v3-9007cd576ff595afb6a103a199437d28580836f2a3a5b551819f0f8574d4cf80`。完整决策见[0055：Bounded Multi-tool Response Batches](./decisions/0055-bounded-multi-tool-response-batches.md)。

## Structured Tool Outcome Ledger

AgentLoop现在为每个provider tool request建立Host-owned typed entry，记录连续request index、exact tool-use ID、工具名、outcome与安全result code。`requested`、`admitted`、`dispatched`及各status count全部从entries推导，不解析ToolResult或assistant文案。真实40请求回归场景可准确区分32项admitted中的24次成功、1次错误、7次同批跳过，以及8次over-budget拒绝。

预算耗尽或provider invocation上限强制text-only收尾前，Host把bounded canonical账本摘要附到最后一个真实ToolResult，让模型获得权威计数而不伪造user/system message。摘要分别报告`unused_admission_slots`和`tool_requests_closed=true`，避免整批因装不进剩余额度被拒绝后，模型把零散空位误解为仍可调用。Durable turn commit成功后，终端另显示`Tool summary:`；commit失败不显示，event sink失败仍不影响执行、Action Audit或提交。普通主动结束的turn不会为账本额外调用provider。

新的`turn_committed` schema v5在conversation items之外保存top-level typed ledger，并严格校验每个entry与tool request/result的identity、顺序和error flag。v1/v2/v3/v4继续replay为空legacy ledger，resume只append v5且不重写旧prefix。Top-level ledger不进入provider history、compaction或context identity；model-visible annotation仍是既有ToolResult content，所以Effective Context representation保持`ctx-v3`/`ctx-v4`。System prompt升级为v19，fingerprint为`v19-accfbb73aa611061c8a8cb6be5bb54012ce5809fbbe91050439383e3d35318b7`，empty full-context identity为`ctx-v3-29ff59405090ba544b2bacb144d5961daecc7d0d6359123a9262c097d0fa654d`。Provider wire shape和projection未变，因此adapter contract保持v20。完整决策见[0056：Structured Tool Outcome Ledger](./decisions/0056-structured-tool-outcome-ledger.md)。

## Durable Tool Ledger Inspection

持久化账本现在可以直接从终端查看。离线命令`session tools [selector] --limit N`与当前REPL的`/tools [count]`都只读取严格replay后的Session state；默认返回最近5个committed turns，最多20个，并保留原turn number、record sequence和commit timestamp。默认模式显示每turn派生汇总，`--details`或`/tools details [count]`才展开连续request index、tool name、typed outcome和safe result code。

展示不包含tool-use ID、tool arguments、path、prompt、assistant text、ToolResult prose、absolute workspace、approval grant或Action identity。详情输出最多32 KiB，只在完整行边界截断并显示sentinel。Schema-v5的空ledger准确表示该turn没有工具请求；v1/v2/v3/v4由于当时没有持久账本，会明确显示unavailable而不是伪装成零请求。Strict replay失败仍让整个查询安全失败，命令不创建Session root、不获取writer lease、不调用provider或tool，也不修改latest、transcript、runtime、Effective Context或Action Audit。

这是Host-only inspection slice：canonical system prompt保持v19及原fingerprint，provider adapter contract保持v20，ToolArguments v1、ActionIdentity v1、`turn_committed` v5、Action Audit与`context_compacted` schema以及`ctx-v3`/`ctx-v4`representation均不升级。完整决策见[0057：Durable Tool Ledger Inspection](./decisions/0057-durable-tool-ledger-inspection.md)。

## Runtime Context Meter 与 Provider Token Usage

每次真实provider invocation现在会在发送前发布同一次full preflight的`ContextFitReport`，终端以10格方块条区分当前input、requested output reserve和剩余window；工具结果引发的continuation也逐次更新。REPL底栏在两次输入之间保留最近一次短context状态，同步生成期间则使用即时事件，不宣称已经实现异步固定底栏。

Anthropic与OpenAI-compatible adapter把厂商返回的actual input/output usage放进Host-only response envelope，不进入`AssistantText`、conversation history或Session。Anthropic同时支持non-streaming usage及stream中的`message_start`/`message_delta`；OpenAI-compatible支持non-streaming usage并在stream request中请求`stream_options.include_usage`、解析finish后的usage-only chunk。缺失或malformed metadata记为unknown invocation，绝不按0或本地estimate混入actual totals；provider调用失败也记unknown，因为远端仍可能产生费用。

Runtime按最新invocation、最近user turn及当前profile target累计真实用量，普通turn与compaction分开。工具continuation属于同一turn；count-only inspection不属于generation usage。成功`/provider use`或`/model`切换会清零，`/resume`和`/session new`不会。`/usage`显示逐invocation与aggregate；这些值仅存在当前进程，不持久化、不计算货币费用，也不承诺跨进程计费对账。

该变化把provider adapter contract升级为v21，因为OpenAI-compatible stream request和两类adapter response transport增加usage contract。Canonical system prompt保持v19，17个tool schema/order、ToolArguments v1、ActionIdentity v1、`turn_committed` v5、Action Audit、`context_compacted`及Effective Context `ctx-v3`/`ctx-v4`均不变。完整决策见[0058：Runtime Context Meter 与 Provider Token Usage](./decisions/0058-runtime-context-meter-and-provider-token-usage.md)。

## Context 与 Compaction Observability

REPL新增`/compact preview`与`/compactions [count]`。Preview在当前Session锁内冻结Effective Context，复用固定的至少4个effective turns、保留最近2个turns策略和当前target assessment；它只报告eligible、将summary/retain的turn数量与context压力，不构造summary request、不获取compaction lease、不写checkpoint。Fake或unknown target明确显示unknown；Anthropic official target为了exact inspection可能使用count-only API，但不会发生generation。

`/compactions`从已经strict replay的当前Session state中选择最近5条、最多20条`context_compacted`记录，只展示sequence、timestamp、schema、manual/high-water/overflow trigger、80% threshold、full/summarized/retained turn数量和previous checkpoint。它不展示summary、binding、context ID、prompt或credential。既有v2/v3 checkpoint没有持久化before/after token count，因此历史查询明确标为unavailable，而不是重算或升级schema。

`/context`与preview把当前`input + output reserve`相对window分为normal、70%-79%接近阈值、80%-89% auto-compact range、90%-100% near full、overflow及unknown。分级只是Host展示；真正的普通prompt仍按包含pending user input的exact initial request和既有80%/overflow策略重新判定。`/usage`新增当前runtime最近一次compaction invocation的known/unknown actual usage；该slice当时仍让runtime切换整体清零且不持久化usage，后续ADR 0062现已增加独立Session audit。

该slice是Host-only observability。Canonical system prompt保持v19，provider adapter contract保持v21，17个tool schema/order、ToolArguments v1、ActionIdentity v1、`turn_committed` v5、`context_compacted` v2/v3和Effective Context `ctx-v3`/`ctx-v4`均不变。完整决策见[0059：Context 与 Compaction Observability](./decisions/0059-context-and-compaction-observability.md)。

## Provider Output-limit 与 Compaction Failure Diagnostics

Anthropic与OpenAI-compatible现在把普通generation或compact summary耗尽输出额度统一表示为`output_limit`，不再混入一般`response_invalid`。结构化错误只携带requested output limit、可用的严格provider usage及是否观察到不完整内容，不保存raw response或partial text。非流式响应会在拒绝前读取合法usage；OpenAI-compatible stream保留finish reason后的usage-only tail，Anthropic stream组合message start/delta中的input/output计量。

Runtime在output-limit异常路径也会记录已知actual usage，因此后续`/usage`能准确显示失败的turn或compaction调用；metadata缺失或malformed仍是unknown。One-shot与REPL会展示requested limit、actual input/output或明确不可用，并说明不完整回复不是final answer、没有committed turn。已经在该尝试较早阶段完成的工具副作用不会回滚，仍以Action Audit为准；stream partial text只在终端临时可见并明确标记未提交。

非缩减compaction现在以结构化`CompactionCandidateError`保留可比较的source/candidate input count与计量方法，终端会显示例如`input 4900 -> 5100 tokens; estimated`。该失败仍不安装summary、不追加checkpoint、不修改Effective Context；summary generation的进程内usage若已知仍可查看，后续ADR 0062还会追加独立`compaction_failed` audit保存跨重启证据。系统不自动retry、不提交partial text，也不自动提高profile output reserve。

Provider adapter contract因失败transport与异常usage计量升级为v22。Native request、成功response、tool/history projection不变；canonical system prompt保持v19，17工具及顺序、ToolArguments v1、ActionIdentity v1、`turn_committed` v5、Action Audit v1、`context_compacted` v2/v3和Effective Context `ctx-v3`/`ctx-v4`均不升级。完整决策见[0060：Provider Output-limit 与 Compaction Failure Diagnostics](./decisions/0060-provider-output-limit-and-compaction-failure-diagnostics.md)。

## Process-local Runtime Output Budget Control

普通prompt与REPL启动现在接受全局`--max-output-tokens`，REPL新增`/output`、`/output <tokens>`与`/output reset`。有效预算限制为1至100,000,000；查看命令同时显示当前有效值、profile或direct route默认值、来源和known model maximum。Fake runtime明确拒绝覆盖，profile文件和Session选择均不因临时控制而修改。

Runtime把预算调整作为新的provider route candidate：先重建provider及model capability，再用当前committed Effective Context执行与provider/model切换相同的target-aware筛查。Known model-output或context overflow保持旧provider、旧route、旧generation及旧预算不变；unknown count以warning应用但下一次真实调用仍执行完整preflight。成功后才原子替换provider并提升generation，保证prepared action lease不能跨越route变化。

预算更新不清空“进入当前profile以来”的process-local usage，但会丢弃基于旧reserve的latest context meter。`/model`保留临时预算并针对新model重新筛查，`/provider use`或active selection变化清除覆盖并恢复新profile默认值。`/output reset`也处理临时值恰好等于默认值的情况。后续成功或失败turn的BindingSnapshot自然记录实际`max_output_tokens`与route fingerprint，但resume不会从历史binding恢复临时覆盖；调整命令本身不追加`runtime_changed`。

该slice不自动retry或续写截断回答，不修改compaction的4096-token Host cap、profile schema或provider成功response。Canonical system prompt保持v19，provider adapter contract保持v22，17工具及顺序、ToolArguments v1、ActionIdentity v1、`turn_committed` v5、Action Audit v1、`context_compacted` v2/v3和Effective Context `ctx-v3`/`ctx-v4`均不变。完整决策见[0061：Process-local Runtime Output Budget Control](./decisions/0061-process-local-runtime-output-budget-control.md)。

## Durable Session Provider Usage Audit

Provider实际Token用量现在不仅保留在进程内tracker，也附着到严格replay的Session终局事实。普通成功turn把所有generation与tool continuation按顺序写入`turn_committed` v6；失败turn使用record-local `turn_failed` v2。成功compaction使用`context_compacted` v4，失败compaction追加Host-only `compaction_failed` v1。每个调用要么保存有界input/output pair，要么明确保存unknown；不会把metadata缺失解释为0。

`/usage`继续显示当前runtime/profile的进程内窗口，`/usage session`累计当前Session中的成功turn、失败turn、成功compaction与失败compaction，`/usage turns`显示最近10个成功或失败turn。Resume和进程重启后仍可查询；旧`turn_committed` v1-v5、`turn_failed` v1及`context_compacted` v2/v3缺少该字段，因此显示legacy unavailable而不是0。Usage audit不进入full/effective history、不进入summary、不参与context identity，也不从历史binding重建当前runtime。

成功usage与对应turn或checkpoint在同一record中原子提交；失败usage与安全失败分类一起提交。Non-reducing compaction仍不安装checkpoint或改变Effective Context，但会留下独立失败audit。系统不保存raw response、partial text、credential、价格或厂商billing细分，也不声称缺失终局记录的provider调用能够形成crash-proof账单。

该slice保持canonical system prompt v19、provider adapter contract v22、17工具及顺序、ToolArguments v1、ActionIdentity v1、Action Audit v1和Effective Context `ctx-v3`/`ctx-v4`不变。新记录使用`turn_committed` v6、`turn_failed` v2和`context_compacted` v4；旧prefix不重写。完整决策见[0062：Durable Session Provider Usage Audit](./decisions/0062-durable-session-provider-usage-audit.md)。

## Bounded Read-only Git Change Observation

模型可见工具面现在在原17个工具之后追加`git_status`和`git_diff`。`git_status({})`把workspace顶层仓库的staged、unstaged和untracked路径状态解析为稳定排序的JSONL，不读取untracked文件内容；完整raw status最多1 MiB和10,000条，模型结果最多200条或32 KiB，截断时有明确sentinel。`git_diff(scope, path)`只接受`staged | unstaged`与`.`或literal workspace-relative path，只返回tracked patch，不执行rename detection、external diff、textconv或submodule recursion；结果最多64 KiB并显式标记截断。

专用runner使用固定argv、`shell=False`、closed stdin、5秒timeout、有界pipe capture与TERM到KILL process-group cleanup，并关闭optional locks、pager、prompt、fsmonitor、untracked cache、hooks、外部config/attributes、external diff和submodule recursion。V1要求workspace本身就是Git顶层且使用内部non-symlink `.git`目录；linked-worktree pointer、`commondir`、object alternates、external config include、configured external filter、unsafe metadata和非Git workspace都会安全失败。它是有界Git进程边界而非OS sandbox，也不接受任意Git argv、revision或写操作。

模型工具继续按`workspace-read`经过PermissionGate、Action Audit与共享8/32/24预算。REPL新增`/changes`、`/changes unstaged`和`/changes staged`，直接显示status或经过terminal-control转义的root patch，不调用provider、不消耗tool budget、不写Session或Action Audit。Canonical system prompt升级为v20，provider adapter contract升级为v23，19-tool catalog使empty full-context identity变为`ctx-v3-cb7ce2ad36fc600b23c66362f02e4e139beee17e721a06eb490b82a7ae302a9e`；ToolArguments v1、ActionIdentity v1、Session/Action Audit schemas与`ctx-v3`/`ctx-v4`representation不升级，旧Session不重写。完整决策见[0063：Bounded Read-only Git Change Observation](./decisions/0063-bounded-read-only-git-change-observation.md)。

## Bounded Reachable Git History Observation

模型可见工具面在19个工具之后追加`git_log(limit, path)`与`git_show(commit_id, path)`。`git_log`只遍历当前`HEAD`可达历史，接受1–50条与`.`或一个literal workspace-relative path，返回包含完整commit/parent ID、committer ISO时间、最多1024 bytes subject及其截断标记的稳定JSONL；raw结果最多1 MiB，模型结果最多32 KiB。它不枚举`--all`、refs、reflog、signature、notes、author/email或任意revision。

`git_show`只接受完整40/64位小写十六进制ID，并先通过固定`merge-base --is-ancestor`确认它是当前`HEAD`可达commit，再返回一行JSON metadata、最多8 KiB commit message和有界tracked patch；总输出最多64 KiB，message与patch分别标记截断。External diff、textconv、rename detection、signature、color、submodule recursion与replacement objects均被关闭。缩写/大写ID、不可达或非commit object、unborn HEAD、非法path、非UTF-8或malformed输出全部安全失败。

两个工具继续按`workspace-read`经过PermissionGate、Action Audit与共享8/32/24预算，不需要approval。REPL新增`/commits [count] [path]`与`/commit <full-id> [path]`，直接展示完整ID并转义subject/message/patch中的terminal controls；它们不调用provider、不消耗model tool budget、不写Session或Action Audit。Canonical system prompt升级为v21，provider adapter contract升级为v24，21-tool catalog使empty full-context identity变为`ctx-v3-bf336060a8cf9fb75df3766f81b6dae9ef175e8b6e0929f0a0ef10ebab387dd7`。ToolArguments v1、ActionIdentity v1、Session/Action Audit schemas与`ctx-v3`/`ctx-v4`representation不升级，旧Session不重写。完整决策见[0064：Bounded Reachable Git History Observation](./decisions/0064-bounded-reachable-git-history-observation.md)。

## Opt-in Bounded Live Tool Details

REPL新增进程内`/tool-details`、`/tool-details compact`与`/tool-details full`。每次启动固定从compact开始，one-shot也继续使用compact；设置不写profile、Session、transcript、Action Audit或Effective Context。Compact完全保留既有脱敏单行。Full把tool start改为多行，但file/edit/patch/search content继续隐藏；普通工具只展开原safe summary，`run_command`额外显示结构化JSON argv、cwd、timeout及执行解释。

Command详情不会把direct argv伪装成shell source：普通请求明确显示Host关闭shell parsing；模型显式请求常见shell并提供`-c`类option时，终端标明shell interpreter及source所在argv位置。Argv行最多7 KiB，全部详情最多4行/8 KiB，截断携带rendered byte count；C0/C1、Unicode format及line/paragraph separator controls在写终端前转义。启用full时会警告argv可能包含敏感值。

只有full显式请求时AgentLoop才从immutable ToolArguments生成详情；compact与one-shot事件本身不携带argv详情，TerminalEventSink再渲染所选形式。事件仍是best-effort临时观察，不能改变permission、approval、execution、Action Audit、turn commit或provider failure；full也不提供PTY、retained shell、stdin forwarding或更高命令权限。本slice审阅system prompt与所有模型可见合同后确认无行为变化，因此canonical system prompt保持v21、provider adapter contract保持v24、21-tool catalog及empty Effective Context identity不变，所有Session/context schema也不升级。完整决策见[0065：Opt-in Bounded Live Tool Details](./decisions/0065-opt-in-bounded-live-tool-details.md)。

## Trusted Command Result Observability

`run_command`现在除既有model-visible ToolResult外，还由执行器直接产生content-free typed observation，记录process status、exit code或signal、monotonic duration、stdout/stderr captured与total bytes、各自truncation及cleanup completeness。Terminal绝不通过解析ToolResult JSON获取这些事实；同一observation同时生成既有JSON字段与Host事件元数据，避免provider-facing serialization成为可信UI事实来源。

Compact完成行会追加exit或lifecycle status、duration及两路输出byte count；截断与cleanup不完整会明确显示。`/tool-details full`把这些字段展开为最多6行/2 KiB。两种模式都不显示stdout/stderr原文或base64，也不增加argv、credential、absolute path、raw ToolResult或provider payload暴露。Denied、approval rejected/cancelled、preparation failure和executor exception没有execution details；若Action Audit finish持久化失败，仍只报告`outcome-unknown`，不能用process metadata伪装durable action完成。

Observation与result details只在当前live event链路存在，不写Session、Action Audit schema、provider history、profile或Effective Context。本slice审阅全部模型可见合同后确认无变化，因此canonical system prompt保持v21、provider adapter contract保持v24、21-tool catalog、tool schema/order、empty Effective Context identity及所有Session/context schema均不升级。完整决策见[0066：Trusted Command Result Observability](./decisions/0066-trusted-command-result-observability.md)。

## Persistent Inline Terminal Frontend

真实TTY不再按“读一次PromptSession、同步跑完整turn、再创建下一次PromptSession”工作，而由一个non-full-screen `prompt_toolkit.Application`长期持有输入区、状态栏、补全、history、审批焦点和inline scrollback。提交后buffer立即清空并保持新prompt可见；busy期间允许编辑一份draft，但Enter不会排队、插入或触发slash mutation。审批保存并恢复draft，Ctrl-C请求取消，Ctrl-D等待active worker完成清理后退出。

一个closed `TerminalViewState`、纯reducer与有界local queue把单后台worker的assistant、tool、context、usage、compaction和failure事件交给唯一TTY renderer。Assistant delta保持独立FIFO事件，以便每个已收到的stream chunk单独flush；工具、审批、失败和durable final事实不可丢失。Renderer和terminal sink仍是best-effort，不能改变执行、Action Audit或turn commit。One-shot、redirect、injected stream与non-TTY继续走旧同步路径。

`TurnCancellation`贯穿ProjectSession、AgentLoop、provider stream、tool边界、approval broker和`run_command`。Command会轮询取消并执行既有有界TERM到KILL process-group cleanup；blocking provider SDK只能在调用返回或下一stream chunk时观察取消，系统不使用unsafe thread exception injection。该Host-only改造保持canonical system prompt v21、provider adapter contract v24、21-tool catalog、Effective Context identity及全部Session/Action Audit schema不变。完整决策见[0067：Persistent Inline Terminal Frontend](./decisions/0067-persistent-inline-terminal-frontend.md)。

## Terminal Message Hierarchy 与 Hanging Indent

真实TTY把conversation和Host过程信息分成稳定视觉层级。已提交用户消息使用`› `，assistant正文使用`• `；两者都保留两列role prefix，显式换行及按terminal display width产生的自动换行统一从正文列继续。新的conversation message block前使用缩进两列、长度为terminal width约三分之一且最多24格的低强度短分隔线。Plain用户文本先转义terminal controls再按显示宽度折行，assistant Markdown则先从可用宽度扣除role prefix后渲染，避免长行回到终端最左侧或越过右边界。

Markdown stream不再先单独写出`• `。未形成安全render boundary的delta继续留在内存；marker与第一段可见正文在同一次frontend write中出现，后续chunk使用continuation indent。Routine tool、context、usage、compaction和slash output作为缩进Host block，在color模式下使用dim或dim-green；warning、approval、error、partial与durability uncertainty继续保留高对比。`NO_COLOR`只移除ANSI样式，不移除结构。

终端协议无法portable地为单独一行选择更小字号，因此本slice不伪造字号能力，也不引入alternate-screen TUI。One-shot、redirect、无role UI的injected stream、Session、Action Audit及全部模型可见合同保持不变；canonical system prompt仍为v21，provider adapter contract仍为v24。完整决策见[0068：Terminal Message Hierarchy and Hanging Indent](./decisions/0068-terminal-message-hierarchy-and-hanging-indent.md)。

## Host Workbench Navigation 与 Failure Guidance

REPL的Host工作台现在支持分组`/help <session|tools|git|context|provider|input>`、`/session list [count] [open|closed] [model=<name>]`及`/actions [count] [status=<status>] [tool=<name>]`。Session筛选只读取已验证的workspace-bound metadata，结果保持newest-first并显示current/latest及持久provider/model；resume仍要求`latest`或完整Session ID。Audit筛选只使用严格replay的lifecycle status和canonical tool name，不解析result prose，不执行repair、retry或export。

已知context/provider/runtime/authorization/Session失败由共享Host formatter追加保守`Next:`建议；它不会自动retry、声称rollback或把未提交turn与已完成Action Audit副作用混为一谈。常驻终端把typed events映射为准备provider请求、运行具体工具、处理结果、compaction、usage、approval与finalization阶段；`ProjectSession`只在`SessionWriter.append_turn`前发出content-free `TurnCommitStarted`，因此`Saving Session`对应真实durable append，而不是事后猜测。

全部变化只影响Host查询、临时事件和terminal text，不写新Session record，也不进入provider history。Canonical system prompt保持v21、provider adapter contract保持v24、21-tool catalog、Effective Context identity及全部Session/Action Audit schema不变。完整决策见[0069：Host Workbench Navigation and Failure Guidance](./decisions/0069-host-workbench-navigation-and-guidance.md)。

## Assistant Turn Execution Trace Grouping

真实TTY现在把一次用户提交触发的完整AgentLoop执行呈现为一个Assistant Turn，并在用户消息与本轮首个可见输出之间固定留一行。Provider产生的companion/final正文继续以`• `标记；context preflight、tool lifecycle、approval及其diff、usage、ledger、compaction和failure等Host事实则在每个logical line前使用`  │ `轨迹线。若模型没有阶段性正文而直接请求工具，界面从轨迹线开始，不伪造空`•`。两者视觉归属同一turn，但轨迹不会被伪装为模型原话，已有颜色、风险强调与脱敏边界保持不变。

Assistant正文、Host轨迹和后续assistant continuation之间不再插入conversation分隔线。常驻frontend只在`TurnFinished`后画一次低强度短线，因此该线位于最终正文或失败说明之后、下一个live `›`之前；slash command结果仍是turn外Host block。One-shot、redirect、无role UI的injected stream、Session、Action Audit和provider history不变。

这是纯Host terminal presentation变化。Canonical system prompt保持v21、provider adapter contract保持v24、21-tool catalog、ToolArguments v1、ActionIdentity v1、Effective Context identity及全部Session/compaction/Action Audit schema不变。完整决策见[0070：Assistant Turn Execution Trace Grouping](./decisions/0070-assistant-turn-execution-trace-grouping.md)。

## Durable Session Naming 与 Terminal Identity

新Session以`session_header` v2持久保存`New session N`默认名。首个普通assistant回复成功后、turn落盘前，固定标题prompt v1可通过同一pinned provider发起最多3次无工具请求；source最多4096 UTF-8 bytes，output reserve固定512 tokens以容纳provider可能计入输出额度的隐藏推理，最终接受的标题仍最多48 characters和160 UTF-8 bytes。Workspace内自动标题按casefold检查重名，冲突会进入下一次请求的rejected set；三次后或provider失败时，Host使用有界fallback和稳定编号。标题与正文共享每turn最多24次provider invocation，普通循环已用满预算时不会再调用标题模型。

新`turn_committed` v7把首轮`session_name + source(model|fallback)`、正文和全部provider usage写在同一条record中；失败、取消或未提交turn不会留下名称。旧`turn_committed` v1-v6和`session_header` v1继续原样replay，旧无标题turn使用Host确定性兼容显示名而不重写transcript。`/session rename <name>`通过append-only `session_named` v1设置manual名称，`--auto`恢复首轮自动标题；完整UUID仍是精确resume identity。

`/session show`、`/session list`和TTY底栏显示名称并在turn/new/resume/rename后刷新。Slash Host命令现在回显自身`›`输入，并把输入、Host结果和一次短分隔线写成完整块。名称不进入普通provider history、canonical Agent system prompt、tool contract、Action Audit、compaction或Effective Context；system prompt保持v21，独立标题projection使adapter contract升级到v25。完整决策见[0071：Durable Session Naming and Terminal Identity](./decisions/0071-durable-session-naming-and-terminal-identity.md)。

## Session Lifecycle Management 与 Naming Diagnostics

Session现在可以通过`/session archive`和`/session unarchive`写入可逆的`session_archive_changed` v1组织标记。归档不是关闭、删除或切换Session，不改变完整history、runtime绑定、Effective Context、`latest`指针或UUID resume身份；归档Session仍可继续对话，也仍可按UUID或`latest`恢复。重复设置同一状态是幂等操作，不追加无意义记录。`/session show`、列表摘要和TTY底栏都会显示归档状态。

`/session list`新增`active|archived`、精确`model=`和大小写不敏感的名称字面子串`name=`筛选，并可与`open|closed`和1至100条数量上限组合。筛选只读取严格replay后的workspace Session元数据，保持newest-first，不做模糊搜索、名称resume、tag、folder或pin。

首轮自动标题使用Host兜底时，新`turn_committed` v8会把有界原因与标题及首轮turn原子持久化：provider输出上限、provider失败、无效候选、重复标题或provider调用预算耗尽。只有`source=fallback`可携带原因；model标题和普通后续turn不得携带。终端只显示安全分类，不显示provider原文、异常或标题请求内容。v1-v7继续严格replay且不会重写，其中v7标题没有该诊断字段。

这些变化属于Session元数据、Host查询和终端呈现。Canonical system prompt保持v21，provider adapter contract保持v25，21个model-visible工具、ToolArguments v1、ActionIdentity v1、Action Audit v1、compaction和Effective Context identity均不变。完整决策见[0072：Session Archive, Search, and Title Fallback Diagnostics](./decisions/0072-session-archive-search-and-title-fallback-diagnostics.md)。

## Pinned Sessions 与 Snapshot-based Quick Switching

`/session pin`和`/session unpin`通过append-only `session_pin_changed` v1保存可逆收藏状态。Pin不是rename、archive、close或resume，不改变history、runtime binding、Effective Context、`latest`或UUID身份；重复设置同一状态不追加record。旧transcript没有该记录时严格replay为`pinned=false`。Session show、列表摘要和TTY底栏显示收藏状态，`/session list pinned|unpinned`可继续与open/closed、active/archived、model、name及数量筛选组合。

快速切换不把名称或编号升级为身份。`/session switch`默认从严格replay、newest-first的其他Session建立最多10条的进程内快照；`/session switch list`可使用同一过滤器并将上限调整到1至20。每条预览包含编号、名称、完整UUID、turn数量、生命周期/归档/收藏状态、创建时间和持久runtime来源，但不读取对话正文。`/session switch <number>`只从当前快照取出对应完整UUID，随后无论编号有效与否都清空快照。

真正切换完全复用现有`ProjectSession.switch_session`事务：target-aware只读prepare、当前runtime context screening、完整transcript stale/CAS验证、`session_resumed` durable commit、writer transfer及`latest`更新。普通prompt、new/rename/archive/pin、直接`/resume`和任何picker刷新都会清空旧快照，因此旧编号不会在新目录中被悄悄重新解释。Known context拒绝、stale冲突或precommit失败保留当前Session与runtime；commit point后的partial结果继续按原resume语义如实报告。

这是Host-only Session元数据和导航变化。Canonical system prompt保持v21，provider adapter contract保持v25，21-tool catalog、ToolArguments v1、ActionIdentity v1、Action Audit v1、`turn_committed` v8、compaction和Effective Context identity均不变。完整决策见[0073：Pinned Sessions and Snapshot-based Quick Switching](./decisions/0073-pinned-sessions-and-snapshot-quick-switching.md)。

## Read-only Session Inspection 与 Bounded Turn Preview

`/session show`继续无参数显示current Session，同时`/session show <latest|完整UUID>`可严格回放任意目标的元数据而不执行resume。`/session preview <latest|完整UUID> [1-10]`默认选择最近3个、最多10个已提交完整turn；standalone `session preview [selector] --limit N`提供同一投影。REPL selector只接受`latest`或canonical lowercase UUID4，名称、编号和path都不是预览身份。

Preview只投影每个turn最终的user与assistant文本，不重复tool companion text、tool result、Action Audit、usage或compaction summary。终端控制字符先被转义，完整输出最多32 KiB并显式标记截断。完整tool因果和Host执行事实仍分别通过`/tools`、`/actions`及原transcript检查，预览不会把简化显示冒充完整审计。

目标读取使用existing-only、strict replay和`allow_repair=false`，不创建空workspace状态、不获取writer lease、不修复incomplete tail、不append record，也不调用provider。成功或失败都不改变current Session、`latest`、runtime、history、Effective Context、picker snapshot或任何schema。Canonical system prompt保持v21，provider adapter contract保持v25，21-tool catalog及Effective Context identity均不变。完整决策见[0074：Read-only Session Inspection and Bounded Turn Preview](./decisions/0074-read-only-session-inspection-and-bounded-turn-preview.md)。

## Session Search、Turn Navigation、Export、Fork 与 Repair

跨Session搜索以大小写敏感literal query独立匹配每个完整turn的最终user/assistant logical lines，返回完整UUID、1-based turn、role、line与有界excerpt。单次最多扫描10,000个目录entry、选择100个stable UUID顺序的transcript、读取16 MiB、返回100个match并渲染32 KiB；任何candidate/read/match/render截断都会明确说明，no-match只对实际扫描范围成立。`/session turns`使用独立的1-based start和1至10轮count查看搜索定位，不把最近预览语法变成有歧义的offset。

Conversation export通过stdout提供Markdown或export-local JSON v1，只包含Session身份与全部最终user/assistant turn。选择上限为1,000轮和1 MiB文本，完整render上限为2 MiB；超限整体失败，不静默截断。Tool companion、ToolUse/Result、ledger、Action Audit、usage、failure、compaction与raw record都不进入这个可读投影，内部JSONL仍是完整审计来源。

Fork只接受strict source snapshot中的正整数完整turn边界。新Session获得新UUID，并在header后写入`session_forked` v1，持久保存parent UUID、复制turn数量和exact source transcript SHA-256。选中turn的完整provider-neutral items保留ToolUse/Result因果；current ledger直接复制，legacy pre-ledger turn从request/result导出最小一致ledger。复制的provider usage为空，父级Action Audit、failure、runtime event、name、archive/pin和compaction都不复制；末尾runtime record安装调用方当前binding，REPL原子选择child而parent bytes不变。`latest`替换前的失败会持久删除新建child与lock，cleanup失败不会被隐藏；若替换已发生但目录durability未知，则保留可能已被引用的child。ProjectSession若无法构建child AgentLoop，会释放candidate writer lease并保持current内存Session不变。

Doctor使用no-follow descriptor只读分类`valid | repairable_tail | invalid`。只有“严格可回放且newline结束的完整prefix + invalid UTF-8/JSON final fragment”可修复；empty、中部/完整行损坏、完整JSON仅缺最终newline都保持invalid。Repair必须显式调用并获取目标writer lease与existing directory lock，复查descriptor/path identity，先以完整source SHA-256命名创建private durable backup，再只截断fragment并append+fsync现有`recovery` v1。它不resume、不更新`latest`、不切换runtime/current Session，也不修复active writer。

五个阶段均为Host管理面；只有fork新增record-local `session_forked` v1，其他既有schema不前进。Canonical system prompt保持v21，provider adapter contract保持v25，21-tool catalog、ToolArguments v1、ActionIdentity v1、`turn_committed` v8、Action Audit、compaction和`ctx-v3`/`ctx-v4` identity均不变。完整决策见[0075](./decisions/0075-bounded-cross-session-final-text-search.md)、[0076](./decisions/0076-bounded-session-turn-range-inspection.md)、[0077](./decisions/0077-bounded-conversation-export.md)、[0078](./decisions/0078-provenance-linked-session-forking.md)与[0079](./decisions/0079-explicit-session-diagnosis-and-tail-repair.md)。

## Foundation 1D：Bounded Literal Grep 与 Versioned Tool Arguments

模型可见只读工具面扩展为固定顺序的`read_file, glob, grep`。`grep(query, include)`使用与glob相同的portable workspace-relative selector选择non-symlink regular files，再在strict UTF-8 logical lines内执行case-sensitive literal substring search；每个matching line只输出一次compact JSONL，包含POSIX relative path、1-based line number与完整line text。它不支持regex、index、Unicode normalization、`.gitignore`、multiple patterns或context windows。

Grep具有明确hard bounds：最多1,000个candidates、每file 1 MiB、aggregate 16 MiB、200个matching lines和32 KiB model-visible output，并继续受selector的entry/directory/depth bounds约束。Unreadable、oversized、NUL或invalid-UTF-8 selected file均为whole-call safe error；只有match/output cap返回complete JSON records的stable prefix与`{"truncated":true}` sentinel。No-match仅在bounded candidate set被完整搜索时为空成功。读取时再次执行regular/non-symlink与descriptor identity检查，同时保留local single-user TOCTOU边界。

为表达grep的两个参数，in-memory `ToolUse`改用immutable `ToolArguments` v1 canonical JSON object。Foundation 1D当时让新`turn_committed`使用record-local schema v2保存`arguments_version + arguments`；legacy schema-v1 read/glob records在replay时转换为同一generic representation，旧JSONL不重写，resume当时只append v2。后续assistant tool text、multi-tool batch与tool outcome ledger曾依次把writer升级到v3/v4/v5，provider usage audit升级到v6，首轮原子Session标题升级到v7，当前标题fallback诊断再升级到v8，同时保留v1-v7 reader。`turn_failed`新写v2，`context_compacted`新写v4并继续兼容v2/v3；current Effective Context representation为ctx-v3/v4，并继续replay旧ctx-v1/v2 checkpoint。

三个工具继续共享每user turn三次顺序execution预算，AgentLoop和ProjectSession仍显式composition/dispatch而非dynamic registry。Anthropic与OpenAI-compatible ordinary count/create按相同catalog投影exact three schemas，compact summary仍no-tools，parallel calls仍关闭。Adapter contract升级为v5；canonical model system prompt升级为v4并声明literal grep、no-match/truncation解释及仍不可用的write/Bash/regex能力。Generic arguments、prompt与catalog会按设计改变current-binary context IDs，但不重写历史checkpoint。

完整设计见[0021：Foundation 1D Bounded Literal Grep](./decisions/0021-foundation-1d-bounded-literal-grep.md)。

## Foundation 1C：Bounded Workspace Glob

模型可见只读工具面现在包含固定顺序的`read_file`与`glob(pattern)`。`glob`使用workspace-relative、`/`分隔的portable pattern，支持component `*`、`?`、bracket class与whole-component `**`；裸pattern不隐式递归，hidden component必须显式以`.`匹配，也不读取`.gitignore`。结果只包含non-symlink regular files，使用POSIX relative path与deterministic UTF-8 lexical order；目录、special files和所有symlink都不返回或遍历。

搜索有多重hard bounds：pattern最多4096 characters/bytes与64 components，最多200个matches、32 KiB output、10,000个scanned entries、1,000个directories和32层深度。Match/output cap返回stable prefix与`[truncated]`；traversal/depth bound因无法证明完整性而返回安全error，不泄露absolute workspace或raw OS failure。实现只使用stdlib `os.scandir`与component `fnmatchcase`，没有shell或新增dependency；local single-user TOCTOU边界保持诚实可见。

两个工具共享每个user turn三次顺序execution预算。AgentLoop仍显式dispatch，未知工具和limit都形成structured result，provider failure或durable commit failure不会提交candidate turn。一个窄的canonical catalog固定`read_file, glob`顺序，同时驱动Effective Context identity及Anthropic/OpenAI-compatible ordinary count/create schemas；compact summary继续no-tools，parallel calls继续关闭。

Foundation 1C当时为保持append-only兼容，曾以schema-v1 `ToolUse.path`作为read/glob single-string seam，adapter分别投影`{"path":...}`与`{"pattern":...}`；它让旧read-only Session与mixed glob/read turn无需重写即可resume和compact。该临时seam现已由Foundation 1D的`ToolArguments`与record-local turn schema v2取代，但legacy v1 decoder继续兼容。Foundation 1C当时的adapter v4、prompt v3和两工具context identity仍作为历史设计事实保留。

完整设计见[0020：Foundation 1C Bounded Workspace Glob](./decisions/0020-foundation-1c-bounded-workspace-glob.md)。

## Foundation 1B：确定性的受限 read_file 工具循环

REPL 和 `prompt` 命令完成以下最小、可测路径：

```text
终端输入 → AgentLoop（固定 canonical system prompt snapshot + 有序因果上下文）
  → ScriptedFakeProvider → 在当前 workspace 内可选 read_file
  → 结构化 tool result → ScriptedFakeProvider → 最终文本输出
```

Provider 的一次响应只能是最终 assistant 文本或一个 `read_file` 请求。Loop 只有在 provider 结束后才返回最终文本，并且只有该成功发生后，才提交本次尝试中的完整 user 输入、可能的 tool request/result 和最终 assistant 文本。

每个 user turn 最多允许三次文件读取。超额请求会收到结构化上限错误；如果 provider 随后仍再次请求工具，loop 会确定性停止。

`read_file` 只接受解析后仍在当前 workspace 内的相对路径。它拒绝绝对路径、`..` 或符号链接逃逸、缺失路径、目录、不可读文件和无效 UTF-8；最多返回 32 KiB UTF-8 文本并携带截断标记。它不能写入、重命名、删除、执行命令、搜索或访问网络。

默认 `ScriptedFakeProvider` 保持可见回显行为，不会自行请求工具。其 scripted 形式为测试提供确定性工具循环；`demo-read <path>` 将同一条固定链路公开为手动终端验证入口。

`prompt` 是一次性命令，但每次成功 turn 都会自动保存。同一 REPL 中，`/history <count>` 只显示当前 Session 已完成的 user/final-assistant 回合，不显示内部工具数据。

Foundation 1B 原始切片只验证了进程内原子历史；Foundation 3D 进一步将完整 turn 持久化到 workspace JSONL。若在非交互终端中直接运行 `coquo`，程序会提示使用 `coquo prompt "..."` 并以非零状态退出，避免管道或 CI 意外卡住。

详细决策见 [0001：单轮 Loop](./decisions/0001-foundation-0-single-turn-loop.md)、[0002：确定性 REPL](./decisions/0002-foundation-0-deterministic-repl.md)、[0003：内存文本历史](./decisions/0003-foundation-1a-in-memory-text-history.md) 和 [0004：受限 read_file 工具循环](./decisions/0004-foundation-1b-bounded-read-file-tool-loop.md)。

## Foundation 3H：Pre-turn Automatic Context Compaction

普通one-shot与REPL prompt现在会在发送新turn前评估exact initial request：current Effective Context + pending user message + requested output reserve。Known `FITS`且`(input + reserve) * 100 >= window * 80`时最多尝试一次proactive `high_water` compact；known `CONTEXT_EXCEEDED`时最多尝试一次mandatory `overflow` compact。`UNKNOWN`不猜测、不生成summary，fake runtime保持无请求无噪声，`MODEL_OUTPUT_EXCEEDED`则因compact无法修复reserve而直接拒绝。

`PreparedAgentTurn`在history mutation前固定唯一pending `UserMessage`和committed context snapshot。Pending item进入source与candidate assessment，因此判断覆盖真正将发送的request；它不进入summary source、checkpoint、context identity或durable history。Checkpoint成功后prepared turn只rebase committed snapshot，仍以同一个pending tuple发送一次，并且只有完整普通turn成功后才持久化。

Automatic与manual `/compact`共用3F-2的prepare → runtime work → revalidate/commit/install transaction：至少4个完整effective turns、保留最近2个turns、summary更早complete turns、known comparable count、candidate known `FITS`且严格减少pending-inclusive input、checkpoint append+fsync后才安装memory。一个`provider_for_turn()` lease固定provider/route/capability/status/generation，覆盖initial assessment、summary、candidate assessment和完整tool loop，同时阻止switch、另一turn、manual compact、resume transition与close。

每个prompt只有一次automatic attempt，不递归compact，也不在tool continuation或provider error后重试。Proactive failure若仍是安全precommit且原request known `FITS`，会发warning后继续原turn；mandatory failure则保留原overflow rejection并且不发送普通generation。Stale或checkpoint durability不确定时不能继续旧request；若checkpoint已经durable commit而后续generation失败，checkpoint保留，pending turn不提交。

新的`context_compacted`使用closed schema v3，持久化`trigger = manual | high_water | overflow`，并且只有`high_water`携带固定`high_water_percent = 80`。Schema-v2 checkpoint继续按legacy manual provenance replay；trigger只作审计和`/context`展示，不进入`ctx-v2` identity，也不持久化token count、fit report或pending prompt。Typed prompt events只报告安全计量、context ID、turn counts、checkpoint sequence和reason code；one-shot事件写stderr，stdout仍只有model response。

Canonical model system prompt已审阅：automatic timing完全由Host控制，模型仍不能请求compact，既有untrusted Host-summary framing已覆盖compact后的模型输入。因此version 2、exact text与fingerprint不变。完整设计见[0019：Pre-turn Automatic Context Compaction](./decisions/0019-pre-turn-automatic-context-compaction.md)。

## Foundation 3G：Target-aware Resume Prepare/Commit

Startup `--resume` 与 REPL `/resume` 现在先prepare target、构造candidate Effective Context并用当前runtime screening，之后才durable commit。Known context/model-output overflow在任何resume audit、tail repair或latest pointer写入前拒绝；`UNKNOWN`以warning fail open，fake runtime明确screening unavailable且不发provider request。恢复仍只恢复Session state，不按历史binding重建runtime。

`SessionStore.prepare_resume()`是物理只读的一次性独占lease：它要求既有root/directory lock/target lock/latest/transcript，使用`O_NOFOLLOW` retained descriptor重放，并把incomplete final crash tail只记录为pending recovery。Transcript stale token包含device/inode/size/mtime/ctime和exact-byte SHA-256；`latest` selector另有pointer token。Commit在第一笔写前验证transcript、pathname、target lock与latest CAS，因此append、same-size replacement、inode/symlink/lock swap以及count期间latest移动都作为retryable conflict拒绝。显式UUID/path忽略无关latest移动；same-current selector直接返回无写入no-op。

Commit先candidate-replay proposed records，再按`Recovery`（若需要）→`SessionResumed`→atomic latest update执行。`Recovery`允许紧跟`SessionClosed`但保持closed，只有后续`SessionResumed`重新打开。Prepared descriptor/lock在成功后转移给`SessionWriter`，普通append也通过descriptor并校验pathname identity，消除revalidate/reopen TOCTOU。

`SessionResumed`的fsync是语义commit point。Typed result区分precommit/stale、recovery-only、durability unknown、resume-applied/latest-failed和latest-replaced/directory-fsync-unknown；commit point后的错误不再声称“全部未变”或做不可靠rollback。Top-level `--resume ... prompt`把resume evidence写stderr，使stdout只保留最终model response；known reject以exit 2和空stdout结束。

Manager的context-transition lease固定current provider/route/capability/status/generation，并阻止switch、turn、compact和close。Screen使用candidate loop的`effective_context_snapshot()`，所以compacted Session只按summary + retained suffix计量；下一次真实invocation仍执行完整preflight。Canonical model system prompt已审阅：本切片没有模型可见变化，保持version 2、exact text与fingerprint不变。完整设计见[0018：Target-aware Resume Prepare/Commit](./decisions/0018-target-aware-resume-prepare-commit.md)。

## Foundation 3F-2：Controlled Compact Transaction

REPL `/compact` 现在能在保留完整 append-only transcript 与 `/history` 的同时，手动缩短 provider-visible effective context。Foundation 3F-2的固定policy要求至少4个完整effective turns，保留最近2个turns原文，并用当前真实provider对更早projection生成一次summary；fake runtime不可用，该原始切片本身不自动触发，也不重试原user turn。Foundation 3H随后在新turn发送前按known evidence调用同一transaction，但仍不做failed-turn retry。

Compact generation使用独立版本化 prompt和专用 no-tools request。Anthropic native body省略`tools`，OpenAI-compatible同时省略`tools`与`parallel_tool_calls`；count与generation共享同一input projection。只接受正常结束的非空文本，tool call、refusal、truncation与malformed response全部fail closed。

Summary不属于`ConversationItem`或真实turn。Effective state是`Host summary + retained complete-turn suffix`，adapter以明确的untrusted continuation framing投影summary。Normal Agent canonical system prompt升级为v2，说明Host summary是早期conversation context而不是system instruction或新user request。无summary context仍沿用原`ctx-v1` identity；summary-bearing context使用`ctx-v2`。

Session不重写旧行：普通records继续是schema v1，legacy Foundation 3F-2 `context_compacted`是schema v2，当前manual与automatic checkpoint写schema v3。V3增加trigger provenance与可选high-water percentage；mixed replay接受v2/v3并把v2解释为manual，从所有`TurnCommitted`重建full history，从latest checkpoint重建summary/retained suffix，让后续turn同时追加到full/effective。Checkpoint append复用candidate replay validation、O_APPEND、flush/fsync，然后才安装内存effective state。

Transaction在generation前冻结writer/session/sequence、loop、full/effective state与source context ID；generation和candidate assessment结束后重新检查这些事实。Candidate必须与source使用可比较的known count、known `FITS`且严格减少input tokens。任何precommit、stale或persistence failure都不写`TurnFailed`，也不改变effective memory。

`/context`在compact后显示checkpoint source、summary presence、retained real turns与checkpoint sequence，而summary不计入transcript turn/item。完整设计见[0017：Controlled Compact Transaction](./decisions/0017-controlled-compact-transaction.md)。

## Provider-neutral Effective Context Snapshot 与 `/context`

`AgentLoop` 现在明确区分 append-only transcript 派生的 full history、provider-visible effective history 和单次 invocation request。3F-1 中 full/effective history 在 restore、成功 commit 与 resume 后仍完全相等；真实 turn 的初始请求和每次 tool continuation 都从同一个 `EffectiveContextSnapshot` 加上当前 pending suffix 派生，因此没有模型行为变化，但 future compact 不再需要改写 `/history` 或 durable transcript truth。

完整 committed history 使用统一的 strict validator，只接受 `UserMessage, (ToolUse, matching ToolResult)*, AssistantText`；tool pair 必须相邻、ID 匹配且全局唯一。Session replay、loop restore 与 effective-context construction共享该因果规则，同时保留各自的 schema、大小与 provider invocation terminal validation。

Snapshot 对 current system prompt、neutral `read_file` contract 与完整 effective turns做 canonical JSON + domain-separated SHA-256，得到稳定 `ctx-v1-...` content identity。Identity不包含 Session/runtime/provider/audit/token metadata，不持久化到 JSONL，也不声称 transcript tamper-proof。

REPL `/context` 在 `ProjectSession` facade lock 内冻结 context 与 target，显示 source、context ID、full/effective turn/item counts、exact/estimated/unknown input、reserve、两类模型限制、fit与known remaining capacity。该命令不调用 generation/tool、不写 transcript或audit，也不修改 history/runtime。Fake runtime明确 unavailable；OpenAI-compatible使用本地 estimate；official Anthropic exact inspection可能调用 count-only `messages.count_tokens`，但不调用 `messages.create`。

Session schema继续为v1，不保存 effective context/checkpoint/count。详细决策见 [0016：Provider-neutral Effective Context Snapshot](./decisions/0016-provider-neutral-effective-context-snapshot.md)。Canonical model system prompt已审阅；Host-only inspection和full-history passthrough不改变模型可见能力，因此version 1与fingerprint不变。

## Target-aware runtime switch UX

长生命周期 runtime 的 `/provider use`、`/model` 与对应 `ProjectSession` API 现在会在提交 candidate 前，对当前 committed conversation context 做 destination-specific screening。`AgentLoop` 构造当前 canonical system prompt 与 exact committed causal history 的只读 snapshot；空 Session 保持 `history=()`，不会为了计量伪造 user message。

Adapter 的计量路径接受空历史或以 `AssistantText` 结束的完整 committed history，但真实 `respond()` 仍严格要求以 `UserMessage` 或 `ToolResult` 结束的 invocation history。Anthropic/OpenAI-compatible 的 count 与 create 因而继续共享同一 native projection，又不会放宽真实发送的因果验证。

Manager 使用已经准备好的同一个 provider/route/capability candidate：

- known context/model-output overflow 在 active selection 与 client 交换前抛 `RuntimeSwitchContextError`，关闭 candidate，旧 runtime、selection 与 generation 不变；
- `FITS` 提交并返回 count method/value、reserve 与 window；
- `UNKNOWN` fail open，但 REPL 以 warning 明确 compatibility 未确认、没有删除历史、下一次真实 invocation 仍会 full preflight；
- fake destination 不需要 compatibility report。

`ProjectSession` 在 facade lock 内冻结 history、执行 screening/commit，再追加既有 schema-v1 `RuntimeChanged`。若 runtime 已切换但 audit append 失败，会抛携带已生效结果的 `RuntimeSwitchAuditError`，不误报为未切换，也不做不可靠 rollback。Transcript binding 现在保存真实 runtime generation。Rejected switch 不写 conversation、`TurnFailed` 或 runtime-change record。

Foundation 3E 的原始切片不处理 `/resume`/`--resume` 的切换前判断；该边界现已由 Foundation 3G 的只读 prepare、current-runtime screening 与 durable commit transaction 补齐。Runtime switch 本身仍不实现 compact、历史删除或自动新 Session。

详细决策见 [0015：Target-aware Runtime Switch UX](./decisions/0015-target-aware-runtime-switch-ux.md)。Canonical model system prompt 已审阅；这仍是 Host-side runtime control，version 1 与 fingerprint 不变。

## Target-specific request counting 与 per-invocation preflight

Runtime 现在会把 provider client、exact route、context/model-output capability 与 redacted status 固定为完整 turn snapshot。Snapshot 是唯一的 provider invocation 入口，因此初始请求、每次 `read_file` continuation 和工具上限后的最终请求都会重新 preflight。

判断明确区分三个概念：context window、模型最大输出，以及当前 route 的 requested output reserve。`input + reserve == window` 允许；已知 `>` 时在发送前抛出 typed local error；任一必要事实 unknown 时不猜测并允许 provider 最终裁决。失败 turn 不提交 conversation history，只追加安全的 `TurnFailed` audit record。

Anthropic official endpoint 使用官方 SDK `messages.count_tokens` 对与 create 共用的 model/system/messages/tools projection 做 exact count；失败安全退化为 compact UTF-8 JSON 的 `ceil(bytes / 4)` estimate。OpenAI-compatible Chat Completions 始终使用同形 local estimate，不盲调其他协议的 count endpoint。

Profile registry schema v4 增加 `model_max_output_tokens` override；private discovery cache schema v2 可逐字段保存 context 与 model-output positive limits。`route`、`/status` 和 `/provider current` 展示两个限制与 requested reserve，但不记录成功请求的 last-token meter。Foundation 3H现在消费新turn发送前的fit report决定是否compact；每次真实invocation的preflight仍是最终gate。

详细决策见 [0014：Target-specific Request Counting 与 Preflight](./decisions/0014-target-specific-request-counting-and-preflight.md)。Canonical model system prompt 已审阅；本切片只增加 Host 发送前控制，没有模型可见能力变化，因此保持 version 1 与原 fingerprint。

## Provider-owned model context capability

Runtime 现在能在不伪造未知限制的前提下解析当前 exact endpoint/model 的 context window。解析优先级固定为：

1. 命名 profile 的 exact override；
2. 只匹配官方 provider/endpoint/exact model 的 built-in catalog；
3. fresh private XDG discovery cache；
4. provider-owned live discovery；
5. `unknown`。

Anthropic 官方 endpoint 复用同一个官方 SDK client 的 Models API。Generic OpenAI-compatible `/models` 不存在统一 context metadata contract，因此不会被盲目探测。

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

`provider show` 将用户配置标为 `context window override`；离线 `route` 和 runtime `/status` 显示 resolved value 与 source。成功 discovery 只进入：

```text
${XDG_CACHE_HOME:-~/.cache}/coquo/model-context-capabilities.json
```

Cache 不保存 credential value、raw provider body 或 Session 内容。Profile registry schema v3 reader 兼容 v1/v2/v3，写操作只升级实际写入层，`provider migrate` 可显式升级。

这一切片只建立容量事实，尚不计算当前请求 token、不阻止超限请求，也不自动 compact。详细设计见 [0013：Provider-owned Model Context Capability](./decisions/0013-provider-owned-model-context-capabilities.md)。

## Resume Runtime Binding 与首个工具动作

恢复Session始终使用调用方当前runtime，不会从历史transcript重建provider。旧`session_resumed` v1只重新打开lifecycle，却不更新replay中的current binding；因此恢复后的纯文本turn会在最终`turn_committed`时自然切换binding，但若provider首先请求工具，严格的`action_requested`校验会在commit前发现当前runtime与历史binding不同并安全失败。

新`session_resumed`使用record-local schema v2，在同一个append+fsync语义commit point内记录已经完成context screening的当前redacted `BindingSnapshot`。Startup `--resume`与REPL Session切换都传入其固定context-transition runtime；candidate replay先安装该binding，恢复后的首个Action才可继续经过不放宽的binding equality与Action lease校验。低层`SessionStore.open`未显式传入binding时沿用replay状态。

旧v1记录继续读取且不重写；v1不允许binding字段，v2必须有一个完整有效binding。`SessionResumed` fsync、latest pointer partial outcome、resume CAS、model history和Effective Context identity均保持原语义。Canonical system prompt保持v25，provider adapter保持v26，21个tool schema、ToolArguments、Action Audit、Task records与context representation均不变。详见[0091：Resume Runtime Binding at the Durable Commit Point](./decisions/0091-resume-runtime-binding-at-the-durable-commit-point.md)。

## 自适应前台 Task 编排

Task现在会把当前completion proposal对应的Host/reviewer检查以有界事实投影给下一Stage。新`reflection` Stage严格关闭工具，只能用`TASK_REFLECTION_JSON`提出`continue`、`correction`、`revise-plan`、`needs-human`或`fail`建议；Host只在Stage对应的普通Session Turn已经提交后追加`task_reflection_recorded`。Reflection不能执行、验收、授权或完成Task。

`correction` Stage继续走普通AgentLoop、PermissionGate、approval、Action Audit、工具预算、取消与Session原子提交。Correction产生的新completion proposal会使旧proposal的check/verification失去当前效力而保留历史。后续计划提案使用schema v2，记录直接前序plan、修订原因及可选reflection来源；旧v1仍可回放，任何修订计划都必须重新显式accept。

`/task drive <id> [1-16]`实现有界、可取消、纯前台的状态机：它可提出初始计划、执行已接受步骤、运行确定性Host检查、在失败后反思并执行一次建议的Correction/继续，或提出修订计划。它会在pause、恢复要求、预算、Stage上限、计划待接受/耗尽、人工证据、independent reviewer、manual completion或reflection升级时准确停止。Driver不会自动调用可能产生token/API费用的independent reviewer；`/task next`只读显示下一决定及费用边界。

`task_pause_changed`只阻止自动Driver，人工Stage和管理命令仍可用。`task_context_checkpoint`保存来源sequence、checkpoint链、accepted plan进度、current completion Stage、未解决条件编号和latest reflection ID；它不保存对话、工具参数或完整输出，必须经candidate replay及append+fsync后生效，完整Task transcript永不删除或重写。Task prompt可使用checkpoint加较短的recent Stage suffix。

Canonical system prompt升级为v26；provider adapter保持v26，21个tool schema与顺序、ToolArguments v1、普通Turn预算、Session/Action Audit schema及`ctx-v5`/`ctx-v6`representation均不变。无项目指令的empty full-context ID更新为`ctx-v5-4f33f80622dd368a51b4046c5292951f2dd42fdb05b3d9be798dfa6b5f2457a4`。详见[0092：Adaptive Foreground Task Orchestration](./decisions/0092-adaptive-foreground-task-orchestration.md)。

## Task Proposal Control Boundary

`/task`系列继续是人类/Operator命令，Foreground Driver继续由Host拥有；未来模型Task接口只进入独立proposal adapter，不需要生成slash command，也不能直接操作`TaskStore`。底层`ConversationRequest`与`PreparedAgentTurn`现在可固定一个精确工具名子集；Anthropic count/create和OpenAI-compatible estimate/create按照同一子集并保持全局canonical顺序。Provider若请求未暴露工具，会在任何dispatch之前失败。

AgentLoop新增与`ActionDispatcher`分开的Task-control dispatch seam。Control call必须是该assistant response唯一的工具调用，并在处理后关闭工具，只允许下一次text-only finalization；它仍进入普通ToolUse/ToolResult因果链、共享Turn预算、Session transcript和Host tool ledger，但proposal本身不取得Action lease，也不因为协调请求创建Action Audit。

内部`TaskControlProposal`固定绑定proposal kind、Task/Stage identity、pinned Effective Context ID、tool-use ID与有界ToolArguments。成功dispatch必须携带匹配proposal；AgentLoop只有在完整Session Turn已成功commit后才调用Host proposal sink。恢复时不相信assistant或ToolResult正文，而要求committed Turn内存在唯一匹配control call，且Host ledger对相同ID和tool name记录`succeeded`。当前尚未加入任何公开Task coordination tool，因此普通模型行为、21-tool catalog与system prompt v26不变；provider adapter contract因精确子集投影升级为v27，`ctx-v5`/`ctx-v6`、Session/Task/Action Audit schema与8/32/24预算不变。详见[0094：Task Proposal Control Boundary](./decisions/0094-task-proposal-control-boundary.md)。

## 模型可见 Task 协调工具

完整canonical catalog现在在原21个普通工具后追加`task_propose_plan`、`task_report_reflection`、`task_report_blocker`和`task_propose_completion`。普通prompt仍只曝光原21个工具；planning Stage只获得有界read/Git观察加plan/blocker，reflection Stage只获得reflection/blocker，execution与correction Stage获得21个普通工具加completion/blocker。Provider若请求不属于当前Stage精确子集的工具，会在dispatch前失败。

四个工具只提交提议，不是filesystem Action：它们不取得Action lease、不经过PermissionGate、也不创建Action Audit，但仍进入ToolUse/ToolResult因果、共享Turn预算、Session transcript与Host tool ledger。Control call必须独占一个assistant response，处理后只允许text-only finalization。Blocker使用封闭category，令当前Task进入blocked并让Driver以`model-blocked`停止，但不能授予权限、补齐证据、终止或完成Task。

Proposal sink只暂存与Task、Stage、context、tool-use ID和规范参数绑定的immutable值。耐久顺序固定为`stage_started -> Session Turn append+fsync -> stage_committed -> Task proposal record`；因此Session/Stage提交失败不会写Task proposal，而Task最后append失败可从精确committed Turn与成功Host ledger恢复。同一tool-use ID和相同规范参数的恢复是幂等的，参数变化、跨Task重复ID或同Stage不同proposal会拒绝。新plan/completion/reflection/blocker记录分别使用schema v3/v2/v2/v1；旧plan v1/v2、completion v1与reflection v1继续读取且不重写。旧`TASK_PLAN_JSON`、`TASK_REFLECTION_JSON`及`TASK_COMPLETION_PROPOSAL`只保留历史Stage恢复兼容。

Canonical system prompt升级为v27，provider adapter contract升级为v28。Effective Context representation仍为`ctx-v5`/`ctx-v6`，catalog内容变化使无项目指令的empty full-context ID更新为`ctx-v5-63362449120e69a39d2a03b22c8c1937ee66d2fd67d065d4e3ccfd3466d88aa7`。ToolArguments v1、Session/Action Audit/compaction/Task Stage schema、8/32/24预算、acceptance policy及workspace硬边界不变。详见[0095：Model-visible Task Coordination Tools](./decisions/0095-model-visible-task-coordination-tools.md)。

## 模型提议的 Task Admission

普通Prompt现在在原21个工具后额外曝光`task_propose_start(objective, reason, acceptance_criteria)`，模型可说明一项工作为何需要多个有界Stage。该调用只形成proposal：它不创建或接受Task、不执行Stage、不取得Action lease、不经过PermissionGate且不创建Action Audit。它必须独占assistant tool response、共享8/32/24预算，并在receipt后强制text-only结束；四个既有Stage协调工具在普通Prompt中仍不可用，Task Stage也不获得`task_propose_start`。

Immutable `TaskAdmissionProposal`把目标、原因、1-16条验收条件、pinned Effective Context ID和tool-use ID绑定到deterministic `tap-v1-...` identity。Pending状态不另写重复record，而是只从durable committed Turn派生：replay要求exact ToolUse、匹配的non-error ToolResult receipt及相同ID/name的Host ledger `succeeded`事实。Assistant文字、未提交turn或失败ledger都不能制造proposal。

用户通过`/task proposals [pending|accepted|rejected|all]`与`/task proposal show|accept|reject|drive`只管理current Session的proposal。`accept`第一次调用只读预览规范name、budget、completion policy、prepared criteria以及configuration/confirmation SHA-256；携digest再次确认且candidate仍一致时，才创建带`task_admission_origin` v1的Task并向源Session追加`task_admission_resolved` v1。Origin同时持久化两个digest；若Task创建成功但Session append失败，完全相同的重试会找回同一个Task并补写resolution，不同配置或confirmation则拒绝。接受不调用provider，`drive`才把已接受proposal交给现有有界前台Driver。Reject只追加可选原因的rejected resolution且不创建Task；pending/rejected proposal不能drive。

Canonical catalog现在共26个定义，普通Prompt曝光22个，Task Stage精确子集不变。知情接受与Driver交接是Host/terminal生命周期变化，因此system prompt保持v28、provider adapter contract保持v29，Effective Context仍为`ctx-v5`/`ctx-v6`，无项目指令的empty full-context ID保持`ctx-v5-0112c304e7ae0718fad6efdc4e7f5b258d267d9922854d3846fe76f1e594abf0`。`turn_committed` v8、ToolArguments v1、Action Audit、compaction、预算及旧Session/Task transcript均不变且不重写。Provider启动失败保留accepted admission和准确failed Stage；重启后可继续drive。若Session Turn已提交但Task proposal append失败，`/task recover`只从精确committed ToolUse与成功Host ledger补写，不重跑provider或重复Stage。自动接受、accept后自动Driver和跨Session隐式修改仍未实现。详见[0097：Informed Task Admission and Foreground Handoff](./decisions/0097-informed-task-admission-and-foreground-handoff.md)。

## 自然语言 Task 生命周期交接

普通Prompt新增`task_accept_admission`、`task_accept_plan`和`task_confirm_completion`，把当前用户的明确自然语言决定转换成封闭结构化请求。模型不生成slash command，Host也不对“OK”“同意”等文本做关键词匹配；模型负责理解语言，Host只接受精确pending admission、latest unaccepted plan或current completion proposal。模糊话语、模型自身建议、项目指令、文件、工具结果和summary都不能充当人工授权。

生命周期工具dispatch时不修改Task。Host先把请求绑定到current Session、pinned Effective Context、tool-use ID、subject及confirmation SHA-256/plan ID/completion Stage ID；AgentLoop提交完整普通Session Turn和成功tool ledger后，post-commit sink再恢复精确因果、复核stale状态并调用既有Task API。Admission与plan接受随后产生typed foreground handoff：常驻终端在旧worker有界清理后自动启动同一个`drive_task`，普通REPL在`prompt()`返回后接续；两者都不伪造user message或slash command。One-shot只提交生命周期状态，不读取stdin或偷偷进入交互循环。

Completion confirmation只能用该直接user Turn为未解决的`human` criteria写入证据；任何未验证Host-check或independent-reviewer条件都会在成功tool receipt前拒绝。三个工具均不能授予filesystem权限、批准Action、绕过Task预算或伪造验收来源。`/task`继续用于自定义admission配置、精确预览、审计、拒绝、暂停、恢复、independent review及高级控制，但不再是普通成功路径的必经入口。

Catalog由26增至29个定义，普通Prompt由22增至25个工具；Task Stage最小子集不变。Canonical system prompt升级为v29，provider adapter contract升级为v30，无项目指令的empty full-context ID更新为`ctx-v5-d7662f867a8ebb6f1be1be18eaa0090ef96fb22547cd3a9d7104dc2f69a0328e`。Effective Context representation、ToolArguments v1、8/32/24预算、Session/Task/Action Audit schema及旧transcript不变且不重写。详见[0098：Natural-language Task Lifecycle Handoffs](./decisions/0098-natural-language-task-lifecycle-handoffs.md)。

## 可恢复的 Provider Tool 参数校验

Anthropic与OpenAI-compatible adapter现在区分“无法安全表示的provider响应”和“可规范保存但违反普通工具具体schema的参数”。前者包括坏JSON、非object、未知工具、重复或无效ID、超过全局16 KiB的参数，以及无效Task协调参数，仍以`response_invalid`安全终止且不提交Turn。后者会先冻结为ToolArguments，再由既有Host工具边界返回匹配的错误ToolResult；下一次continuation原样回传该ToolUse/ToolResult因果，让模型在同一Turn缩短或修正调用。

该变化不放宽任何执行边界。`write_file`的provider schema新增`maxLength: 4096`提示，而Host仍同时强制4096 characters和4096 UTF-8 bytes；PermissionGate和approval不能提高限制。Task proposal/lifecycle工具继续在adapter层完整校验，不能用错误参数接近Task耐久状态机。

Provider adapter contract升级为v31；canonical system prompt保持v29。Catalog数量和顺序不变，但`write_file` exact schema使无项目指令的empty full-context ID更新为`ctx-v5-e681ce5f35a3bd5b4d0591912d49119c767e97ad87b9ecad6806777c3a6caecd`。Effective Context representation、ToolArguments v1、Session/Task/Action Audit schema、8/32/24预算和旧transcript均不变且不重写。详见[0099：Recoverable Provider Tool Argument Validation](./decisions/0099-recoverable-provider-tool-argument-validation.md)。

## TTY Host 包装与进程内命令历史

常驻TTY现在把灰色Host block和Turn内`  │ `轨迹按当前显示宽度转换为有界视觉行，再为每一行应用相同缩进或轨迹前缀。超长context、tool、usage、failure与slash结果因此不会依赖终端在右边缘自行折行后回到第0列；非TTY、重定向输出、assistant Markdown及approval内部样式保持原路径。

Prompt history启动时仍从当前Session最多1000条已提交user prompt建立，但当前进程每次接受的普通prompt或单行slash command都会立即进入同一有界内存历史，供Up/Down与Ctrl-R召回。Slash history不写Session transcript、Action Audit或provider history，进程退出即消失；Session切换会以目标Session历史替换普通prompt来源，并保留触发切换的slash command。Canonical system prompt v26、provider adapter v26、Effective Context identity及全部持久schema不变。详见[0093：TTY Host Wrapping and Process-local Command History](./decisions/0093-tty-host-wrapping-and-process-local-command-history.md)。

## 常驻活动提示与 Task 输出对齐

常驻TTY在输入框上方新增一行有界活动提示。普通turn以`Preparing turn`开始，Task worker以`Preparing Task Stage`开始；随后typed frontend事件会把文字更新为provider准备、模型回复、具体工具执行、审批、compaction、Session保存或Task生命周期处理等阶段。该行只显示文字，不包含符号或动画，并在回到`Ready`后整行隐藏。活动文字只来自Host状态，不包含file content、完整argv、provider载荷或Task正文。

Assistant完整回复现在始终复用`• `和两空格悬挂缩进，包括Task编排中流式完成文本不一致时的防御性回退；plain streaming在模型显式换行后也会恢复两空格续行前缀。Markdown、灰色Host block及`  │ `轨迹仍沿用各自既有的display-width包装。该活动行是瞬时prompt-toolkit UI，不进入Session/Task transcript、Action Audit、provider history、compaction、Effective Context或Eval证据。Canonical system prompt保持v29、provider adapter contract保持v31，全部模型可见、预算及持久schema契约不变。详见[0100：Persistent Activity Indicator and Task Output Alignment](./decisions/0100-persistent-activity-indicator-and-task-output-alignment.md)。

## `turn_committed` v5 继承内容兼容性

`turn_committed` v3引入普通`tool_use.assistant_text`，v4引入原子的`assistant_tool_batch`，v5在这些既有能力上增加Host tool ledger。历史v5 writer因此会把继承字段与ledger同时持久化，包括无companion text时仍存在的`assistant_text: null`。当前codec曾在手写版本集合中漏掉v5，导致严格回放把合法字段误报为unknown，并可能在首轮Session名称查重扫描历史Session时阻止新turn提交。

Item codec现在按能力引入版本表达继承：所有已支持的v3及以后schema读写普通assistant companion text，v4及以后读写assistant tool batch；支持版本仍是闭合的v1-v8集合，不接受未知未来版本。v1/v2拒绝新字段、v5 ledger严格校验、完整causality和旧transcript bytes均保持不变。Canonical system prompt保持v29、provider adapter contract保持v31，Effective Context、Session schema编号及其他持久契约不变。详见[0101：turn_committed v5 Inherited Assistant Content Replay](./decisions/0101-turn-committed-v5-inherited-assistant-content-replay.md)。

## 有界独立 Brave/Tavily 网页搜索

`web_search(query, max_results)`为Coquo增加第一条Host拥有的公共网页搜索路径。模型只提供统一query和结果数；Host选择固定Brave或Tavily Search API，不接受模型指定endpoint，也不读取结果页面。因此它和后续Provider原生搜索、MCP搜索及通用`web_fetch`保持不同的调用因果与来源标识。普通Prompt以及Task planning、execution、correction Stage可使用该工具，reflection Stage不开放。

Brave走固定GET与subscription-token header；Tavily走固定Bearer POST，并固定basic search、单来源一个chunk、关闭自动参数、生成答案、raw content和images，Tavily官方将其计为一次basic-search credit。Host把query限制为512字符/2 KiB、结果数限制为1至10，固定15秒timeout、256 KiB response和32 KiB JSONL输出，并最多解析100条原始结果。Transport禁止redirect且只接受JSON；两种返回都归一为保留provider顺序的title、URL、snippet、domain和显式backend，过滤非HTTP(S)、带credential、含控制字符、畸形、超长及重复URL。第三方结果始终是不可信数据。

搜索属于新的`network-read` action：`read-only`和`workspace-write`拒绝，只有`danger-full-access`按正交的`ask | auto`策略继续。底层`WebSearchTool`可从一个有效key解析backend，两个key时可用`COQUO_WEB_SEARCH_BACKEND`消歧；ADR 0103随后规定普通ProjectSession启动时总是关闭独立来源，只有REPL的显式`/search use brave|tavily`才激活。`/search status|sources`只读检查，`/search reset`恢复Provider原生默认或关闭全部来源。第一个激活来源是当前唯一执行的primary；额外来源只建立未来fan-out接口，不会被请求或计费。命令配置仅在当前进程生效，不写Session，也不调用provider。Ask在网络请求前显示完整query、数量、实际backend及对应额度提示；query和不含credential的backend配置fingerprint参与exact ActionIdentity、approval binding和durable Action Audit，普通live摘要及`/actions`列表隐藏query正文。凭据绝不进入模型参数、ActionIdentity、ToolResult、Session或审计。Timeout或transport不确定返回`partial`并禁止自动retry，因为请求或计费可能已发生。

Catalog现在包含22个ordinary tools和30个总定义。Canonical system prompt升级v30、provider adapter contract升级v32，empty full-context identity变为`ctx-v5-468d2b764f1b20902080a07d4a00f027eb531ea5651cc90c74b681956bbc80b9`；ToolArguments v1、ActionIdentity v1、`ctx-v5`/`ctx-v6`representation及Session、Task、Action Audit schema均不变，旧transcript不重写。非持久化ApprovalPreview升级v2以携带所选backend；ActionPrecondition增加不含secret的configuration SHA-256种类而不改变ActionIdentity版本。确定性测试通过注入transport覆盖双后端协议、选择、权限、审批、审计、截断、坏响应和不确定失败，不访问真实网络或消耗API额度。详见[0102：Bounded Independent Web Search](./decisions/0102-bounded-independent-web-search.md)。

## Provider 原生网页搜索

Provider preset、消息protocol与原生搜索adapter现在分别建模。Profile schema升级v5，并可选择`auto`、`none`、一个已实现adapter，或导入`custom-manifest-v1`。Catalog目前预置Anthropic、OpenAI、xAI、DashScope、OpenRouter、DeepSeek、Zhipu、Moonshot、Ark、Hunyuan、Qianfan、Ollama与local；Anthropic、DashScope、OpenRouter声明原生搜索，OpenAI只对名称含`search-preview`的model声明，其他preset和custom默认不可用。Custom可选择OpenAI-compatible或Anthropic-messages protocol。旧Profile无需保留，可按v5重建；store仍提供低成本旧schema读取。

Session启动时，当前route声明原生搜索就默认激活`provider`，否则不激活任何来源；Brave和Tavily即使存在key也始终默认关闭。`/search use provider|brave|tavily [...]`显式切换进程内有序来源，`/search reset`恢复Provider默认，Provider/model切换也重置为新route默认。当前只执行第一个primary。Provider原生搜索属于provider generation，不是Coquo ToolUse，不消费普通tool request，不进入PermissionGate、Action lease或Action Audit；选择独立来源时会关闭Provider搜索并重新曝光Host `web_search`。

固定adapter投影Anthropic server tool、OpenAI `web_search_options`、DashScope `extra_body.enable_search`及OpenRouter server tool；已支持citation会追加为最终assistant正文的有界Markdown `Sources:`，从而随Session普通历史持久化。为避免把厂商server-tool stream事件误解析为Host调用，原生搜索当前采用buffered provider invocation，再向终端发送一个完整text delta。Custom manifest只允许有界`extra_body`、一个非function server tool及预置citation格式，拒绝protected request字段、credential形字段、endpoint/header、代码、自定义parser与超限结构；CLI只在Profile创建/替换时读取并规范保存内容，不保存源path。

Canonical system prompt升级v31、provider adapter contract升级v33，Profile fingerprint升级v4，empty full-context identity变为`ctx-v5-9ec8e77ded21f83ef65f66cb8c54d0e1c79e64d19bbfaa988e9a7d919b1d1e80`。ToolArguments、ActionIdentity、Session、Task、Action Audit、compaction及provider usage schema不变。完整边界见[0103：Provider-native Web Search](./decisions/0103-provider-native-web-search.md)。

## OpenAI Responses protocol 与 Provider-owned history

Coquo现在把`openai_responses`作为与Anthropic Messages、OpenAI Chat Completions并列的一等wire protocol。OpenAI内置route使用Responses；DeepSeek按model选择，`deepseek-v4-flash`使用Responses并声明官方Provider原生`web_search`，其他DeepSeek model继续使用Chat Completions且不猜测搜索能力。Custom Profile也可显式选择`openai-responses`。旧V4 Flash Chat Profile仍可读取并保持Chat语义，但不会静默获得原生搜索。

Responses adapter发送stateless完整history，以`instructions`和`input`分离system policy与消息，固定`store=false`，并同时投影Host function tools及可选Provider `web_search`。Host `ToolUse/ToolResult`分别映射为使用同一`call_id`的`function_call/function_call_output`；Provider返回的`reasoning`和`web_search_call`则进入新的有界`ProviderOwnedItem`，由`ProviderResponseEnvelope`交给AgentLoop随turn保存和后续原样回传，但绝不进入Host dispatch、PermissionGate、工具预算或Action Audit。未知hosted tool、重复ID、未完成item及坏结构均fail closed。

Responses stream以语义event的terminal response object为最终真相，增量发送`response.output_text.delta`，并在结束时补入规范化citation；`response.incomplete`输出上限保留usage与partial observation但不提交turn。无工具的Session标题与compact调用允许解析后丢弃其独立reasoning，但拒绝Provider工具调用。新`turn_committed`使用schema v9保存Provider-owned item，v1-v8继续只读replay且不重写；Effective Context升级为`ctx-v7`/`ctx-v8`，当前empty full-context ID为`ctx-v7-a9178c934e67352a98ba3641b927acc250d800c1af8d9d1de1bfaa2f2028a6e7`。Provider adapter contract升级v34，system prompt仍为v31。完整边界见[0104：OpenAI Responses Protocol and Provider-owned History](./decisions/0104-openai-responses-protocol-and-provider-owned-history.md)。

## Provider Search Resilience、Controls 与 Observability

真实DeepSeek Responses可能在整体`response.completed`时保留一个`status=failed`的`web_search_call`，例如Provider内部`open_page`被`SSRF_BLOCKED`；兼容中转也可能把可选`annotations`返回为null、单个对象或嵌套`url_citation`。Coquo现在把failed搜索调用作为合法Provider-owned终局事实原样保存，仍拒绝completed Response里的非终局状态；citation兼容上述有界形状，危险或畸形单条只丢弃并显示内容无关warning，不再推翻有效正文。未知hosted tool、重复ID、坏required content和不完整Response继续fail closed。

进程内Provider搜索新增`auto|required`模式、最多20个canonical allowed domains及`low|medium|high` context size。OpenAI Responses支持三者；Anthropic只支持domain，OpenAI Chat search只支持context，不支持的adapter/option组合明确拒绝。`/search mode|domains|context`只修改当前runtime，并在reset或Provider/model切换时恢复默认。终端以独立低强度`Provider search:`轨迹显示阶段与调用数、失败数、动作类型、来源数、接受/丢弃citation数；`/session preview`与`/session turns`从既有v9 Provider-owned item派生相同的无正文摘要，不显示query、URL、页面内容或reasoning。

有序来源现在表示primary加显式的model-mediated fallback：`/search use provider tavily`保持Provider搜索为primary，同时把Tavily支持的Host `web_search`暴露为fallback。模型只有在同一history观察到Provider搜索失败或结构化citation不可用后才能请求它；Host不猜query、不自动请求、不并行fan-out，也不绕过`network-read`、PermissionGate、approval、Action Audit、额度提示或普通预算。Canonical system prompt升级v32，provider adapter contract升级v35；Effective Context representation仍为`ctx-v7`/`ctx-v8`，但current empty full-context ID更新为`ctx-v7-3ac4ba4e6ffa39c1184cfff6cc4200eb30607553fdf886451c0d967765ff0432`。其他持久schema不变。详见[0105：Provider Search Resilience, Controls, and Observability](./decisions/0105-provider-search-resilience-controls-and-observability.md)。

## Bounded Fetch、Structured Read 与 Controlled Transfer Tools

普通工具新增`web_fetch`、`compare_files`、`git_blame`、`git_refs`、`json_query`、`checksum_file`、`archive_list`、`move_directory`和`download_file`。Provider adapter不再用易漂移的catalog数字下标映射Task工具，而是按canonical名称选择schema；Anthropic Messages、OpenAI Chat Completions与OpenAI Responses继续投影同一套provider-neutral定义。六个本地观察工具统一属于`workspace-read`：它们分别限制UTF-8 diff、current-HEAD blame、local refs、strict JSON Pointer、256 MiB流式SHA-256及ZIP/未压缩TAR只读metadata，均不执行任意命令或archive extraction。

`web_fetch`与`download_file`共享一个标准库public-web GET transport：仅HTTP(S)标准端口，拒绝credential URL及任何非public或混合DNS结果，连接固定到已验证IP且保留Host/TLS hostname，每次redirect重新验证，禁止proxy、cookie、auth、body、自定义header、压缩响应与JavaScript。`web_fetch`是`network-read`，最多20秒、512 KiB body与64 KiB输出；`download_file`把远端读取和workspace原子安装合并成一个`network-write`，最多30秒与16 MiB，并在网络前后复查目标状态。`network-write`在read-only/workspace-write均deny，只有danger-full-access按ask/auto继续。

`move_directory`使用Linux `renameat2(RENAME_NOREPLACE)`，只允许同文件系统移动到missing destination，拒绝symlink parent、descendant destination、replacement、stale state和不支持原子no-replace的平台。三类新action都有独立ApprovalPreview v3；Routine terminal摘要继续隐藏URL、query、pointer和正文，ask审批则显示授权所需的exact URL/path。Transport或durability不确定性保持partial且不得自动retry。Canonical system prompt升级v33，provider adapter contract升级v36，empty full-context ID变为`ctx-v7-d9d80c3188613943154a2c3f8df40062d52ff14fdb19b3b8628d557e81e13c95`；Effective Context仍为`ctx-v7`/`ctx-v8`，所有持久schema及旧transcript replay不变。详见[0106：Bounded Fetch, Structured Read, and Controlled Transfer Tools](./decisions/0106-bounded-fetch-structured-read-and-controlled-transfer-tools.md)。

## Unified Extension Contract 与 ToolSet Snapshot

MCP接入前，所有现有模型可见工具先迁入同一套不可变`ExtensionToolContract`。每份contract同时绑定精确provider-neutral schema、`builtin | mcp | extension`来源及generation、`host-action | task-*`执行归属、`direct | deferred`暴露方式和允许的`PermissionAction`集合，并生成覆盖全部字段的content identity。Generation-one `ToolRegistrySnapshot`成为catalog、终端权限标签和未来extension来源的统一事实；`/tools catalog`可以检查registry、contract、source generation与exposure，不再维护重复的权限展示表。

每个普通Turn在prepare时从一个Registry generation冻结精确`ToolSetSnapshot`。Provider count与create都投影该快照内的原始定义；Provider若返回快照外工具会在dispatch前拒绝且不提交Turn。Compaction rebase继续使用同一快照，ActionLease也通过新Effective Context identity绑定它。ProjectSession在PermissionGate和执行前确认contract存在、归属Host action并允许executor推导出的权限分类；这层一致性校验不替代workspace、stale、approval、sandbox、audit及各工具硬边界。

Epoch 0只允许`direct` contract；纯`promote()`接口只能从同一Registry快照按canonical顺序增加`deferred` contract并生成后续epoch，且只能发生在ActionLease发放前。当前所有built-in仍为direct，本阶段没有MCP transport、server lifecycle、credential、model-visible discovery tool或自动promotion。Provider原生搜索仍是adapter拥有的能力，Task coordination仍走专用dispatcher。Effective Context升级为`ctx-v9`/`ctx-v10`，empty full-context ID为`ctx-v9-6e8bb3a51d3138760bdb6e8ea9db1ab94927599529048ba7bee2d7e792fe2b0e`；provider adapter contract升级v37，system prompt保持v33，其他持久schema不变且旧Session不重写。详见[0107：Unified Extension Contract and ToolSet Snapshots](./decisions/0107-unified-extension-contract-and-tool-set-snapshots.md)。

## Confined stdio MCP 配置与只读探测

MCP配置schema v1只接受本地`stdio`与`confined-stdio` trust，分为XDG user和workspace project两个scope，同名跨scope冲突即拒绝。Server command必须是absolute POSIX executable；args、workspace-relative cwd及环境映射均有硬边界。配置只保存`TARGET=SOURCE_ENV_NAME`，不保存值；新server默认disabled。配置通过scope lock、revision CAS、symlink拒绝、`0600`临时文件和atomic replace实现add/replace、enable/disable与remove。

`mcp probe`每次只启动一个temporary process，并复用`LinuxBubblewrapCommandSandbox(workspace_writable=False)`：host root与workspace均只读，private temp/home/config，遮蔽敏感HOME路径，drop capability且seccomp禁止socket。Host确认sandbox activation后才发送`initialize`、`notifications/initialized`和有界分页`tools/list`；成功、协议错误、timeout与cancel都会关闭stdin并按exit、process-group terminate、kill顺序回收。Cleanup不完整单独报错。

Stdio使用严格newline-delimited JSON-RPC，拒绝duplicate key、non-finite number、wrong ID、server-to-client request、未知protocol、重复cursor/tool、过大message、过多message/page/tool及过深或过宽JSON。Server instructions、description、schema、annotation、JSON-RPC error正文及stderr内容均不展示；终端只显示脱敏身份、capability/tool名称、schema byte数、页数、时长、stderr byte数及cleanup状态。Standalone提供`mcp add|list|show|enable|disable|remove|probe`；REPL只提供Host-only `/mcp list|status|show|probe`。

本阶段没有`tools/call`、常驻server manager、HTTP/SSE/OAuth、resources/prompts/sampling，也不把枚举工具加入Extension Contract、Registry、ToolSet、Provider request、PermissionGate、Action Audit或Session history。Canonical system prompt保持v33、adapter contract保持v37、Effective Context保持`ctx-v9`/`ctx-v10`，全部既有持久schema不变。详见[0108：Confined stdio MCP Configuration and Inspection](./decisions/0108-confined-stdio-mcp-configuration-and-inspection.md)。

## MCP Tool Normalization 与 Quarantine Catalog

启用的受限stdio server现在可以被归一为content-addressed `McpQuarantineCatalog`，但不会直接进入初始ToolSet。每个listed tool都会获得由configured server、remote name和hash组成的最长64字符qualified name，并绑定user/project scope、configuration revision、negotiated protocol、schema fingerprint及稳定disposition。Schema规范化只接受object root与闭合的递归关键字/type子集；不支持的reference、keyword、composition、required或additional-properties形式会作为带脱敏reason code的rejected candidate保留。一个server probe失败则保留脱敏source issue，不展示server error或stderr正文。

Accepted candidate被转换为`source=mcp`、`execution=mcp-remote`、`exposure=deferred`且无PermissionAction的`ExtensionToolContract`。Source name绑定scope、server与protocol，source generation绑定配置revision；schema与有界description进入精确定义，但description明确标记为untrusted server data。MCP annotation与output schema不进入contract，`readOnlyHint`等提示不能授予workspace-read或其他权限。Catalog按qualified name canonical排序并由完整候选/拒绝事实生成identity；Session-local service只在credential-free配置identity不变时缓存。

Standalone `mcp catalog`与Host-only `/mcp catalog`会显式刷新并仅显示catalog ID、数量、qualified name、scope/server、revision、protocol、schema fingerprint和reason code；不显示description、schema、annotation、argument、credential、server error或stderr，也不调用Provider、不写Session或Action Audit。Built-in source与Registry因新增Slice 4 discovery contract升级generation 2，组合MCP的Registry使用generation 3。详见[0109：MCP Tool Normalization and Quarantine Catalog](./decisions/0109-mcp-tool-normalization-and-quarantine-catalog.md)。

## Progressive MCP Discovery 与 ToolSet Epoch Transition

模型初始只看到两个固定direct contract：`tool_search(query,max_results)`和`tool_promote(names)`，而不是全部MCP schema。前者只对当前Turn冻结Registry里的MCP deferred contract执行有界case-insensitive literal-term搜索并最多返回8个候选；后者最多接受8个同Turn先前搜索实际返回的exact qualified name。两类discovery call都必须独占一个assistant tool response，不进入PermissionGate或Action Audit，也不能依据annotation、猜测或其他extension自动激活。

Promotion仍使用同一Registry snapshot的canonical `ToolSetSnapshot.promote()`，重复可见名称保持幂等；真实增加时生成下一epoch。对于已经持有ActionLease的ProjectSession，Host先复核Session、runtime generation、旧context、旧ToolSet及当前MCP Registry identity，然后退休旧lease，构造绑定新ToolSet ID的Effective Context并签发新的non-recreatable lease，下一次Provider count/create统一使用新定义。旧approval与ActionIdentity无法跨epoch复用；配置或Registry stale会拒绝且不提交candidate Turn。

本阶段仍不实现`tools/call`。模型即使请求已晋升MCP contract，也只会收到`mcp_execution_unavailable` ToolResult，不会进入built-in executor、PermissionGate、approval、Action Audit或MCP process。Canonical system prompt升级v34、provider adapter contract升级v38；Effective Context representation仍为`ctx-v9`/`ctx-v10`，empty full-context ID更新为`ctx-v9-2f737163e792a16fbae49a629f54afc5cf43d49b75f1afe47b12ff5ed4e60d3e`，持久Session/Task/Action Audit等schema不变。详见[0110：Progressive MCP Discovery and ToolSet Epochs](./decisions/0110-progressive-mcp-discovery-and-toolset-epochs.md)。

## 受审计MCP执行、结果规范化与进程生命周期

晋升后的`mcp-remote` contract现在由Host固定赋予`dangerous` PermissionAction，不采信server的`readOnlyHint`、`destructiveHint`或其他annotation。MCP调用必须处于`danger-full-access`并按ask/auto经过现有PermissionGate；ask preview只显示exact qualified tool并隐藏arguments。ActionIdentity通过当前lease绑定ToolSet/context，另以expected-configuration precondition绑定catalog candidate、server scope/revision、protocol、schema及catalog ID。参数在Action Audit前按照冻结的受支持JSON Schema子集校验；`pattern`、引用及其他不支持的keyword会被隔离而不会在Host内执行server正则或schema代码；permission deny或approval reject不会启动可复用process。

`McpProcessManager`按scope、server、configuration revision、protocol与catalog ID惰性启动受限stdio process，并在首次call前重新完成initialize、tools/list及remote name/schema fingerprint验证。健康process只接受串行调用，当前进程最多保留8个server、每个generation最多128次完成调用，容量满时按确定性LRU退出。配置/catalog变化、process退出、协议失败、取消、call上限、Session关闭或live schema不匹配都会淘汰generation；status检查会对照当前启用配置，catalog refresh会回收其他catalog generation，cleanup失败的generation仍由manager持有以便再次有界回收；request送出后不自动retry。`/mcp status`只显示server、scope、revision、protocol、generation、完成调用数、alive及stderr byte元数据。

`tools/call`使用30秒timeout和已有严格newline-delimited JSON-RPC边界。取消会尽力发送`notifications/cancelled`，随后回收process。结果只接受闭合CallToolResult形状、最多64个content block及可选structured content；text进入总计64 KiB的模型结果，image/audio/blob只保留经过base64校验的类型、MIME及byte count，resource link和embedded resource做有界结构校验，`_meta`与annotation被丢弃。普通结果为succeeded，`isError`及JSON-RPC error为known failed；timeout、cancel、送达后的transport/protocol错误、畸形结果、截断或cleanup不完整为partial/outcome-uncertain，均进入既有Action Audit与ToolResult因果且不得自动retry。

Canonical system prompt升级v35，fingerprint为`v35-8537a2ef36ba8aa29068cc93f9b09231c0ea4e51a534fdb473e591408a7b5dca`，empty full-context ID更新为`ctx-v9-8e257b8889c2794ab1deef575bf96a22a9394cdac71e54234cb769adeaafadc7`；Effective Context仍为`ctx-v9`/`ctx-v10`。ApprovalPreview升级v4。Provider adapter contract保持v38，因为wire projection与parser没有变化；其他Extension、Registry、ToolSet、ToolArguments、ActionIdentity、Session、Task、Action Audit、Profile及compaction schema均不变，旧记录不重写。详见[0111：Audited MCP Execution and Process Lifecycle](./decisions/0111-audited-mcp-execution-and-process-lifecycle.md)。

## 有界MCP通知与精确本地Tool Policy

MCP JSON-RPC现在严格识别`notifications/progress`、`notifications/message`和`notifications/tools/list_changed`，同时继续把unknown notification计入同一每请求上限。Host只保留各类型计数并最多发出一次无正文活动事件，不保存或展示progress message、token、logging data及其他server内容。畸形已识别通知或洪泛在call送达后按outcome-uncertain失败，通知状态仍随runtime observation返回。

`tools/list_changed`不会原地修改当前Turn冻结的ToolSet，也不会自动重试当前call。Host在call终止后淘汰对应process generation并使quarantine catalog缓存失效；后续catalog-dependent操作重新probe，若schema或其他身份变化，既有contract/lease检查按stale拒绝。Terminal tool details只新增脱敏计数和`catalog-invalidated`事实。详见[0112：Bounded MCP Notifications and Catalog Invalidation](./decisions/0112-bounded-mcp-notifications-and-catalog-invalidation.md)。

独立schema-v1 MCP tool policy分为XDG user与workspace project scope，使用scope lock、revision CAS、symlink拒绝、`0600`及atomic replace。每条rule精确绑定qualified name、server scope/name/revision、remote name、protocol、input-schema fingerprint、action与policy revision，只允许`workspace-read`或`dangerous`。完全匹配为`applied`；缺失为`default`；任一字段变化为`stale`并回退`dangerous`。Annotation、description、notification及result均不能授予权限。

Policy identity进入catalog configuration identity；disposition、effective action及revision进入candidate identity，effective action继续进入contract、Registry、ToolSet、Effective Context、ActionIdentity precondition与process generation。`mcp policy list|show|set|clear`提供本地管理；`set`必须先从实时accepted catalog解析exact candidate并核对调用者提供的schema fingerprint，不能为猜测、rejected或stale工具写规则。System prompt升级v36，fingerprint为`v36-0ab649c44e73ce244ef761512272188dd4540f46ed5243bcd61c2bbf63d9815d`，empty full-context ID为`ctx-v9-97c4e14f393e36bfc0f7b17f6715ca84a0dde30771a46fd81da434b08f538693`；provider adapter保持v38，持久schema不变。详见[0113：Fingerprint-bound Local MCP Tool Policy](./decisions/0113-fingerprint-bound-local-mcp-tool-policy.md)。

## 远程MCP、OAuth与扩展Capability

MCP配置升级至v2并继续读取旧v1 stdio文件。`streamable-http`服务器使用credential-free HTTPS endpoint和`remote-https` trust；Host固定public DNS/IP、验证TLS hostname、拒绝redirect与压缩、限制JSON/SSE响应并在内存中校验`MCP-Session-Id`。静态bearer只按环境变量名读取；所有远程工具固定为`dangerous`并继续经过quarantine catalog、冻结ToolSet、PermissionGate、审批、ActionIdentity、结果规范化及Action Audit。详见[0114](./decisions/0114-streamable-http-and-remote-network-trust.md)。

用户级`mcp-oauth.json`保存revision-bound OAuth 2.1 PKCE pending state及token，使用私有目录、`0600`和原子替换。`mcp oauth begin|complete|status|logout`完成HTTPS metadata discovery、S256、loopback redirect、state校验、code exchange、expiry和单次refresh；token值不进入project配置、terminal状态、Session、Action Audit或模型。详见[0115](./decisions/0115-local-oauth-21-pkce-lifecycle.md)。

`mcp resources list|read|subscribe|unsubscribe`提供有界Resource读取与revisioned subscription恢复；binary只保留校验后的byte metadata，resource通知只保留计数。`mcp prompts list|get`只接受有界text消息并明确标为不可信、非权威模板，不自动进入Effective Context。Root默认不暴露，只有server配置显式开启时才响应唯一workspace URI。详见[0116](./decisions/0116-bounded-mcp-resources-prompts-subscriptions-and-roots.md)。

Sampling与Elicitation通过独立`McpReverseRequestCoordinator`处理，限制反向请求数、nesting、消息、token、schema及输出。Sampling必须同时具有Host授权和no-tools sampling callback，Elicitation必须具有显式交互callback；正常runtime默认不安装这些callback，因此安全默认值仍是拒绝。详见[0117](./decisions/0117-bounded-mcp-sampling-and-elicitation.md)。

`mcp doctor`执行一次脱敏live conformance probe，报告transport、protocol、known/unknown capability、tool数量和cleanup，不展示server正文、credential或session ID。初始化失败会尝试关闭remote session，status区分stdio与Streamable HTTP generation；legacy HTTP/SSE暂不支持。单个SSE `data:`行可承载一个完整的有界MCP message，不再受无关的64 KiB行限制；解码消息与完整HTTP响应仍分别限制为1 MiB，event及JSON结构边界不变。工具schema仅在根节点接受已知Draft 7 `$schema`声明；直接根字符串属性还可携带有界`x-mcp-header`路由提示，原始元数据继续进入schema指纹但在Provider投影前移除，live schema复核后仅把安全参数投影为对应`MCP-Param-*` header。未知dialect、嵌套或重复header提示及其他不支持关键字仍被隔离。System prompt为v37，fingerprint为`v37-d7ad600e357ae981d083683cbe35580475da88854a0edbe933ce4106bae11c66`，empty full-context ID为`ctx-v9-febbf229c7b658d6fd2b4f31dc6129cfd7a91487e5f723ef6bf9aafa5969a7b4`；provider adapter保持v38。详见[0118](./decisions/0118-mcp-interoperability-and-production-hardening.md)。

## MCP审批与只读运维诊断

MCP ask审批现在由已准备candidate的精确server scope/name解析当前配置transport。`stdio`只描述本地受限executable、只读Host/workspace、private temporary paths及socket禁止；`streamable-http`则明确参数经HTTPS发送到位于本地command sandbox之外的remote service，且外部副作用不能rollback。两类审批都继续隐藏arguments、endpoint、header和credential。ApprovalPreview升级v5，以必需的`stdio|streamable-http` transport字段避免错误边界说明；该对象仍是非持久Host UI表示。

MCP ToolResult的已知失败码会生成保守的`Next:`建议，区分缩小/分页请求、刷新catalog并创建新Turn、检查redacted OAuth/server状态、检查cleanup/runtime、避免自动重放不确定transport调用及根据有界server error修订参数。`mcp catalog explain <reason-code>`只从闭合静态表解释Host拥有的quarantine code，不展示server schema、正文或错误。

`mcp policy stale`把当前规则分为可由成功catalog明确证明的`stale`与因source probe失败或catalog不完整而无法证明的`unresolved`；完全匹配的active规则不列入结果。`mcp policy prune --dry-run`不获取mutation意图、不修改policy文件，只为confirmed stale规则输出包含scope和`--if-revision`的现有`clear`命令，并明确排除unresolved规则。System prompt、provider adapter、ToolSet、Effective Context及全部持久schema不变。详见[0120](./decisions/0120-transport-aware-mcp-approval-and-policy-diagnostics.md)。

## 提前Session命名与Terminal渲染安全

未命名首轮现在在第一次普通provider响应完成后、任何tool派发前执行既有的有界无工具标题请求。标题调用继续计入同一Turn的24次provider总预算；候选名称先作为进程内TTY底栏状态展示，只有最终Turn成功时才与正文、来源、fallback原因及usage原子写入`turn_committed`。失败或取消会撤销临时名称，commit前仍会复核重名并使用稳定编号fallback。

真实TTY为持久输出预留最右一列，并把自动内容宽度封顶为100个显示单元，使宽终端也生成带续行前缀的真实换行，而不依赖更窄的IDE或复制视图二次软换行。启动runtime/Session块、assistant Markdown、普通文本、Host轨迹、slash及approval都使用该宽度；每个可见活动事件和最终回复写出前会刷新当前宽度，同时保留尚未完成的流式后缀。启动块也使用次要Host缩进，窄屏banner切换为纵向布局。Approval先以无ANSI纯文本完成安全包装，再由外层轨迹应用warning颜色，因此不会再显示字面量`\x1b[...]`。System prompt、adapter、tool contract、Effective Context与全部持久schema不变。详见[0119](./decisions/0119-early-session-title-and-terminal-rendering-safety.md)。

## 有界声明式Skills与ToolSet收窄

Skill v1使用严格的`<name>/SKILL.md`包，frontmatter只接受`manifest-version`、`name`、`description`和可选`allowed-tools`，正文与元数据共同进入稳定fingerprint。Host只扫描workspace-local `.coquo/skills`、project-shared `.agents/skills`及XDG user `coquo/skills`三个精确根，按该顺序选择active候选并保留shadowed与invalid诊断；symlink、非UTF-8、CRLF、未知字段、YAML错误、读取漂移和全部大小边界均fail closed。`skills list|show|doctor`是无provider、无Session、无Action Audit的只读检查。

每个普通Turn固定一个SkillInventorySnapshot，其identity进入Effective Context与ActionLease。模型先以独占回复调用`skill_search`查询冻结的active metadata，再用同Turn返回的精确name+fingerprint独占调用`skill_load`；Host在加载正文前重新读取inventory，任何变化都stale reject。成功ToolResult包含完整有界instructions但不含绝对路径，且Skill只是不受信任的流程指导，不是system authority、permission、approval、tool implementation或执行证据。

可选`allowed-tools`只对当前已有Host/MCP action tools做交集：缺失表示继承，空列表移除全部普通action，非空列表也不能增加或promote工具；Task、lifecycle与discovery control保留。加载后的限制创建后续immutable ToolSet epoch与replacement lease，并只从Effective Context中仍完整保留的Host成功`skill_load` pair跨Turn恢复；compaction删除该pair后，summary中的Skill文字不会重新激活。System prompt为v38，provider adapter为v39，full/compacted Effective Context升级v11/v12并加入`skill_inventory_id`，旧版本继续可验证。详见[0121](./decisions/0121-bounded-declarative-skills-and-toolset-restriction.md)。

## 有界Skill资源、组合预算与REPL观测

Skill inventory v2把`SKILL.md`之外的包内regular files作为有界资源索引：最多64个resource、128个目录、单文件64 KiB、总计256 KiB，路径最长256字符。目录枚举与文件读取均使用no-follow descriptor，symlink、非regular file、越界、读取漂移与包逃逸全部fail closed。索引只包含相对path、bytes、严格UTF-8可读标记及path+content fingerprint；binary只可被索引，不能进入模型context。

`skill_load`现在返回完整instructions与资源索引；`skill_read_resource`要求当前Effective Context中仍存在精确active Skill pair，并绑定Skill fingerprint、resource path、resource fingerprint及Turn-pinned inventory identity。Host重新加载并验证后只返回一个完整有界UTF-8 ToolResult，不执行资源、不安装依赖、不产生Action Audit，也不扩张ToolSet。

最多4个不同Skill可同时active，每Turn最多尝试4次`skill_load`，active instructions累计最多65536 bytes；重复active name拒绝。多个Skill按成功load pair的因果顺序组合，`allowed-tools`依次求交，并只从Effective Context仍完整保留的Host结果恢复。`/skills|active|list|show|doctor`提供无provider、无Session mutation、无Action Audit的当前激活与catalog检查。Inventory升级`skills-v2`，full/compacted Effective Context升级v13/v14且继续严格验证legacy v11/v12，built-in Registry升级generation 4，system prompt升级v39，provider adapter升级v40。详见[0122](./decisions/0122-bounded-skill-resources-composition-and-observability.md)。

## Skill authoring、本地导入、Task审计与执行边界

`/compact preview`现在从当前Effective Context和精确保留的verbatim turns分别投影active Skills，显示before/after、将失活的名称及压缩后的action tools；它不调用provider、不生成summary也不修改Session。只有完整成功`skill_load` pair仍被保留时Skill才继续active，summary文字不能恢复激活。

Standalone `skills init|check|search|conflicts`提供无provider、无Session的模板创建、canonical校验、确定性metadata搜索及source shadow诊断；REPL增加只读`/skills search`与`/skills conflicts`。`skills import <local-directory>`只复制显式本地目录，不联网、不clone、不安装依赖：source先经过同一no-follow loader，destination与lock均独占创建，复制后重新加载并检查source drift、target inode replacement、shadowing及全部fingerprint。Lock v1位于对应scope的`skill-locks/`而不是被扫描的`skills/`内，只记录scope、name、manifest/resource fingerprint与必要资源元数据，不含source path、credential、正文或时间；`skills lock show|verify`提供检查。

SessionStore可从严格replay的committed Turn只读投影`skill_load`请求身份、Host ledger outcome及安全source/fingerprint，不返回instructions。`task skills <task-id>`只选择Task Stage持久绑定的精确`turn_record_sequence`；普通Task读取仍不依赖Session健康。离线Eval升级`host-baseline-v3`，Task execution Stage会走普通Skill发现与加载路径。

包内script始终只是resource，不存在`skill_run_script`、dynamic import或implicit subprocess。读取仍走`skill_read_resource`；执行必须由模型明确请求既有`run_command`，继续经过dangerous PermissionGate、approval、Action Audit、Linux sandbox、timeout、cancellation与output bounds。以上均为Host命令、只读投影或更强本地边界，因此registry generation 4、system prompt v39、provider adapter v40、Skill inventory v2、Effective Context v13/v14及全部Session/Task schema不变。详见[0123](./decisions/0123-skill-authoring-import-audit-and-execution-boundary.md)。

## 显式Skill内化与远程隔离安装

普通Prompt新增两个必须独占回复的commit-coupled协调工具。只有当前用户明确要求把流程保存为Skill时，模型才可调用`skill_propose_create`提交完整有界声明；Host在最终assistant正文与Session Turn成功落盘后，才把它写为inactive候选。只有用户直接批准该精确候选后，模型才可调用`skill_accept_create`；Host再次从committed Turn恢复成功ToolUse/ToolResult/ledger因果，核对owner Session、pending状态、fingerprint、固定scope与非只读模式，再复用`import_skill()`及lock完成安装。系统不会从偶然成功、重复行为或经验中自动学习Skill。

生成或下载的候选位于不参与inventory扫描的`.coquo/skill-candidates/v1/`，保存private immutable package、闭合metadata及只允许`created -> installed|rejected`的append-only事件。`skills fetch`与`/skills fetch`复用PinnedWebGetTransport，只接受无query的public HTTPS raw `SKILL.md`或有界ZIP；每次redirect继续校验public address，ZIP拒绝路径逃逸、重复/case-fold冲突、多package root、symlink/special/encrypted entry及count、size、expanded size与compression ratio越界。下载不会安装或激活，必须先用candidate list/show检查完整instructions与resource，再显式install或reject；安装仍生成精确import lock。

每个prepared Turn继续冻结SkillInventorySnapshot，新安装不会热改当前Turn的ToolSet，只在后续Turn可发现。Registry升级generation 5，system prompt升级v40，provider adapter升级v41，full/compacted Effective Context升级v15/v16并继续读取legacy v13/v14；Skill inventory v2及Session、Task、Action Audit、import lock schema不变。详见[0124](./decisions/0124-explicit-skill-authoring-and-quarantined-remote-install.md)。

## Frozen Declarative Preauthorization Hooks

Hook schema v1只支持`before_action_authorization`事件与`continue|deny|require_ask|advisory`四种无副作用effect。规则以exact tool name、PermissionAction、规范workspace相对path prefix及`builtin|mcp`来源做有界确定性匹配；不接受regex、shell、HTTP、模型调用、argument mutation、credential或任意表达式。user与project配置使用strict revisioned JSON、跨scope唯一ID、默认disabled、revision CAS及私有原子写入；`hooks ...`负责独立管理，`/hooks`系列只读检查当前状态。

每个Turn准备时冻结完整`HookSetSnapshot`，包括disabled规则，并把`hooks-v1`身份纳入Effective Context与ActionLease验证；运行中配置变化只影响后续Turn。求值发生在Tool hard preparation、PermissionAction分类、Extension Contract验证和ActionIdentity构造之后，但早于ActionCoordinator与Action Audit admission。`deny`直接返回有界model-visible错误，`require_ask`只能把`auto`收紧为`ask`，`advisory`只附加到正常ToolResult；任何effect都不能把PermissionGate拒绝变成允许、提升MCP policy、绕过沙箱或Tool硬边界。损坏配置在Provider调用前fail closed。

system prompt提升到v41，provider adapter提升到v42，full/compacted Effective Context提升到v17/v18并将v15/v16保留为legacy Skill-v3表示。Hook没有新增model-visible Tool schema，因此Registry仍为generation 5，Session、Task、Action Audit、ToolSet与Skill inventory schema不变。见[0125](./decisions/0125-frozen-declarative-preauthorization-hooks.md)。

## Durable Hook Observation 与 Audit

Hook配置与HookSetSnapshot升级为v2，并继续严格读取v1配置。除原有`before_action_authorization`外，新增`after_action`、Turn committed/failed及Task Stage started/committed/failed、blocked、terminated事件。只有preauthorization可使用`deny|require_ask`；其余事件只允许`continue|advisory`，lifecycle事件不接受action matcher，`after_action`可按闭合terminal outcome匹配。仍不支持shell、HTTP、模型、后台或其他可执行handler。

每次求值生成有界content-free `HookAuditEntry`，只含event、exact HookSet ID、subject、匹配Hook ID/effect、aggregate result及安全action元数据，不保存Hook message、Tool argument、文件内容或credential。Action求值和Turn终局随`turn_committed` v10或`turn_failed` v3原子持久化；Task求值随`stage_started|committed|failed` v3及`task_blocker_recorded|task_terminated` v2持久化。旧record继续读取且不能携带新ledger。Turn在action后失败时，已发生的action Hook求值也会进入失败record。

Lifecycle advisory通过typed transient terminal event展示但不复制到audit ledger；after-action advisory可进入普通ToolResult，但不能改变真实Tool或Action Audit outcome。Standalone `hooks evaluations [session]`、`hooks task <task-id>`及REPL `/hooks evaluations [count]`、`/hooks task <task-id> [count]`只做strict replay和有界content-free投影，不调用Provider或修改状态。system prompt为v42，provider adapter为v43，HookSet为`hooks-v2`，Effective Context为v19/v20并保留v17/v18 legacy Hook-v1表示；Registry保持generation 5。见[0126](./decisions/0126-durable-hook-observation-and-audit.md)。

## Audited Pinned Local Hook Handlers

Hook配置及HookSetSnapshot升级为v3，同时严格读取v1/v2并只写v3。规则可选配置一个固定direct executable、6 KiB内最多16个固定argument、1至30秒timeout及可执行文件SHA-256；handler规则自身必须是空message的`continue`，实际effect来自运行结果。workspace相对可执行文件拒绝symlink，绝对路径解析到实际文件；文件必须regular、可执行且不超过16 MiB。Host把一个不含消息、Tool参数、文件内容、credential或任意result的closed JSON event envelope追加到固定argv，绝不做shell解析。

每次handler调用都是一个名为`hook_handler`的synthetic dangerous Action，复用现有ActionLease、PermissionGate、ask/auto审批、Action Audit、取消、RunCommandTool及Linux bubblewrap。read-only和workspace-write会拒绝handler；enable Hook不等于授权执行。执行阶段再次检查固定指纹，stale时形成闭合failed Action且不启动进程。sandbox保持Host root只读、workspace可写、私有temp/home、socket禁止、环境allowlist、timeout、有界输出及process-group cleanup；handler的stdout/stderr原文不进入Action Audit或模型历史。

成功handler只接受一个closed JSON stdout结果，effect为`continue|deny|require_ask|advisory`。只有preauthorization可返回deny/require_ask；观察事件只接受continue/advisory。preauthorization的准备、权限、审批、执行、timeout、stale或协议失败均fail closed；after-action与lifecycle失败只产生advisory，不能改写权威action、Turn或Task结果。每event最多执行4个handler、普通Turn最多12个，禁止递归、自动retry与rollback。Turn/Task lifecycle handler只在对应权威record提交后执行，其运行事实进入Session Action Audit。

Standalone新增`hooks fingerprint`、handler-aware `hooks add`、`hooks template local-handler`、strict workspace-local `hooks import`、readiness-aware `hooks doctor`及`hooks runs [session]`；REPL新增`/hooks runs [count]`。Import始终落为disabled revision 1；enable前必须匹配固定指纹。approval preview升级到v6，system prompt为v43，provider adapter为v44，HookSet为`hooks-v3`，Effective Context为v21/v22并保留v19/v20 legacy Hook-v2；Registry及Session/Task record schema不变。见[0127](./decisions/0127-audited-pinned-local-hook-handlers.md)。

## B6：Writable Team Roles、Host-owned Linked Worktree 与 Parent-only Integration

B6.1–B6.4将authority workspace与Child execution root彻底分开，并把可写Team成员收敛为两个固定契约：`isolated-workspace-writer-v1`只获得受控文件写入/编辑能力，`isolated-coder-v1`在父级`danger-full-access`、可用的现有Linux command sandbox和严格Host边界同时满足时，额外获得`run_command`。两种角色都不能获得Team、Task、Child delegation、网络、MCP、Skill或integration控制；父权限是上限，不会被Team审批提升。每个assignment在Host管理的`.coquo/worktrees/<workspace-fingerprint>/`下创建一个绑定authority repository、base ref/HEAD、member、assignment和capability snapshot的linked worktree；worktree目录是执行根，authority workspace仍是唯一集成目标。`.coquo/worktrees`是敏感local runtime state，不是安全边界。角色与隔离worktree的概念边界参考了learning-submodules中的MewCode/Claw-Code研究材料，但Coquo未复制其实现、prompt或运行依赖。

Host在Child达到可审查终态后执行一次bounded seal：检查HEAD未漂移、路径包含/符号链接/特殊文件/git metadata/gitlink/大小/数量限制和并发快照一致性，并把manifest与patch摘要及SHA-256写入worktree ledger；patch bytes保存在相邻的`0600`不可变本地artifact中，不进入Session正文、approval preview或模型上下文。终端文字、handoff和artifact都只是非可信证据；篡改、来源漂移、目标冲突、脏目标、非祖先目标、未知进程结果和无法验证的副作用均fail closed，不自动重试、reset、清理或声称成功。

B6.5加入父Session专属的`team_worktree_integrate` Action。它不是Team-control捷径，也不把apply当作complete：Host先通过PermissionGate和精确approval preview绑定一次性Action identity，再重查assignment ownership、sealed terminal evidence、source/target ref与HEAD、ancestry、cleanliness、digest、active-operation和`git apply --check`，最后只执行固定参数的`git apply`。应用成功后authority workspace保持未提交、未自动merge/rebase/commit，后续集成必须等待用户在目标上明确恢复干净状态；冲突或check失败不改变目标。Action开始后若进程退出或结果无法证明，记录`integration_unknown`并禁止重试，必须由Host观察恢复。

B6.6同步迁移模型可见契约到Registry generation 9、catalog 62、普通父ToolSet 58、system prompt v48、Provider adapter v48以及Effective Context `ctx-v27`/`ctx-v28`；旧Team/Child/Context版本仍按既有兼容策略replay。CLI与REPL提供成员`--role`以及`team worktree status|diff|recover|retire --confirm`，但不会隐式集成、commit、push或删除worktree。Team close不自动retire；只有显式、可审查的Host retirement才能移除满足条件的worktree和artifact。

B6.7通过双Child隔离运行、artifact tamper、source/target drift、conflict、provision/seal/integration unknown、cancellation保留worktree、Provider-free recovery、apply与work completion分离、legacy replay、CLI/REPL和fake smoke验证上述边界。最终发布门禁仍是全量pytest、Ruff check/format、`uv lock --check`与`git diff --check`；真实Provider、网络、凭据和API cost不属于离线发布门禁。完整理由与状态见[0144](./decisions/0144-host-owned-linked-worktree-lifecycle.md)和[0145](./decisions/0145-authority-execution-scope-and-child-actions.md)。

## Task–Child–Team 统一编排桥接

Task、Child Run、Team assignment和Team schedule继续各自拥有自己的append-only ledger；新增Host-only `TaskOrchestrationService`只负责把精确identity绑定到Task Stage、复用已有ProjectSession/Team service执行路径、验证终态证据并写入一次normalized external Stage terminal。外部Stage使用`stage_delegated`、`stage_external_committed`和`stage_external_failed`三类record-local schema-v1记录；旧Task/Session/Child/Team/schedule transcript不重写，普通foreground Stage仍与external Stage终态区分。

桥接顺序固定为Task owner/workspace/objective/identity校验、外部准入、delegation durable append、复用既有执行/监督、跨ledger终态观察、evidence digest校验和Task terminal append。Child必须有已发布的`ChildHandoff`；Team assignment必须经过`observe_terminal`；schedule允许lazy empty roster，但提交前必须从精确schedule ledger读取最终assignment roster，聚合每个assignment handoff并生成canonical digest。取消、失败、中断、缺少handoff、owner/workspace/identity mismatch、ledger不一致、进程未知或durability不确定均fail closed并要求recovery，不自动retry、清理orphan、猜测ID或声称成功；重复observe不会重复追加Task terminal record。

Permission/approval、budget和workspace边界只按父级上限向下传播，不能由Team/Child提升，也不绕过PermissionGate、sandbox、timeout、output、edit conflict、causality或audit。该桥接不新增model-visible tool，不改变Registry、system prompt、Provider adapter或Effective Context版本；Child仍不能获得Task、Team、递归delegation、MCP、Skill或integration控制。Team schedule resume只重新获取精确nonterminal schedule lease，不追加第二个started record，也不从Task ledger重建schedule。详见[0146：Task–Child–Team Unified Orchestration Bridge](./decisions/0146-task-child-team-orchestration-bridge.md)。

## 有界递归只读 Child 与 Host-owned 工作流编排

本切片允许一个保守的递归能力：Host可委派depth 1的只读Child；该Child在Host明确启用固定`read-only-explorer-v1`能力时，最多再创建一个depth 2 Grandchild。Grandchild不能继续委派。Host始终拥有准入、权限上限、预算、取消、运行lease、durable Child ledger、handoff与恢复；递归Child不能获得Task、Team、Skill、Hook、MCP、写入、shell、网络或integration能力。

递归Child使用独立的role prompt contract/fingerprint。depth 1的递归Child在固定只读ToolSet之外只获得Host显式暴露的Child controls；depth 2只读Grandchild不获得这些controls。depth 2的Child delegation记录使用schema v2并记录`parent_child_run_id`与`root_child_run_id`；旧depth 1记录仍可replay，所有身份、capability、owner、prompt fingerprint和schema不一致均fail closed。

同时新增Host-owned高层工作流骨架：Architect → Explorer → Executor → Reviewer → Integrator。它只复用Task及Task–Child–Team bridge的精确identity和执行边界，不复制Provider/Child/Team worker或ledger。Host推进阶段并持久化受界定状态；Explorer、Executor、Reviewer结果一律是`untrusted evidence`。Reviewer `passed`进入integration，`rejected`进入rework，`unknown`进入`recovery-required`；accept只是Host显式决定，不自动写文件、merge、commit、push、调用Provider或创建隐藏的Task/Child/Team。

工作流现在把每个外部Explorer/Executor stage的精确Stage Task ID、target、Child/Team/assignment/schedule身份、handoff观察、状态和evidence digest持久化为有限投影；外部ledger仍是唯一权威。Explorer使用工作流根Task，Executor先通过`TaskStore.derive`获得独立stage Task，以保留external usage unknown时的fail-closed预算语义，并在CLI中显示`Stage Task`。准入、执行、观察和恢复是四个显式Host操作；`workflow start|show|advance|explore-start|execute-start|recover|review|integrate-preflight|integrate|accept|rework|recover-integration`均为不调用Provider的control-plane命令，展示Child Run/Team身份和`evidence: untrusted`，拒绝profile/provider选择。恢复只重新观察记录中的精确身份；取消、缺失handoff、未知进程、lease或durability不确定均进入recovery-required，不自动retry、替代创建、清理、merge、commit或push。持久Child worker继续使用单workspace lease、最多四个并发线程、队列/执行lease、heartbeat、terminal event与orphan recovery；新增确定性测试证明双Child并行、lease独占、取消恢复和跨重载身份保持。详见[0148](./decisions/0148-bounded-recursive-child-and-host-workflow-orchestration.md)与[0149](./decisions/0149-durable-workflow-stage-observation-and-recovery.md)。

## ADR 索引

128. [0128：Coquo Product Identity Migration](./decisions/0128-coquo-product-identity-migration.md)
129. [0129：Shared Agent Runtime Assembly Boundary](./decisions/0129-shared-agent-runtime-assembly-boundary.md)
130. [0130：Durable Child Run Identity and State](./decisions/0130-durable-child-run-identity-and-state.md)
131. [0131：Child Admission and Detached Session Binding](./decisions/0131-child-admission-and-detached-session.md)
132. [0132：One-Shot Child Foreground Execution](./decisions/0132-child-foreground-execution.md)
133. [0133：Process-Local Child Run Supervision](./decisions/0133-child-process-local-supervision.md)
134. [0134：Child Cancellation, Bounded Wait, and Restart Recovery](./decisions/0134-child-cancellation-wait-and-restart-recovery.md)
135. [0135：Evidence-Backed Child Handoff and Parent Delivery](./decisions/0135-evidence-backed-child-handoff-and-parent-delivery.md)
136. [0136：Model Child Delegation Controls](./decisions/0136-model-child-delegation-controls.md)
137. [0137：Command Sandbox Capability Readiness](./decisions/0137-command-sandbox-capability-readiness.md)
138. [0138：Durable Team Identity and Member Registry](./decisions/0138-durable-team-identity-and-members.md)
139. [0139：Recoverable Team Member Child Assignments](./decisions/0139-recoverable-team-member-child-assignments.md)
140. [0140：Durable Team Mailbox and Assignment Delivery](./decisions/0140-durable-team-mailbox-and-assignment-delivery.md)
141. [0141：Durable Team Work Board and Manual Review](./decisions/0141-durable-team-work-board-and-manual-review.md)
142. [0142：Bounded Team Scheduler and Recovery](./decisions/0142-bounded-team-scheduler-and-recovery.md)
143. [0143：Parent-Owned Team Control Approval and Reply Evidence](./decisions/0143-model-visible-team-controls.md)
144. [0144：Host-Owned Linked Worktree Lifecycle and Bounded Change Sealing](./decisions/0144-host-owned-linked-worktree-lifecycle.md)
145. [0145：Authority/Execution Scope and Restricted Child Actions](./decisions/0145-authority-execution-scope-and-child-actions.md)
146. [0146：Task–Child–Team Unified Orchestration Bridge](./decisions/0146-task-child-team-orchestration-bridge.md)
147. [0147：Persistent Child Background Runtime](./decisions/0147-persistent-child-background-runtime.md)

## Durable Team Identity 与 Member Registry

B1现在提供workspace-bound的持久Team与成员身份，但不把身份误当作线程、模型上下文或权限。每个Team使用`.coquo/teams/<workspace-fingerprint>/<team-id>.jsonl`独立append-only transcript；header绑定创建它的owner Session，Team生命周期为不可逆的`open -> closed`。记录采用strict closed schema-v1 replay，连续sequence、workspace/fingerprint/path校验、大小上限、no-follow、原子创建、append+fsync、独占writer及durability uncertainty poisoning共同保证损坏或部分写入fail closed。

成员记录保留固定角色`read-only-investigator-v1`与不可变UUID/名称，名称在Team完整历史中casefold唯一并最多64名。状态只允许`active <-> disabled -> left`；disable仅阻止未来assignment，left保留历史身份且不可重新加入。`coquo team ...`与REPL `/team ...`是Host-only的创建、列出、查看、关闭和成员状态管理入口，不调用Provider、不创建Child、不写owner Session transcript、不改变latest或Effective Context。REPL mutation要求当前Session是Team immutable owner；standalone命令按精确Team管理而不隐式切换Session。

B1明确不包含assignment、handoff、mailbox、消息、shared task board、scheduler、写权限、递归委派、长驻worker或model-visible Team tool。B2现在把每个assignment绑定到全新的Child Run和detached Session：Team ledger按`pending_child -> child_bound -> terminal_observed`记录关系，Child ledger继续是执行状态权威。B3在此基础上增加append-only owner/member mailbox；新Team Child准入前冻结最多8条inbox消息并预分配delivery/reply ID，精确提交的Child Turn与handoff才允许发布一次member reply并观察delivery。B4增加append-only work item board：依赖只能指向较早item，ready work由Host手动assign，Child终态进入review，Host以明确evidence complete或release；Team close要求work、mailbox、assignment和reply门禁全部满足。消息读取不删除证据，失败或中断不消耗pending消息，generic和已准入v1 Child保持旧契约。B5增加append-only schedule wave、schema-v3 assignment provenance、每Team单一OS lifetime lease、确定性选择、取消与无Provider恢复；schedule选择仍有界且本地，assignment提交默认复用persistent Child runtime，显式注入的Supervisor才走process-local worker。调度只把Child终态送入review，不自动complete、retry或作为永久daemon运行。B7/B8增加专用`TEAM_CONTROL`运行路径与11个仅普通父Session可见的Team controls；拒绝、owner错误、预算和审批失败均返回有界ToolResult，accepted effect在父Turn后续失败时仍保留。Registry generation 8、system prompt v47、Provider contract v47、Effective Context v25/v26与旧v23/v24 replay保持一致，Child、Task Stage和compact summary不暴露Team controls。详见[0138](./decisions/0138-durable-team-identity-and-members.md)、[0139](./decisions/0139-recoverable-team-member-child-assignments.md)、[0140](./decisions/0140-durable-team-mailbox-and-assignment-delivery.md)、[0141](./decisions/0141-durable-team-work-board-and-manual-review.md)、[0142](./decisions/0142-bounded-team-scheduler-and-recovery.md)、[0143](./decisions/0143-model-visible-team-controls.md)与[0147](./decisions/0147-persistent-child-background-runtime.md)。

## Shared Agent Runtime 与 Durable Child Run Foundation

`AgentRuntimeFactory -> AgentRuntime -> AgentLoop` 现在是父 Session 的统一
组装和单回合编排路径。Runtime 只持有一个 loop 和一份易失 turn state；权限、
Action Audit、Session/Task 持久化、Hook、标题与 compaction 仍由
`ProjectSession` 通过显式 callback 保持 Host 所有权。恢复、切换、新建和 fork
都成对安装 writer/runtime，未增加线程或并行 Provider 使用。

Child Run 已有独立 workspace-bound JSONL ledger。Host 可以创建、查看、列出、
取消并准备一个已有 Session 下的 bounded objective。`child prepare` 会冻结
脱敏的只读执行 envelope，创建不改变 `latest` 的 detached Child Session，
并将状态推进到 `ready`；它不调用 Provider，也不写入父 Session。准备失败会
以有界原因持久化为 `failed`，精确的部分创建状态可安全重试。

普通父Turn现在会在固定catalog末尾看到`child_spawn`、`child_status`、`child_wait`与`child_cancel`。每个控制必须独占一次assistant response，但成功后不会强制final；父Agent可以继续工作或在后续response观察另一个Child。每Turn最多成功spawn四个Child，每次wait最多30秒且累计预留最多60秒。Child仍使用A3固定只读工具、一个Turn和depth one，不含写、命令、网络、MCP、Skill/Task控制或递归委派。

委派审批独立于Action PermissionGate。`ask`在创建前展示精确objective、脱敏route/model、工具、预算、process-local限制与额外Provider成本；`auto`只移除交互。父Session先持久化不含objective正文的`child_delegation_decided`，接受后Child ledger才以header和`child_run_delegated`原子创建，再进入原有admission；拒绝或取消不会创建Child或detached Session。status/wait/cancel每次验证durable parent ownership，终态wait通过普通ToolResult交付A7 handoff，父Turn后来失败也不回滚已经发生的Child效果。

在A7 Child切片完成时，模型契约迁移为Registry generation 7、system prompt v45、provider adapter v46及Effective Context v23/v24；v21/v22保持严格legacy读取。随后B8.1已将当前父Prompt契约原子迁移到generation 8、system prompt v47、Provider v47和Effective Context v25/v26；旧版本仍可replay。Child、Task Stage与compact summary始终不暴露父Child/Team控制。确定性fake路径已在一个父Turn内spawn两个真实Child、继续父工具工作、wait/deliver两个handoff，并严格回放父Session及两个detached Child Sessions。完整决策见[0136](./decisions/0136-model-child-delegation-controls.md)。

`child run <id>` 现在会为 `ready` Child 获取独立执行租约，重建脱敏 Provider 路由，
通过同一 `AgentRuntimeFactory -> AgentRuntime -> AgentLoop` 路径执行一个只读 Turn，
并在 Child Session Turn durable commit 后记录 `completed`；路由/构造/执行失败会
记录有界 `failed`，不会伪造完成。Child Session 不更新 `latest`，父 Session 与
父 runtime 保持不变。

REPL现在可用`/child start <id>`将`ready` Child提交到workspace-bound的durable queue。
`child start`随后尝试启动一个可重启的本地`python -m coquo.background_worker`进程；队列
最多保留32个pending项，单个worker进程最多复用4个Child执行线程，空闲后有界退出，不是
无限常驻daemon。每个worker继续调用同一个A4 executor且不共享父writer、Provider manager
或runtime状态。队列ledger只记录queued/claimed/heartbeat/requeued/terminal观察，Child
JSONL ledger仍是执行与终态唯一权威；queue、worker lease、heartbeat和worker-state均
append/fsync或atomic-write并在路径、权限、sequence、UUID、大小与durability uncertainty
失败时fail-closed。父Session可以在Child运行时继续提交自己的prompt；worker失败只隔离到
该Child并发布有界诊断。运行中的取消先落盘`cancel_requested`再由共享executor协作式观察，
Provider阻塞时保持`cancelling`；`wait`通过durable Child ledger轮询，不依赖提交它的进程。
READY claim只有在取得新执行锁证明无人执行时才可requeue；RUNNING/CANCELLING claim在取得
recovery lease后只能标记`interrupted`，绝不自动retry。`child status`、`/child status`和
`child recover`可观察worker PID/ID、heartbeat、active submissions、queue状态、orphan候选
和有界诊断。显式注入的process-local Supervisor仍保留给测试与刻意的进程内调用；未注入时
ProjectSession及Team assignment默认复用这个persistent runtime。详见[0147](./decisions/0147-persistent-child-background-runtime.md)。

每个终态Child现在可以追加一个`child_run_handoff_published` schema-v1 record。completed handoff通过`committed_turn()`和`turn_evidence()`绑定精确Child Turn序号、原始record SHA-256与assistant text digest；正文同时受32 KiB字符和UTF-8字节边界约束，截断带明确marker。failed、cancelled、interrupted及准备失败只生成含稳定outcome/result code的Host摘要，不复制objective、Provider error或traceback。重复相同publication幂等，不同publication冲突，已发布completed handoff读取时仍会重新验证Child Session证据。

`child_handoff_delivered`是父Session中的content-free schema-v1 audit receipt，只保存父/Child身份、终态序号、handoff digest、source和可选ToolUse ID。Host必须先durably提交receipt再展示正文；standalone audit writer不会追加`session_resumed`或更新`latest`。receipt不进入history、Effective Context、usage、tool ledger、compaction、export或fork，worker也从不持有父writer。Session或Child append发生fsync不确定后，对应writer会poison并要求inspection，不能盲重试。本A7 Host-only能力不改变Tool Registry、system prompt、Provider contract或Effective Context版本；模型侧委派控制、消息和Team仍属于后续切片。详见
[ADR 0129](./decisions/0129-shared-agent-runtime-assembly-boundary.md)、
[ADR 0130](./decisions/0130-durable-child-run-identity-and-state.md) 与
[ADR 0131](./decisions/0131-child-admission-and-detached-session.md)、
[ADR 0132](./decisions/0132-child-foreground-execution.md)、
[ADR 0133](./decisions/0133-child-process-local-supervision.md)、
[ADR 0134](./decisions/0134-child-cancellation-wait-and-restart-recovery.md) 与
[ADR 0135](./decisions/0135-evidence-backed-child-handoff-and-parent-delivery.md)。

1. [0001：Foundation 0 单轮 Loop](./decisions/0001-foundation-0-single-turn-loop.md)
2. [0002：Foundation 0 确定性 REPL](./decisions/0002-foundation-0-deterministic-repl.md)
3. [0003：Foundation 1A 内存文本历史](./decisions/0003-foundation-1a-in-memory-text-history.md)
4. [0004：Foundation 1B 受限 read_file 工具循环](./decisions/0004-foundation-1b-bounded-read-file-tool-loop.md)
5. [0005：Foundation 2A Provider-neutral Model Routing](./decisions/0005-foundation-2a-provider-neutral-model-routing.md)
6. [0006：Foundation 2B Adapter-owned Compatibility Policy](./decisions/0006-foundation-2b-adapter-owned-compatibility-policy.md)
7. [0007：Foundation 3A Anthropic 非流式 Adapter](./decisions/0007-foundation-3a-anthropic-non-streaming-adapter.md)
8. [0008：Foundation 3B 本地多 Provider Runtime](./decisions/0008-foundation-3b-local-multi-provider-runtime.md)
9. [0009：Foundation 3C 命名 Provider Profile 与 Runtime Manager](./decisions/0009-foundation-3c-named-provider-profiles-and-runtime-manager.md)
10. [0010：Foundation 3D 稳定 Profile Identity 与可恢复 Session](./decisions/0010-foundation-3d-stable-profile-identity-and-durable-sessions.md)
11. [0011：解耦 REPL 展示与 Slash Dispatch](./decisions/0011-decoupled-repl-presentation-and-slash-dispatch.md)
12. [0012：第一版 Canonical Model System Prompt](./decisions/0012-first-canonical-model-system-prompt.md)
13. [0013：Provider-owned Model Context Capability](./decisions/0013-provider-owned-model-context-capabilities.md)
14. [0014：Target-specific Request Counting 与 Per-invocation Preflight](./decisions/0014-target-specific-request-counting-and-preflight.md)
15. [0015：Target-aware Runtime Switch UX](./decisions/0015-target-aware-runtime-switch-ux.md)
16. [0016：Provider-neutral Effective Context Snapshot](./decisions/0016-provider-neutral-effective-context-snapshot.md)
17. [0017：Controlled Compact Transaction](./decisions/0017-controlled-compact-transaction.md)
18. [0018：Target-aware Resume Prepare/Commit](./decisions/0018-target-aware-resume-prepare-commit.md)
19. [0019：Pre-turn Automatic Context Compaction](./decisions/0019-pre-turn-automatic-context-compaction.md)
20. [0020：Foundation 1C Bounded Workspace Glob](./decisions/0020-foundation-1c-bounded-workspace-glob.md)
21. [0021：Foundation 1D Bounded Literal Grep](./decisions/0021-foundation-1d-bounded-literal-grep.md)
22. [0022：Foundation 4A Permission Policy Contract](./decisions/0022-foundation-4a-permission-policy-contract.md)
23. [0023：Foundation 4A Exact Action Identity、Single-use Approval Grant与Durable Action Audit](./decisions/0023-foundation-4a-exact-action-identity-and-durable-audit.md)
24. [0024：Foundation 4A Approval Coordination、Runtime Integration与Controlled `write_file`](./decisions/0024-foundation-4a-approval-coordination-and-controlled-write.md)
25. [0025：Foundation 4A Action Audit Observability](./decisions/0025-foundation-4a-action-audit-observability.md)
26. [0026：Foundation 4B Exact Edit Preparation、Execution与Authorization Composition](./decisions/0026-foundation-4b-exact-edit-preparation-execution-and-authorization.md)
27. [0027：Foundation 4B Model-visible Exact Edit Integration](./decisions/0027-foundation-4b-model-visible-exact-edit-integration.md)
28. [0028：Foundation 4C Controlled Command Contract与Side-effect-free Preparation](./decisions/0028-foundation-4c-controlled-command-contract-and-preparation.md)
29. [0029：Foundation 4C Bounded Command Execution与Process-group Cleanup](./decisions/0029-foundation-4c-bounded-command-execution-and-process-cleanup.md)
30. [0030：Foundation 4C Durable Model-visible Command Integration](./decisions/0030-foundation-4c-durable-model-visible-command-integration.md)
31. [0031：Foundation 4D Controlled Single-directory Creation](./decisions/0031-foundation-4d-controlled-single-directory-creation.md)
32. [0032：Foundation 4E Controlled No-overwrite File Move](./decisions/0032-foundation-4e-controlled-no-overwrite-file-move.md)
33. [0033：Foundation 4F Controlled Regular-file Deletion](./decisions/0033-foundation-4f-controlled-regular-file-deletion.md)
34. [0034：Foundation 4G Controlled Empty-directory Deletion](./decisions/0034-foundation-4g-controlled-empty-directory-deletion.md)
35. [0035：Foundation 1E Bounded One-level Directory Listing](./decisions/0035-foundation-1e-bounded-directory-listing.md)
36. [0036：Foundation 4H Controlled Bounded Regular-file Copy](./decisions/0036-foundation-4h-controlled-bounded-file-copy.md)
37. [0037：工具批次 A Bounded Workspace Navigation](./decisions/0037-batch-a-bounded-workspace-navigation.md)
38. [0038：工具批次 B Process-isolated Regex Grep](./decisions/0038-batch-b-process-isolated-regex-grep.md)
39. [0039：工具批次 C Structured Exact Multi-edit Patch](./decisions/0039-batch-c-structured-exact-multi-edit-patch.md)
40. [0040：Shared Six-call Tool Budget](./decisions/0040-shared-six-call-tool-budget.md)
41. [0041：Live Redacted Tool Activity Events](./decisions/0041-live-redacted-tool-activity-events.md)
42. [0042：Provider-neutral Assistant Tool Text Representation](./decisions/0042-provider-neutral-assistant-tool-text-representation.md)
43. [0043：Provider Mixed-response Inbound Normalization](./decisions/0043-provider-mixed-response-inbound-normalization.md)
44. [0044：`turn_committed` v3 Assistant Tool Text Persistence](./decisions/0044-turn-committed-v3-assistant-tool-text-persistence.md)
45. [0045：Provider Mixed-response History Projection](./decisions/0045-provider-mixed-response-history-projection.md)
46. [0046：AgentLoop 与 Terminal Assistant Tool Text Integration](./decisions/0046-agent-loop-and-terminal-assistant-tool-text-integration.md)
47. [0047：Provider-neutral Synchronous Response Streaming](./decisions/0047-provider-neutral-synchronous-response-streaming.md)
48. [0048：OpenAI-compatible Chat Completions Streaming](./decisions/0048-openai-compatible-chat-completions-streaming.md)
49. [0049：Anthropic Messages Streaming](./decisions/0049-anthropic-messages-streaming.md)
50. [0050：AgentLoop、Runtime 与 Terminal Streaming Integration](./decisions/0050-agentloop-runtime-and-terminal-streaming-integration.md)
51. [0051：TTY Markdown Rendering](./decisions/0051-tty-markdown-rendering.md)
52. [0052：Exact Bounded Informed Approval Previews](./decisions/0052-exact-bounded-informed-approval-previews.md)
53. [0053：TTY Prompt Editor 与交互反馈](./decisions/0053-tty-multiline-prompt-editor.md)
54. [0054：Sequential Tool-call Budget Hardening](./decisions/0054-sequential-tool-call-budget-hardening.md)
55. [0055：Bounded Multi-tool Response Batches](./decisions/0055-bounded-multi-tool-response-batches.md)
56. [0056：Structured Tool Outcome Ledger](./decisions/0056-structured-tool-outcome-ledger.md)
57. [0057：Durable Tool Ledger Inspection](./decisions/0057-durable-tool-ledger-inspection.md)
58. [0058：Runtime Context Meter 与 Provider Token Usage](./decisions/0058-runtime-context-meter-and-provider-token-usage.md)
59. [0059：Context 与 Compaction Observability](./decisions/0059-context-and-compaction-observability.md)
60. [0060：Provider Output-limit 与 Compaction Failure Diagnostics](./decisions/0060-provider-output-limit-and-compaction-failure-diagnostics.md)
61. [0061：Process-local Runtime Output Budget Control](./decisions/0061-process-local-runtime-output-budget-control.md)
62. [0062：Durable Session Provider Usage Audit](./decisions/0062-durable-session-provider-usage-audit.md)
63. [0063：Bounded Read-only Git Change Observation](./decisions/0063-bounded-read-only-git-change-observation.md)
64. [0064：Bounded Reachable Git History Observation](./decisions/0064-bounded-reachable-git-history-observation.md)
65. [0065：Opt-in Bounded Live Tool Details](./decisions/0065-opt-in-bounded-live-tool-details.md)
66. [0066：Trusted Command Result Observability](./decisions/0066-trusted-command-result-observability.md)
67. [0067：Persistent Inline Terminal Frontend](./decisions/0067-persistent-inline-terminal-frontend.md)
68. [0068：Terminal Message Hierarchy and Hanging Indent](./decisions/0068-terminal-message-hierarchy-and-hanging-indent.md)
69. [0069：Host Workbench Navigation and Failure Guidance](./decisions/0069-host-workbench-navigation-and-guidance.md)
70. [0070：Assistant Turn Execution Trace Grouping](./decisions/0070-assistant-turn-execution-trace-grouping.md)
71. [0071：Durable Session Naming and Terminal Identity](./decisions/0071-durable-session-naming-and-terminal-identity.md)
72. [0072：Session Archive, Search, and Title Fallback Diagnostics](./decisions/0072-session-archive-search-and-title-fallback-diagnostics.md)
73. [0073：Pinned Sessions and Snapshot-based Quick Switching](./decisions/0073-pinned-sessions-and-snapshot-quick-switching.md)
74. [0074：Read-only Session Inspection and Bounded Turn Preview](./decisions/0074-read-only-session-inspection-and-bounded-turn-preview.md)
75. [0075：Bounded Cross-Session Final-text Search](./decisions/0075-bounded-cross-session-final-text-search.md)
76. [0076：Bounded Session Turn-range Inspection](./decisions/0076-bounded-session-turn-range-inspection.md)
77. [0077：Bounded Conversation-only Session Export](./decisions/0077-bounded-conversation-export.md)
78. [0078：Provenance-linked Session Forking](./decisions/0078-provenance-linked-session-forking.md)
79. [0079：Explicit Session Diagnosis and Tail Repair](./decisions/0079-explicit-session-diagnosis-and-tail-repair.md)
80. [0080：Fail-closed Linux Command Sandbox](./decisions/0080-fail-closed-linux-command-sandbox.md)
81. [0081：Host Workbench Diagnostics and Prompt History Search](./decisions/0081-host-workbench-diagnostics-and-prompt-history-search.md)
82. [0082：Host Policy and Tool Discoverability](./decisions/0082-host-policy-and-tool-discoverability.md)
83. [0083：Foundation 5A Root AGENTS.md Project Instructions](./decisions/0083-foundation-5a-root-agents-project-instructions.md)
84. [0084：Deterministic Offline Host Eval Baseline](./decisions/0084-deterministic-offline-host-eval-baseline.md)
85. [0085：Actual Coding Task Eval](./decisions/0085-actual-coding-task-eval.md)
86. [0086：Durable Task Identity and Host Management](./decisions/0086-durable-task-identity-and-host-management.md)
87. [0087：Durable Stage Lifecycle and Turn Evidence](./decisions/0087-durable-stage-lifecycle-and-turn-evidence.md)
88. [0088：Foreground Task Stage Execution and Recovery](./decisions/0088-foreground-task-stage-execution-and-recovery.md)
89. [0089：Task Planning, Acceptance, Budgets, and Management](./decisions/0089-task-planning-acceptance-budgets-and-management.md)
90. [0090：Structured Task Acceptance and Independent Review](./decisions/0090-structured-task-acceptance-and-independent-review.md)
91. [0091：Resume Runtime Binding at the Durable Commit Point](./decisions/0091-resume-runtime-binding-at-the-durable-commit-point.md)
92. [0092：Adaptive Foreground Task Orchestration](./decisions/0092-adaptive-foreground-task-orchestration.md)
93. [0093：TTY Host Wrapping and Process-local Command History](./decisions/0093-tty-host-wrapping-and-process-local-command-history.md)
94. [0094：Task Proposal Control Boundary](./decisions/0094-task-proposal-control-boundary.md)
95. [0095：Model-visible Task Coordination Tools](./decisions/0095-model-visible-task-coordination-tools.md)
96. [0096：Model-proposed Task Admission](./decisions/0096-model-proposed-task-admission.md)
97. [0097：Informed Task Admission and Foreground Handoff](./decisions/0097-informed-task-admission-and-foreground-handoff.md)
98. [0098：Natural-language Task Lifecycle Handoffs](./decisions/0098-natural-language-task-lifecycle-handoffs.md)
99. [0099：Recoverable Provider Tool Argument Validation](./decisions/0099-recoverable-provider-tool-argument-validation.md)
100. [0100：Persistent Activity Indicator and Task Output Alignment](./decisions/0100-persistent-activity-indicator-and-task-output-alignment.md)
101. [0101：turn_committed v5 Inherited Assistant Content Replay](./decisions/0101-turn-committed-v5-inherited-assistant-content-replay.md)
102. [0102：Bounded Independent Web Search](./decisions/0102-bounded-independent-web-search.md)
103. [0103：Provider-native Web Search](./decisions/0103-provider-native-web-search.md)
104. [0104：OpenAI Responses Protocol and Provider-owned History](./decisions/0104-openai-responses-protocol-and-provider-owned-history.md)
105. [0105：Provider Search Resilience, Controls, and Observability](./decisions/0105-provider-search-resilience-controls-and-observability.md)
106. [0106：Bounded Fetch, Structured Read, and Controlled Transfer Tools](./decisions/0106-bounded-fetch-structured-read-and-controlled-transfer-tools.md)
107. [0107：Unified Extension Contract and ToolSet Snapshots](./decisions/0107-unified-extension-contract-and-tool-set-snapshots.md)
108. [0108：Confined stdio MCP Configuration and Inspection](./decisions/0108-confined-stdio-mcp-configuration-and-inspection.md)
109. [0109：MCP Tool Normalization and Quarantine Catalog](./decisions/0109-mcp-tool-normalization-and-quarantine-catalog.md)
110. [0110：Progressive MCP Discovery and ToolSet Epochs](./decisions/0110-progressive-mcp-discovery-and-toolset-epochs.md)
111. [0111：Audited MCP Execution and Process Lifecycle](./decisions/0111-audited-mcp-execution-and-process-lifecycle.md)
112. [0112：Bounded MCP Notifications and Catalog Invalidation](./decisions/0112-bounded-mcp-notifications-and-catalog-invalidation.md)
113. [0113：Fingerprint-bound Local MCP Tool Policy](./decisions/0113-fingerprint-bound-local-mcp-tool-policy.md)
114. [0114：Streamable HTTP and Remote Network Trust](./decisions/0114-streamable-http-and-remote-network-trust.md)
115. [0115：Local OAuth 2.1 PKCE Lifecycle](./decisions/0115-local-oauth-21-pkce-lifecycle.md)
116. [0116：Bounded MCP Resources, Prompts, Subscriptions, and Roots](./decisions/0116-bounded-mcp-resources-prompts-subscriptions-and-roots.md)
117. [0117：Bounded MCP Sampling and Elicitation](./decisions/0117-bounded-mcp-sampling-and-elicitation.md)
118. [0118：MCP Interoperability and Production Hardening](./decisions/0118-mcp-interoperability-and-production-hardening.md)
119. [0119：Early Session Title Preparation and Terminal Rendering Safety](./decisions/0119-early-session-title-and-terminal-rendering-safety.md)
120. [0120：Transport-aware MCP Approval and Policy Diagnostics](./decisions/0120-transport-aware-mcp-approval-and-policy-diagnostics.md)
121. [0121：Bounded Declarative Skills and ToolSet Restriction](./decisions/0121-bounded-declarative-skills-and-toolset-restriction.md)
122. [0122：Bounded Skill Resources, Composition, and Observability](./decisions/0122-bounded-skill-resources-composition-and-observability.md)
123. [0123：Skill Authoring, Local Import, Task Audit, and Execution Boundary](./decisions/0123-skill-authoring-import-audit-and-execution-boundary.md)
124. [0124：Explicit Skill Authoring and Quarantined Remote Install](./decisions/0124-explicit-skill-authoring-and-quarantined-remote-install.md)
125. [0125：Frozen Declarative Preauthorization Hooks](./decisions/0125-frozen-declarative-preauthorization-hooks.md)
126. [0126：Durable Hook Observation and Audit](./decisions/0126-durable-hook-observation-and-audit.md)
127. [0127：Audited Pinned Local Hook Handlers](./decisions/0127-audited-pinned-local-hook-handlers.md)
128. [0148：有界递归只读 Child 与 Host-owned 工作流编排](./decisions/0148-bounded-recursive-child-and-host-workflow-orchestration.md)
129. [0149：持久化工作流阶段观察与恢复](./decisions/0149-durable-workflow-stage-observation-and-recovery.md)
130. [0150：上游 Provider API 错误事实与安全展示](./decisions/0150-upstream-provider-error-facts-and-safe-display.md)
131. [0151：统一只读可观察性时间线](./decisions/0151-unified-read-only-observation-timeline.md)
132. [0152：可观察性事件流、诊断与留存](./decisions/0152-observation-stream-diagnosis-and-retention.md)
133. [0153：Provider Reasoning Effort Modes](./decisions/0153-provider-reasoning-effort-modes.md)
134. [0154：Child/Team恢复边界与Provider档位矩阵](./decisions/0154-child-team-recovery-and-effort-matrix.md)
135. [0155：实时Provider回合与工具时间线](./decisions/0155-live-provider-round-and-tool-timeline.md)

## 上游 Provider API 错误事实与安全展示

OpenAI-compatible（包括 Responses 路径）和 Anthropic adapter 现在在统一的
`ProviderFailure` 中保留上游错误的有限事实：HTTP 状态码（100–599）、标准
`error.type`、`error.code`、上游 message、request ID 和安全解析的
`Retry-After` 秒数。Coquo 自己的 `kind`、`diagnostic_code`、`retryable` 和
Host message 仍然保留，因此上游事实不会替代 Host 的分类、重试或停止语义；
例如 3xx、4xx、429、5xx 即使被 SDK 归入通用失败，也会显示实际状态码。

CLI 的 Provider failure 输出现在分行显示这些字段，便于判断是认证、权限、
请求、限流、模型、服务端还是传输问题。所有上游字段在 adapter 边界做长度、
可打印字符和状态码约束；非 JSON 或未知 body 不会被完整复制，headers、raw
body、credential 和 token 永不进入错误对象、Session 或终端。该切片只改善事实
保留与展示，不引入自动 retry、fallback、等待、重新发送或 telemetry。详见
[0150：上游 Provider API 错误事实与安全展示](./decisions/0150-upstream-provider-error-facts-and-safe-display.md)。

## 统一只读可观察性时间线

O1–O3新增Host-only的`ObservationEvent`投影契约和`observe timeline`命令，
在不新增第二份日志、不复制对话/工具/消息/handoff正文、不迁移旧schema的前提下，
从现有Session、Task、Child Run和Team durable ledger生成统一文本或JSONL时间线。
事件保留稳定身份、sequence、时间、phase/status、证据等级、parent event和有限
关联ID；Session/Task/Team严格replay的事实标为`host-verified`，Child生命周期为
`host-observed`，Child handoff为`untrusted`。workspace-wide合并通过已有Task admission、
Stage delegation、Child parent delegation、Team control和assignment ID建立跨账本父子关系，
缺失关系则保持无父事件而不按时间或正文猜测。普通进程内Turn携带易失的
`trace_id/turn_id/session_id`，同进程派生context可继承trace；Detached Child不会跨重启
接收未持久化的父trace，而是通过durable parent/session/tool/stage/assignment身份关联。
这不改变Session、Provider、system prompt或model-visible tool contract；重启后的历史trace
只能依据现有durable身份重建。详见
[0151：统一只读可观察性时间线](./decisions/0151-unified-read-only-observation-timeline.md)。

## 可观察性事件流、诊断与留存

O4–O9在O1–O3的Host-only契约上增加进程内有界`ObservationStream`，把现有
PromptEvent投影为不含正文的live事件，并携带当前易失trace/turn关联；原有
终端event sink仍独立，stream异常不会改变Agent因果结果。Provider生命周期、
context preflight、compaction、usage、tool、permission、Task、Child、Team和
worker事件使用同一事件形状；后台队列snapshot投影为`background_*`的
Host-observed事件，只保留稳定ID、状态、时间及worker/lease引用，Child ledger仍是
执行事实源。

`observe timeline`新增trace、status、evidence、record type和ISO-8601时间窗口的
有界过滤；`observe diagnose`只读报告缺失父链接、不可信handoff、失败/未知结果和
过期后台claim，并给出人工恢复建议，不自动retry、recover、approve或修改账本。
进程内事件按数量和可选年龄留存，绝不删除Session、Task、Child、Team或queue记录；
不保存prompt、模型/工具/handoff正文、headers、凭据或token，也不引入远程telemetry。
详见[0152：可观察性事件流、诊断与留存](./decisions/0152-observation-stream-diagnosis-and-retention.md)。

## Provider 推理强度模式

Runtime现在提供Host-owned的进程内推理强度并集
`none|minimal|low|medium|high|xhigh|max`，它与`max_output_tokens`分离：前者
控制Provider推理策略，后者限制可见输出大小。CLI一次调用可使用全部七档，
REPL可用`/effort`查看、`/effort <level>`切换或`/effort reset`恢复；切换只
允许发生在turn之间，并以脱敏的`runtime_changed` binding审计，不写入reasoning正文。

Profile在配置时声明native kind、native levels、Host到native的映射以及可选默认档位。
OpenAI-compatible Chat Completions和Responses由adapter发送映射后的原生字段；
Anthropic Messages使用字符串adaptive effort（`thinking`与`output_config.effort`），
不支持旧式数字`budget_tokens`。缺少映射时明确失败，不再隐式把`max`改成`high`；
旧binding缺少新字段时按unset回放。Child只继承脱敏的路由事实，不因推理强度提高权限、
工具或预算。档位不会改变并发、Child数量或循环上限。
详见[0153：Provider Reasoning Effort Modes](./decisions/0153-provider-reasoning-effort-modes.md)。

## Child/Team恢复边界与Provider档位矩阵

并行Child读取Session transcript时，Host只读回放使用有界稳定快照重试，短暂的
追加尺寸变化不会再被误报为损坏；持续变化仍然fail-closed。Supervisor的恢复
通过durable execution lease判断所有权：活动worker只能报告`still_owned`，释放lease
后才允许恢复为`interrupted`，不自动重试、恢复执行或执行READY Child。

Task到Child/Team的观察只有在terminal记录和身份一致的handoff都可验证时才收敛。
失败、取消和中断统一进入一次幂等的Task失败阶段；handoff缺失或不一致保持恢复错误。
Provider档位回归矩阵覆盖所有Host档位在OpenAI Chat/Responses及Anthropic adaptive
字符串映射中的行为，未映射档位fail-closed，旧式数字`budget_tokens`继续不支持。
详见[0154：Child/Team恢复边界与Provider档位矩阵](./decisions/0154-child-team-recovery-and-effort-matrix.md)。

## 实时Provider回合与工具时间线

一个用户prompt仍只产生一个持久化Session Turn，但Agent loop内部的每次模型请求
现在以Host-only的`ProviderInvocationStarted/Finished`成对呈现。开始事件在preflight
或Provider I/O之前发出；结束事件只携带invocation序号、上限、`turn | session-title`
用途、`final-text`、`tool-request`、`failed`或`cancelled`结果、有限工具数量及Host测得的
有界耗时，不保留模型响应、reasoning或原始工具参数。首Turn自动Session标题调用也按
真实共享预算编号显示，因此不再出现无法解释的round 1直接跳到round 3。现有工具
开始/结束、安全摘要和assistant delta继续复用。

持久TTY现在显示逻辑Turn起点、逐个Model round、工具执行、流式最终文本和Provider
等待秒数；动态等待行是临时的，每轮永久结束行同时保留耗时，复制scrollback也能
分辨供应商慢响应与终端延迟。只有Session prompt从durable commit路径返回后才显示
`Turn committed`。
Provider round超过5秒仍未完成时，TTY还会每5秒追加一条低频Host-only的`still waiting`
心跳，明确显示当前round和累计等待时间；心跳不包含Provider响应、请求正文或工具参数。
生命周期事件保持FIFO且不可丢弃，队列保留独立文本delta以便逐块刷新。非TTY成功的prompt和
eval保持原有stdout/stderr安静契约，公开NDJSON事件格式留待独立版本化设计。该切片
参考Claw-Code对run、assistant round与tool result的概念分层，但不复制其TUI、prompt、
wire format或实现。详见
[0155：实时Provider回合与工具时间线](./decisions/0155-live-provider-round-and-tool-timeline.md)。
