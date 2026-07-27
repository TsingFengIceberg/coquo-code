# 0045：Provider Mixed-response History Projection

- 状态：Accepted
- 日期：2026-07-27
- 范围：将provider-neutral mixed `ToolUse`准确投影回Anthropic与OpenAI-compatible continuation history

## 背景

ADR 0042定义了`ToolUse.assistant_text`，ADR 0043完成native mixed response入站normalization，ADR 0044完成Session v3持久化。但AgentLoop若在工具执行后继续调用provider，adapter仍必须把同一条assistant response的companion text和tool call一起回传；若丢弃文字、拆成独立final assistant message或改变tool-use/result顺序，provider看到的历史就不再等同于原始response，并可能破坏工具因果。

两个wire protocol使用不同native形状。Anthropic Messages在一个assistant message的ordered content blocks中表达text和`tool_use`；OpenAI-compatible Chat Completions在一个assistant message的`content`与`tool_calls`字段中表达同一语义。该差异应只存在于adapter projection，不能泄露到AgentLoop、Session或tool层。

## 决策

Anthropic history serializer把mixed `ToolUse`投影为一个assistant message，先写一个exact text block，再写匹配ID、name与input的唯一`tool_use` block；紧随其后的中立`ToolResult`继续投影为user `tool_result` message。Pure tool call不产生text block，保持既有wire shape。

OpenAI-compatible history serializer把mixed `ToolUse`投影为一个assistant message：`content`保存exact companion text，`tool_calls`保存唯一function call；紧随其后的中立`ToolResult`继续投影为tool-role message。Pure tool call继续使用`content: null`。

Serializer在构造native history前复用当前`ToolUse`验证，拒绝empty、invalid UTF-8、NUL、character/byte overflow以及broken tool causality；它不trim、不合并成final answer、不生成新tool ID，也不把companion text复制到ToolResult。Provider continuation因此看到原始assistant mixed item后立即跟随匹配结果。

这项ordinary create/count history projection变化把provider adapter contract升级到v17，并按既有规则改变route fingerprint。Tool catalog、schema与顺序、六次共享预算、ToolArguments v1、`turn_committed` v3、Action Audit、`context_compacted` v2/v3以及`ctx-v1`/`ctx-v2`representation均不变。本slice不改变canonical system prompt v15；下一ADR单独完成AgentLoop与终端接入并升级prompt。

## 验证要求

- 两个serializer都精确保留mixed text、tool ID、name、arguments和紧邻结果；
- pure tool-call wire shape保持兼容；
- 两个adapter-backed AgentLoop continuation都收到各自正确native mixed history；
- malformed companion text、multiple tools、unknown tools与broken causality继续fail closed；
- ordinary count/create使用同一projection，compact summary仍不暴露工具。

## 明确不做

- 支持一次response中的多个或并行tool calls；
- 把wire-specific message/block对象引入provider-neutral history；
- streaming、thinking/reasoning block持久化或任意content-part协议；
- 改写旧transcript、重新执行已恢复的工具或把Session binding当作runtime配置。

## 验证证据

2026-07-27在locked offline环境中完成确定性验证：1062项pytest通过，Ruff check与format check、`uv lock --check --offline`及`git diff --check`通过。两个serializer的exact native shape、pure-call兼容、mixed inbound normalization及adapter-backed continuation均有测试覆盖；未使用credential、网络、真实provider endpoint或API费用。
