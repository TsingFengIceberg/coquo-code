<div align="center">

<img src="./docs/assets/coquo-mark.png" alt="Coquo COQ mark" width="240">

# Coquo

[English](./README_en.md) | 中文

[![Python](https://img.shields.io/badge/Python-3.12%E2%80%933.13-3776AB?logo=python&logoColor=white)](./pyproject.toml)
[![uv](https://img.shields.io/badge/uv-managed-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![pytest](https://img.shields.io/badge/pytest-8%2B-0A9EDC?logo=pytest&logoColor=white)](./pyproject.toml)
[![Ruff](https://img.shields.io/badge/Ruff-0.9%2B-D7FF64?logo=ruff&logoColor=black)](./pyproject.toml)

</div>

Coquo 是一个面向本地单用户使用、以学习为先的 Coding Agent CLI 原型。模型负责决策，Host 在明确的 workspace 边界内执行受控工具，并把结构化结果写回模型。

名称取自拉丁文 *coquō*（“我烹饪”），表达将需求、上下文、工具与模型决策组织成经过验证的软件变更。

> **当前状态：** 已支持命名Provider Profile、真实与离线runtime、可恢复Session、前台多Stage Task、两层Eval及35个普通受限工具，覆盖本地编码、Git观察、网页搜索与抓取、结构化读取、受控文件传输、受审计MCP工具执行和声明式Skill加载。精确能力与安全边界见[已实现Foundation与设计演进](./docs/implemented-foundations.md)。

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

要求 Python 3.12 或 3.13、最新稳定版 [uv](https://docs.astral.sh/uv/) 和 Git。项目使用 `uv.lock` 管理可复现环境。模型使用`run_command`还要求Linux、`/usr/bin/bwrap`与`libseccomp.so.2`；独立网页搜索要求进程环境中存在`BRAVE_SEARCH_API_KEY`或`TAVILY_API_KEY`。缺少这些可选条件时其他功能仍可使用，对应工具会fail closed。

```bash
cd coquo
uv sync
uv run coquo
```

裸命令会在真实终端中启动 REPL。未选择真实 provider 时使用确定性的 fake provider，不访问网络：

```text
›

  fake · ~/Projects/coquo
```

正式命令为 `coquo`，`coquo` 是简写；也可使用模块入口：

```bash
uv run coquo --version
uv run python -m coquo --help
```

## 主要命令

完整参数始终以命令自身帮助为准：

```bash
uv run coquo --help
uv run coquo provider --help
uv run coquo session --help
uv run coquo task --help
```

### 执行任务与启动 REPL

| 用途 | 命令 |
| --- | --- |
| 启动新 Session 的 REPL | `uv run coquo` |
| 恢复当前 workspace 的最新 Session | `uv run coquo --resume latest` |
| 执行一次 prompt | `uv run coquo prompt "解释这个 workspace"` |
| 在指定 workspace 执行 | `uv run coquo -C ../project prompt "解释项目结构"` |
| 使用命名 profile | `uv run coquo --profile work prompt "解释 README"` |
| 临时覆盖本进程输出预算 | `uv run coquo --profile work --max-output-tokens 8192 prompt "生成详细报告"` |
| 临时覆盖 profile 的 model | `uv run coquo --profile work --model model-v2 prompt "继续"` |
| 使用直接 model route | `uv run coquo --model anthropic/claude-opus-4-8 prompt "解释 README"` |
| 在 REPL 逐次审批 workspace 写入 | `uv run coquo --permission-mode workspace-write --approval ask` |
| 一次性允许 workspace 自动写入 | `uv run coquo --permission-mode workspace-write --approval auto prompt "创建 note.txt"` |
| 在 REPL 逐次审批本地命令 | `uv run coquo --permission-mode danger-full-access --approval ask` |
| 一次性自动运行获准命令 | `uv run coquo --permission-mode danger-full-access --approval auto prompt "运行项目测试"` |
| 启动可显式选择Tavily的REPL | `TAVILY_API_KEY=... uv run coquo --permission-mode danger-full-access --approval ask`，进入后执行`/search use tavily` |
| 使用Profile声明的Provider原生搜索 | `uv run coquo --profile search-provider prompt "搜索Python 3.14官方发布说明并列出来源"` |
| 查看版本 | `uv run coquo --version` |

常用权限模式：

```bash
uv run coquo                                      # read-only REPL
uv run coquo --permission-mode workspace-write --approval ask
uv run coquo --permission-mode danger-full-access --approval ask
uv run coquo --permission-mode workspace-write --approval auto prompt "修改并验证项目"
```

### 配置 Provider

内置 provider 使用 catalog 中的 protocol、默认 endpoint 和 credential 环境变量名：

```bash
export ANTHROPIC_API_KEY='...'
uv run coquo provider add work \
  --provider anthropic \
  --model claude-opus-4-8
```

自定义 OpenAI-compatible endpoint 必须显式给出 protocol 和 base URL。Profile 只保存 credential 的环境变量名，不保存 key value：

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

官方DeepSeek V4 Flash会自动选择Responses并默认启用Provider原生搜索；其他DeepSeek model继续使用Chat Completions：

```bash
uv run coquo provider add deepseek-flash \
  --provider deepseek \
  --model deepseek-v4-flash \
  --api-key-env DEEPSEEK_API_KEY \
  --max-output-tokens 16384
```

实现Responses的自定义endpoint可显式选择`openai-responses`：

```bash
uv run coquo provider add responses-gateway \
  --provider custom \
  --model vendor/model \
  --protocol openai-responses \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY
```

Provider preset、消息protocol和原生搜索adapter彼此独立。Built-in Profile默认`auto`，只启用catalog明确声明的能力；custom Profile默认`none`。可为兼容endpoint显式选择已实现adapter：

```bash
uv run coquo provider add search-provider \
  --provider custom \
  --model vendor/search-model \
  --protocol openai-compatible \
  --base-url https://gateway.example/v1 \
  --api-key-env VENDOR_API_KEY \
  --native-search-adapter openai-chat-web-search-options-v1
```

未来厂商扩展可用`--native-search-manifest path/to/search.json`导入有界声明式配置：

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

Manifest只允许受限`extra_body`、非function server tool和预置citation格式，不允许endpoint、header、credential、可执行代码或自定义parser；CLI在创建/替换Profile时一次性验证并保存规范数据，不保存文件路径。旧Profile不必迁移，可删除后按新schema重建。`provider show`和`route`会显示最终能力、adapter、来源及manifest digest。

常用 profile 管理命令：

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

选择优先级为：显式 `--profile` → 显式 direct `--model` → workspace active → user active → fake/offline。`provider use` 会在候选 route、credential 和 client 准备成功后才原子切换；失败时保留旧配置与旧 client。

### 检查 Route 与 Context Window

`route` 是离线诊断命令：不构造 provider client，不读取 key value，也不发起网络请求。

```bash
uv run coquo --profile vendor route
uv run coquo --model openai/gpt-5 route
```

命名 profile 可为 exact endpoint/model 配置上下文窗口：

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

使用`route`查看离线解析结果，在REPL中使用`/status`和`/context`查看当前runtime与context状态。Capability解析、request preflight、自动compact和切换前screening的完整规则见[已实现Foundation与设计演进](./docs/implemented-foundations.md)。

### 管理 Session

```bash
uv run coquo prompt "第一轮"
uv run coquo session list
uv run coquo session show latest
uv run coquo session actions latest
uv run coquo session tools latest
uv run coquo session tools latest --limit 5 --details
uv run coquo --resume latest prompt "继续上一轮"
uv run coquo --resume <session-uuid>
```

Session绑定workspace，并以append-only JSONL保存成功turn。新turn还保存Host逐请求工具账本，记录实际成功、错误、跳过和预算拒绝，不依赖模型自报。使用上面的`session`与`--resume`命令即可检查、审计和恢复；完整replay、screening与durability语义见[已实现Foundation与设计演进](./docs/implemented-foundations.md)。

### 管理 Task

```bash
uv run coquo task create "实现可恢复的多阶段任务" \
  --name "Task runtime" \
  --accept "人工确认变更符合目标" \
  --criterion '{"kind":"path-exists","description":"测试文件存在","path":"tests/test_app.py","path_type":"file"}' \
  --completion-policy auto-verified \
  --max-stages 12 --max-provider-invocations 288 --max-tool-requests 384
uv run coquo task list --status ready --archive active --name runtime
uv run coquo task show <task-uuid>
uv run coquo task timeline <task-uuid>
```

Task用于管理可恢复的前台多阶段工作，既可由自然语言交互发起，也可通过`task`与`/task`命令检查和控制。完整状态机、验收与恢复边界见[已实现Foundation与设计演进](./docs/implemented-foundations.md)及Task相关ADR。

### REPL 命令

| 命令 | 作用 |
| --- | --- |
| `/help [session\|task\|tools\|git\|context\|provider\|search\|mcp\|skills\|hooks\|policy\|input]` | 按类别查看Host控制命令；`task`显示持久任务入口，`hooks`显示声明式策略检查入口 |
| `/history <count>` | 显示当前 Session 最近的完整回合 |
| `/actions last`、`/actions [count] [status=<状态>] [tool=<名称>]` | 快速查看最近一次动作，或按状态和工具名筛选当前Session的脱敏Action Audit |
| `/tools catalog [tool-name]` | 显示39个规范工具的权限与Prompt/Stage可用性，或查看单个工具的参数schema和主要硬边界 |
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
| `/search status`、`/search sources` | 查看Provider/Brave/Tavily可用性、有序激活来源及当前primary，不显示key |
| `/search use <source> [source...]` | 设置primary；`provider brave|tavily`可增加一个显式模型介导fallback，不做fan-out或结果融合 |
| `/search mode <auto\|required>` | 设置Provider原生搜索由模型自行决定或每次强制使用 |
| `/search domains <domain> [domain...]`、`/search domains reset` | 设置或清除Provider adapter支持的域名限制 |
| `/search context <low\|medium\|high\|reset>` | 设置或恢复Provider adapter支持的搜索context大小 |
| `/search reset` | 清除REPL覆盖；Provider原生搜索可用时恢复Provider，否则关闭全部来源 |
| `/hooks [active\|list\|show <id>\|doctor\|evaluations [count]\|runs [count]\|task <task-id> [count]]` | 不调用模型，只读检查当前Hook配置、持久化求值及受审计handler运行记录；使用独立`hooks`命令修改配置 |
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
| `/task proposals [pending\|accepted\|rejected\|all]` | 只读列出当前Session内由模型提交的Task admission proposal |
| `/task proposal show <admission-id>` | 查看一个proposal的目标、原因、验收条件、来源与决议状态 |
| `/task proposal accept <admission-id> [<config-json>]` | 只读预览规范Task配置、prepared criteria与精确confirmation SHA-256；不创建Task |
| `/task proposal accept <admission-id> confirm <sha256> [<config-json>]` | 复核同一candidate后幂等创建带来源Task；不调用provider或执行Stage |
| `/task proposal reject <admission-id> [原因]` | 持久拒绝pending proposal且不创建Task |
| `/task proposal drive <admission-id> [1-16]` | 仅对已接受proposal启动现有有界前台Driver；pending/rejected proposal拒绝 |
| `/task list [1-100] [status=<状态>] [active\|archived] [name=<文本>]` | 只读筛选当前workspace的持久Task |
| `/task show <task-id>`、`/task timeline <task-id>` | 严格回放Task详情或完整Stage时间线，不显示对话与工具正文 |
| `/task continue <task-id> <Stage目标>`、`/task recover <task-id>` | 执行一个普通Turn，或只用已提交Session证据恢复而不重跑provider/工具 |
| `/task plan <task-id>`、`/task plan accept <task-id>`、`/task run <task-id> [1-16]` | 生成提议、人工接受，并在前台按顺序执行有界Stage；run会显示停止原因 |
| `/task reflect <task-id>`、`/task correct <task-id> [目标]`、`/task revise <task-id>` | 对当前失败验收做无工具反思，执行一个受控修正Stage，或提出带前序来源的新计划 |
| `/task drive <task-id> [1-16]`、`/task next <task-id>` | 有界推进自适应前台状态机，或只读预览下一决定；reviewer条件会停止并提示潜在token/API费用 |
| `/task checkpoint <task-id>`、`/task pause <task-id> [原因]`、`/task resume <task-id>` | 追加有界派生checkpoint，或只暂停/恢复自动Driver而不禁止人工Stage命令 |
| `/task verify <task-id> <条件编号> <证据>`、`/task verify host <task-id>` | 为人工条件提交证据，或运行path/digest/command/Action Audit确定性检查 |
| `/task review <task-id>`、`/task complete <task-id>` | 用当前provider发起独立无工具review；全部条件满足后手动完成或按策略自动完成 |
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

REPL的输入、呈现、Session管理、Task控制、上下文观测与Git检查均可通过上表直接使用；实现与持久化细节见[已实现Foundation与设计演进](./docs/implemented-foundations.md)和[架构决策记录](./docs/decisions/)。

用于观察受限工具循环的确定性演示命令：

```bash
uv run coquo demo-read README.md
uv run coquo demo-read ../outside.txt   # 验证 workspace 逃逸拒绝
```

`demo-read` 不是实际模型接口，不写文件、不执行 shell，也不访问网络。

MCP工具默认按`dangerous`处理。可先运行`mcp catalog`取得exact qualified name与schema fingerprint，再用`mcp policy set <qualified-name> --schema-fingerprint <fingerprint> --action workspace-read`为精确的本地stdio版本声明只读策略；远程工具始终保持`dangerous`。`mcp catalog explain <reason-code>`解释隔离原因，`mcp policy stale`检查失效或暂时无法确认的策略，`mcp policy prune --dry-run`只预览带revision的清理命令。`mcp add-http`、`mcp oauth`、`mcp resources`、`mcp prompts`及`mcp doctor`分别用于远程配置、授权、非Tool capability检查和互操作诊断。

声明式Skill可放在workspace的`.coquo/skills`或`.agents/skills`，也可放在XDG user配置目录；使用`skills init|check|search|import|lock`维护本地包，使用`skills fetch|candidate|install`隔离检查和安装公开raw/ZIP包，或在REPL中用`/skills`检查当前激活与候选。模型只会在用户明确要求时提议保存流程，不会自动从经验学习；详细格式、预算与权限边界见implemented-foundations。

Hook可在普通PermissionGate之前增加本地约束，并观察action终局及选定Turn/Task生命周期。可选的固定指纹本地handler会作为独立dangerous Action经过审批、Action Audit及命令沙箱，绝不成为特权回调。使用`hooks add|fingerprint|template|import|list|show|doctor|enable|disable|remove|runs`管理和检查默认disabled的user或project规则；详细边界见implemented-foundations及ADR 0125-0127。

## 配置与本地状态

| 路径 | 内容 |
| --- | --- |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/providers.json` | user provider profiles 与 active selection |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/mcp-servers.json` | user MCP server定义；只保存endpoint、客户端元数据和环境变量名称，不保存credential value |
| `<workspace>/.coquo/mcp-servers.json` | project MCP server定义；新server默认disabled |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/mcp-tool-policies.json` | user MCP tool精确权限策略 |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/mcp-oauth.json` | user remote MCP OAuth pending state与token；私有`0600`文件 |
| `<workspace>/.coquo/mcp-tool-policies.json` | project MCP tool精确权限策略 |
| `${XDG_CONFIG_HOME:-~/.config}/coquo/hooks.json` | user声明式Hook配置；新规则默认disabled |
| `<workspace>/.coquo/hooks.json` | project声明式Hook配置；按Turn冻结后生效 |
| `<workspace>/.coquo/provider.json` | workspace active profile |
| `<workspace>/.coquo/sessions/.../*.jsonl` | Session transcript |
| `<workspace>/.coquo/tasks/.../*.jsonl` | 独立的Task transcript |
| `${XDG_CACHE_HOME:-~/.cache}/coquo/model-context-capabilities.json` | private context capability discovery cache |

`.coquo/` 可能包含用户输入、模型回答、源码片段和工具结果，应加入目标项目的 `.gitignore`，不要提交、同步或公开。配置和 capability cache 不保存已知 credential value，但系统无法识别用户文本或源码中自行出现的未知 secret。

## 开发与验证

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

`pytest`验证函数、模块和协议边界；`eval run`用scripted fake provider把固定轨迹送入完整Host路径。`eval task prepare/score`则离线创建小型代码任务，并在候选目录外以可见测试和Host私有测试评分实际结果。只有显式写出`--real-provider`并选择profile/model的`eval task run`才会调用真实厂商；它固定在新建隔离任务目录内运行，工具事件写入stderr，稳定Host评分写入stdout。依赖变化后先执行 `uv lock`，再检查锁文件。Coquo 不为目标 workspace 安装 Node、Rust、Java、Docker、数据库等项目环境。

## 详细文档

- [已实现 Foundation 与设计演进](./docs/implemented-foundations.md)：system prompt、工具循环、route policy、多 provider runtime、profile、Session、context capability、compaction、permission/approval与controlled write的集中说明。
- [架构决策记录](./docs/decisions/)：每个学习切片的完整问题、取舍、边界与验证记录。
- [确定性离线 Host Eval 基线](./docs/decisions/0084-deterministic-offline-host-eval-baseline.md)：固定任务、隔离执行、Host事实评分及与pytest/真实模型评测的边界。
- [Actual Coding Task Eval](./docs/decisions/0085-actual-coding-task-eval.md)：实际代码结果、受保护文件、Host私有测试、显式真实provider opt-in与命令沙箱边界。
- [Durable Task Identity and Host Management](./docs/decisions/0086-durable-task-identity-and-host-management.md)：Task/Stage/Turn/Action层次、独立持久身份与Host-only管理边界。
- [Durable Stage Lifecycle and Turn Evidence](./docs/decisions/0087-durable-stage-lifecycle-and-turn-evidence.md)：Stage start/terminal状态机、独占writer、重启中断语义与Session Turn证据。
- [Foreground Task Stage Execution and Recovery](./docs/decisions/0088-foreground-task-stage-execution-and-recovery.md)：普通AgentLoop复用、Task framing、精确崩溃恢复、失败映射与终端接入。
- [Task Planning, Acceptance, Budgets, and Management](./docs/decisions/0089-task-planning-acceptance-budgets-and-management.md)：计划执行、累计Stage间预算、完成提议、人工验收与生命周期管理。
- [Structured Task Acceptance and Independent Review](./docs/decisions/0090-structured-task-acceptance-and-independent-review.md)：结构化条件、只读Host verifier、独立无工具review与自动完成策略。
- [Adaptive Foreground Task Orchestration](./docs/decisions/0092-adaptive-foreground-task-orchestration.md)：验收反馈、Reflection/Correction、计划修订、有界Driver、Task checkpoint与人工控制。
- [Resume Runtime Binding at the Durable Commit Point](./docs/decisions/0091-resume-runtime-binding-at-the-durable-commit-point.md)：恢复时把当前runtime binding写入同一个durable commit point，使恢复后的首个工具动作继续通过严格Action Audit绑定校验。
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
- [TTY Host Wrapping and Process-local Command History](./docs/decisions/0093-tty-host-wrapping-and-process-local-command-history.md)：灰色Host输出的视觉续行缩进、slash命令召回及不持久化边界。
- [Task Proposal Control Boundary](./docs/decisions/0094-task-proposal-control-boundary.md)：Operator命令、模型proposal与Host Driver分层，精确工具子集、终止式control call及commit后发布/恢复边界。
- [Model-visible Task Coordination Tools](./docs/decisions/0095-model-visible-task-coordination-tools.md)：四个结构化Task提议工具、Stage最小曝光、commit-coupled账本写入、幂等恢复及旧协议兼容。
- [Model-proposed Task Admission](./docs/decisions/0096-model-proposed-task-admission.md)：普通Prompt的Task创建提议、用户显式决议、Session派生pending状态及跨Task/Session提交边界的幂等接受。
- [Informed Task Admission and Foreground Handoff](./docs/decisions/0097-informed-task-admission-and-foreground-handoff.md)：规范配置预览、SHA-256精确确认、独立前台Driver交接、重启恢复及完整离线Eval。
- [Persistent Activity Indicator and Task Output Alignment](./docs/decisions/0100-persistent-activity-indicator-and-task-output-alignment.md)：输入框上方的瞬时阶段文字、Task/普通回复统一悬挂缩进及不持久化边界。
- [`turn_committed` v5 Inherited Assistant Content Replay](./docs/decisions/0101-turn-committed-v5-inherited-assistant-content-replay.md)：v5继承的companion text与tool batch回放、严格ledger校验及旧transcript不重写边界。
- [Bounded Independent Web Search](./docs/decisions/0102-bounded-independent-web-search.md)：Brave/Tavily Host工具、`network-read`审批、凭据隔离及有界结果规范化。
- [Provider-native Web Search](./docs/decisions/0103-provider-native-web-search.md)：Profile声明的搜索dialect、默认激活、来源选择及Provider/Host责任边界。
- [OpenAI Responses Protocol and Provider-owned History](./docs/decisions/0104-openai-responses-protocol-and-provider-owned-history.md)：Responses路由、语义stream、Provider-owned item持久化与回传。
- [Provider Search Resilience, Controls, and Observability](./docs/decisions/0105-provider-search-resilience-controls-and-observability.md)：失败search action兼容、citation降级、进程内控制、脱敏观测与显式fallback。
- [Bounded Fetch, Structured Read, and Controlled Transfer Tools](./docs/decisions/0106-bounded-fetch-structured-read-and-controlled-transfer-tools.md)：公开页面抓取、结构化只读观察、目录移动、受控下载与`network-write`边界。
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
- [Claw-Code prompt 学习入口](./docs/references/claw-code-prompts/README.md)：只读参考结构与 Coquo 的采用差异。
- [Harness-study](https://github.com/TsingFengIceberg/Harness-study)：相关 Harness 阅读与学习笔记。

## 当前范围与下一步

Coquo目前提供35个普通受限工具，覆盖workspace读写、命令验证、Git观察、网页搜索与抓取、结构化读取、受控下载、渐进式MCP发现及声明式Skill加载，并另有持久Task协调工具。命名Provider Profile、Session恢复、context与compaction、PermissionGate与Action Audit、前台多Stage Task、终端REPL及离线Eval均已接入。

项目仍定位为本地单用户CLI原型；MCP目前支持受限stdio与Streamable HTTP及扩展capability，Skills目前支持有界本地包、渐进发现、上下文生命周期与ToolSet收窄。可执行Skill、市场、浏览器自动化及后台或并行智能体尚未实现。精确工具契约、版本、兼容性与安全边界统一记录在[已实现Foundation与设计演进](./docs/implemented-foundations.md)和[架构决策记录](./docs/decisions/)中。
