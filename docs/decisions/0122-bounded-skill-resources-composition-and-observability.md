# 0122: Bounded Skill Resources, Composition, and Observability

- Status: Accepted
- Date: 2026-08-06
- Scope: Skill package resources, multi-Skill activation budgets, and REPL inspection

## Context

The first Skill slice loads one bounded `SKILL.md` and can narrow the current ToolSet, but it intentionally excludes package resources, composition limits, and current-Session inspection. Letting a model read arbitrary sibling files would bypass the Skill inventory identity, while unlimited retained Skill bodies could consume context and compound restrictions without a visible bound. Operator commands also need to distinguish packages currently installed from exact successful loads still retained in Effective Context.

## Decision

Skill packages may contain at most 64 resources outside `SKILL.md`, including nested directories. Resource paths are package-relative and use bounded safe segments; package escape, symbolic links, non-regular files, more than 128 directories, a resource larger than 64 KiB, or more than 256 KiB total resource bytes rejects the package. The Host enumerates and reads through no-follow directory descriptors, checks exact file identity before and after each bounded read, and records sorted path, byte count, UTF-8 readability, and a path-and-content fingerprint in the immutable inventory. Binary resources may be indexed but cannot be read into model context.

`skill_load` returns the complete instructions and bounded resource index. `skill_read_resource` is an isolated discovery/control call and requires the exact active Skill name and fingerprint, exact listed path and resource fingerprint, and the unchanged Turn-pinned inventory identity. The Host reloads and verifies the package before returning one complete bounded strict-UTF-8 resource in closed JSON. It never executes a resource, imports code, installs dependencies, grants permission, creates an Action Audit entry, or expands the ToolSet.

Up to four distinct Skill names may be active in Effective Context, up to four `skill_load` attempts are accepted in one Turn, and cumulative active instruction bodies may not exceed 65536 bytes. Duplicate active names are rejected. Active Skills are reconstructed only from complete successful Host `skill_load` ToolUse/ToolResult pairs retained in Effective Context, deduplicated by exact identity in causal order. Their `allowed-tools` restrictions intersect sequentially; Task, lifecycle, and discovery controls remain outside that action intersection. Compaction deactivates a Skill only by removing its exact load pair.

The REPL adds `/skills`, `/skills active`, `/skills list`, `/skills show <name>`, and `/skills doctor`. These Host-only commands inspect retained activation, instruction usage, resource counts, remaining action tools, current packages, roots, and catalog issues without a provider call, Session mutation, or Action Audit. Standalone `skills list|show|doctor` remains available for automation.

## Compatibility and contracts

Resource metadata changes inventory identity, so new snapshots use `skills-v2`; `skill-v1` manifest fingerprints remain unchanged. New full and compacted Effective Context representations are v13/v14 and require `skills-v2`, while legacy v11/v12 continue to validate only `skills-v1`. The canonical tool order adds `skill_read_resource` immediately after `skill_load`; built-in Registry generation advances to 4, system prompt to v39, and provider adapter contract to v40. Existing Session ToolUse/ToolResult and compaction record schemas are sufficient, and legacy compacted v12 records remain readable.

## Explicit non-goals

- resource execution, dynamic imports, dependency installation, binary decoding, templates rendered by the Host, or implicit resource loading;
- automatic Skill selection, unload commands, manual activation that fabricates model history, or activation inferred from prose and compact summaries;
- permission expansion, MCP promotion, background loading, hot mutation inside a Turn, or a marketplace/package manager.

## Verification

Deterministic tests cover nested text and binary indexing, package symlink rejection, resource fingerprints and drift, exact active-resource reads, binary rejection, multi-Skill action intersection, duplicate and active-count rejection, per-Turn load and cumulative instruction limits, legacy Effective Context identity validation, canonical provider schemas, and read-only REPL/standalone inspection. The complete offline release gate remains required.
