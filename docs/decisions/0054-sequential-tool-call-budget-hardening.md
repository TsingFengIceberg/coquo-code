# 0054：Sequential Tool-call Budget Hardening

- 状态：Accepted
- 日期：2026-07-28
- 范围：超预算任务的model行为、OpenAI-compatible多tool-call诊断与既有顺序因果边界

## 背景

一次DeepSeek-compatible真实REPL观察要求在empty workspace中创建三个父子目录和八个文件。若使用受控`mkdir`与`write_file`，仅mutation就至少需要11次调用，另有一次workspace inspection，无法装入每个user turn最多六次的共享预算。第一轮`list_directory`成功后，provider在下一次stream response中发出nonzero tool-call index；OpenAI-compatible协议中该index表示同一assistant response里的后续tool call。旧adapter正确地在任何该response内的工具执行前fail closed，但只报告`tool-call index was invalid`，没有解释这是unsupported multiple calls；system prompt虽已要求one call per response和六次预算，也未明确说明无法装入预算时必须分批停止，不能把多个calls打包。

Session Action Audit证明失败前只执行了前一轮只读`list_directory`。多call response没有形成provider-neutral `ToolUse`，因此没有进入permission、approval、mutation executor或`turn_committed`；目标workspace仍为空。该结果符合failure atomicity，但需要更准确的诊断与model guidance。

## 决策

OpenAI-compatible stream parser继续要求唯一choice index 0，并且每个tool-call fragment只能属于index 0。Malformed、missing、negative或non-integer index仍报告invalid index；任何positive index明确报告provider response包含multiple tool calls，而Leonervis只支持one sequential tool call。Parser仍不返回第一个call、不缓存后续calls、不执行partial batch，也不自动重试或降级到non-streaming。

Canonical model system prompt升级为v17。除既有“每个response最多一个tool call并等待Host result”外，新增两条明确规则：即使任务超过remaining budget也绝不能把多个calls塞入一个response；无法在当前turn完成时，只使用剩余顺序额度，随后停止并准确报告已完成与待完成工作，等待later user turn继续。

该变化不修改Host强制六次预算、第七次structured limit result、第八次停止语义、17个tool schema/order、`parallel_tool_calls=false`请求投影、permission/approval、Action Audit、Session schema或AgentLoop因果关系。Accepted provider response shape也没有改变，因此provider adapter contract保持v19。System prompt fingerprint更新为`v17-1c66b2e9cf6b622477408f99106294b2cdab14a9983a7fb6b4d628218307b851`，empty full-context identity更新为`ctx-v1-4bcd666498bd96b3af1aa59a1d6793b31cdcdcff1dc274db80c6f051f1e8b6da`；Effective Context representation仍为`ctx-v1`/`ctx-v2`，旧transcript和checkpoint不重写。

## 验证要求

- `index=0` fragmented single tool call继续按wire order组装并执行既有strict schema validation；
- `index=0`后出现`index=1`时明确分类multiple calls，且不返回或执行任何该response内call；
- malformed、missing、negative和non-integer index继续fail closed；
- system prompt exact text、v17 fingerprint、provider count/create parity与empty Effective Context identity更新；
- 六次budget、seventh limit、permission、Action Audit、Session replay与compaction representation保持不变。

## 明确不做

- parallel/multiple tool execution、batch `ToolUse`representation或pending-call queue；
- 丢弃provider后续calls并只执行第一个；
- malformed stream后的自动retry、non-stream fallback或额外API费用；
- 提高每turn六次预算或加入recursive mkdir/batch write工具。

## 验证证据

2026-07-28完成offline release gate：`1153 passed`；Ruff lint、Ruff format、`uv lock --check --offline`与`git diff --check`通过。Focused parser测试构造先后出现`index=0`与`index=1`的stream，确认adapter明确报告unsupported multiple calls且不返回任何call；system prompt v17 exact text、fingerprint与empty Effective Context identity golden同时通过。未调用真实provider，未使用credential、网络或API费用。
