# 0043：Provider Mixed-response Inbound Normalization

- 状态：Accepted
- 日期：2026-07-27
- 范围：Anthropic Messages与OpenAI-compatible Chat Completions的mixed assistant/tool response解析

## 背景

ADR 0042已经用`ToolUse.assistant_text`定义“assistant text + exactly one tool call”的provider-neutral内部表示，但两个真实adapter仍在native response入口拒绝该形状。Anthropic以有序content blocks表达文字与`tool_use`，OpenAI-compatible以`message.content`和`message.tool_calls`表达同一语义；如果各自形状直接泄露到AgentLoop，Host会形成provider-specific分支并增加静默丢文字或错误执行多个工具的风险。

## 决策

Anthropic parser在`stop_reason == "tool_use"`且恰好一个合法known `tool_use` block时，允许同时存在text blocks。所有text block按wire顺序精确拼接为一个companion string，并与已验证tool ID、name和immutable arguments绑定成`ToolUse.assistant_text`。没有text block时仍产生既有pure `ToolUse`；text-only response仍要求`end_turn`并产生`AssistantText`。

OpenAI-compatible parser在`finish_reason == "tool_calls"`且恰好一个合法known function call时，允许`message.content`是非空字符串，并将其原样绑定为同一provider-neutral字段。`None`或empty content仍表示pure tool call；text-only response仍要求`finish_reason == "stop"`且不能携带tool calls。

两个parser都复用ADR 0042的non-empty、valid UTF-8、32 KiB character与32 KiB byte上限。Whitespace-only text合法且不trim；empty Anthropic text-block aggregate、oversized/invalid companion text、unsupported block/content shape、错误stop/finish reason、zero/multiple calls、unknown tool和malformed arguments继续映射为stable `response_invalid`，不会猜测调用顺序、选择一个call或丢弃文字。

这是单向入站normalization slice。Provider history serializer仍明确拒绝`assistant_text`，AgentLoop仍在event、dispatch、permission、approval、Action Audit与side effect前抛出`AssistantToolTextNotIntegratedError`，`turn_committed` schema v2仍拒绝持久化。这样真实provider mixed response现在能够被准确解码，但在后续runtime/Session slice完成前仍不会执行。

Provider response contract发生变化，因此adapter contract升级为v16，route fingerprint自然变化。Canonical system prompt仍要求每个tool response只返回tool call并保持v15；tool schemas/order、parallel-call禁用、compact-summary no-tools、ToolArguments v1、ActionIdentity v1、Session/Action Audit schema、`context_compacted` v2/v3 replay、empty Effective Context ID和`ctx-v1`/`ctx-v2`representation均不变。

## 验证要求

- Anthropic mixed blocks与OpenAI-compatible content/tool_calls统一产生相同形状的`ToolUse.assistant_text`；
- companion text保持exact whitespace/newline，Anthropic多个text blocks按wire顺序拼接；
- pure text与pure tool response保持既有结果；
- wrong stop/finish reason、empty或超限companion text、multiple calls、unknown tool及malformed arguments继续fail closed；
- adapter wrapper沿同一parser路径返回neutral result，不引入provider-specific AgentLoop类型；
- history serialization、AgentLoop与Session encoder继续明确拒绝，且拒绝前无event、audit或side effect；
- adapter contract为v16，system prompt、tool projection、Effective Context golden和Session/context schemas保持不变。

## 明确不做

- 执行mixed response、展示companion text或继续下一次provider invocation；
- 将mixed assistant/tool history投影回任一provider；
- 持久化、resume、compact replay或升级`turn_committed` schema；
- 支持multiple/parallel tool calls、streaming delta或Anthropic block-level位置回放；
- 放宽tool input、permission、approval、workspace、audit、budget或durability边界。

## 验证证据

2026-07-27在locked offline环境中完成确定性验证：1049项pytest通过，Ruff check与format check、`uv lock --check --offline`及`git diff --check`通过；两个adapter wrapper均使用各自SDK response类型证明mixed native response规范化为同一provider-neutral `ToolUse.assistant_text`。三个fake CLI入口均输出`Fake response: Hello`，resume从1 turn增长到2 turns，blank prompt以exit 2且empty stdout拒绝。未使用credential、网络、真实provider endpoint或API费用。
