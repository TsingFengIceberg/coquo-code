# 0048：OpenAI-compatible Chat Completions Streaming

- 状态：Accepted
- 日期：2026-07-27
- 范围：OpenAI-compatible同步chat-completions chunk解析与资源回收

## 背景

OpenAI-compatible endpoint以ordered chat completion chunks分别传递assistant text、function call ID/name/arguments fragments和finish reason。不同兼容服务可能产生malformed index、变化的call ID、多个tool calls、缺失finish reason或无效JSON；若Host边收边执行，会把尚未完成且未验证的provider数据跨过工具边界。

## 决策

普通Agent streaming request在既有native body上只把`stream`设为`true`；system/messages/tools、`parallel_tool_calls: false`、max output与sampling policy保持相同。Parser要求每个chunk恰有choice index 0，每个delta最多一个function tool-call fragment且tool index固定0；call ID一旦出现不得变化，function name和JSON arguments按wire顺序拼接。

Text-only stream必须以`stop`结束且包含非空文字。Tool stream必须以`tool_calls`结束，最终只有一个完整ID/name/arguments，并可携带exact companion text。`length`/`max_tokens`、content filter/refusal、unsupported finish reason、continued-after-finish、multiple calls、bad index/ID/type、missing finish、invalid JSON和unknown/malformed tool arguments全部映射到既有stable provider failure，不执行工具。

SDK stream在success和failure后都best-effort close；cleanup exception不覆盖已经确定的response或原始adapter failure。`respond()`与compact summary继续non-streaming，count projection仍不包含transport-only stream差异。OpenAI-compatible streaming使global adapter contract概念上升级到v18；最终交付还包含Anthropic streaming，因此current binary使用后续v19。

## 验证要求

- fragmented text、name和arguments保持wire顺序并产生统一neutral response；
- request准确设置`stream=True`且成功/失败都close；
- missing finish、bad index、multiple calls、invalid JSON和token-limit finish fail closed；
- mixed text/tool response继续通过现有schema和history projection；
- stream cleanup failure不掩盖成功或原始解析错误。

## 明确不做

- Responses API、SSE parser或厂商私有reasoning delta；
- parallel/multiple tool calls、partial dispatch或自动retry；
- 改变compact summary、tool catalog、permission或Session语义。

## 验证证据

2026-07-27在locked offline fixtures中覆盖text、mixed fragmented tool、request flag、close和malformed chunks；这些测试包含在1103项全量pytest中。未调用真实endpoint。
