# 0046：AgentLoop 与 Terminal Assistant Tool Text Integration

- 状态：Accepted
- 日期：2026-07-27
- 范围：mixed assistant/tool response的顺序执行、即时终端展示、durable commit与失败原子性

## 背景

ADR 0042–0045已分别完成内部表示、native入站解析、Session v3持久化和provider历史回传，但普通CLI仍需要一条完整运行路径。Companion text不能被误当作结束turn的final answer，也不能在工具执行失败、provider continuation失败或Session commit失败时变成权限、批准或成功证明；同时用户应在模型请求工具时立即看到这段文字，而不是等整个turn结束。

## 决策

AgentLoop收到带`assistant_text`的单个`ToolUse`后，先发出typed `AssistantToolTextReceived`，再依次发出既有`ToolRequestStarted`与`ToolRequestFinished`或limit事件。随后工具照常经过argument validation、prepared action lease、PermissionGate、optional approval、durable Action Audit和hard executor boundary；exact `ToolUse`与匹配`ToolResult`立即进入下一次provider continuation history。只有后续纯`AssistantText`才结束turn并触发durable `turn_committed` v3。

Companion text与tool call仍是一个原子的conversation item，不创建独立assistant turn。它不会改变权限、approval、Action Audit identity、tool result或execution status。第七次mixed tool request可以先显示文字，但只得到limit result且不执行；若provider随后第八次仍请求工具，其文字仍可显示，AgentLoop确定性停止且candidate turn不提交。六次共享预算和所有工具hard bounds保持不变。

CLI复用`TerminalEventSink`原样渲染companion text。One-shot把它与tool lifecycle events写到stderr，stdout仍只包含最终answer；REPL在自己的output stream中按收到顺序展示。若文本本身已有结尾换行，sink不再追加第二个换行；否则补一个终止换行。该事件是ephemeral presentation，不写transcript、不进入Effective Context，也不替代Session v3保存的mixed `ToolUse`或durable Action Audit。

Terminal sink异常继续被隔离，不能影响工具、审计或turn commit。Provider continuation失败或durable turn commit失败时，candidate history不进入内存或Session；已执行工具的真实副作用和Action Audit不能回滚或伪装为未发生。ProjectSession端到端测试覆盖显示、执行、v3 commit、close/resume与恢复后provider回传。

Canonical system prompt升级到v16，允许一个tool response携带brief companion text，并明确它不是final answer、Tool result、permission、approval或execution proof。Empty full-context golden更新为`ctx-v1-bc29d5392990da88d9a0641d78cfc051d0d9e92b9f3452e90b1259ae16df2b58`；representation仍为`ctx-v1`/`ctx-v2`。Adapter contract保持ADR 0045的v17，tool catalog/schema/order、ToolArguments v1、`turn_committed` v3、Action Audit和`context_compacted` v2/v3不变，旧transcript不重写。

## 验证要求

- Mixed response按`companion text -> tool started -> tool finished`顺序发出事件并继续provider调用；
- continuation与最终commit精确保留mixed `ToolUse -> ToolResult`因果；
- one-shot保持companion/tool events在stderr、final answer在stdout，REPL保持顺序展示；
- single-line、multi-line与已有结尾换行的文字不丢失且不重复添加空行；
- sink failure不改变执行或commit，provider/commit failure不提交candidate turn；
- 第七/第八次mixed请求不能绕过共享预算；
- ProjectSession close/resume恢复exact text并在下一次provider请求中继续使用。

## 明确不做

- token streaming、增量content events或终端重绘；
- multiple/parallel tool calls、独立thinking/reasoning持久化或新的conversation item类型；
- 持久化live line、从terminal output恢复事实或用companion text替代Action Audit；
- 改变permission/approval策略、workspace/symlink/durability边界或工具执行语义。

## 验证证据

2026-07-27在locked offline环境中完成确定性验证：1062项pytest通过，Ruff check与format check、`uv lock --check --offline`及`git diff --check`通过。AgentLoop、TerminalEventSink、one-shot、REPL及ProjectSession close/resume均覆盖mixed response；三个fake CLI入口输出`Fake response: Hello`，resume从1 turn增长到2 turns，blank prompt以exit 2且empty stdout拒绝。未使用credential、网络、真实provider endpoint或API费用。
