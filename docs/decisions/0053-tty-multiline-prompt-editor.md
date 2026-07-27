# 0053：TTY Prompt Editor 与交互反馈

- 状态：Accepted
- 日期：2026-07-27
- 范围：REPL输入边界、exact多行提交、Session派生历史、slash交互、terminal状态与assistant反馈

## 背景

REPL此前直接调用Python `input()`。GNU readline只能编辑一条物理行并补全顶层slash command，无法编写结构化多行prompt，也不能把粘贴的代码块作为一个user turn。要求用户转义换行或拆成多个model turn会改变原始文本、丢失缩进，并让terminal输入方式意外决定对话语义。

本slice只把Claw-Code作为“interactive input component与agent runtime分离”的设计参考。Leonervis不导入或复制其实现，也不增加尚未独立实现的`/paste`命令。

## 决策

新增provider-neutral `PromptEditor` protocol，输出closed `submit | cancel | exit`结果。REPL持有一个editor实例并只消费这些结果；editor不拥有provider invocation、slash dispatch、Session状态、permission或assistant response渲染。

当stdin/stdout正是当前进程的真实TTY时，`TerminalPromptEditor`使用prompt-toolkit 3.0.52或更高版本。Enter提交完整buffer，Alt+Enter插入LF；若terminal或窗口管理器拦截Alt组合，Esc后Enter触发同一binding。Host不承诺Ctrl+Enter，因为常见terminal会把它编码为普通Enter或Ctrl-J，无法可靠区分。Bracketed paste把粘贴的多行文本放进一个buffer。提交文本原样返回，不整体trim，也不做换行正规化。

TTY input presentation改为单独一行`›`marker，续行使用两个空格对齐；prompt-toolkit bottom toolbar显示`selected model · workspace`，model缺失时依次回退profile/provider，fake runtime显示`fake`。Home目录缩写为`~`，字段有界且所有non-printable character在进入ANSI parser前替换为`?`。Session与完整runtime详情仍可通过startup status、`/session`和`/status`查看；状态栏不进入model input或Session。

Ctrl-C取消非空草稿并返回下一次prompt，空buffer时退出。Ctrl-D在空buffer时退出，否则保留普通forward-delete语义。Cancel和exit绝不调用模型或修改Session。以`/`开头的多行文本是普通model input，因为Host command必须是单行control message。

每个提交最多256 KiB characters且最多256 KiB valid UTF-8 bytes，并拒绝NUL。Validation failure停留在输入边界，不调用模型。Injected stream、non-TTY input、pipe与redirect使用`StreamPromptEditor`：继续按确定性单行读取，只移除一个物理line ending，并应用相同提交边界。

一次submit形成一个普通pending `UserMessage`；成功turn在一条`turn_committed`记录内持久化，并以exact多行文本replay。全空白buffer沿用既有行为，由REPL忽略。草稿是ephemeral状态，不是transcript record。

每次read前，REPL从当前Session的完整committed turns派生user prompt history，并重建prompt-toolkit的in-memory projection。它保留最新最多1000条、合计最多4 MiB，单项仍受prompt边界约束。这样Up和Ctrl-R可使用resume后的durable prompts，并在`/resume`或`/session new`后立即切换；slash、取消及失败未提交turn不会进入。该projection不新增record，也不复制持久化事实。

Slash completion只作用于cursor在buffer末尾的单行slash prefix。候选显示简短Host说明，并包含`/provider list|current|use`与`/session show|list|new`二级命令。新增`/clear`只向REPL输出ANSI clear+home sequence；它不调用模型、不修改turns/transcript、Session identity、runtime或Effective Context。

真实TTY的每次普通提交先显示ephemeral `• Working...`。第一个可见assistant或tool lifecycle事件先清除此状态；assistant companion与final输出各以`•`role marker开始。One-shot、pipe、redirect和注入stream不启用这些标记，其stdout/stderr合同不变。Waiting、role marker及live lines都不持久化，也不改变failure atomicity；sink failure继续由既有event boundary隔离。

这是Host-only输入与presentation变化。Canonical system prompt保持v16，provider adapter contract保持v19，17个model-visible tool的schema、order和共享六次预算不变。ToolArguments v1、ActionIdentity v1、Action Audit v1、`turn_committed` v3、`context_compacted` v2/v3及`ctx-v1`/`ctx-v2`representation均不升级。Exact提交文本会自然改变包含它的具体turn content identity，但identity representation不变。

## 验证要求

- Alt+Enter与Esc后Enter都插入LF并产生一次exact多行提交；
- `›`marker、续行对齐和bounded control-safe `model · workspace` toolbar不改变提交文本；
- bracketed paste产生一个多行buffer和一次submit；
- Session派生history取回完整多行entry，并在new/resume后切换且排除slash/失败turn；
- 顶层与provider/session二级completion显示准确说明；
- `/clear`只清屏，不调用模型或改变Session；
- TTY waiting与assistant marker顺序稳定，默认one-shot sink输出不变；
- Ctrl-C区分草稿取消与空buffer退出，Ctrl-D在空buffer退出；
- exact缩进、内部换行与结尾LF进入一次AgentLoop调用；
- 取消的草稿与无效bounded input不调用模型；
- 多行slash prefix绕过Host slash dispatch并原样交给模型；
- 成功多行turn只持久化一次，close/resume后exact replay；
- injected与non-TTY stream保留确定性单行fallback；
- system prompt、provider contract、tool catalog、Session schema与Effective Context representation golden保持不变。

## 后果与明确不做

Prompt-toolkit成为locked runtime dependency。Editor history仍是当前进程中的可编辑projection，durable事实仍来自Session transcript和`/history`。本contract承诺Alt+Enter与Esc后Enter，不承诺Ctrl+Enter或Shift+Enter。

本slice不精简既有启动Logo/详情，也不实现full-screen TUI、生成期间固定底部输入框、消息插入/排队、后台generation、鼠标编辑、persistent draft recovery、syntax highlighting、`/paste` mode、shell-style prompt expansion或model-generated completion；也不修改one-shot prompt argument，不让non-TTY input变成多行。

## 验证证据

2026-07-27完成offline release gate：`1152 passed`；Ruff lint、Ruff format、`uv lock --check --offline`与`git diff --check`通过。三个public fake CLI入口均输出`Fake response: Hello`，resume smoke从1个turn增长到2个turn，blank prompt保持exit 2。真实伪TTY smoke验证Alt+Enter产生exact `first line\nsecond line` transcript、`• Working...`到同一行assistant `•`的切换、`/provider list`二级补全、`/clear`清屏、Up取回完整多行prompt，以及`/session new`后历史为空；全程使用fake provider，无credential、网络或API费用。
