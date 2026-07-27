# 0047：Provider-neutral Synchronous Response Streaming

- 状态：Accepted
- 日期：2026-07-27
- 范围：provider-neutral response stream契约、non-streaming兼容与有界增量

## 背景

Leonervis此前只在provider完整返回后才得到`AssistantText`或`ToolUse`。这保证了工具参数完整和turn failure atomicity，但真实模型生成较长文字时，REPL在整个请求结束前没有反馈。两个provider的native stream事件形状不同，不能直接泄露到AgentLoop或terminal；现有fake、自定义provider和测试provider也不能因streaming接入而被迫实现新方法。

## 决策

新增可选的同步`respond_stream(request, event_sink=...)`能力。Provider只通过`ProviderTextDelta`按wire顺序报告non-empty exact text fragment，并在stream完整消费、参数组装和验证结束后返回既有provider-neutral `AssistantText | ToolUse`。统一helper收集所有delta，并要求其拼接结果与最终response的`text`或`assistant_text`完全一致；不一致、无效事件、超过1 MiB text上限或非法UTF-8/NUL均fail closed。

Streaming是可选能力。AgentLoop只有存在event sink时才prefer stream；provider没有`respond_stream`、显式不支持stream或调用方不需要即时事件时继续走既有`respond()`。因此fake/custom provider和现有non-stream测试契约保持兼容，不添加异步runtime、background reader、retry或provider fallback。

Tool call在完整provider-neutral response返回前不能进入dispatch。Native stream最多处理100,000个ordered events/chunks，tool identifier累计最多4 KiB，argument fragments累计最多64 KiB；最终仍必须通过现有known-tool schema、`ToolArguments` 16 KiB canonical JSON和具体tool参数限制。文字delta只是ephemeral presentation evidence，不进入conversation history、Session、Action Audit或Effective Context identity。

该slice只定义内部可选transport seam，不改变provider request projection、model-visible tool surface或system prompt。Adapter contract暂不在本ADR升级；具体wire协议在后续ADR分别升级。

## 验证要求

- exact text delta可组装为最终text或mixed tool companion text；
- mismatch、invalid event、oversized stream和非法text fail closed；
- `prefer_stream=False`及无stream provider保持`respond()`兼容；
- tool executor在完整`ToolUse`返回前不可见任何partial arguments；
- event sink失败不能改变provider response、tool execution或turn commit。

## 明确不做

- async iterator、并行provider请求或后台生成；
- partial tool execution、incremental JSON容错或猜测缺失参数；
- thinking/reasoning block持久化、token telemetry或stream resume；
- 修改Session schema、system prompt、tool schema或Effective Context representation。

## 验证证据

2026-07-27在locked offline环境中完成provider-neutral、AgentLoop与terminal确定性测试，并纳入1103项全量pytest；未使用credential、网络、真实provider endpoint或API费用。
