# 0055：Bounded Multi-tool Response Batches

- 状态：Accepted
- 日期：2026-07-28
- 范围：provider多工具回复、Host顺序执行、三层预算、Session/context兼容与failure atomicity

## 背景

真实DeepSeek-compatible REPL场景要求一次创建多个目录和文件。Provider在一次stream response中返回多个tool calls；旧OpenAI-compatible adapter把positive tool-call index判为unsupported multiple calls，因此整份response在任何新动作执行前fail closed。这个旧边界安全，但模型无法用协议原生batch表达一组有序动作，六次总预算也使普通多文件任务很容易被迫拆成多个user turns。

Leonervis需要接受provider原生多调用回复，但不能把“同一回复包含多个调用”误解为并行执行许可，也不能绕过既有PermissionGate、逐次approval、Action Audit、workspace hard bounds、causal pairing或atomic turn commit。

## 决策

Provider-neutral contract新增immutable `AssistantToolBatch`：保存non-empty ordered `ToolUse` tuple和属于整份assistant response的optional companion text。Batch内ID必须唯一，child `ToolUse`不重复保存assistant text。单调用继续使用既有`ToolUse`形状，避免无意义改写旧历史。

OpenAI-compatible non-stream parser按native `message.tool_calls[]`顺序完整解析。Stream parser以tool-call `index`为key分别累积ID、function name与JSON argument fragments，允许不同index交错及一个delta包含多个entries；只在stream正常结束后检查index连续为`0..N-1`、ID稳定且唯一、name/arguments完整、JSON与closed tool schema有效。Anthropic non-stream parser按content block顺序收集多个`tool_use`；stream parser按连续content-block index完成每个tool block，再执行相同ID、数量、input与tool schema检查。两类adapter都必须先完整解析整份response，任何一个call malformed都会拒绝整批，AgentLoop看不到partial batch。

三层Host预算固定为：

```text
每个assistant response最多8个tool calls
每个user turn最多32个admitted tool requests
每个user turn最多24次provider invocations，最后一次为text-only
```

AgentLoop先验证batch size、全历史tool-use ID唯一性及剩余总预算，再执行第一个动作。可接纳batch按provider顺序逐个进入PermissionGate、ask/auto approval、Action Audit与executor，永不并行。多调用batch中一个动作不是`succeeded`时，后续调用不执行，并各自得到明确error `ToolResult`。若整批无法装入剩余32次预算，整批零执行，每个call都获得budget error result，下一次provider request设置`allow_tools=false`。第24次provider invocation同样设置`allow_tools=false`；provider若仍返回工具，turn失败且不提交。

OpenAI-compatible text-only count/create projection同时省略`tools`与`parallel_tool_calls`；Anthropic同时省略`tools`与`tool_choice`。普通tool-enabled projection仍保留`parallel_tool_calls=false`或`disable_parallel_tool_use=true`作为保守provider hint，但Host会正确接收provider仍然返回的有界batch，并始终顺序执行。

Provider history保持一份assistant batch对应全部有序results：OpenAI-compatible投影一个assistant `tool_calls[]`后跟多个ordered `role=tool` messages；Anthropic投影一个assistant text/tool_use block sequence后跟一个user message中的ordered `tool_result` blocks。Count与create复用同一projection。

`turn_committed` current schema升级为v4并新增`assistant_tool_batch` item。v1/v2/v3继续严格读取，新writer不重写旧prefix；v4也继续保存单个旧形状`ToolUse`。Effective Context full/compacted representation分别升级为`ctx-v3`与`ctx-v4`，batch、companion text、call order和全部results进入identity；旧`ctx-v1`/`ctx-v2` checkpoint仍可replay，恢复后的current snapshot使用新representation。Compaction把batch与对应results作为complete turn中的原子因果组序列化，不拆分或重新执行。

Canonical model system prompt升级为v18，fingerprint为`v18-6ddfaa8302427bbe25c1ee28cee6b1e5975949da111a96876baa8e834cd86f8c`。Provider adapter contract升级为v20。Empty full-context identity为`ctx-v3-9007cd576ff595afb6a103a199437d28580836f2a3a5b551819f0f8574d4cf80`。Tool schema/order、ToolArguments v1、ActionIdentity v1、Action Audit v1、ordinary Session records v1和`context_compacted` record schema v2/v3均不升级。

## Failure atomicity

- Malformed、oversized、duplicate-ID或incomplete provider batch在任何该batch动作前整体拒绝。
- 可接纳batch的执行是顺序且逐动作durable，不是跨动作transaction；前面已成功的副作用和Action Audit不会因后续失败而回滚。
- Skipped与over-budget calls形成匹配的error `ToolResult`，保持provider continuation causality，但不进入PermissionGate、executor或Action Audit。
- 只有最终assistant text和`turn_committed` durable commit成功后，candidate turn才进入Session history；continuation或commit失败不提交candidate history。
- `partial`、timeout、cancel与outcome-unknown继续遵守各工具既有“不得自动重试”边界。

## 验证要求

- OpenAI-compatible non-stream/stream与Anthropic non-stream/stream都保留两个以上calls的顺序、companion text及完整arguments；
- OpenAI interleaved index fragments正确组装，non-contiguous index、changed/duplicate ID、第9个call及malformed JSON整批fail closed；
- Anthropic duplicate ID、第9个tool block、invalid input与incomplete block整批fail closed；
- AgentLoop对可接纳batch严格顺序dispatch，前项非成功后skip余项，over-budget batch零dispatch；
- 第24次provider invocation与budget-final continuation均为text-only，违反者不提交turn；
- OpenAI-compatible与Anthropic count/create的tool-enabled、text-only和batch-history projection保持一致；
- Session v4 batch round-trip、v1/v2/v3 replay与legacy-prefix preservation通过；
- Effective Context identity区分batch order/text，compaction不拆分batch因果组；
- Permission、approval、Action Audit、workspace/symlink、durability、resume及commit failure atomicity保持原有证明。

## 明确不做

- 并行工具执行、跨动作transaction、成功副作用回滚或自动retry；
- recursive mkdir、batch write或绕过现有17个closed tool schema的通用文件计划；
- 动态预算、按工具收费的预算权重、后台任务、subagent或跨user-turn自动续跑；
- provider stream失败后的non-stream fallback、隐藏重试或额外API调用。

## 验证证据

2026-07-28完成offline release gate：`1174 passed`；Ruff lint、Ruff format、`uv lock --check --offline`与`git diff --check`通过。三个public fake CLI入口均输出`Fake response: Hello`，resume smoke保持同一Session并从1 turn增长到2 turns，blank prompt保持exit 2。Focused tests覆盖OpenAI-compatible与Anthropic的non-stream/stream batch、interleaved index、duplicate ID、第9个call、text-only projection、AgentLoop顺序/skip/zero-dispatch/第24次调用、Session v1/v2/v3 replay与v4 append，以及Effective Context/compaction原子因果。全程未使用credential、网络或API费用。
