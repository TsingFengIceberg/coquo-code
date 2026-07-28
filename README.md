<div align="center">

<img src="./docs/assets/leo-mark.png" alt="LEO mark" width="240">

# Leonervis Code

[English](./README_en.md) | 中文

[![Python](https://img.shields.io/badge/Python-3.12%E2%80%933.13-3776AB?logo=python&logoColor=white)](./pyproject.toml)
[![uv](https://img.shields.io/badge/uv-managed-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![pytest](https://img.shields.io/badge/pytest-8%2B-0A9EDC?logo=pytest&logoColor=white)](./pyproject.toml)
[![Ruff](https://img.shields.io/badge/Ruff-0.9%2B-D7FF64?logo=ruff&logoColor=black)](./pyproject.toml)

</div>

Leonervis Code 是一个面向本地单用户使用、以学习为先的 Coding Agent CLI 原型。模型负责决策，Host 在明确的 workspace 边界内执行受控工具，并把结构化结果写回模型。

> **当前状态：** 已支持命名 provider profile、真实/离线 runtime、可恢复 Session，以及17个受限工具。Anthropic与OpenAI-compatible可把单次provider回复中的有序多工具调用转换为统一batch，Host完整验证后仍逐个经过PermissionGate、approval与Action Audit，绝不并行。持久tool ledger、compaction checkpoint与provider usage audit可安全查看，context压力和当前进程Token用量也即时可见。当前三层预算为每个回复最多8个调用、每个user turn最多32个工具请求、最多24次provider invocation且最后一次只允许文字。Foundation 5A暂缓。

## 目录

- [快速开始](#快速开始)
- [主要命令](#主要命令)
  - [执行任务与启动 REPL](#执行任务与启动-repl)
  - [配置 Provider](#配置-provider)
  - [检查 Route 与 Context Window](#检查-route-与-context-window)
  - [管理 Session](#管理-session)
  - [REPL 命令](#repl-命令)
- [配置与本地状态](#配置与本地状态)
- [开发与验证](#开发与验证)
- [详细文档](#详细文档)
- [当前范围与下一步](#当前范围与下一步)

## 快速开始

要求 Python 3.12 或 3.13、最新稳定版 [uv](https://docs.astral.sh/uv/) 和 Git。项目使用 `uv.lock` 管理可复现环境。

```bash
cd leonervis-code
uv sync
uv run leonervis-code
```

裸命令会在真实终端中启动 REPL。未选择真实 provider 时使用确定性的 fake provider，不访问网络：

```text
›

  fake · ~/Projects/leonervis-code
```

正式命令为 `leonervis-code`，`leonervis` 是简写；也可使用模块入口：

```bash
uv run leonervis --version
uv run python -m leonervis_code --help
```

## 主要命令

完整参数始终以命令自身帮助为准：

```bash
uv run leonervis-code --help
uv run leonervis-code provider --help
uv run leonervis-code session --help
```

### 执行任务与启动 REPL

| 用途 | 命令 |
| --- | --- |
| 启动新 Session 的 REPL | `uv run leonervis-code` |
| 恢复当前 workspace 的最新 Session | `uv run leonervis-code --resume latest` |
| 执行一次 prompt | `uv run leonervis-code prompt "解释这个 workspace"` |
| 在指定 workspace 执行 | `uv run leonervis-code -C ../project prompt "解释项目结构"` |
| 使用命名 profile | `uv run leonervis-code --profile work prompt "解释 README"` |
| 临时覆盖本进程输出预算 | `uv run leonervis-code --profile work --max-output-tokens 8192 prompt "生成详细报告"` |
| 临时覆盖 profile 的 model | `uv run leonervis-code --profile work --model model-v2 prompt "继续"` |
| 使用直接 model route | `uv run leonervis-code --model anthropic/claude-opus-4-8 prompt "解释 README"` |
| 在 REPL 逐次审批 workspace 写入 | `uv run leonervis-code --permission-mode workspace-write --approval ask` |
| 一次性允许 workspace 自动写入 | `uv run leonervis-code --permission-mode workspace-write --approval auto prompt "创建 note.txt"` |
| 在 REPL 逐次审批本地命令 | `uv run leonervis-code --permission-mode danger-full-access --approval ask` |
| 一次性自动运行获准命令 | `uv run leonervis-code --permission-mode danger-full-access --approval auto prompt "运行项目测试"` |
| 查看版本 | `uv run leonervis-code --version` |

`prompt`用于脚本和一次性任务；裸命令用于有状态多轮REPL。成功turn会自动保存，工具执行时会显示脱敏的`[tool 1/32] ...`状态行。

常用权限模式：

```bash
uv run leonervis-code                                      # read-only REPL
uv run leonervis-code --permission-mode workspace-write --approval ask
uv run leonervis-code --permission-mode danger-full-access --approval ask
uv run leonervis-code --permission-mode workspace-write --approval auto prompt "修改并验证项目"
```

REPL的`ask`审批会在`write_file`、`edit_file`和`patch_file`前显示有界candidate diff，并为copy、move、delete、mkdir和command显示必要风险事实；批准后workspace状态变化仍会stale reject。One-shot的工具状态写入stderr，最终回答写入stdout；REPL内可用`/actions`查看持久化Action Audit。17个工具的参数、权限、workspace/symlink、timeout、stale-state和durability边界见[已实现Foundation与设计演进](./docs/implemented-foundations.md)及[架构决策记录](./docs/decisions/)。

### 配置 Provider

内置 provider 使用 catalog 中的 protocol、默认 endpoint 和 credential 环境变量名：

```bash
export ANTHROPIC_API_KEY='...'
uv run leonervis-code provider add work \
  --provider anthropic \
  --model claude-opus-4-8
```

自定义 OpenAI-compatible endpoint 必须显式给出 protocol 和 base URL。Profile 只保存 credential 的环境变量名，不保存 key value：

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

常用 profile 管理命令：

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

选择优先级为：显式 `--profile` → 显式 direct `--model` → workspace active → user active → fake/offline。`provider use` 会在候选 route、credential 和 client 准备成功后才原子切换；失败时保留旧配置与旧 client。

### 检查 Route 与 Context Window

`route` 是离线诊断命令：不构造 provider client，不读取 key value，也不发起网络请求。

```bash
uv run leonervis-code --profile vendor route
uv run leonervis-code --model openai/gpt-5 route
```

命名 profile 可为 exact endpoint/model 配置上下文窗口：

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

使用`route`查看离线解析结果，在REPL中使用`/status`和`/context`查看当前runtime与context状态。Capability解析、request preflight、自动compact和切换前screening的完整规则见[已实现Foundation与设计演进](./docs/implemented-foundations.md)。

### 管理 Session

```bash
uv run leonervis-code prompt "第一轮"
uv run leonervis-code session list
uv run leonervis-code session show latest
uv run leonervis-code session actions latest
uv run leonervis-code session tools latest
uv run leonervis-code session tools latest --limit 5 --details
uv run leonervis-code --resume latest prompt "继续上一轮"
uv run leonervis-code --resume <session-uuid>
```

Session绑定workspace，并以append-only JSONL保存成功turn。新turn还保存Host逐请求工具账本，记录实际成功、错误、跳过和预算拒绝，不依赖模型自报。使用上面的`session`与`--resume`命令即可检查、审计和恢复；完整replay、screening与durability语义见[已实现Foundation与设计演进](./docs/implemented-foundations.md)。

### REPL 命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 查看控制命令 |
| `/history <count>` | 显示当前 Session 最近的完整回合 |
| `/actions [count]` | 显示当前 Session 最近的脱敏 Action Audit，默认20条、最多100条 |
| `/tools [count]` | 显示当前 Session 最近turn的持久工具账本汇总，默认5个、最多20个 |
| `/tools details [count]` | 展开逐请求工具名、结果状态和安全result code，总输出最多32 KiB |
| `/status` | 显示脱敏 runtime、model 和 context-window 状态 |
| `/context` | 只读检查当前 Effective Context、内容 ID、计数与 target fit |
| `/usage` | 查看当前进程内最近调用、最近turn及当前profile的真实provider Token用量 |
| `/usage session` | 查看当前Session跨重启保留的turn、失败与compaction累计用量 |
| `/usage turns` | 查看最近10个成功或失败turn的持久Token用量 |
| `/output [tokens\|reset]` | 查看、临时调整或恢复当前runtime的输出Token预算 |
| `/compact preview` | 只读预览固定compaction选择与当前context压力，不生成summary或修改Session |
| `/compact` | 使用当前真实 provider 手动总结较早完整回合并持久化 effective-context checkpoint |
| `/compactions [count]` | 查看最近的持久compaction checkpoint，默认5条、最多20条 |
| `/provider list` | 列出命名 profile |
| `/provider current` | 显示当前 profile/provider/model |
| `/provider use <name>` | 为当前 workspace 原子切换 active profile |
| `/model <model>` | 仅覆盖当前进程 model，不修改 profile |
| `/session show` | 显示当前 Session |
| `/session list` | 列出 workspace Session |
| `/session new` | 保持当前 runtime，开始空白 Session |
| `/resume <latest\|id>` | 保持当前 runtime，切换 Session |
| `/clear` | 只清空当前终端画面，不修改 Session 或 history |
| `/exit`、`/quit` | 正常退出 |

常用REPL操作：

```text
/status
/context
/compact preview
/compactions 5
/usage
/usage session
/usage turns
/actions
/tools details 3
/compact
/resume latest
/history 5
```

真实TTY使用`›`输入标记和`model · context · workspace`状态栏。每次真实provider调用前会显示方块context条，调用后显示厂商实际返回的input/output Token；工具continuation分别计量，turn结束后汇总当前turn与profile。`/context`和`/compact preview`会标明normal、接近80%、auto-compact、接近满载或unknown；`/usage`还显示当前runtime最近一次compaction generation。`/usage session`与`/usage turns`从严格replay的Session终局记录读取跨重启用量；旧记录显示legacy unavailable，缺失usage metadata明确计为unknown而不按0处理。Provider用尽输出上限时，终端会显示requested limit与可用的actual usage；不完整回复不会成为final answer或committed turn，已完成的工具副作用不会回滚。`/output`显示effective、configured default和known model maximum；`/output 8192`只调整当前进程，`/output reset`恢复profile或direct route默认值。调整会在当前Effective Context上先筛查known overflow，并重建provider route；profile文件、Session历史和已有usage累计不变。Model切换保留临时预算并重新筛查，新profile切换清除它。非缩减`/compact`失败会显示source与candidate input计量，并保持checkpoint及Effective Context不变，同时持久保存失败调用的usage audit。进程内统计仍在成功`/provider use`或`/model`切换后清零；Session统计持久保留，但不计算费用。Enter提交，Alt+Enter换行；若terminal拦截Alt组合，可先按Esc再按Enter。提交后assistant内容以`•`开头，工具turn另显示Host生成的`Tool summary:`。TTY会渲染assistant Markdown；pipe/redirect保留原始Markdown。`NO_COLOR=1`关闭颜色但保留Markdown布局。完整边界见[已实现Foundation与设计演进](./docs/implemented-foundations.md)。

用于观察受限工具循环的确定性演示命令：

```bash
uv run leonervis-code demo-read README.md
uv run leonervis-code demo-read ../outside.txt   # 验证 workspace 逃逸拒绝
```

`demo-read` 不是实际模型接口，不写文件、不执行 shell，也不访问网络。

## 配置与本地状态

| 路径 | 内容 |
| --- | --- |
| `${XDG_CONFIG_HOME:-~/.config}/leonervis-code/providers.json` | user provider profiles 与 active selection |
| `<workspace>/.leonervis-code/provider.json` | workspace active profile |
| `<workspace>/.leonervis-code/sessions/.../*.jsonl` | Session transcript |
| `${XDG_CACHE_HOME:-~/.cache}/leonervis-code/model-context-capabilities.json` | private context capability discovery cache |

`.leonervis-code/` 可能包含用户输入、模型回答、源码片段和工具结果，应加入目标项目的 `.gitignore`，不要提交、同步或公开。配置和 capability cache 不保存已知 credential value，但系统无法识别用户文本或源码中自行出现的未知 secret。

## 开发与验证

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
git diff --check
```

依赖变化后先执行 `uv lock`，再检查锁文件。Leonervis Code 不为目标 workspace 安装 Node、Rust、Java、Docker、数据库等项目环境。

## 详细文档

- [已实现 Foundation 与设计演进](./docs/implemented-foundations.md)：system prompt、工具循环、route policy、多 provider runtime、profile、Session、context capability、compaction、permission/approval与controlled write的集中说明。
- [架构决策记录](./docs/decisions/)：每个学习切片的完整问题、取舍、边界与验证记录。
- [AgentLoop 与 Terminal Assistant Tool Text Integration](./docs/decisions/0046-agent-loop-and-terminal-assistant-tool-text-integration.md)：mixed response的顺序执行、即时展示、failure atomicity与Session恢复。
- [AgentLoop、Runtime 与 Terminal Streaming Integration](./docs/decisions/0050-agentloop-runtime-and-terminal-streaming-integration.md)：stream preflight、完整工具组装、即时REPL显示与durable final确认。
- [TTY Markdown Rendering](./docs/decisions/0051-tty-markdown-rendering.md)：safe-block streaming、TTY layout、raw redirect与terminal control边界。
- [Exact Bounded Informed Approval Previews](./docs/decisions/0052-exact-bounded-informed-approval-previews.md)：prepared candidate diff、exact action绑定、风险摘要、terminal安全与stale revalidation。
- [TTY Prompt Editor 与交互反馈](./docs/decisions/0053-tty-multiline-prompt-editor.md)：exact多行输入、Session派生历史、slash补全、清屏与临时assistant状态。
- [Sequential Tool-call Budget Hardening](./docs/decisions/0054-sequential-tool-call-budget-hardening.md)：超预算任务分批、multiple-call精确诊断与不变的顺序执行边界。
- [Bounded Multi-tool Response Batches](./docs/decisions/0055-bounded-multi-tool-response-batches.md)：provider batch抽取、Host顺序执行、8/32/24预算、Session/context兼容与failure atomicity。
- [Structured Tool Outcome Ledger](./docs/decisions/0056-structured-tool-outcome-ledger.md)：逐请求Host计账、强制text-only权威摘要、Session v5与提交后终端汇总。
- [Durable Tool Ledger Inspection](./docs/decisions/0057-durable-tool-ledger-inspection.md)：严格replay后的有界账本查询、`session tools`、`/tools`与旧Session可用性标记。
- [Runtime Context Meter 与 Provider Token Usage](./docs/decisions/0058-runtime-context-meter-and-provider-token-usage.md)：逐调用context进度、厂商usage归一化、turn/profile进程内累计与unknown语义。
- [Context 与 Compaction Observability](./docs/decisions/0059-context-and-compaction-observability.md)：只读compact preview、持久checkpoint历史、context风险分级与最近compaction用量。
- [Provider Output-limit 与 Compaction Failure Diagnostics](./docs/decisions/0060-provider-output-limit-and-compaction-failure-diagnostics.md)：结构化输出截断、失败调用usage计量、未提交说明与非缩减压缩证据。
- [Process-local Runtime Output Budget Control](./docs/decisions/0061-process-local-runtime-output-budget-control.md)：CLI/REPL临时预算、target-aware筛查、切换语义与usage连续性。
- [Durable Session Provider Usage Audit](./docs/decisions/0062-durable-session-provider-usage-audit.md)：成功/失败终局usage、跨resume统计、legacy unavailable与Host-only边界。
- [Provider Mixed-response History Projection](./docs/decisions/0045-provider-mixed-response-history-projection.md)：Anthropic与OpenAI-compatible continuation history的准确native投影。
- [`turn_committed` v3 Assistant Tool Text Persistence](./docs/decisions/0044-turn-committed-v3-assistant-tool-text-persistence.md)：nullable companion text、v1/v2 replay兼容与旧prefix不重写。
- [Provider Mixed-response Inbound Normalization](./docs/decisions/0043-provider-mixed-response-inbound-normalization.md)：两类provider native mixed response到统一`ToolUse`的严格转换。
- [Provider-neutral Assistant Tool Text Representation](./docs/decisions/0042-provider-neutral-assistant-tool-text-representation.md)：companion text的内部原子表示、边界与context identity。
- [Live Redacted Tool Activity Events](./docs/decisions/0041-live-redacted-tool-activity-events.md)：typed工具生命周期、终端输出通道、脱敏摘要、sink失败隔离及不改变模型/Session契约的依据。
- [Bounded One-level Directory Listing](./docs/decisions/0035-foundation-1e-bounded-directory-listing.md)：一层目录观察、entry type、no-follow路径、扫描/输出上限与empty/truncated语义。
- [Controlled No-overwrite File Move](./docs/decisions/0032-foundation-4e-controlled-no-overwrite-file-move.md)：双路径identity、workspace-move审批、no-overwrite hard-link/unlink、stale检查与truthful partial。
- [Controlled Empty-directory Deletion](./docs/decisions/0034-foundation-4g-controlled-empty-directory-deletion.md)：空目录workspace-delete审批、empty-state/identity复查、rmdir原子空条件与parent durability。
- [Controlled Regular-file Deletion](./docs/decisions/0033-foundation-4f-controlled-regular-file-deletion.md)：单文件workspace-delete审批、target/parent identity、unlink durability与不可自动重试的partial。
- [Controlled Single-directory Creation](./docs/decisions/0031-foundation-4d-controlled-single-directory-creation.md)：单目录path合同、workspace-create审批、stale检查、fsync与partial durability。
- [Durable Model-visible Command Integration](./docs/decisions/0030-foundation-4c-durable-model-visible-command-integration.md)：spawn前durable commit point、CLI approval/audit、六工具顺序、provider adapter v8、system prompt v7与兼容性。
- [Bounded Command Execution与Process-group Cleanup](./docs/decisions/0029-foundation-4c-bounded-command-execution-and-process-cleanup.md)：direct argv、closed environment、有界output、UTF-8/base64、timeout/cancel与TERM→KILL清理。
- [Controlled Command Contract与Side-effect-free Preparation](./docs/decisions/0028-foundation-4c-controlled-command-contract-and-preparation.md)：argv/cwd/timeout边界、`dangerous`权限绑定、environment allowlist与exact approval identity。
- [Model-visible Exact Edit Integration](./docs/decisions/0027-foundation-4b-model-visible-exact-edit-integration.md)：第五个工具的schema/order、provider parity、system prompt v6、Effective Context identity与ProjectSession dispatch。
- [Exact Edit Preparation、Execution与Authorization Composition](./docs/decisions/0026-foundation-4b-exact-edit-preparation-execution-and-authorization.md)：唯一exact replacement、无副作用prepare、原子replace、stale检查，以及为何Slice 0–3仍不改变模型契约。
- [Action Audit Observability](./docs/decisions/0025-foundation-4a-action-audit-observability.md)：standalone与REPL只读查看、脱敏字段、数量边界和不改变模型契约的依据。
- [Approval Coordination与Controlled `write_file`](./docs/decisions/0024-foundation-4a-approval-coordination-and-controlled-write.md)：coordinator顺序、prepared-turn lease、CLI approval UX、create/overwrite hard bounds与partial outcome语义。
- [Exact Action Identity与Durable Action Audit](./docs/decisions/0023-foundation-4a-exact-action-identity-and-durable-audit.md)：exact manifest/digest、prepared-turn lease、single-use grant、append-only lifecycle与crash/recovery语义。
- [Permission Policy Contract](./docs/decisions/0022-foundation-4a-permission-policy-contract.md)：permission/approval正交语义、action classes、deterministic decision matrix、stable reasons与纯policy边界。
- [Bounded Literal Grep](./docs/decisions/0021-foundation-1d-bounded-literal-grep.md)：literal/include语义、JSONL line结果、content/file bounds、generic arguments与mixed turn schema replay。
- [Bounded Workspace Glob](./docs/decisions/0020-foundation-1c-bounded-workspace-glob.md)：portable pattern、hidden/symlink policy、stable bounds、共享tool budget与legacy schema-v1 seam。
- [Pre-turn Automatic Context Compaction](./docs/decisions/0019-pre-turn-automatic-context-compaction.md)：80% high-water、pending-turn隔离、一次尝试、共享runtime lease与schema-v3 trigger provenance。
- [Target-aware Resume Prepare/Commit](./docs/decisions/0018-target-aware-resume-prepare-commit.md)：只读prepare、当前runtime screening、exact stale/CAS与durable partial outcomes。
- [Controlled Compact Transaction](./docs/decisions/0017-controlled-compact-transaction.md)：manual `/compact`、no-tools summary、mixed Session schema 与 persist-before-memory 原子性。
- [Provider-neutral Effective Context Snapshot](./docs/decisions/0016-provider-neutral-effective-context-snapshot.md)：full/effective context边界、稳定 `ctx-v1` identity 与只读 `/context`。
- [Target-aware runtime switch UX](./docs/decisions/0015-target-aware-runtime-switch-ux.md)：切换前 committed-context screening、known reject/unknown allow 与原子审计语义。
- [Target-specific request counting 与 preflight](./docs/decisions/0014-target-specific-request-counting-and-preflight.md)：每次 provider invocation 的 native input 计量、两类限制与 typed local rejection。
- [Provider-owned model context capability](./docs/decisions/0013-provider-owned-model-context-capabilities.md)：context/model-output limit 解析与缓存设计。
- [Canonical model system prompt](./docs/decisions/0012-first-canonical-model-system-prompt.md)：模型可见契约、版本和 fingerprint。
- [Stable profile identity and durable Sessions](./docs/decisions/0010-foundation-3d-stable-profile-identity-and-durable-sessions.md)：profile UUID/revision 与 Session 持久化。
- [Claw-Code prompt 学习入口](./docs/references/claw-code-prompts/README.md)：只读参考结构与 Leonervis 的采用差异。
- [Harness-study](https://github.com/TsingFengIceberg/Harness-study)：相关 Harness 阅读与学习笔记。

## 当前范围与下一步

当前model-visible surface固定为`read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file, delete_directory, list_directory, copy_file, read_file_lines, stat_path, list_tree, grep_regex, patch_file`。Provider单次回复可包含最多8个有序工具调用；每个user turn最多接纳32个工具请求和24次provider invocation，最后一次只允许文字。Host在整批解析和预算验证后逐个执行；一个动作非成功会让同批后续动作明确skipped，无法装入剩余预算的整批零执行。所有真实动作仍分别经过permission、approval、executor和Action Audit。

Provider batch、结构化tool outcome ledger及持久查看、脱敏live activity、mixed response、streaming、TTY Markdown rendering、process-local输出预算控制与Session级provider usage audit现已完成，Foundation 5A仍暂缓。当前版本为canonical system prompt v19、provider adapter contract v22、ToolArguments v1、ActionIdentity v1、`turn_committed` schema v6、`turn_failed` schema v2、Action Audit schema v1、`context_compacted` v2/v3 replay且新记录使用v4，以及current `ctx-v3`/`ctx-v4`representation；旧Session与`ctx-v1`/`ctx-v2`checkpoint继续兼容，empty full-context identity为`ctx-v3-29ff59405090ba544b2bacb144d5961daecc7d0d6359123a9262c097d0fa654d`。Recursive copy/delete、ignore-aware或indexed search、fuzzy/free-form patch、directory move、non-empty delete、recursive mkdir、shell source string、interactive PTY、network tool、自动retry/fallback、并行工具、多Agent与远程服务仍不可用。当前usage持久化设计见[ADR 0062](./docs/decisions/0062-durable-session-provider-usage-audit.md)。
