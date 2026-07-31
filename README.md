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

> **当前状态：** 已支持命名 provider profile、真实/离线 runtime、可恢复 Session、工作区根`AGENTS.md`项目指令、确定性离线Host Eval与实际Coding Task Eval、可跨重启继续的前台多Stage Task，以及21个受限工具。每个Task Stage复用普通Turn、PermissionGate、approval、Action Audit与Session提交，支持计划、精确恢复、累计预算、人工验收和明确终态。`run_command`在Linux上强制使用bubblewrap与seccomp隔离，workspace是唯一Host持久可写区且网络socket被拒绝；沙箱不可用时命令不会降级到Host直接执行。当前普通Turn三层预算为每个回复最多8个调用、每个user turn最多32个工具请求、最多24次provider invocation且最后一次只允许文字。

## 目录

- [快速开始](#快速开始)
- [主要命令](#主要命令)
  - [执行任务与启动 REPL](#执行任务与启动-repl)
  - [配置 Provider](#配置-provider)
  - [检查 Route 与 Context Window](#检查-route-与-context-window)
  - [管理 Session](#管理-session)
  - [管理 Task](#管理-task)
  - [REPL 命令](#repl-命令)
- [配置与本地状态](#配置与本地状态)
- [开发与验证](#开发与验证)
- [详细文档](#详细文档)
- [当前范围与下一步](#当前范围与下一步)

## 快速开始

要求 Python 3.12 或 3.13、最新稳定版 [uv](https://docs.astral.sh/uv/) 和 Git。项目使用 `uv.lock` 管理可复现环境。模型使用`run_command`还要求Linux、`/usr/bin/bwrap`与`libseccomp.so.2`；缺少任一项时其他功能仍可使用，但命令会fail closed。

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
uv run leonervis-code task --help
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

REPL的`ask`审批会在`write_file`、`edit_file`和`patch_file`前显示有界candidate diff，并为copy、move、delete、mkdir和command显示必要风险事实；批准后workspace状态变化仍会stale reject，也不会关闭command沙箱。沙箱把Host root设为只读、workspace重新挂载为读写、提供私有`/tmp`、遮蔽已知HOME敏感路径并禁止socket；它不提供回滚、资源配额或敌对并发事务。One-shot的工具状态写入stderr，最终回答写入stdout；REPL内可用`/actions`查看持久化Action Audit。21个工具的参数、权限、workspace/symlink、timeout、stale-state和durability边界见[已实现Foundation与设计演进](./docs/implemented-foundations.md)及[架构决策记录](./docs/decisions/)。

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

### 管理 Task

```bash
uv run leonervis-code task create "实现可恢复的多阶段任务" \
  --name "Task runtime" \
  --accept "每个Stage使用普通Turn预算" \
  --accept "每个Action保留权限与审计" \
  --max-stages 12 --max-provider-invocations 288 --max-tool-requests 384
uv run leonervis-code task list --status ready --archive active --name runtime
uv run leonervis-code task show <task-uuid>
uv run leonervis-code task timeline <task-uuid>
```

Task是高于普通Turn的持久目标，单独保存在workspace内并绑定一个已有Session。Standalone命令负责创建与只读检查；进入owner Session的REPL后，可显式继续一个Stage、生成并接受计划、前台连续运行、恢复中断、验证验收条件和写入终态。每个Stage仍是普通Turn，因此不会获得跨Stage的blanket approval，也不会绕过工具、沙箱、审计或Session durability边界。

### REPL 命令

| 命令 | 作用 |
| --- | --- |
| `/help [session\|task\|tools\|git\|context\|provider\|policy\|input]` | 按类别查看Host控制命令；`task`显示持久任务入口 |
| `/history <count>` | 显示当前 Session 最近的完整回合 |
| `/actions last`、`/actions [count] [status=<状态>] [tool=<名称>]` | 快速查看最近一次动作，或按状态和工具名筛选当前Session的脱敏Action Audit |
| `/tools catalog [tool-name]` | 显示21个规范工具的权限与可用性，或查看单个工具的参数schema和主要硬边界 |
| `/tools [count]` | 显示当前 Session 最近turn的持久工具账本汇总，默认5个、最多20个 |
| `/tools details [count]` | 展开逐请求工具名、结果状态和安全result code，总输出最多32 KiB |
| `/tool-details [compact\|full]` | 查看或切换当前进程的live工具详情；默认compact，full会显示有界结构化command argv |
| `/changes` | 不调用模型，显示当前Git仓库的staged、unstaged和untracked路径状态 |
| `/changes unstaged` | 不调用模型，显示工作树相对index的有界tracked patch |
| `/changes staged` | 不调用模型，显示index相对HEAD的有界tracked patch |
| `/commits [count] [path]` | 不调用模型，显示当前HEAD可达的近期提交，默认10条、最多50条 |
| `/commit <full-id> [path]` | 不调用模型，显示一个当前HEAD可达提交的有界message与tracked patch |
| `/status` | 汇总当前Session、权限/审批、最近context压力、工具预算、沙箱依赖及脱敏runtime状态 |
| `/permissions [permission-mode [approval-mode]]` | 显示当前PermissionGate矩阵，或只读预览另一组mode/approval组合，不修改运行时 |
| `/sandbox check` | 用固定`/usr/bin/true`检查Linux、bubblewrap、seccomp和真实沙箱activation；不调用模型或写Session |
| `/context` | 只读检查当前 Effective Context、内容 ID、计数与 target fit |
| `/instructions` | 不显示正文，只读检查根`AGENTS.md`是否加载、UTF-8字节数与内容指纹 |
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
| `/session show [latest\|id]` | 显示当前或指定Session的严格回放元数据，不执行切换 |
| `/session preview <latest\|id> [1-10]` | 只读显示指定Session最近的完整user/final-assistant回合，默认3轮、最多10轮 |
| `/session turns <latest\|id> <start> [1-10]` | 从指定的1-based完整turn开始只读显示，默认3轮 |
| `/session search <文本>` | 在最多100个Session、16 MiB transcript内字面搜索最终user/assistant文本 |
| `/session export <latest\|id> [markdown\|json]` | 将有界完整对话视图输出到终端/stdout，不包含工具与审计正文 |
| `/session fork <latest\|id> <turn>` | 复制截至指定完整turn的完整模型因果到新Session，并保留父级来源 |
| `/session doctor <latest\|id>` | 只读诊断transcript为valid、repairable tail或invalid |
| `/session repair <latest\|id>` | 先持久备份，再只修复可证明未完成的最终record |
| `/session list [count] [open\|closed] [active\|archived] [pinned\|unpinned] [model=<名称>] [name=<文本>]` | 按状态、归档、收藏、精确model和名称字面子串组合筛选workspace Session |
| `/session switch`、`/session switch list [筛选]` | 建立最多20条的进程内编号快照；再用`/session switch <编号>`单次安全切换 |
| `/session new` | 保持当前 runtime，开始空白 Session |
| `/session rename <名称>` | 持久重命名当前 Session；使用`--auto`恢复首个成功turn生成的自动标题 |
| `/session archive`、`/session unarchive` | 可逆地标记或取消归档当前Session；不改变history、runtime、latest或resume身份 |
| `/session pin`、`/session unpin` | 可逆地收藏或取消收藏当前Session；不改变history、runtime、latest或resume身份 |
| `/task start <目标>` | 创建绑定当前Session的持久Task；不调用模型或执行Stage |
| `/task list [1-100] [status=<状态>] [active\|archived] [name=<文本>]` | 只读筛选当前workspace的持久Task |
| `/task show <task-id>`、`/task timeline <task-id>` | 严格回放Task详情或完整Stage时间线，不显示对话与工具正文 |
| `/task continue <task-id> <Stage目标>`、`/task recover <task-id>` | 执行一个普通Turn，或只用已提交Session证据恢复而不重跑provider/工具 |
| `/task plan <task-id>`、`/task plan accept <task-id>`、`/task run <task-id> [1-16]` | 生成提议、人工接受，并在前台按顺序执行有界Stage；run会显示停止原因 |
| `/task verify <task-id> <条件编号> <证据>`、`/task complete <task-id>` | 将人工证据绑定当前模型完成提议；全部条件满足后才能完成 |
| `/task cancel <task-id> <原因>`、`/task fail <task-id> <原因>` | 写入明确的cancelled或failed终态 |
| `/task rename <task-id> <名称>`、`/task archive <task-id>`、`/task unarchive <task-id>` | 管理显示名称和可逆归档状态 |
| `/task derive <parent-task-id> <目标>` | 创建有父级来源但生命周期、预算与权限独立的新Task |
| `/resume <latest\|id>` | 保持当前 runtime，切换 Session |
| `/clear` | 只清空当前终端画面，不修改 Session 或 history |
| `/exit`、`/quit` | 正常退出 |

常用REPL操作：

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

真实TTY现在由一个常驻的inline `prompt_toolkit.Application`持有输入区和状态栏。提交后空白prompt立即留在底部，模型回复与工具事件显示在已提交prompt和新draft之间；busy期间可继续编辑一份draft，但Enter不会排队或插入第二条消息。Ctrl-R会在独立搜索栏中对当前Session最近1000条已提交prompt做大小写不敏感的反向搜索；slash补全可继续到工具名、permission mode、Action Audit status/tool筛选值和常用子命令，明确的近似拼写只给出建议而不自动执行。Ctrl-C请求协作取消当前turn，Ctrl-D会先取消并等待provider、tool与Action Audit清理后退出；审批暂时接管输入并在结束后恢复draft。`/clear`只重置画面，不修改Session、history或transcript。

底栏阶段由typed Host事件驱动，可区分准备turn/provider请求、规划动作、运行具体工具、处理工具结果、等待审批、compaction、记录provider usage、真实Session持久提交、最终收尾与取消。已知context、provider、runtime、authorization、Session及`run_command`结果码会给出保守的`Next:`建议，但不会自动retry、声称rollback或掩盖已完成工具副作用；`/status`、`/sandbox check`、`/tools catalog`、`/actions last`及其他slash检查都不进入模型历史。Command审批会明确说明Host只读、workspace可写和socket禁止的实际沙箱边界。

已提交用户消息固定以`› `开头，assistant正文固定以`• `开头；显式换行和终端自动换行都从标记后的正文列继续。用户消息与本轮首个可见输出之间固定留一行；若模型直接请求工具而没有阶段性正文，界面会从`  │ `Host轨迹开始，不伪造空`•`。同一user turn内的context、tool、approval、usage和failure等Host执行事实使用`  │ `轨迹线归入该Assistant Turn，但不会冒充模型原话；slash结果仍是turn外Host block，并以“回显的`›`输入 + Host结果 + 一次低强度短线”组成完整交互块。模型正文与Host轨迹切换时不再插入分隔线，完整turn结束后才在下一个`›`前显示一次低强度短线。Warning、approval和error继续保持醒目；`NO_COLOR=1`会关闭颜色与dim但保留角色、轨迹、分隔线、缩进和布局。

真实TTY使用`›`输入标记和`model · Session名称 · context · workspace`状态栏。新Session先持久显示`New session N`；首个成功turn在落盘前会用同一provider发起最多3次独立的无工具标题请求，自动标题重名时重试，仍不满足时由Host添加稳定编号兜底。标题调用与正文共享每turn最多24次provider invocation，并和首轮正文、usage一起原子写入`turn_committed` v8；若因provider输出上限、provider失败、标题无效、标题重名或调用预算而使用Host兜底，安全原因也会持久显示。失败或未提交turn不会留下名称。`/session rename <名称>`可手动命名，`/session rename --auto`可恢复首轮自动标题；`/session archive`只添加可逆归档标记，归档Session仍可按UUID或`latest`恢复和继续使用。名称和归档状态会即时刷新，UUID仍是精确resume标识。每次真实provider调用前会显示方块context条，调用后显示厂商实际返回的input/output Token；工具continuation分别计量，turn结束后汇总当前turn与profile。Live工具行默认保持脱敏compact；`/tool-details full`会在当前进程内展开有界结构化command argv、cwd、timeout及direct/shell解释提示，并警告argv可能包含敏感值，文件/edit/patch/search内容仍不显示。Command完成行会显示可信的exit/status、duration和stdout/stderr byte统计；full模式再展开signal、各路truncation与cleanup completeness，但任何模式都不显示stdout/stderr原文。`/changes`系列直接运行固定的只读Git观察，不调用provider、不消耗模型tool budget、不写Session或Action Audit；untracked只显示路径，不显示内容。`/context`和`/compact preview`会标明normal、接近80%、auto-compact、接近满载或unknown；`/usage`还显示当前runtime最近一次compaction generation。`/usage session`与`/usage turns`从严格replay的Session终局记录读取跨重启用量；旧记录显示legacy unavailable，缺失usage metadata明确计为unknown而不按0处理。Provider用尽输出上限时，终端会显示requested limit与可用的actual usage；不完整回复不会成为final answer或committed turn，已完成的工具副作用不会回滚。`/output`显示effective、configured default和known model maximum；`/output 8192`只调整当前进程，`/output reset`恢复profile或direct route默认值。调整会在当前Effective Context上先筛查known overflow，并重建provider route；profile文件、Session历史和已有usage累计不变。Model切换保留临时预算并重新筛查，新profile切换清除它。非缩减`/compact`失败会显示source与candidate input计量，并保持checkpoint及Effective Context不变，同时持久保存失败调用的usage audit。进程内统计仍在成功`/provider use`或`/model`切换后清零；Session统计持久保留，但不计算费用。Enter提交，Alt+Enter换行；若terminal拦截Alt组合，可先按Esc再按Enter。提交后assistant内容以`•`开头，工具turn另显示Host生成的`Tool summary:`。TTY会渲染assistant Markdown；pipe/redirect保留原始Markdown。`NO_COLOR=1`关闭颜色但保留Markdown布局。完整边界见[已实现Foundation与设计演进](./docs/implemented-foundations.md)。

`/session pin`通过append-only元数据保存收藏状态；`/session list pinned`可和其他筛选组合。`/session switch`只保存当前进程的一份“编号到完整UUID”快照，显示名称、turn数、开闭/归档/收藏状态、model和创建时间；`/session switch <编号>`消费一次后立即清空。任何普通prompt、Session元数据修改、直接resume或失败刷新也会废弃旧快照。真正切换仍执行现有prepare、context screening、stale/CAS验证与durable resume commit，失败时当前Session保持不变。

`/session show <id>`和`/session preview <id> [1-10]`允许在不切换的情况下检查另一个Session。Preview通过strict replay选择最近完整turn，只显示最终user/assistant文本，不混入tool companion text、tool result或Action Audit；输出转义终端控制字符并限制为32 KiB。两个命令都不调用provider、不获取writer lease、不修复tail、不追加record，也不改变current Session、`latest`、runtime、history、Effective Context或picker快照。Standalone脚本可使用`leonervis-code -C <workspace> session preview [latest|id] --limit 3`获得同一投影。

`/session search`只在最终对话文本中执行有界、大小写敏感的字面搜索，并返回完整UUID与turn编号；`/session turns`可继续查看对应位置。`/session export`只向stdout输出Markdown或JSON对话视图，不把内部Action Audit、tool result、usage或compaction summary冒充可分享对话。

`/session fork`从严格source snapshot的完整turn边界创建新UUID，复制完整ToolUse/ToolResult因果但不复制父Session的Action Audit、provider usage、failure、compaction或归档/收藏状态；`session_forked` v1持久记录父UUID、turn边界和源transcript SHA-256。`/session doctor`永远只读；`/session repair`只接受“有效完整前缀+未完成最终JSON fragment”，获取writer lease、先写入digest命名的私有备份，再截断fragment并append+fsync现有`recovery` v1。中部损坏、完整JSON缺换行或正在写入的Session都不会被修复。

`/commits`与`/commit`复用同一固定只读Git runner：只遍历当前`HEAD`可达历史，`git_show`只接受完整40/64位小写十六进制commit ID；subject、message和patch均有界并显式标记截断，终端控制字符会被转义。

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
| `<workspace>/.leonervis-code/tasks/.../*.jsonl` | 独立的Task transcript |
| `${XDG_CACHE_HOME:-~/.cache}/leonervis-code/model-context-capabilities.json` | private context capability discovery cache |

`.leonervis-code/` 可能包含用户输入、模型回答、源码片段和工具结果，应加入目标项目的 `.gitignore`，不要提交、同步或公开。配置和 capability cache 不保存已知 credential value，但系统无法识别用户文本或源码中自行出现的未知 secret。

## 开发与验证

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
git diff --check
uv run leonervis-code eval list
uv run leonervis-code eval run all
uv run leonervis-code eval run all --format json
uv run leonervis-code eval task list
tmp=$(mktemp -d)
uv run leonervis-code eval task prepare inventory-validation "$tmp/task"
uv run leonervis-code eval task score inventory-validation "$tmp/task"
```

`pytest`验证函数、模块和协议边界；`eval run`用scripted fake provider把固定轨迹送入完整Host路径。`eval task prepare/score`则离线创建小型代码任务，并在候选目录外以可见测试和Host私有测试评分实际结果。只有显式写出`--real-provider`并选择profile/model的`eval task run`才会调用真实厂商；它固定在新建隔离任务目录内运行，工具事件写入stderr，稳定Host评分写入stdout。依赖变化后先执行 `uv lock`，再检查锁文件。Leonervis Code 不为目标 workspace 安装 Node、Rust、Java、Docker、数据库等项目环境。

## 详细文档

- [已实现 Foundation 与设计演进](./docs/implemented-foundations.md)：system prompt、工具循环、route policy、多 provider runtime、profile、Session、context capability、compaction、permission/approval与controlled write的集中说明。
- [架构决策记录](./docs/decisions/)：每个学习切片的完整问题、取舍、边界与验证记录。
- [确定性离线 Host Eval 基线](./docs/decisions/0084-deterministic-offline-host-eval-baseline.md)：固定任务、隔离执行、Host事实评分及与pytest/真实模型评测的边界。
- [Actual Coding Task Eval](./docs/decisions/0085-actual-coding-task-eval.md)：实际代码结果、受保护文件、Host私有测试、显式真实provider opt-in与命令沙箱边界。
- [Durable Task Identity and Host Management](./docs/decisions/0086-durable-task-identity-and-host-management.md)：Task/Stage/Turn/Action层次、独立持久身份与Host-only管理边界。
- [Durable Stage Lifecycle and Turn Evidence](./docs/decisions/0087-durable-stage-lifecycle-and-turn-evidence.md)：Stage start/terminal状态机、独占writer、重启中断语义与Session Turn证据。
- [Foreground Task Stage Execution and Recovery](./docs/decisions/0088-foreground-task-stage-execution-and-recovery.md)：普通AgentLoop复用、Task framing、精确崩溃恢复、失败映射与终端接入。
- [Task Planning, Acceptance, Budgets, and Management](./docs/decisions/0089-task-planning-acceptance-budgets-and-management.md)：计划执行、累计Stage间预算、完成提议、人工验收与生命周期管理。
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
- [Bounded Read-only Git Change Observation](./docs/decisions/0063-bounded-read-only-git-change-observation.md)：固定Git status/diff、仓库metadata边界、`/changes`与19工具契约。
- [Bounded Reachable Git History Observation](./docs/decisions/0064-bounded-reachable-git-history-observation.md)：当前HEAD历史、完整可达commit ID、`/commits`、`/commit`与21工具契约。
- [Opt-in Bounded Live Tool Details](./docs/decisions/0065-opt-in-bounded-live-tool-details.md)：process-local compact/full切换、有界结构化command argv、shell解释提示与terminal安全边界。
- [Trusted Command Result Observability](./docs/decisions/0066-trusted-command-result-observability.md)：可信content-free命令结果、compact/full完成展示与raw stdout/stderr非披露边界。
- [Persistent Inline Terminal Frontend](./docs/decisions/0067-persistent-inline-terminal-frontend.md)：常驻inline输入区、单worker、UI审批broker、协作取消与plain路径兼容。
- [Terminal Message Hierarchy and Hanging Indent](./docs/decisions/0068-terminal-message-hierarchy-and-hanging-indent.md)：稳定角色标记、正文列续行、低强度Host信息与高风险强调。
- [Host Workbench Navigation and Failure Guidance](./docs/decisions/0069-host-workbench-navigation-and-guidance.md)：Session/Audit过滤、分类帮助、已知失败下一步与真实持久化阶段。
- [Assistant Turn Execution Trace Grouping](./docs/decisions/0070-assistant-turn-execution-trace-grouping.md)：同轮模型正文与Host执行轨迹归组、权威边界及turn末分隔。
- [Durable Session Naming and Terminal Identity](./docs/decisions/0071-durable-session-naming-and-terminal-identity.md)：无工具模型标题、原子首轮命名、重名兜底、旧transcript兼容、精确resume身份及slash/底栏边界。
- [Session Archive, Search, and Title Fallback Diagnostics](./docs/decisions/0072-session-archive-search-and-title-fallback-diagnostics.md)：可逆归档、组合列表筛选、标题兜底原因、v8兼容与不变的resume语义。
- [Pinned Sessions and Snapshot-based Quick Switching](./docs/decisions/0073-pinned-sessions-and-snapshot-quick-switching.md)：可逆收藏、组合筛选、一次性编号快照及复用原resume事务的失败原子性。
- [Read-only Session Inspection and Bounded Turn Preview](./docs/decisions/0074-read-only-session-inspection-and-bounded-turn-preview.md)：不切换Session的严格元数据检查、最终对话预览及32 KiB终端边界。
- [Bounded Cross-Session Final-text Search](./docs/decisions/0075-bounded-cross-session-final-text-search.md)：跨Session最终对话字面搜索、候选/读取/匹配边界与完整性标记。
- [Bounded Session Turn-range Inspection](./docs/decisions/0076-bounded-session-turn-range-inspection.md)：指定1-based turn范围、严格回放与32 KiB只读呈现。
- [Bounded Conversation-only Session Export](./docs/decisions/0077-bounded-conversation-export.md)：Markdown/JSON stdout对话导出及与完整审计的边界。
- [Provenance-linked Session Forking](./docs/decisions/0078-provenance-linked-session-forking.md)：完整turn因果复制、`session_forked` v1来源与父Session不变性。
- [Explicit Session Diagnosis and Tail Repair](./docs/decisions/0079-explicit-session-diagnosis-and-tail-repair.md)：只读doctor、私有备份及仅未完成最终record的显式修复。
- [Fail-closed Linux Command Sandbox](./docs/decisions/0080-fail-closed-linux-command-sandbox.md)：bubblewrap只读Host视图、workspace读写挂载、seccomp断网、敏感路径遮蔽及无降级执行。
- [Host Workbench Diagnostics and Prompt History Search](./docs/decisions/0081-host-workbench-diagnostics-and-prompt-history-search.md)：综合状态、沙箱probe、工具目录、最近审计、命令失败指引与Ctrl-R历史搜索。
- [Host Policy and Tool Discoverability](./docs/decisions/0082-host-policy-and-tool-discoverability.md)：单工具schema/硬边界查看、PermissionGate只读预览、上下文补全与非执行式拼写建议。
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

当前model-visible surface固定为`read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file, delete_directory, list_directory, copy_file, read_file_lines, stat_path, list_tree, grep_regex, patch_file, git_status, git_diff, git_log, git_show`。Provider单次回复可包含最多8个有序工具调用；每个user turn最多接纳32个工具请求和24次provider invocation，最后一次只允许文字。Host在整批解析和预算验证后逐个执行；一个动作非成功会让同批后续动作明确skipped，无法装入剩余预算的整批零执行。所有模型工具仍分别经过permission、approval、executor和Action Audit。

Foundation 5A只读取workspace根目录唯一规范名称`AGENTS.md`：missing表示无项目指令；现有文件必须是non-symlink、strict UTF-8普通文件，不含NUL且最多32 KiB。Host在每个user turn准备时读取并冻结一次，工具continuation始终复用该快照，下一turn才重载；不搜索parent或subdirectory，也不自动加载`CLAUDE.md`或`LEONERVIS.md`。项目指令作为独立provider block参与token计量和Effective Context identity，但不写Session transcript；它从属于canonical Host策略与当前直接user request，不能放宽permission、approval、workspace、symlink、budget、audit、sandbox或durability边界。`/instructions`只显示元数据且不调用provider或修改Session。

Provider batch、结构化tool ledger、streaming/Markdown终端、Session管理与usage audit、Git只读观察、fail-closed命令沙箱、Foundation 5A、两类Eval及前台durable Task现已完成。当前版本为canonical system prompt v24、provider adapter contract v26、ToolArguments v1、ActionIdentity v1、`turn_committed` schema v8、`turn_failed` schema v2、Action Audit schema v1、`context_compacted`新记录v4、Task Stage新记录v2，以及current `ctx-v5`/`ctx-v6`representation；旧Session、Task Stage v1与历史context identity/checkpoint继续兼容，缺失项目指令时的current empty full-context identity为`ctx-v5-bd663ddc5d94403891caac9f91d76a319200967331a18163859e203cd6bbb116`。后台Task、调度、SubAgent、team、worktree编排、并行Stage/Action、自动通用retry、network tool、resource quota、Host sandbox bypass、真实模型Eval排行榜与远程服务仍不可用。Task执行与恢复见[ADR 0088](./docs/decisions/0088-foreground-task-stage-execution-and-recovery.md)，计划和验收见[ADR 0089](./docs/decisions/0089-task-planning-acceptance-budgets-and-management.md)。
