# 0040：Shared Six-call Tool Budget

- 状态：Accepted
- 日期：2026-07-26
- 范围：AgentLoop每user turn的model-visible工具预算

## 背景

最初只有少量只读工具时，共享三次顺序预算足以验证bounded model-tool loop。当前surface已经扩展到17个工具，一次普通coding workflow常需要搜索、读取、修改、验证和复查。三次预算会在修改后运行验证之前耗尽，迫使用户发送只用于重置额度的新消息，同时并没有提升permission、workspace或executor本身的安全性。

预算仍必须有固定Host上限。System prompt只能帮助模型规划，不能代替AgentLoop的不可绕过检查；approval mode也不能决定或扩大预算。

## 决策

`MAX_TOOL_EXECUTIONS_PER_TURN`固定为6。每个新user turn从零开始计数，17个工具共享同一个顺序预算；不存在按工具、action class或permission mode分开的额度。一次请求在进入normal dispatch前消耗一个名额，所以成功、工具错误、permission denial、approval rejection/cancel和executor failure都不会退还额度。

前六次请求照常经过schema validation、PermissionGate、optional approval、Action Audit与executor。第七次请求不进入这些边界，也不产生Action Audit；Host只返回与该`tool_use_id`配对的`tool call limit reached for this conversation turn`错误结果。模型随后只有一次收尾响应机会：final assistant text可以连同完整因果链原子提交；若再次请求工具，AgentLoop抛`ToolLoopLimitError`且candidate turn不提交。下一个真实user turn重新获得六次预算。

Budget increase不改变工具的workspace、symlink、大小、timeout、stale-state、permission、approval、causality、durability或failure-atomicity边界。`--approval auto`只减少人工交互，不增加额度；approval等待、失败或partial result也不会重置计数。Provider仍每次只能返回final text或exactly one tool call，parallel calls继续关闭。

Canonical system prompt升级到v15并明确六次共享额度、第七次不执行以及收到limit result后停止请求工具。工具schema、顺序和Anthropic/OpenAI-compatible projection逻辑不变，因此provider adapter contract保持v15。Prompt snapshot变化使empty full-context identity更新为`ctx-v1-ea0e03265910b48b3cd97e3ace999507379a5e5cf168c6898390870266df051f`，但ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、`context_compacted` v2/v3 replay及`ctx-v1`/`ctx-v2`representation均不升级。旧Session不重写；resume后的新turn使用当前binary的prompt和预算。

## 验证要求

- 混合工具共享同一六次额度；
- 前六次进入dispatch，第七次不进入permission、approval、executor或Action Audit；
- 第七次limit result与原`tool_use_id`因果配对并可随final text提交；
- 第八次仍请求工具时确定性停止且full/effective history均不提交candidate turn；
- prompt version/fingerprint、provider parity和Effective Context identity同步验证；
- 完整pytest、Ruff、format、lock与diff release gate通过。

## 明确不做

- CLI/profile/model可配置预算或无上限模式；
- 按read/write/command分别计数、失败退款或approval后重置；
- parallel tool calls、自动开启新turn或Host自行继续任务；
- 修改任何单个工具的hard bounds或permission classification。

## 验证证据

2026-07-26在locked环境中完成确定性验证：1000项pytest通过，Ruff check与format check通过，`uv lock --check --offline`与`git diff --check`通过。测试未使用credential、网络、真实provider endpoint或API费用。
