# 0035：Foundation 1E Bounded One-level Directory Listing

- 状态：已接受
- 日期：2026-07-26
- 范围：Foundation 1E Slice 0–4

## 问题

现有只读工具能读取已知文件、按pattern查找普通文件、按literal内容搜索UTF-8文件，却不能直接观察目录本身。`glob`刻意只返回普通文件，因此empty directory、只含子目录的层级、symlink entry和special entry都不可见。Foundation 4D与4G已经允许模型创建和删除空目录，这使“能够改变目录、却不能专门检查目录结构”成为明显的可观测性缺口。

把这一能力交给`run_command(["ls", ...])`会不必要地要求`danger-full-access`，引入平台输出差异并扩大到任意进程执行。直接实现recursive tree则会把一层只读观察扩大为深度、目录数、ignore、循环、跨层竞态和大输出问题，不适合作为同一个小切片。

## 决策

### 只列一个目录的直接子项

新增第十一个model-visible工具：

```text
list_directory(path)
```

`.`明确表示workspace root；其他path必须是有界、合法UTF-8、portable `/`分隔的workspace相对目录路径。绝对路径、Windows drive、反斜杠、空组件、嵌入的`.`/`..`、NUL、超过4096 characters、超过4096 UTF-8 bytes、超过64 components或单component超过255 bytes都会拒绝。

Target必须存在、是directory且target与parent components都不能是symlink。工具只枚举该目录的一层，不递归，不跟随symlink，不读取文件内容，也不解析`.gitignore`。Hidden entries会和普通entries一起返回；direct child按no-follow stat语义分类为：

```text
file | directory | symlink | other
```

结果使用deterministic UTF-8 lexical path order和compact JSON Lines：

```json
{"path":".github","type":"directory"}
{"path":"README.md","type":"file"}
{"path":"current","type":"symlink"}
```

返回path用于观察workspace内的实际UTF-8名称；其他工具仍独立执行自己的portable-input校验，因此某个平台特有的名称不因被列出而自动成为可操作路径。

### 明确的扫描、结果与输出上限

一次调用最多扫描10,000个direct entries。只有完整扫描在该上限内结束后才排序和格式化；第10,001个entry会使整个调用安全失败，不返回一个可能误解为stable-first的任意scandir prefix。

完整扫描后最多返回前200条记录，model-visible output最多32 KiB。Count或byte cap触发时只返回完整JSON records，并追加：

```json
{"truncated":true}
```

同时设置`ToolResult.truncated=true`。未截断的空字符串表示该次有界扫描观察到empty directory；截断结果不证明省略的entry不存在。读取期间entry消失或无法no-follow stat时，调用整体失败而不静默跳过。

目录枚举不是跨并发进程的原子filesystem snapshot：扫描开始前、期间或结束后出现的新entry可能不属于同一个瞬时状态。本合同保证descriptor-bounded target、no-follow分类、稳定格式与诚实失败，不声称敌对并发下的线性化tree view。

### Workspace-read、Session与Action Audit

`list_directory`复用`PermissionAction.WORKSPACE_READ`。所有permission modes都由PermissionGate自动allow，`approval=ask`也不会要求人工确认；permission和auto policy仍不能绕过workspace、path、symlink、类型、扫描或输出hard bounds。

ProjectSession继续把每次模型请求绑定到prepared-turn lease、current runtime generation与Effective Context。调用经过durable Action Audit，成功记录`ok`，工具错误记录`tool_error`；ToolResult必须立即跟随同一个`tool_use_id`，provider continuation或durable turn commit失败不会提交candidate conversation turn。新turn继续使用`turn_committed` schema v2保存generic ToolArguments，旧transcript无需重写，resume和compaction不会重新执行目录枚举。

### Model-visible与版本影响

Canonical tool order变为：

```text
read_file, glob, grep, write_file, edit_file, run_command, mkdir, move_file, delete_file, delete_directory, list_directory
```

十一个工具继续共享每个user turn最多三次顺序执行。Anthropic与OpenAI-compatible ordinary count/create投影同一个closed schema/order，compact-summary请求继续不暴露工具，parallel tool calls仍关闭。

Provider adapter contract从v12升级到v13。Canonical system prompt从v11升级到v12，说明one-level、hidden/type、no-follow、JSONL与empty/truncated解释。Empty full-context identity更新为`ctx-v1-7776df09d6ace66621cee46719755307b7d816bccde25f61064b4205c689b3b2`，prompt fingerprint更新为`v12-2d7991a78c3c3af9ec87ac4264db1ed06ff389b8191eb1388014366e83f134b3`。

ToolArguments v1、ActionIdentity v1、`turn_committed` schema v2、Action Audit schema v1、ordinary Session record schemas、`context_compacted` v2/v3 replay及`ctx-v1`/`ctx-v2`representation均不升级。

## 被拒绝的方案

- **调用`run_command(["ls", ...])`**：把窄只读观察升级为dangerous任意进程执行，并产生平台相关文本。
- **第一版直接递归**：需要额外定义depth、directory count、symlink、ignore、跨层竞态和partial tree语义。
- **把目录加入现有`glob`结果**：会改变glob长期稳定的regular-files-only合同，使pattern结果类型和旧prompt语义含混。
- **返回size、mode、mtime、inode或symlink target**：这些metadata和隐私边界应由未来独立`stat`能力决定；当前只返回操作所需的最小type信息。
- **超过scan limit时返回scandir prefix**：原始枚举顺序不稳定，不能声称它是deterministic sorted prefix。
- **默认隐藏dot entries或读取`.gitignore`**：目录直接观察应报告真实子项；ignore-aware project view属于独立语义。

## 验证

确定性测试覆盖：

- root与nested path、empty directory、stable UTF-8 order及一层非递归行为；
- regular file、directory、symlink、FIFO/other与hidden entry分类；
- malformed、absolute、Windows drive、反斜杠、`.`边界、`..`、NUL及path/component bounds；
- missing target、file target、symlink target、symlink/non-directory parent拒绝；
- scan limit whole-call error、200-result truncation、32 KiB cap与JSONL sentinel；
- AgentLoop exact tool-use/result causality与共享三次预算；
- ProjectSession workspace-read allow、no-human-approval、durable Action Audit与atomic turn commit；
- Anthropic/OpenAI-compatible schema和parser parity、system prompt golden与Effective Context identity。

完整确定性release gate于2026-07-26通过：850 tests passed，Ruff check、Ruff format check、`uv lock --check`与`git diff --check`均通过。三个public fake CLI入口、两turn resume、blank-prompt rejection及临时workspace真实目录枚举smoke均通过；未使用credential、网络或真实provider费用。

## 后续边界

Foundation 1E不实现recursive tree、metadata/stat、symlink target读取、ignore-aware view或目录大小汇总。下一独立slice可在`copy_file`与受控directory move之间重新评估实际coding workflow收益；recursive listing/deletion仍应单独设计，不能静默扩展本工具。
