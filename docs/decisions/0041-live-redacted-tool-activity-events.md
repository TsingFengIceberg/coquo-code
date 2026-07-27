# 0041：Live Redacted Tool Activity Events

- 状态：Accepted
- 日期：2026-07-27
- 范围：AgentLoop、ProjectSession与CLI之间的临时工具生命周期观察

## 背景

模型请求工具后，既有CLI只在最终回答或人工approval时显示信息。只读工具、自动允许的写入和命令执行期间没有稳定的即时反馈，用户无法区分模型仍在思考、工具正在执行或工具已经结束。Action Audit能够在事后提供durable truth，但它不替代当前turn中的live progress。

该能力需要被CLI one-shot与REPL复用，不能散落成各处`print()`，也不能让终端写入失败影响工具执行、Action Audit、turn commit或Session状态。Live line还必须比model-visible ToolResult更严格脱敏，因为事件会在参数执行校验前发出。

## 决策

AgentLoop在每次正常工具dispatch前后发出typed `ToolRequestStarted`与`ToolRequestFinished`事件。事件使用共享六次预算的同一index；第七次请求只发出`ToolRequestLimited`，不发started/finished且不进入dispatch。若dispatch抛异常而Host无法安全断言最终effect，finish状态为`outcome-unknown`，不伪称`failed`。

ProjectSession通过现有`PromptEventSink`转发工具事件，并从ActionCoordinator的结构化execution outcome/result code映射`succeeded | failed | partial`，从PermissionGate与approval resolution映射`denied | rejected | cancelled`，从无副作用prepare rejection映射`error`。状态不解析untrusted ToolResult文本。ActionCoordinator只为Host调用方保留已执行action的outcome/result code；durable Action Audit record不变。

事件只包含最小安全摘要：file tools显示workspace-relative path；绝对path/cwd隐藏为`<absolute>`；grep只显示include与query/pattern UTF-8 byte count；write/edit/patch只显示path与byte/edit count；command只显示executable basename、argument count、cwd与timeout。事件不包含file/edit/query content、完整argv、absolute workspace、digest、lease、tool/session/request ID、credential、raw ToolResult或provider body。控制字符被转义，字段与整条摘要都有界。

CLI使用可复用`TerminalEventSink`把typed event交给纯presentation renderer，并每条立即flush。One-shot把事件写到stderr，stdout只保留最终assistant text，便于脚本消费；REPL把事件写到自己的stdout。颜色只复用既有TTY/`NO_COLOR`策略。首版采用稳定追加行，不加入spinner、原地刷新、progress bar、Rich依赖、TUI或event bus。

Event sink是best-effort UI边界：捕获`Exception`并忽略，不能改变dispatch、approval、audit、causality、commit或failure propagation。Live event不写Session transcript、不参与resume/compaction、不进入model history，也不是durable truth；崩溃后应以Action Audit和完整transcript为准。

这是纯Host-side presentation能力。17个工具的schema与canonical order不变，model没有新输入或行为，因此canonical system prompt保持v15，provider adapter contract保持v15，empty full-context identity保持`ctx-v1-ea0e03265910b48b3cd97e3ace999507379a5e5cf168c6898390870266df051f`。ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、`context_compacted` v2/v3 replay及`ctx-v1`/`ctx-v2`representation均不升级。

## 验证要求

- started/finished严格按顺序携带共享budget index，第七次只产生limited且不dispatch；
- success、prepare error、permission deny、approval reject/cancel、failed、partial与unknown outcome都有准确typed mapping；
- summary覆盖完整tool surface，隐藏content、query、完整argv与absolute path并转义control characters；
- sink失败不改变工具结果、Action Audit、turn commit或既有异常传播；
- one-shot最终文本保持stdout-only，live events进入stderr；REPL在最终回答前显示事件；
- live events不新增Session record，Action Audit仍为durable source of truth；
- system prompt、provider projection、Effective Context identity与Session schema goldens保持不变。

## 明确不做

- 持久化live event、从transcript重播终端动画或把event发送给model；
- spinner、progress percentage、耗时估计、并行工具UI、TUI或Web event stream；
- 显示工具参数/结果原文、absolute path、完整command argv、digest或内部ID；
- 因展示失败重试工具、改变action状态或吞掉executor/audit失败。

## 验证证据

2026-07-27在locked offline环境中完成确定性验证：1032项pytest通过，Ruff check与format check、`uv lock --check --offline`及`git diff --check`通过；三个fake CLI入口均输出`Fake response: Hello`，resume从1 turn增长到2 turns，blank prompt以exit 2拒绝。额外脚本化one-shot场景经真实CLI主路径调用一次`read_file`，依次输出started、succeeded与最终`READ_OK`，未使用credential、网络、真实provider endpoint或API费用。
