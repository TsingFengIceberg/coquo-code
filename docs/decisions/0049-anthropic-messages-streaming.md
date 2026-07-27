# 0049：Anthropic Messages Streaming

- 状态：Accepted
- 日期：2026-07-27
- 范围：Anthropic同步Messages stream事件解析与资源回收

## 背景

Anthropic Messages以`message_start`、indexed content-block lifecycle、text/input JSON delta、message stop reason和`message_stop`表达一个response。Text与tool use是ordered blocks而不是OpenAI-compatible字段；Host需要保留这种顺序语义，同时拒绝缺失、重复、错序或未完成的event sequence。

## 决策

普通Agent streaming request在既有Messages body上只把`stream`设为`true`。Parser要求assistant `message_start`先出现，content block index从0连续递增，每个active block必须在message-level completion前stop，并最终看到唯一stop reason和`message_stop`。Text block按block/event wire顺序发出exact delta并汇总；tool block要求stable ID/name，`input_json_delta`完整拼接后才解析和进入known-tool validation。

Text-only stream只接受`end_turn`与非空文字。Tool stream只接受`tool_use`、exactly one tool block，并可把所有text block拼接为同一neutral `ToolUse.assistant_text`。`max_tokens`、refusal、unknown event/block/delta、bad role/index/order、multiple tools、ambiguous initial input加JSON fragments、invalid JSON或missing `message_stop`全部fail closed。

SDK stream在success和failure后best-effort close，cleanup exception不覆盖既有结果。`respond()`、count、discovery和compact summary保持原路径。该native protocol变化把global adapter contract从概念v18升级为current v19；route fingerprint随最终constant变化。

## 验证要求

- text blocks与fragmented input JSON准确组装为neutral response；
- strict role、index、block lifecycle、stop reason和message-stop顺序；
- request准确设置`stream=True`且resource总被best-effort close；
- malformed/incomplete/refused/truncated stream在工具执行前拒绝；
- mixed response继续保留exact companion text和工具因果。

## 明确不做

- beta event、thinking/signature block或provider-specific block持久化；
- block-level resume、自动retry、multiple tool use或partial execution；
- 修改normal history projection、Session schema或model prompt。

## 验证证据

2026-07-27在locked offline event fixtures中覆盖text、mixed fragmented tool、request flag、close、错误role/index/JSON、missing stop和max-token stop；这些测试包含在1103项全量pytest中。未调用真实Anthropic API。
