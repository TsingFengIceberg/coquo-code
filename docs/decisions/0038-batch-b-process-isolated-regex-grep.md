# 0038：工具批次 B Process-isolated Regex Grep

- 状态：Accepted
- 日期：2026-07-26
- 范围：`grep_regex`

## 背景

Literal `grep`不能表达alternation、anchors或character classes。直接在Host线程运行backtracking regex会让恶意或意外pattern长期占用CPU；stdlib `re`没有可移植的per-match timeout。本轮保持locked依赖不变，因此不能依赖第三方regex timeout。

## 决策

`grep_regex(pattern, include)`使用当前Python runtime的stdlib `re` dialect，case-sensitive且无额外flags，并对每个logical line独立调用`search`。Pattern必须非空、单行、无NUL且最多4096 characters/UTF-8 bytes。它不支持跨行、capture output、replacement、flags参数、index、fuzzy或ignore-aware search。

File selection复用literal grep的portable include contract和no-symlink regular-file policy：最多1,000 candidates、每file 1 MiB、aggregate 16 MiB；selected file必须strict UTF-8且无NUL。输出按candidate path和line稳定排序，每matching line只返回一次`path/line/text` JSONL，最多200 matches和32 KiB，结果上限使用完整record与truncated sentinel。

Compile、selector traversal、file reads和matching全部在`spawn` worker process中执行。Host从process start后只等待固定1秒whole-call timeout；超时先`terminate`并最多等待1秒，仍存活则`kill`并再次有界等待。Host关闭Pipe与process handle，worker非零退出、EOF或invalid payload都映射为稳定错误，不向模型暴露traceback。灾难性回溯因此最多损失一个有界worker，不阻塞主Agent进程。

Process isolation不是OS sandbox、memory quota或workspace外访问的通用防护。Worker只运行Host固定代码并接收已验证的pattern/include；它仍继承本地process能够继承的OS环境。整体1秒也覆盖大workspace traversal/read，因此合法但昂贵的调用可能timeout，用户应缩窄include或简化pattern。

`grep_regex`归类`workspace-read`，无需人工approval但保留durable Action Audit、prepared-turn lease、causality和共享三次预算。它在canonical order中位于`list_tree`之后、`patch_file`之前。

## 验证要求

- alternation/anchors/classes、case-sensitive和CR/LF logical lines；
- invalid/blank/multiline/oversized pattern与invalid include；
- candidate/file/aggregate/match/output bounds、binary/NUL/symlink拒绝；
- catastrophic backtracking timeout、worker cleanup和invalid worker result；
- provider双投影/parser parity、workspace-read audit和turn causality。

## 明确不做

- PCRE或第三方regex兼容承诺；
- user-controlled flags、multiline/DOTALL、capture group output或replacement；
- index、ripgrep integration、`.gitignore`语义或跨文件match；
- OS sandbox、persistent worker pool或并行search。
