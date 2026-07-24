# 0033：Foundation 4F Controlled Regular-file Deletion

- 状态：已接受
- 日期：2026-07-24
- 范围：Foundation 4F Slice 0–6

## 问题

模型已经能读取、写入、精确编辑、创建目录、移动普通文件和运行受控命令，但还没有表达“永久删除一个普通文件”的专用合同。让模型改用`run_command(["rm", ...])`会把一个可精确约束的workspace动作扩大成`dangerous`任意进程执行，要求`danger-full-access`，并失去专用路径、审批、stale-state和结果语义。

删除与覆盖或移动也不同：成功unlink后名称立即消失，若随后parent fsync失败，Host不能恢复原文件，也不能把结果描述成完全失败。第一版必须限制为一个现有普通文件，拒绝目录、symlink、批量和递归删除，并如实保留“文件已消失但durability未知”的partial状态。

## 决策

### 独立的普通文件删除合同

新增model-visible工具：

```text
delete_file(path)
```

`path`必须是有界、合法UTF-8、portable `/`分隔的workspace相对路径。绝对路径、Windows drive、反斜杠、空组件、`.`、`..`、NUL、超过4096 characters、超过4096 UTF-8 bytes、超过64 components或单component超过255 bytes都会在prepare阶段拒绝。

Target必须已经存在、不是symlink且是regular file；parent必须已存在、为real directory且整条parent路径中没有symlink。工具不读取内容，因此binary或较大普通文件可以删除。第一版不删除目录或symlink，不支持glob、批量、递归、回收站、备份或恢复，也不通过`run_command`实现。

### Side-effect-free prepare与workspace-delete权限

Prepare返回immutable `PreparedDeleteFile`，绑定：

- 规范化workspace相对path；
- parent的device和inode；
- target的device、inode、mode、size、mtime、ctime和link count；
- `PermissionAction.WORKSPACE_DELETE`；
- 一个组合后的`expected-state-sha256` precondition。

Missing target、directory、symlink、无效path或不安全parent在permission前hard reject，不创建Action Audit。新增独立`workspace-delete`分类，使审批和审计准确表达永久删除：

- `read-only`拒绝；
- `workspace-write`与`danger-full-access`在`approval=ask`时逐次询问；
- 两种可写mode在`approval=auto`时自动允许。

Approval只展示workspace相对path并绑定exact ActionIdentity、prepared-turn lease和一次性grant。等待期间target或parent变化会产生不同precondition并以stale拒绝。

### 执行、持久性与partial结果

ActionCoordinator保持固定顺序：

```text
prepare exact target
→ durable action_requested
→ durable permission_decided
→ optional durable approval_resolved
→ lease and target revalidation
→ durable action_execution_started
→ open real parent directory descriptor
→ recheck parent and target identity
→ unlink target
→ fsync parent
→ durable action_execution_finished
→ provider continuation
→ atomic turn_committed
```

执行前通过real parent directory descriptor复查parent identity和target stat，随后按名称执行unlink。POSIX没有本项目可移植使用的conditional-unlink primitive，因此在最后一次stat与unlink之间仍存在极小TOCTOU窗口；本合同面向Leonervis当前本地单用户、受控并发模型，不声称抵抗同workspace中的敌对并发进程。Workspace containment、no-symlink prepare/revalidation、runtime lease和Action Audit仍是强制边界。

稳定结果为：

- `succeeded / file_deleted`：unlink成功且parent fsync成功；model result为`{"operation":"deleted","path":"..."}`；
- `failed / file_not_deleted`：执行前stale/conflict或unlink前/期间明确失败，Host未观察到删除成功；
- `partial / file_deleted_durability_unknown`：unlink已成功，文件名已消失，但parent fsync失败或删除持久性无法确认。

Partial后不得自动重试，因为目标名称可能已消失，重试既不能恢复durability确认，也可能错误作用于后来创建的同名文件。Provider continuation或`turn_committed`失败不会撤销真实删除；durable Action Audit保留而candidate turn不提交。`action_execution_started`若无法持久化，executor不得运行。

### Model-visible与版本影响

Canonical tool order变为：

```text
read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file
```

九个工具继续共享每个user turn最多三次顺序执行。Anthropic与OpenAI-compatible ordinary count/create投影相同closed schema；compact-summary请求继续不暴露工具。

Provider adapter contract从v10升级到v11。Canonical system prompt从v9升级到v10，明确永久普通文件删除、不可删除目录/symlink、partial后不自动重试以及approval不绕过hard bounds。Empty full-context identity更新为`ctx-v1-42200fbe6c48a76d91ac0dde71e12be0e41674b1ad06c8b82bf82a541e3049e8`。

ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、普通Session records、`context_compacted` v2/v3 replay及`ctx-v1`/`ctx-v2`representation均不升级：本slice只扩展closed tool/permission vocabulary和current-binary model contract，不改变这些representation的结构。

## 不做什么

- 不删除目录，即使目录为空；
- 不支持recursive、glob或batch delete；
- 不删除symlink本身，也不跟随symlink；
- 不提供trash、undo、backup或restore；
- 不用`run_command(["rm", ...])`作为内部实现；
- 不声称OS sandbox或敌对并发下的完全conditional unlink；
- 不改变三次共享tool预算、Session replay或compaction语义。

## 验证

确定性测试覆盖path bounds、side-effect-free prepare、missing/directory/symlink hard rejection、binary/large regular file、stale target和changed parent、unlink失败、unlink后fsync失败partial、invalid precondition、permission矩阵、ask accept/reject、approval期间变化、Action Audit、provider projection、AgentLoop工具顺序、system prompt及Effective Context identity。完整release gate同时运行pytest、Ruff、format、lock、diff、三个fake CLI入口、resume smoke与blank-prompt rejection；不使用credential、网络或真实provider费用。

## 后果

Leonervis现在能以比任意shell命令更窄、更可审计的方式永久删除一个普通文件，并能在删除已可见但durability未知时保持事实准确。代价是第一版刻意不覆盖目录清理、批量工作流或恢复体验；下一独立slice应只考虑empty-directory removal，并重新评估目录为空的stale identity、并发创建child和parent durability，而不是直接加入递归删除。
