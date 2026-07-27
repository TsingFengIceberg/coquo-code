# 0042：Provider-neutral Assistant Tool Text Representation

- 状态：Accepted
- 日期：2026-07-27
- 范围：provider-neutral conversation contract对“assistant text + exactly one tool call”的内部表达

## 背景

部分provider能够在同一个assistant response中同时返回文字和一个tool call。Leonervis此前的`ProviderResponse`只能表达纯`AssistantText`或纯`ToolUse`，两个真实adapter也会拒绝mixed response。直接修改provider parser并执行工具会同时改变history projection、Session持久化、AgentLoop展示与失败语义，范围过大；因此先独立确定一个有界、不可变且不会静默丢失信息的内部表示。

## 决策

继续使用`ToolUse`作为一个provider-neutral assistant tool request，并新增`assistant_text: str | None = None`。`None`保持既有纯工具调用；非`None`表示与同一`tool_use_id`、tool name和immutable `ToolArguments`原子绑定的companion text。它不是独立`AssistantText`，不会把一个工具响应误写成已经完成turn的final answer，也不引入第二个tool call或新的conversation item种类。

Companion text必须是非空、可编码为UTF-8的字符串，最多32 KiB characters且最多32 KiB UTF-8 bytes。文本不做trim或normalization；因此非空的whitespace-only内容仍按provider原文保留。Frozen dataclass保证绑定后不可变，compact source和Effective Context identity都包含该字段，使mixed与pure tool request具有不同身份；in-memory history validation和compaction始终把tool-use/result作为不可拆分的因果对处理。

本slice只建立表示，不开放执行。Anthropic与OpenAI-compatible response parser继续拒绝mixed native response，history serializer也明确拒绝带companion text的`ToolUse`。AgentLoop在event、dispatch、permission、approval、Action Audit或filesystem/process side effect之前fail closed；当前`turn_committed` schema v2 encoder也明确拒绝，不能静默丢字段。后续slice必须一起升级native parse/projection、Session schema和runtime presentation/commit后，才能接受真实mixed response。

既有纯工具调用的canonical payload与Effective Context ID不变；新字段只在非`None`时进入identity。因此`ctx-v1`/`ctx-v2`representation version暂不升级。由于provider仍看不到、不能产生或replay该能力，canonical system prompt保持v15，provider adapter contract保持v15，tool schema/order、ToolArguments v1、ActionIdentity v1、Session/Action Audit schema及`context_compacted` v2/v3 replay均不变。

## 验证要求

- `ToolUse`准确区分`None`与有界non-empty companion text，并拒绝empty、non-string、invalid UTF-8及character/byte overflow；
- whitespace-only text保持原样且frozen instance不能被普通赋值修改；
- catalog factory、Effective Context identity与compact source保留该字段；
- complete-history validation继续强制同一`tool_use_id`的紧邻ToolResult与完整turn边界；
- 两个provider history serializer、Session encoder与AgentLoop均明确fail closed而不丢字段或产生side effect；
- pure tool-call goldens、system prompt、provider projection、Session schema与既有context identity保持不变。

## 明确不做

- 接受或执行Anthropic/OpenAI-compatible mixed native response；
- 在终端展示companion text或把它当作final answer；
- 持久化新的mixed turn、升级adapter contract或Session record schema；
- 支持multiple/parallel tool calls、交错保留Anthropic block位置或streaming增量拼接；
- 改变permission、approval、Action Audit、tool budget或工具hard bounds。

## 验证证据

2026-07-27在locked offline环境中完成确定性验证：1044项pytest通过，Ruff check与format check、`uv lock --check --offline`及`git diff --check`通过；三个fake CLI入口均输出`Fake response: Hello`，resume从1 turn增长到2 turns，blank prompt以exit 2且empty stdout拒绝。未使用credential、网络、真实provider endpoint或API费用。
