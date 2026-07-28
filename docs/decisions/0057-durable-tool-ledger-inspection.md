# 0057：Durable Tool Ledger Inspection

- 状态：Accepted
- 日期：2026-07-28
- 范围：严格Session replay后的有界tool-ledger查询、离线CLI与REPL展示

## 背景

ADR 0056让每个新`turn_committed` v5持久保存Host-owned structured tool ledger，并在当前turn durable commit后显示一次临时`Tool summary:`。但用户退出或恢复Session后，只能直接搜索JSONL才能重新查看这些事实。Action Audit可以解释每个真实受控动作的permission、approval与execution lifecycle，却不覆盖只读工具、同批跳过或整批over-budget拒绝，因此不能替代per-turn ledger视图。

需要一个纯Host侧、只读且有界的查看入口，使当前REPL和离线Session检查共享同一份严格replay事实，同时不泄露tool arguments、prompt、result prose或内部identity。

## 决策

`SessionStore.tool_ledgers(selector, limit)`与`ProjectSession.tool_ledgers(limit)`返回相同的bounded `ToolLedgerQueryResult`。查询只接受1至20个recent committed turns，结果按原有时间顺序保存turn number、`turn_committed` record sequence、commit timestamp、schema version及可用的`ToolTurnLedger`。离线入口在existing Session root上执行严格只读replay；当前REPL入口直接读取writer已经replay验证的immutable state。

`turn_committed` v5即使ledger为空也表示账本可用且该turn没有请求工具。V1/v2/v3/v4 replay虽然在内存中使用empty legacy ledger维持兼容，但查询根据record-local schema明确返回unavailable，避免把“历史上没有保存”误报成“确定请求数为零”。查询不解析conversation items、ToolResult prose、assistant text或terminal output来重建旧账本。

CLI新增`leonervis-code session tools [selector] --limit N [--details]`，REPL新增`/tools [count]`与`/tools details [count]`。默认limit为5，最大20。Summary模式显示每turn的requested、admitted、dispatched、succeeded及非零outcome counts；details模式另显示request index、tool name、typed outcome和可选safe result code。两种入口调用同一renderer，live `Tool summary:`也复用同一aggregate field helper，避免字段语义漂移。

展示明确排除tool-use ID、arguments、path、prompt、assistant text、ToolResult content、absolute workspace、Action identity和approval grant。Details总输出上限为32 KiB，在完整行边界停止并附加truncated sentinel。持久字段虽然已经过schema校验，renderer仍对展示文本执行control-safe escaping。

## 只读与Failure边界

- 查询不创建缺失的`.leonervis-code`或Session root，不获取writer lease，不更新`latest.json`，也不append、repair或rewrite transcript；
- malformed transcript、workspace mismatch、symlink或其他strict replay错误让整个查询失败，不展示未经验证的partial结果；
- slash命令由Host处理，不进入user/assistant history，不调用provider、tool、PermissionGate、approval或Action Audit；
- 当前Session查询在既有facade lock内读取同一replayed state，与`/resume`或`/session new`切换保持一致；
- 输出截断只影响terminal presentation，不修改ledger或Session事实。

## 版本与兼容性

该能力只读取已有schema-v5字段并增加Host终端入口，没有改变model-visible行为、provider request/response shape、tool catalog、Session JSONL或context identity输入。Canonical system prompt保持v19与既有fingerprint，provider adapter contract保持v20，ToolArguments v1、ActionIdentity v1、`turn_committed` schema v5、Action Audit schema v1、`context_compacted` v2/v3及Effective Context `ctx-v3`/`ctx-v4`均不升级。旧transcript不重写。

## 明确不做

- 从v1-v4 conversation prose推测或补造历史ledger；
- 展示tool arguments、文件内容、query、完整argv、absolute path、internal ID或raw result；
- 通过inspection重试、继续、回滚或重新执行任何请求；
- 新增model-visible ledger tool、自动开启下一user turn或实现Foundation 5A。

## 验证要求

- V5非空ledger、v5空ledger与v1-v4 unavailable状态可区分；
- 最近turn limit保留原turn number、record sequence、timestamp与稳定顺序，并拒绝非ASCII、非整数及越界limit；
- summary与details准确展示derived counts、outcome和safe result code，同时隐藏ID、arguments与conversation/result prose；
- details输出不超过32 KiB，只在完整行边界截断并附带sentinel；
- `session tools`和`/tools`均只读，resume后读取同一持久账本，缺失Session root不会被创建；
- system prompt、provider adapter、tool schemas、Session writer schema和Effective Context golden保持不变；
- focused tests与完整offline release gate通过，不使用credential、网络或真实provider费用。

## 验证证据

2026-07-28完成offline release gate：`1197 passed`；Ruff lint、Ruff format、`uv lock --check --offline`与`git diff --check`通过。三个public fake CLI入口均输出`Fake response: Hello`，resume保持同一Session并从1 turn增长到2 turns，blank prompt保持exit 2且stdout为空。CLI smoke证明`session tools`在新v5 no-tool turn和resume后的两个turn上返回稳定summary，details空状态具有明确提示。Focused tests另覆盖v5 non-empty/empty、v4 unavailable、query limit、detail redaction、32 KiB truncation、slash dispatch与missing-root read-only failure。全程未使用credential、网络或真实provider费用。
