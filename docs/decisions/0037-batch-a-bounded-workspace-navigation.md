# 0037：工具批次 A Bounded Workspace Navigation

- 状态：Accepted
- 日期：2026-07-26
- 范围：`read_file_lines`、`stat_path`、`list_tree`

## 背景

现有`read_file`只返回文件开头且最多32 KiB，`list_directory`只观察一层，模型也无法在不读取内容的情况下确认path type与基本metadata。继续使用`run_command`处理这些只读导航会不必要地进入`danger-full-access`边界。

## 决策

三个工具只接受portable workspace-relative UTF-8 path；`.`只在允许workspace root的`stat_path`和`list_tree`中有效。Path最多4096 characters/bytes、64 components、每component最多255 bytes；拒绝absolute path、Windows drive、backslash、NUL、empty/`.`/`..` component。Host通过directory descriptor逐层no-follow open，任何parent symlink都失败。

`read_file_lines(path, start_line, line_count)`中`start_line`为1–1,000,000，`line_count`为1–200。Target必须是existing non-symlink strict UTF-8 regular file，source最多1 MiB且不能含NUL。Logical lines与literal grep一致处理LF、CRLF和CR；返回`{"line":N,"text":"..."}` JSONL，最多32 KiB，只在完整record边界截断并附加`{"truncated":true}`。超出EOF和empty file都是空成功。

`stat_path(path)`不读取content或symlink target。返回workspace-relative path、`file | directory | symlink | other`、四位octal basic `rwx` mode和`modified_ns`；regular file额外返回size。Final symlink允许no-follow观察，parent symlink拒绝。该结果是一次受控观察，不声明跨调用稳定snapshot。

`list_tree(path, max_depth)`要求existing real directory，depth为1–16。它包含hidden entries，报告workspace-relative path、相对请求root的depth及type，不跟随symlink、不读取文件内容。Traversal最多扫描10,000 entries和1,000 directories；超过任一扫描上限整体报错。完整扫描后按UTF-8 path排序，最多返回500 records/32 KiB，结果上限附加truncated sentinel。并发消失或identity改变整体失败；不声明原子tree snapshot。

三者都归类`workspace-read`，任何permission mode自动allow且不询问，但继续经过prepared-turn lease、PermissionGate、durable Action Audit、tool causality和atomic turn commit。它们追加到既有12-tool canonical order之后。ToolArguments、ActionIdentity、Session、Action Audit、compaction和Effective Context representation不升级；provider schema/prompt/identity版本在批次A/B/C统一接入时一起升级。

## 验证要求

- logical-line spelling、EOF、empty、UTF-8/NUL/source/output bounds；
- root/file/directory/final symlink metadata与parent symlink拒绝；
- hidden entry、depth、stable ordering、scan/result/output limits与tree race；
- malformed、portable path/component bounds与descriptor cleanup；
- workspace-read permission、durable audit、provider continuation和共享三次预算。

## 明确不做

- symlink target读取、ACL/xattr/owner metadata；
- content paging cursor或byte-range read；
- ignore-aware tree、filesystem watch或atomic tree snapshot；
- recursive copy/delete或跨workspace导航。
