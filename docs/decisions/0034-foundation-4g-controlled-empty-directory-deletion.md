# 0034：Foundation 4G Controlled Empty-directory Deletion

- 状态：已接受
- 日期：2026-07-24
- 范围：Foundation 4G Slice 0–6

## 问题

Foundation 4F已经提供受控普通文件删除，但模型仍不能用专用工具删除一个已经清空的目录。要求模型改用`run_command(["rmdir", ...])`会把一个可精确描述的workspace动作扩大成`dangerous`任意进程执行，并失去目录类型、空状态、parent identity、审批展示和持久性结果的专用合同。直接扩展到递归删除则会一次引入树遍历、部分树删除、symlink、并发child、恢复和大范围误删问题，不适合作为同一个小切片。

## 决策

### 只删除一个现有空目录

新增model-visible工具：

```text
delete_directory(path)
```

`path`必须是有界、合法UTF-8、portable `/`分隔的workspace相对路径。绝对路径、Windows drive、反斜杠、空组件、`.`、`..`、NUL、超过4096 characters、超过4096 UTF-8 bytes、超过64 components或单component超过255 bytes都会在prepare阶段拒绝。空path无法表达workspace根目录，因此workspace本身不可删除。

Target必须已经存在、不是symlink、是directory且当前为空；parent必须已存在、为real directory且整条parent路径中没有symlink。第一版不删除非空目录、普通文件或symlink，不支持glob、batch、recursive、trash、backup或undo，也不通过`run_command`实现。

### Side-effect-free prepare与既有workspace-delete权限

Prepare返回immutable `PreparedDeleteDirectory`，绑定：

- 规范化workspace相对path；
- parent的device和inode；
- target directory的device、inode、mode、mtime、ctime和link count；
- prepare时观察到的empty状态；
- 既有`PermissionAction.WORKSPACE_DELETE`；
- 一个组合后的`expected-state-sha256` precondition。

Missing target、regular file、symlink、non-empty target、无效path或不安全parent在permission前hard reject，不创建Action Audit。目录删除复用Foundation 4F的`workspace-delete`分类：`read-only`拒绝；两个可写mode在`approval=ask`时逐次询问，在`approval=auto`时允许。Approval只展示workspace相对path，并继续绑定exact ActionIdentity、prepared-turn lease和single-use grant。

### Revalidation、rmdir与durability

ActionCoordinator保持固定顺序：

```text
prepare exact empty directory
→ durable action_requested
→ durable permission_decided
→ optional durable approval_resolved
→ lease and exact-state revalidation
→ durable action_execution_started
→ open real parent and target directory descriptors
→ recheck parent/target identity and empty state
→ rmdir target name
→ fsync parent
→ durable action_execution_finished
→ provider continuation
→ atomic turn_committed
```

Approval等待期间target、parent或目录内容变化会改变precondition并fail closed。Executor再通过real parent descriptor和no-follow target directory descriptor复查identity与empty状态。最终仍依赖OS `rmdir`的原子“目标必须为空”条件：即使最后一次empty预检后并发出现child，`rmdir`也会失败并保留目录内容。

POSIX没有本项目可移植使用的conditional-rmdir identity primitive，因此最后一次identity检查与按名称`rmdir`之间仍存在极小TOCTOU窗口；本合同面向Leonervis当前本地单用户、受控并发模型，不声称抵抗同workspace中的敌对并发进程。它也不把预先观察到empty当作永久删除许可。

稳定结果为：

- `succeeded / directory_deleted`：`rmdir`成功且parent fsync成功；model result为`{"operation":"deleted","path":"..."}`；
- `failed / directory_not_deleted`：stale/conflict、目录非空或`rmdir`前/期间明确失败，Host未观察到删除成功；
- `partial / directory_deleted_durability_unknown`：目录名已删除，但parent fsync失败或删除持久性无法确认。

Partial后不得自动重试，因为同名目录可能已经消失或被重新创建。Provider continuation或`turn_committed`失败不会撤销真实删除；durable Action Audit保留而candidate turn不提交。`action_execution_started`若无法持久化，executor不得运行。真实删除后若最终audit持久化失败，恢复时保留`outcome-unknown`，异常携带Host已知的execution outcome和result code。

### Model-visible与版本影响

Canonical tool order变为：

```text
read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file, delete_directory
```

十个工具继续共享每个user turn最多三次顺序执行。Anthropic与OpenAI-compatible ordinary count/create投影相同closed schema；compact-summary请求继续不暴露工具。

Provider adapter contract从v11升级到v12。Canonical system prompt从v10升级到v11，明确空目录永久删除、禁止递归/非空删除、partial后不自动重试以及approval不绕过hard bounds。Empty full-context identity更新为`ctx-v1-64ce77996397ddd1f84a27248ddd3e47224948563db506e3bfbda96939799406`。

ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、ordinary Session record schemas、`context_compacted` v2/v3 replay及`ctx-v1`/`ctx-v2`representation均不升级。旧transcript无需重写。

## 被拒绝的方案

- **让模型调用`run_command(["rmdir", ...])`**：不必要地要求`danger-full-access`，且丢失专用路径、empty-state、审批与审计合同。
- **同时支持递归删除**：部分树删除、遍历上限、symlink和恢复语义需要独立设计，风险与本切片不相称。
- **用Python递归遍历后逐项删除**：会把一个OS原子empty-directory条件退化成多步骤可见副作用。
- **只在prepare时检查empty**：approval等待和并发变化会使结论过期，必须revalidate并最终依赖`rmdir`。
- **parent fsync失败时报告普通failure或自动重试**：目录名可能已经消失，不能误报为未执行，也不能安全地作用于后来出现的同名目录。

## 验证

确定性测试覆盖：

- malformed、absolute、Windows drive、反斜杠、`.`、`..`、NUL与path/component bounds；
- missing、regular file、symlink、non-empty target及symlink parent拒绝；
- side-effect-free prepare、immutable prepared action与exact precondition；
- empty-directory成功、target/parent replacement、approval期间child出现及`rmdir`失败；
- parent fsync失败后的durability-unknown partial；
- `read-only` denial、ask reject、auto success、完整tool causality和Action Audit；
- `action_execution_started`失败前零filesystem effect；
- provider continuation、turn commit和final audit失败后的truthful effect/audit语义；
- CLI approval与`/actions`只展示workspace相对path；
- Anthropic/OpenAI-compatible tool parity、system prompt golden与Effective Context identity。

## 后续边界

Foundation 4G不授权递归删除、非空目录删除或目录移动。若未来设计递归删除，必须作为独立高风险slice重新定义tree snapshot、遍历和数量上限、symlink策略、部分树删除、取消、durability、恢复与人工审批信息，不能把`delete_directory`静默扩展成recursive行为。
