# 0056：Structured Tool Outcome Ledger

- 状态：Accepted
- 日期：2026-07-28
- 范围：逐请求Host计账、强制text-only收尾、Session v5与提交后终端汇总

## 背景

真实DeepSeek-compatible压力场景在一个user turn中请求创建33个文件。Host正确执行了32次预算控制：第一个`mkdir`失败、同批7项被跳过、后续24次写入成功、最后8项因整批超出剩余额度而未执行；但模型最终错误声称32次`write_file`全部成功，还把并未返回的第33个文件算入budget error。Live tool lines和Action Audit包含真实事实，但模型与用户都缺少一份Host生成的整轮结构化汇总。

仅依靠system prompt要求模型自己计数不够可靠；从`ToolResult.content`文案反向解析状态也会把不受信任文本当作权威数据。需要由AgentLoop在每个请求完成、跳过或预算拒绝时直接记录typed outcome。

## 决策

核心合同新增immutable `ToolOutcomeEntry`与`ToolTurnLedger`。每个entry保存exact `tool_use_id`、工具名、从1连续递增的request index、typed outcome及可选的bounded safe result code。Outcome包括正常dispatch的`succeeded | error | denied | rejected | cancelled | failed | partial | outcome-unknown`，以及Host合成的`skipped-after-failure | rejected-over-budget`。Ledger拒绝重复ID、index间断、无界字段及不匹配的synthetic result code；`requested`、`admitted`、`dispatched`和各status count只从entries推导，不维护另一份可漂移aggregate state。

AgentLoop在收到完整且有效的tool response后按provider顺序添加entry。超预算整批中的每个request都获得独立`rejected-over-budget` entry；同批前项非成功后的每个未执行request获得`skipped-after-failure` entry。Dispatch exception仍发出ephemeral `outcome-unknown`结束事件并让turn失败，不提交candidate ledger。既有8/32/24预算、顺序执行、PermissionGate、approval、Action Audit、executor和已发生副作用不回滚的边界均不改变。

当剩余调用只能进行text-only finalization时，Host在调用provider前把一行bounded canonical `Host tool ledger:`摘要附到最后一个真实`ToolResult.content`。预算拒绝路径在全部rejected entries已知后附到最后一个budget result；provider-invocation上限路径附到前一批最后一个真实result。摘要用`unused_admission_slots`陈述32项接纳上限内尚未使用的容量，并另以`tool_requests_closed=true`明确本turn已经不能继续请求工具；即使前者非零，模型也只能报告已完成与未完成工作，不得请求或把tool-call syntax打印成普通文字。它不是fake user/system message，也不创建无对应tool-use ID的新conversation item。普通由模型主动结束的turn不额外调用provider只为发送摘要。

成功durable turn commit后，AgentLoop发出一个typed `ToolTurnSummaryCommitted`事件。CLI显示独立的`Tool summary:`行；one-shot继续写stderr，REPL继续使用既有event stream。Commit失败时不发该事件，sink失败仍不影响执行、审计、commit或返回值。终端摘要来自typed ledger而不是模型回答。

## Session与兼容性

新`turn_committed`使用record-local schema v5，在conversation `items`之外保存top-level `tool_ledger.entries`。Replay要求v5 ledger覆盖该turn的每个tool request，identity和顺序完全一致，并要求`succeeded`与非error result、其余outcome与error result相符。Schema v1/v2/v3/v4继续读取为empty legacy ledger；恢复后只append v5，不重写旧prefix。其他普通Session records、Action Audit和`context_compacted` schema v2/v3不升级。

Top-level ledger不进入provider history、compaction summary source或Effective Context identity。只有强制finalization时附加到真实`ToolResult`的model-visible文本自然进入既有conversation identity。因此full/compacted representation保持`ctx-v3`/`ctx-v4`，不因Host-only top-level字段升级。Canonical system prompt升级为v19并要求模型使用Host ledger及服从显式工具关闭状态；fingerprint为`v19-accfbb73aa611061c8a8cb6be5bb54012ce5809fbbe91050439383e3d35318b7`，empty full-context identity为`ctx-v3-29ff59405090ba544b2bacb144d5961daecc7d0d6359123a9262c097d0fa654d`。

Provider native request/response shape、tool schema、tool order、batch history projection及count/create parity均未改变；ledger摘要只是既有`ToolResult.content`。因此provider adapter contract保持v20，ToolArguments v1、ActionIdentity v1与17工具catalog均不升级。

## Failure atomicity

- Provider continuation失败时，candidate items与ledger都不提交；已经执行的动作与Action Audit不回滚。
- Durable `turn_committed`失败时，内存history和terminal summary都不更新。
- Ledger summary只附到已有matching tool result，不拆开tool-use/result因果对，也不改变pinned system prompt snapshot。
- `partial`、timeout、cancel和outcome-unknown继续保留既有不得自动retry语义；ledger只报告观察结果，不提供恢复或补偿。
- Legacy transcript不因v5升级重写；malformed、缺失、顺序错误或与result flag矛盾的v5 ledger fail closed。

## 明确不做

- 新增model-visible ledger tool、fake user/system message或另一次仅用于总结的provider调用；
- 解析ToolResult prose、assistant prose或terminal text来推断trusted status；
- 提升8/32/24预算、并行执行、自动开启新user turn或自动继续剩余工作；
- 回滚成功副作用、跨动作transaction、自动retry或Foundation 5A能力。

## 验证要求

- 逐请求合同验证continuous index、unique ID、synthetic code与derived arithmetic；
- 40请求场景精确得到40 requested、32 admitted、25 dispatched、24 succeeded、1 error、7 skipped和8 over-budget；
- budget与invocation finalization都把权威摘要放入最后一个matching ToolResult并以text-only request发送；
- 31项admitted后拒绝8项整批的场景明确报告1个unused slot与closed工具通道，不把空余容量误报为可继续调用；
- v5 round-trip、malformed ledger拒绝、v1-v4 replay及legacy-prefix preservation通过；
- terminal summary仅在durable commit后出现，one-shot stderr和REPL event stream保持既有分流；
- system prompt fingerprint、Effective Context identity、provider text-only工具省略与完整offline release gate通过。

## 验证证据

2026-07-28完成offline release gate：`1183 passed`；Ruff lint、Ruff format、`uv lock --check --offline`与`git diff --check`通过。三个public fake CLI入口均输出`Fake response: Hello`，resume smoke保持同一Session并从1 turn增长到2 turns，blank prompt保持exit 2且stdout为空。Focused tests以40请求场景证明`40/32/25/24/1/7/8`精确计账，并以39请求场景证明31项admitted、1个unused slot和closed工具通道可以同时准确表达；同时覆盖provider-invocation text-only摘要、commit前后事件边界、Session v5 malformed rejection、v1-v4 replay、legacy-prefix preservation、system prompt fingerprint及current Effective Context identity。全程未使用credential、网络或API费用。
