# 0052：Exact Bounded Informed Approval Previews

- 状态：Accepted
- 日期：2026-07-27
- 范围：prepared action预览、exact identity绑定、有界diff、terminal安全与stale revalidation

## 背景

Leonervis已有PermissionGate、逐次`ask`、single-use approval grant和执行前stale检查，但原审批行主要显示tool、relative path、byte count或command argv。用户批准`write_file`、`edit_file`或`patch_file`时无法看到实际候选变化；仅知道“哪个文件会被改”不足以构成良好的知情审批。与此同时，直接让CLI在询问时重新读取文件会产生第二份snapshot，使展示内容可能与prepared candidate或最终执行边界不一致。

## 决策

新增non-persistent `ApprovalPreview` v1。每个preview携带exact `ActionIdentity.digest`、closed preview kind、可选byte count、可选bounded diff和truncation事实。`ActionCoordinator`在任何Action Audit写入前验证digest及tool-kind匹配；`HumanApprovalRequest`再次保持同一验证。Preview不是approval grant、permission evidence或execution proof。

`write_file`、`edit_file`和`patch_file`的prepared action保留准备阶段已读取的原始UTF-8 bytes以及完整candidate bytes。ProjectSession只从这两份immutable数据生成unified diff，不在CLI或approval handler中重新读取workspace。Create使用`/dev/null -> b/path`；overwrite/edit/patch使用`a/path -> b/path`。Empty create、内容相同但仍会执行的overwrite和missing final newline都有明确展示。

Diff最多160行、24 KiB，每个展示行最多4096 UTF-8 bytes。达到任一限制时显示明确truncated warning，并说明批准仍针对完整candidate，而不是可见prefix。TTY可为header、addition、deletion和warning着色；C0/C1 controls、Unicode format controls、line separators与paragraph separators会转成visible escapes，避免terminal injection或双向文本重排。相对path仍通过安全representation展示，absolute workspace path不进入approval UI。

`copy_file`、`move_file`和`delete_file`显示prepared source byte count；`mkdir`、`delete_directory`和`run_command`显示各自不可回滚、destination-absence、permanent deletion或无OS/filesystem/network sandbox等关键事实。已有exact argv/cwd/timeout和relative source/destination/path继续保留。

只有REPL的`ask`handler显示preview并读取`y/yes`、`n/no`或`c/cancel`。One-shot `ask`仍不读取stdin并安全取消；`auto`不显示preview。Live tool activity与Action Audit presentation继续脱敏，不显示file content、edit text或完整candidate。Preview不持久化到Session，不进入provider history、compaction、resume或Effective Context。

用户接受后仍执行原有refresh/revalidate和single-use grant consumption。Target、source、destination、parent、lease或context在等待期间变化时，执行照旧以stale/conflict拒绝；“用户看过preview”绝不绕过hard bounds、permission、causality、durability或failure atomicity。

该slice是Host-only approval presentation与内部请求合同变化。Canonical system prompt保持v16，provider adapter contract保持v19，17个tool的schema/order及六次预算不变；ToolArguments v1、ActionIdentity v1、Action Audit v1、`turn_committed` v3、`context_compacted` v2/v3和`ctx-v1`/`ctx-v2`representation均不升级，旧transcript不重写。

## 验证要求

- create、overwrite、edit和patch显示来自prepared snapshot的candidate diff；
- empty/no-op-content/missing-final-newline和large diff均有准确有界展示；
- wrong digest或wrong tool-kind preview在任何audit写入前fail closed；
- ESC、bidi controls和其他terminal controls不能改变approval terminal structure；
- copy/move/delete/mkdir/rmdir/command显示必要风险事实但不显示absolute path；
- reject/cancel不执行，accept后stale source仍拒绝，preview不削弱single-use grant；
- one-shot ask不读取stdin，auto和live tool activity不泄露candidate content；
- Session、provider、system prompt、tool schema与Effective Context identity保持不变。

## 明确不做

- full-screen diff viewer、分页器、交互式hunk选择或逐edit批准；
- 把preview、ANSI或用户按键持久化到Session或Action Audit；
- 为binary copy/delete读取或显示文件内容；
- 在approval期间重新读取workspace并把第二份snapshot冒充prepared candidate；
- 允许批准truncated prefix只执行部分candidate；
- `approve always`、动态permission mode切换或跨action复用approval grant。

## 验证证据

2026-07-27完成offline release gate：`1126 passed`；Ruff lint、Ruff format、`uv lock --check --offline`、`git diff --check`、三个public fake CLI入口、resume、blank-prompt及真实fake-provider REPL approval preview smoke均通过。
