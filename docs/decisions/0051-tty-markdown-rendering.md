# 0051：TTY Markdown Rendering

- 状态：Accepted
- 日期：2026-07-27
- 范围：assistant Markdown的TTY展示、stream-safe buffering、raw fallback与terminal control安全

## 背景

模型通常以Markdown组织标题、强调、列表、表格和fenced code，但Leonervis此前把provider text delta原样写入终端，用户会直接看到`#`、`**`和代码围栏。Streaming又不能对每个任意fragment独立解析：尚未闭合的emphasis或code fence可能在后续delta改变语义，而已经写出的ANSI无法可靠回滚。

## 决策

新增独立`TerminalMarkdownRenderer`，使用锁定的Python `rich` Markdown parser和terminal renderer。它只处理presentation副本；provider-neutral response、AgentLoop pending history、Session transcript、resume/compaction和Effective Context继续保存exact原始Markdown。Claw-Code的safe-boundary思想只作学习参考，Leonervis没有导入或复制其Rust renderer。

Streaming renderer累计exact delta，只在fenced code之外的完整blank-line boundary或闭合backtick/tilde fence后渲染stable prefix；response完成时flush剩余suffix。Tool companion在完整response分类后flush，final response的剩余suffix只在durable turn commit后的typed event中flush。Provider failure、Ctrl-C或commit failure丢弃未展示的pending suffix；已经展示的早期complete blocks仍明确只是ephemeral text，不改变既有failure atomicity。

REPL和TTY one-shot启用Markdown rendering。TTY one-shot仍等`session.prompt()`完成durable commit后才渲染final；tool companion/activity继续走stderr。非TTY stdout/stderr、pipe和redirect保持原始Markdown bytes加既有终止换行，维持automation契约。`NO_COLOR`只关闭ANSI style，不关闭标题、列表和code layout等Markdown语义。

Model output属于untrusted terminal data。Renderer在Markdown解析前把ESC、CR、NUL和其他C0/C1 control characters转换为可见escape text，只保留newline与tab；Rich markup、emoji expansion和terminal hyperlinks关闭。输出宽度使用TTY width并限制在40–240 columns，无法查询时固定100 columns。Renderer只生成terminal text/ANSI，不执行HTML、URL、image、shell或file操作。

Terminal output仍是best-effort observer。Event sink write failure不能改变provider response、tool execution、Action Audit或turn commit；one-shot final write发生在durable commit后，输出失败也不回滚Session。该slice没有模型可见行为变化，因此canonical system prompt保持v16，adapter contract保持v19，tool catalog/order、六次预算、ToolArguments v1、ActionIdentity v1、`turn_committed` v3、Action Audit、`context_compacted` v2/v3、empty context golden及`ctx-v1`/`ctx-v2`representation均不变。

## 验证要求

- heading、emphasis、inline/fenced code和list在TTY中不显示原始Markdown marker；
- incomplete paragraph/fence在safe boundary前不输出，closed fence与completion正确flush；
- streaming/non-streaming final及tool companion使用同一renderer且不重复；
- TTY `NO_COLOR`保留Markdown layout但不含ANSI，redirect保留原始Markdown；
- provider control characters不能注入terminal escape sequence；
- abort丢弃pending suffix，durable transcript仍逐字保存原始assistant text；
- renderer/terminal sink不进入model、tool、Session或Effective Context identity。

## 明确不做

- TUI、virtual screen、cursor-based rerender或修改已输出block；
- 图片、HTML、Math、Mermaid、OSC hyperlinks或鼠标交互；
- 把ANSI或rendered layout写入Session、history或provider continuation；
- 修改model prompt以强制Markdown、自动修复malformed Markdown或信任raw HTML；
- 复制Claw-Code renderer或把reference repository变成runtime dependency。

## 验证证据

2026-07-27完成offline release gate：`1117 passed`；Ruff lint、Ruff format、`uv lock --check --offline`、`git diff --check`、三个public fake CLI入口、resume和blank-prompt smoke均通过。
