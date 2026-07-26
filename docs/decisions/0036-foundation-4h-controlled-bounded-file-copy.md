# 0036：Foundation 4H Controlled Bounded Regular-file Copy

- 状态：Accepted
- 日期：2026-07-26
- 范围：Foundation 4H Slice 0–5

## 背景

Foundation 4E已经支持单个普通文件的no-overwrite移动，但复制配置、模板、fixture或备份仍只能借助`run_command`。后者要求`danger-full-access`，且Host不提供OS filesystem/network sandbox；把普通workspace复制迫使进入command边界，与现有细粒度PermissionGate和Action Audit目标不一致。

本slice需要一个独立`copy_file(source, destination)`，同时保留workspace containment、no-symlink、exact-state、no-overwrite、durability、causality和failure-atomicity约束。它不是recursive copy、metadata clone、跨workspace传输或覆盖写入接口。

## 决策

### 工具合同

`copy_file`只接受closed object：

```json
{"source":"path/to/source.bin","destination":"path/to/copy.bin"}
```

两条路径必须是portable workspace-relative file path：UTF-8，最多4096 characters/bytes、64 components、每component最多255 bytes；拒绝absolute path、Windows drive、反斜杠、NUL、空component、`.`和`..`。Source与destination不得相同，双方parent必须已存在、是real directory，且任何parent component不得为symlink。

Source必须是existing non-symlink regular file，最多1 MiB。内容按原始bytes复制，不要求UTF-8。Destination必须完全不存在；file、directory、symlink或其他已存在entry都拒绝，永不覆盖。成功只复制source的基本`rwx` permission bits并清除setuid/setgid/sticky特殊位，不承诺owner、timestamps、ACL、xattr、sparse layout、reflink或hard-link关系。

### Side-effect-free prepare与exact identity

Prepare通过`O_NOFOLLOW`打开source，在有界读取前后比较device、inode、mode、size、mtime、ctime与link count，并记录SHA-256 content digest及完整bounded bytes。Precondition还包含source/destination relative path、两侧parent device/inode和destination-absent状态，生成`expected-state-sha256`。

Approval后和execute开始时都重新观察相同状态。Source内容、identity、任一parent或destination absence变化都会产生stale/conflict，不能沿用旧grant。Prepared bytes是被批准的复制内容，execution不会重新解释文本或跟随替换后的pathname。

### Exclusive install与durability

Filesystem effect前必须先append+fsync `action_execution_started`。Executor通过no-follow parent descriptors重新核对两侧parent及source，在destination parent内exclusive创建hidden temporary file，写入prepared bytes、设置source基本`rwx` permission bits并fsync temporary file。随后使用exclusive hard-link把temporary inode安装为destination；若destination已出现则安全失败且不覆盖。

安装后删除temporary name并fsync destination parent。成功返回：

```json
{"bytes_copied":N,"destination":"...","operation":"copied","source":"..."}
```

Source保持不变。不同filesystem不需要copy-and-delete fallback，因为实现复制bytes而不是rename/link source。

### Failure与partial outcome

Destination安装前的普通失败会清理temporary并报告`failed / file_not_copied`。如果此时temporary cleanup也失败，则报告`partial / temporary_cleanup_failed_destination_absent`，明确destination未由本次调用创建但workspace可能残留temporary file。

Destination name安装后，cleanup失败、directory fsync失败或exact state无法确认都必须报告partial，分别使用`copied_with_temporary_cleanup_failure`、`copied_cleanup_and_durability_unknown`、`file_copied_durability_unknown`或`file_copy_state_unknown`。这些结果都要求inspect workspace且不得自动重试，因为destination可能已存在。Provider continuation或turn commit失败不撤销真实copy；durable Action Audit保留，candidate turn不提交。Final audit失败继续使用既有`outcome-unknown`恢复语义。

### Permission、approval与audit

Copy只新增missing destination，不修改source，因此复用`workspace-create`：`read-only`拒绝；`workspace-write`与`danger-full-access`按`ask | auto`决定是否需要人工批准。Approval展示source与destination两个workspace相对path，不展示absolute path、source bytes、digest、precondition或internal ID。

ActionIdentity继续使用v1，并绑定exact ToolArguments、action class、workspace fingerprint、prepared-turn lease与precondition。五类Action Audit schema-v1 records保持不变；`session actions`和`/actions`只展示脱敏后的source、destination、decision、approval和result code。

### Model-visible与版本影响

Canonical tool order追加为：

```text
read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file, delete_directory, list_directory, copy_file
```

十二个工具继续共享每user turn最多三次顺序执行。Anthropic与OpenAI-compatible ordinary count/create投影相同closed schema，parallel calls关闭，compact-summary仍不暴露工具。

Provider adapter contract从v13升级到v14。Canonical model system prompt从v12升级到v13，说明copy的bounded/no-overwrite/source-retained语义及partial后不得自动重试。Empty full-context golden更新为`ctx-v1-0cd5ddd1c14a00ddcfc01b8879bc83e49a7f8fb5113d5e3d00d98a6f25c413f3`。

ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、ordinary Session schemas、`context_compacted` v2/v3 replay和`ctx-v1`/`ctx-v2`representation均不升级。旧transcript/checkpoint不重写；恢复后的新turn使用current binary的prompt/tool snapshot。

## 验证要求

确定性测试覆盖：

- binary/empty/exact-1-MiB复制、basic rwx permission bits、special-bit stripping与source retained；
- malformed/path/component bounds、missing parent、file/directory/symlink source及existing destination；
- source content/identity、两侧parent和destination absence的stale检测；
- exclusive destination race、temporary write/fsync/cleanup和directory durability failure；
- read-only deny、ask accept、auto allow、Action Audit barrier和partial audit；
- provider continuation与turn commit失败后的真实effect/Session causality；
- catalog、Anthropic/OpenAI-compatible projection/parser parity、system prompt和Effective Context goldens；
- CLI approval与Action Audit只展示两个relative paths，不泄露bytes或internal identity。

## 明确不做

- directory或recursive copy；
- destination overwrite/merge；
- source超过1 MiB；
- owner、timestamps、ACL、xattr、sparse/reflink或hard-link preservation；
- symlink复制或symlink target读取；
- multi-file transaction、glob expansion或ignore-aware copy；
- network、remote workspace或cross-workspace transfer。

## 后续边界

下一独立slice可重新评估controlled directory move或项目instruction loading。Directory move涉及整棵tree、并发child变化、mount/cross-filesystem边界和partial subtree可见性，不能通过放宽`move_file`或`copy_file`静默获得；recursive copy同样需要独立的tree snapshot、entry/byte limits、symlink policy、取消和恢复设计。
