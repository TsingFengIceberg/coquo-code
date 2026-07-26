# 0039：工具批次 C Structured Exact Multi-edit Patch

- 状态：Accepted
- 日期：2026-07-26
- 范围：`patch_file`

## 背景

`edit_file`只能做一个唯一exact replacement。模型要修改同一文件多个独立位置时必须连续调用，消耗共享预算，并让前一次成功、后一次失败产生不必要的中间状态。自由文本unified diff又需要复杂parser、line-offset/fuzz语义和更大的歧义面，不适合作为本slice的受控写边界。

## 决策

工具只接受closed structured object：

```json
{"path":"src/app.py","edits":[{"old_text":"before","new_text":"after"}]}
```

`edits`包含1–16项。每项只有`old_text`与`new_text`；old非空，new可空，各最多4096 characters/UTF-8 bytes，且单项不能是no-op。整个arguments object还受既有ToolArguments v1 canonical JSON 16 KiB总上限约束，所以16项并不意味着每项都能同时达到单项上限。Target path使用批次A的strict portable bounds，必须是existing non-symlink strict UTF-8 regular file；source和result都最多1 MiB。

Prepare只读取一次原始source snapshot。每个old必须在该snapshot中恰好出现一次，包括overlap-aware第二次查找；全部match range必须互不重叠。Host按原始位置排序，在一个pass中构造完整candidate。Edits数组顺序不改变anchor解释，不允许前一个replacement制造或删除后一个match。任何malformed、missing、duplicate、overlap、no-op或oversized结果都在PermissionGate前hard reject且不产生Action Audit。

Prepared action固定为`workspace-overwrite`并绑定原始source SHA-256 precondition、exact ToolArguments、workspace、prepared-turn lease、runtime generation和Effective Context。`read-only`拒绝；可写mode按ask/auto处理。Approval与`session actions`只展示relative path，不展示old/new text、candidate bytes、digest、absolute path或internal IDs。

Approval后Host重新观察source digest；变化、删除或symlink replacement使grant stale。Filesystem effect前必须durable append+fsync `action_execution_started`。Execution复用`WriteFileTool._overwrite`：在同一parent创建并fsync temporary file、保留basic mode、exact digest recheck、atomic `os.replace`并fsync parent。成功返回operation、path、replacement count和bytes written。

Replace前failure保持source并返回`failed / patch_not_applied`。Replace已发生但parent fsync失败返回`partial / patched_durability_unknown`，明确candidate可见但durability未知且不得自动retry。Provider continuation或turn commit失败不回滚真实effect，durable audit保留而candidate turn不提交；final audit failure继续使用既有outcome-unknown recovery语义。

ToolArguments v1已支持bounded canonical nested JSON，因此无需representation升级。ActionIdentity v1、Session/Action Audit schemas、compaction和ctx representation也不升级。批次A/B/C统一将canonical system prompt升级到v14、adapter contract升级到v15，并把empty full-context identity更新为`ctx-v1-ac2b833bb46894c250e2b31370d47911b3464cfa2c71c23ded504f0ea65fd4cf`。

## 验证要求

- out-of-order independent edits、deletion、Unicode/newline和mode preservation；
- malformed/nested schema、1–16 count、text/path/source/result bounds；
- missing/duplicate/overlapping anchors、no-op、binary/directory/symlink target；
- read-only deny、ask accept/reject/cancel、auto、stale approval与single-use grant；
- atomic failure、partial durability、provider/turn/final-audit failure atomicity；
- approval/audit redaction、provider双投影/parser、prompt和Effective Context identity。

## 明确不做

- unified diff、line-number hunk、regex、fuzzy、context or offset fallback；
- create file、multi-file transaction、rename或delete；
- automatic retry、merge、conflict resolution或rollback；
- source/result超过1 MiB或一次超过16 edits。
