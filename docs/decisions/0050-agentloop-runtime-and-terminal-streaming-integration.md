# 0050：AgentLoop、Runtime 与 Terminal Streaming Integration

- 状态：Accepted
- 日期：2026-07-27
- 范围：stream event顺序、runtime preflight/lease、REPL与one-shot presentation、failure atomicity

## 背景

Native adapter能够安全组装stream后，Host仍需决定何时显示文字、何时执行工具，以及如何在provider中断或Session commit失败后避免把ephemeral文字误称为durable answer。REPL需要即时反馈；one-shot同时有严格的stdout-only final answer契约，而response在stream结束前无法确定是final text还是tool companion text。

## 决策

AgentLoop在有event sink时prefer streaming，并把exact provider text转换为typed ephemeral delta event。完整`ToolUse`返回后，先发tool-companion-completed event，再进入既有tool started/permission/approval/execution/finished顺序；完整`AssistantText`只有在`turn_committed`成功append+fsync后才发final-stream-committed event并返回。Provider、runtime、tool continuation或durable commit failure均不安装candidate history；已执行工具的side effect和Action Audit仍不能回滚。

`TurnRuntimeSnapshot.respond_stream()`在消费第一个delta前执行与`respond()`相同的full context preflight，并在同步stream、所有tool continuation和最终commit所在的外层turn lease期间固定provider/route/capability/generation。Known overflow不进入provider stream；runtime switch、另一个turn、compact、resume transition或close不能穿过active lease。

REPL即时写delta，在tool companion或durable final resolution时补至多一个结尾换行；final response不重复打印。中断或预期provider/runtime/authorization/Session错误会终止可见partial line，并明确提示partial text未提交；Ctrl-C generation回到REPL。One-shot暂存delta：tool companion和tool events写stderr，durable final只由`session.prompt()`返回值在commit后打印一次到stdout；中断不泄露partial stdout并返回130。

Terminal sink仍是best-effort observer：sink exception由AgentLoop隔离，不得改变tool、audit或commit。Live delta不写Session；恢复和审计只依赖完整`turn_committed` v3、Action Audit及full transcript。System prompt经审阅保持v16，因为streaming是Host transport/presentation，不改变模型可见工具或行为要求。Adapter contract最终为v19；ToolArguments v1、ActionIdentity v1、Session records、`context_compacted` v2/v3、`ctx-v1`/`ctx-v2`representation及empty context golden均不变。

## 验证要求

- delta、tool-completed、tool lifecycle与final-committed event顺序准确；
- tool arguments完整验证前不执行，durable commit前不确认final；
- commit/provider/sink failure保持candidate history atomicity；
- runtime preflight发生在stream前且lease覆盖stream消费；
- REPL无duplicate final，one-shot保持stdout final/stderr activity；
- Ctrl-C和partial stream不会被报告为已提交turn；
- non-stream provider行为与六次共享tool budget保持兼容。

## 明确不做

- 持久化每个delta、stream replay或中断后续传；
- one-shot实时显示可能成为final answer的文字；
- async UI、TUI、server event bus、token meter或thinking display；
- partial tool arguments、parallel tools、automatic retry/fallback。

## 验证证据

2026-07-27在locked offline环境完成1103项pytest，覆盖两个native adapter、provider-neutral fallback、AgentLoop ordering/commit failure/sink isolation、runtime preflight/lease、terminal buffering/newline/abort、REPL和one-shot中断；Ruff check已通过。未使用credential、网络、真实provider endpoint或API费用。
