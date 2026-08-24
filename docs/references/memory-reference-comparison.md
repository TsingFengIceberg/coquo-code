# Long-Term Memory Reference Comparison

本文件是学习材料索引，不是 Leonervis Code 的运行时设计或第三方兼容承诺。两个项目都只通过
`learning-submodules/` 作为只读参考，生产代码不得导入它们。

## Fixed references

| Reference | Repository | Pinned commit | Primary use |
| --- | --- | --- | --- |
| DeerFlow | `https://github.com/bytedance/deer-flow` | `cc6a2657e7badb4dfeac0338533c9193b12bd9e6` | Fact lifecycle and memory governance |
| Hermes Agent | `https://github.com/NousResearch/hermes-agent` | `29c5a12e04497dfb18d5432f9416f837bfe80919` | Provider abstraction and runtime lifecycle |

## What DeerFlow contributes as a reference

- A backend-neutral `MemoryManager` contract with middleware and tool modes.
- Explicit scope keys for agent, user, and conversation/thread isolation; missing user identity can fail closed in external backends.
- A fact lifecycle: extraction, consolidation, correction/removal, confidence, reinforcement, staleness review, and bounded eviction.
- Explicit search/add/update/delete operations when the host chooses tool mode.
- Queued updates with shutdown draining and bounded backend-specific storage.
- Tests for scope gates, user isolation, queue behavior, prompt-injection resistance, stale facts, consolidation, and eviction.

The useful lesson is governance around durable facts, not the particular DeerMem schema or its external backend integrations.

## What Hermes contributes as a reference

- A provider-neutral `MemoryProvider` interface and a coordinating `MemoryManager`.
- Turn-start recall, optional next-turn background prefetch, completed-turn synchronization, session switching, and bounded shutdown draining.
- Optional provider-owned tools with capability discovery, while keeping context-only providers out of the tool surface.
- Provider lifecycle metadata for sessions, users, parent sessions, agent identity, and subagent context.
- Integration points with context compression and streamed-context sanitization.
- Multiple replaceable external providers without making the host depend on one storage vendor.

The useful lesson is the host/provider boundary and lifecycle sequencing, not Hermes' gateway, plugin, or service-oriented runtime.

## Relationship to existing references

- Claw-Code remains the primary reference for project instruction memory and user-visible inspection commands; it is not sufficient for a governed long-term fact store.
- MewCode Tianba is useful for a small file-based memory flow (scopes, extraction, selection, and age warnings), but its automatic write behavior is less conservative than this project requires.
- DeerFlow supplies the strongest fact-governance reference; Hermes supplies the strongest pluggability and lifecycle reference.

## Coquo boundaries

These references do not authorize implementation. Any future memory slice must preserve workspace containment, append-only durable state, strict replay/schema validation, explicit PermissionGate decisions, Action Audit evidence, untrusted-evidence labeling, and Child/Team isolation. No automatic memory write, remote backend, or provider credential is implied by this note.

English summary: DeerFlow is the reference for governed fact memory; Hermes is the reference for a replaceable provider lifecycle. Both are read-only study inputs and are intentionally not runtime dependencies.
