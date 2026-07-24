# 0032：Foundation 4E Controlled No-overwrite File Move

- 状态：已接受
- 日期：2026-07-24
- 范围：Foundation 4E Slice 0–9

## 问题

模型已经能够创建目录、写入、exact edit和运行受控命令，但还不能把一个普通文件移动到另一个workspace相对路径。让模型改用`run_command(["mv", ...])`并不等价：command固定属于`dangerous`，要求`danger-full-access`，还会扩大到任意进程副作用、平台差异和更弱的结构化审计。

普通Unix `rename()`也不是本slice可接受的默认实现，因为目标在检查后并发出现时，rename通常会替换目标。第一版必须保证destination已存在时绝不覆盖，同时把source、destination、两端parent和approval等待期间的变化绑定到同一个可审计动作。

## 决策

### 独立的普通文件移动合同

新增model-visible工具：

```text
move_file(source, destination)
```

两者都必须是有界、合法UTF-8、portable `/`分隔的workspace相对路径。绝对路径、Windows drive、反斜杠、空组件、`.`、`..`、NUL、超过4096 characters、超过4096 UTF-8 bytes、超过64 components或单component超过255 bytes都会在prepare阶段拒绝。

`source`必须已经存在、不是symlink且是regular file；`destination`必须完全不存在。两端parent必须已存在、为real directory且路径中没有symlink。Source和destination parent必须位于同一filesystem。第一版不移动目录、不创建destination parent、不覆盖任何entry，也不把相同路径视为成功。

### Side-effect-free prepare与独立workspace-move权限

Prepare返回immutable `PreparedMoveFile`，绑定：

- 规范化source和destination相对路径；
- source的device、inode、mode、size、mtime、ctime和link count；
- source parent与destination parent的device/inode；
- destination absence；
- `PermissionAction.WORKSPACE_MOVE`；
- 一个组合后的`expected-state-sha256` precondition。

新增`workspace-move`而不复用create或overwrite，因为移动同时删除一个名称并创建另一个名称，审批和审计应准确表达这种双路径副作用。PermissionGate矩阵为：

- `read-only`拒绝；
- `workspace-write`与`danger-full-access`在`approval=ask`时逐次询问；
- 两种可写模式在`approval=auto`时自动允许。

交互审批显示source和destination两个workspace相对路径；Action Audit也只显示这两个相对路径，不显示绝对workspace、precondition、fingerprint或内部ID。One-shot ask继续安全cancel且不读取stdin。

### 双端revalidation与no-overwrite执行

ActionCoordinator保持固定顺序：

```text
prepare exact source and absent destination
→ durable action_requested
→ durable permission_decided
→ optional durable approval_resolved
→ lease and combined source/destination revalidation
→ durable action_execution_started
→ exclusive hard-link destination
→ fsync destination parent
→ unlink source
→ fsync source parent
→ durable action_execution_finished
→ provider continuation
→ atomic turn_committed
```

Approval等待期间，source内容或metadata变化、任一parent identity变化、source消失/变为symlink/非普通文件，或destination出现，都会产生不同precondition并以`stale_precondition`拒绝，executor不会运行。执行时再次通过directory descriptors核对parent identity、source identity和destination absence。

使用exclusive hard-link再unlink source，而不是普通`rename()`：`os.link`在destination已存在时原子失败，因此不会覆盖并发出现的目标；同filesystem约束也是这一实现的必要条件。工具不读取文件内容，所以合法的binary或较大regular file可以移动。

### 非单步原子性与truthful partial结果

Hard-link + unlink不是一个单步filesystem transaction。Destination link创建后，source unlink或durability确认可能失败，因此Host必须保留真实partial状态，不自动重试，也不声称已经回滚：

- `succeeded / file_moved`：destination已安装并fsync，source已删除且source parent已fsync；model result为`{"destination":"...","operation":"moved","source":"..."}`；
- `failed / file_not_moved`：destination link创建前失败，或执行前发现stale/conflict；
- `partial / destination_linked_source_retained_durability_unknown`：destination已出现、source仍保留，但destination parent durability未知；
- `partial / destination_linked_source_retained`：destination已出现但source无法删除，因此两个名称都保留；
- `partial / file_moved_durability_unknown`：source已删除、destination存在，但source removal durability未知。

出现partial后，模型必须先检查两个路径，不能自动重试。Provider continuation或`turn_committed`失败不会撤销真实filesystem状态；durable Action Audit保留，而candidate conversation turn不提交。`action_execution_started`持久化失败时禁止创建destination。若finished audit持久化失败，已有started-without-finish recovery继续导出unknown outcome。

这仍是本地单用户、受控并发模型下的文件工具，不宣称提供OS sandbox或跨平台敌对并发下的完整filesystem transaction。

### Model-visible合同与版本影响

Canonical tool order固定为：

```text
read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file
```

八个工具继续共享每个user turn最多三次顺序执行。Anthropic和OpenAI-compatible ordinary count/create投影同一closed schema，compact-summary请求仍不暴露工具。

Provider adapter contract从v9升级为v10。Canonical model system prompt从v8升级为v9，说明`move_file`只移动普通文件、destination不得存在、目录移动和删除仍不可用、partial后不得自动重试。Empty full-context golden更新为`ctx-v1-b18f599515bec3196b10a2bf877d39f1da19f6a9eb3b4f1e123ccc3cd16da760`，identity algorithm和representation仍为`ctx-v1`/`ctx-v2`。

ToolArguments保持v1；新`turn_committed`保持schema v2；ActionIdentity与Action Audit保持v1，因为字段结构和重放规则没有变化，只扩展了closed permission action/reason vocabulary。普通Session records保持v1；`context_compacted`继续支持v2/v3 replay。旧transcript和checkpoint不重写，resume与compaction不会重新执行move。

## 不变量

- 模型请求move不等于Host应执行；PermissionGate、approval、revalidation和durable execution-start始终位于副作用之前。
- Hard preparation rejection不生成Action Audit。
- Destination存在时绝不覆盖，auto approval也不能绕过这一约束。
- Source/destination和两端parent共同参与stale identity；审批只授权一次且不能移用于其他路径。
- Symlink、workspace containment、same-filesystem、causality、durability和shared tool budget均不可由permission mode绕过。
- Partial effect必须如实返回并持久审计，不自动重试或伪造rollback。
- Tool result必须紧跟并匹配唯一`tool_use_id`；完整turn只在最终assistant text和durable commit后可见。
- Resume、compaction和runtime切换不会重放文件移动，也不会从Session provenance重建旧runtime。

## 明确不做

- 移动或重命名目录；
- 覆盖existing destination；
- 跨filesystem copy-and-delete fallback；
- 自动创建destination parent；
- file delete、empty-directory removal或recursive delete；
- fuzzy/multi-path批量移动；
- 把`run_command(["mv", ...])`自动降级为workspace-move；
- OS/VM/container sandbox或敌对并发下的完整portable transaction保证。

## 后续

下一独立slice建议加入file-only delete：必须单独定义workspace-delete权限、目标identity、approval后stale检查、unlink与parent fsync、已删除但durability未知的partial语义，以及provider/turn失败后的truthful audit。之后再设计只删除empty directory的能力；recursive delete仍应保持独立且更晚的高风险决策。

## 验证证据

确定性release gate于2026-07-24通过：

```text
730 tests passed
ruff check passed
ruff format --check passed
uv lock --check passed
git diff --check passed
```

三个public fake CLI入口均输出`Fake response: Hello`并退出0；resume smoke最终报告`turns: 2`；空prompt退出2、stdout为空且stderr给出argparse校验错误。未使用credential、网络或真实provider费用。
