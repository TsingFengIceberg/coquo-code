# 0058：Runtime Context Meter 与 Provider Token Usage

- 状态：Accepted
- 日期：2026-07-28
- 范围：逐provider invocation context展示、actual usage归一化与进程内累计

## 背景

Leonervis已经在每次真实provider invocation前执行target-specific context preflight，但`TurnRuntimeSnapshot`只用结果做拒绝判断，成功报告随后被丢弃。两个adapter也会收到厂商usage metadata，却只返回conversation response。用户因此看不到工具continuation如何增长context，也无法区分本次调用、当前turn和进入当前profile以来的Token消耗。

Context预检值与provider实际usage不是同一事实：前者是发送前对完整native input的exact count或明确estimate，并包含requested output reserve；后者是生成完成后由厂商报告的actual input/output。二者不能混算，也不能在metadata缺失时把estimate冒充actual usage。

## 决策

`TurnRuntimeSnapshot`在每次真实调用的full preflight通过后发布typed context事件，再调用pinned provider。AgentLoop为initial request和每次tool continuation附加1-based invocation index与24次上限。CLI以10格方块条分别显示input、reserve和remaining；unknown input/window使用无方块的明确fallback。REPL toolbar只在同步调用结束、重新进入PromptEditor后显示最近一次短状态；当前slice不引入async reader或永久固定底栏。

新增Host-only `ProviderResponseOutcome.usage`，内容为严格有界的provider-neutral input/output pair或unknown。旧`respond()`与`respond_stream()`仍返回既有`AssistantText | ToolUse | AssistantToolBatch`，新增outcome入口供runtime使用，因此fake和自定义provider可继续工作。Usage不进入conversation items、tool result、system prompt、Session transcript、compaction source或Effective Context identity。

Anthropic non-streaming读取message usage；streaming组合`message_start.usage.input_tokens`与`message_delta.usage.output_tokens`。OpenAI-compatible non-streaming读取`prompt_tokens/completion_tokens`；streaming request增加`stream_options={"include_usage":true}`并解析finish reason之后唯一的usage-only chunk。Missing、out-of-range或malformed metadata使该invocation为unknown，不拒绝已经有效的assistant response，也不回退为本地estimate。

Runtime tracker按当前process-local target记录：latest invocation、latest ordinary user turn、profile ordinary totals和profile compaction totals。Tool continuations属于同一turn；manual/automatic compaction单列；Anthropic count-token和本地estimate等inspection不是generation usage。成功`/provider use`、`/model`或clear切换runtime generation时清零，失败切换不清零；`/resume`与`/session new`保持同一runtime所以不清零。Provider调用抛错后记unknown，因为请求可能已经到达远端且产生费用。

`/usage`显示最近调用、最近turn逐调用、profile ordinary/compaction totals与known/unknown counts。每个成功turn后也显示简短turn/profile摘要。One-shot事件继续写stderr，stdout只保留final answer。Terminal sink仍是best-effort observer，失败不改变provider、tool、Action Audit或turn commit。

## 版本与兼容性

OpenAI-compatible streaming request新增`stream_options.include_usage`，两类adapter新增Host-only usage response transport，因此provider adapter contract从v20升级为v21，route fingerprint随之变化。Canonical system prompt经审阅保持v19及原fingerprint；model-visible tool schema/order、ToolArguments v1、ActionIdentity v1、`turn_committed` v5、Action Audit、`context_compacted` v2/v3及Effective Context `ctx-v3`/`ctx-v4`均不升级。旧Session无需迁移或重写。

## Failure 与诚实性边界

- Preflight rejection发生在provider send前，不产生usage record；
- provider调用开始后失败记unknown，不声称0 Token；
- 缺失usage只影响计量，不改变有效response、tool执行或turn failure atomicity；
- actual totals只相加provider报告值，不混入preflight estimate；
- usage tracker是进程内observer，不是durable billing ledger；
- count-only request可能由provider执行，但它是inspection，不计为generation usage；
- 不读取credential、raw response、prompt、tool arguments或assistant prose来推测Token。

## 明确不做

- Token价格、货币成本、预算告警或账单对账；
- usage写入Session、跨进程累计、export或历史补算；
- cache read/write、reasoning、audio等provider-specific细分Token；
- async PromptEditor、始终固定底栏、TUI或后台刷新；
- 为缺失metadata自动重试、切换provider或额外产生API费用。

## 验证要求

- 每次initial/continuation invocation在send前只显示其同一次preflight报告；
- Anthropic与OpenAI-compatible non-stream/stream usage均归一化，missing/malformed为unknown；
- OpenAI-compatible usage-only final chunk不被误判为额外assistant choice；
- actual/unknown、ordinary/compaction、turn/profile累计及switch reset语义确定；
- `/usage`、live events、toolbar meter有界且不泄露内容；
- fake provider与旧custom provider保持兼容且不产生虚假usage；
- system prompt、tool schemas、Session schemas和Effective Context goldens保持不变；
- 完整offline release gate通过，不使用credential、网络或真实provider费用。
