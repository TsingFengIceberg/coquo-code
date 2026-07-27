# 0044：`turn_committed` v3 Assistant Tool Text Persistence

- 状态：Accepted
- 日期：2026-07-27
- 范围：append-only Session中mixed assistant/tool turn的record-local持久化、replay与旧版本兼容

## 背景

ADR 0042定义了`ToolUse.assistant_text`，ADR 0043让两个provider adapter能够把mixed native response解码成该provider-neutral表示。但现有`turn_committed` schema v2只保存tool ID、name、`arguments_version`与arguments；若直接允许AgentLoop提交mixed turn，Session encoder只能丢字段或失败，resume、history、compaction与provider continuation也无法重建原始因果链。

Session transcript是append-only durable truth，不能为增加一个item字段而重写旧记录。升级还必须保持record-local：其他普通records继续schema v1，`context_compacted`继续v2/v3，不应借机进行无关全局schema迁移。

## 决策

新`turn_committed`记录使用schema v3。V3沿用v2的generic immutable `ToolArguments`表示，并要求每个`tool_use` item都包含`assistant_text`字段：mixed tool request保存exact non-empty string，pure tool request保存JSON `null`。显式nullable字段保持closed schema与canonical encoder确定性，避免缺失字段在不同reader中产生不同解释。

Reader同时支持三个record-local版本：v1继续读取早期single-path tool items并转换为当前`ToolArguments`；v2继续读取generic arguments且不允许`assistant_text`字段；v3读取generic arguments与required nullable `assistant_text`。V1/v2在内存中自然得到`assistant_text is None`。旧JSONL prefix不迁移、不规范化、不重写；resume只append新的`session_resumed`与v3 turn。

V3 companion text复用`ToolUse`的non-empty、valid UTF-8、32 KiB character与32 KiB byte上限，并继续服从Session payload的no-NUL和record 1 MiB上限。Decoder保持closed fields，拒绝missing v3 field、v2 extra field、non-string/non-null、empty、lone surrogate、NUL与overflow。所有失败在replay或append前形成`SessionRecordError`，不能部分接受、猜测或静默降级。

Replay把v3 text原样恢复到同一个`ToolUse`，随后继续使用完整history validation强制tool-use/result紧邻匹配、global unique tool ID和single complete turn。Full history始终保留mixed item；compact checkpoint replay仍只用既有summary + retained complete-turn suffix，若mixed turn位于retained suffix则精确恢复，pair不能拆分。`context_compacted` schema不升级，因为compact source与Effective Context identity已在ADR 0042纳入该字段。

该slice只完成durable representation。Provider history serializer和AgentLoop仍fail closed，因此普通runtime尚不会提交mixed turn；SessionStore API与codec已经可以独立保存、resume和重建它。Canonical system prompt保持v15，adapter contract保持v16，tool schema/order、ToolArguments v1、ActionIdentity v1、Action Audit schema、`context_compacted` v2/v3与`ctx-v1`/`ctx-v2`representation均不变。

## 验证要求

- Current v3 pure/mixed turn均canonical encode、decode与replay，pure item显式写`assistant_text: null`；
- exact whitespace/newline companion text经SessionStore append、release、open/resume后不变；
- v1 single-path与v2 generic-arguments transcript继续读取，旧prefix byte-for-byte不变，后续只append v3；
- v1/v2不能编码或接受companion field，v3 missing/unknown/malformed字段fail closed；
- compact checkpoint replay保留完整full history，并原子恢复retained mixed tool-use/result pair；
- duplicate/mismatched/unmatched tool causality及record/size bounds保持原行为；
- system prompt、adapter、tool、Action Audit与context representation版本保持不变。

## 明确不做

- 修改旧Session文件、批量迁移或提供downgrade writer；
- 要求旧binary读取未来v3 records；旧reader应安全拒绝unknown schema；
- 将mixed history投影给provider、执行工具或展示companion text；
- 修改Action Audit、permission、approval、tool budget或workspace/durability边界；
- 升级普通Session record、`context_compacted`或Effective Context representation版本。

## 验证证据

2026-07-27在locked offline环境中完成确定性验证：1058项pytest通过，Ruff check与format check、`uv lock --check --offline`及`git diff --check`通过。Session codec/store测试证明v3 pure/mixed exact round-trip、malformed fail-closed、v1/v2 prefix不重写、resume只append v3，以及checkpoint replay精确恢复retained mixed pair；三个fake CLI入口均输出`Fake response: Hello`，resume从1 turn增长到2 turns，blank prompt以exit 2且empty stdout拒绝。未使用credential、网络、真实provider endpoint或API费用。
