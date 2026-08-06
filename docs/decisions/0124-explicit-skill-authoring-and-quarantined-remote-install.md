# ADR 0124: Explicit Skill Authoring and Quarantined Remote Install

- Status: Accepted
- Date: 2026-08-06

## Context

Leonervis can discover, load, compose, create templates for, and import declarative Skill packages, but it cannot yet preserve a workflow from an explicit natural-language request. Local import also requires a pre-existing directory, while users commonly receive a raw `SKILL.md` or ZIP over HTTPS. Treating either operation as ordinary model file writes would lose package-level validation, commit coupling, source provenance, and the inactive-before-review boundary.

Automatic experience mining is a different capability. It needs durable Memory, repeated independent observations, evaluation, review, promotion, monitoring, and rollback, none of which should be implied by explicit authoring.

## Decision

Add two isolated model-visible coordination tools to ordinary Prompts:

- `skill_propose_create` accepts one complete bounded declarative package proposal only after an explicit current-user request. The Host returns a receipt during the loop but creates the inactive candidate only after the containing Session Turn commits.
- `skill_accept_create` accepts only one exact pending generated candidate after direct user approval. After the containing Turn commits, the Host recovers the exact successful ToolUse/ToolResult/ledger causality, rechecks owner Session, status, fingerprint, and proposed scope, then installs through the existing canonical `import_skill()` transaction and lock.

Both calls must be the only tool call in their assistant response. Neither is available inside a Task Stage. Candidate creation is internal durable state; installation is denied in read-only mode. A proposal cannot select user scope, and acceptance cannot change the proposed scope. The system does not infer Skill creation from incidental success or repeated behavior.

Store all generated and downloaded candidates under `.leonervis-code/skill-candidates/v1/<candidate-id>/`, outside every Skill inventory root. Each candidate contains an immutable private package, closed metadata, and an append-only closed event log with `created` followed by at most one `installed` or `rejected` event. Candidate IDs bind proposal context and tool identity for generated content, or final URL, source bytes, source type, and declared name for remote content.

Add Host commands for public remote acquisition and candidate management. `skills fetch <url>` and `/skills fetch <url>` reuse `PinnedWebGetTransport`; there is no generic model-visible download tool. Fetch accepts only credential-free public HTTPS URLs without query strings and only a raw strict-UTF-8 `SKILL.md` or bounded ZIP. ZIP extraction rejects traversal, absolute or backslash paths, multiple package roots, duplicate and case-fold-colliding entries, symlinks and other special files, encryption, excessive counts, sizes, expansion, or compression ratios. Redirect targets retain the same public-address transport checks and the final URL must remain query-free HTTPS.

Downloaded candidates never activate automatically. Users inspect complete bounded instructions and resources with candidate commands, then explicitly install or reject the exact candidate. Installation reuses local import validation and fingerprint locks. A successful package install followed by candidate-event failure is reported as a partial durable outcome rather than falsely remaining a clean retry.

The SkillInventorySnapshot remains frozen for each prepared Turn. Installation does not mutate that Turn's ToolSet; the new package can be discovered only from a later Turn snapshot.

## Contract changes

The built-in Registry advances to generation 5. The canonical system prompt advances to v40 and the provider adapter contract to v41 because two model-visible schemas and their policy are added. Full and compacted Effective Context representations advance to v15/v16; v13/v14 remain strictly readable as legacy Skill-inventory-v2 representations. Skill inventory remains v2, and Session, Task, Action Audit, and import-lock schemas do not change.

## Consequences

- Explicit natural-language preservation gets commit-coupled durability without making Skill installation an ordinary file Tool or bypassing package checks.
- Remote bytes are inspectable but inert until an exact explicit install.
- Standalone candidate commands do not invoke a provider or create a Session.
- Remote registry search, Git clone, dependencies, updates, publishing, signing, trust scores, automatic learning, Memory mining, and Skill evolution remain out of scope.
