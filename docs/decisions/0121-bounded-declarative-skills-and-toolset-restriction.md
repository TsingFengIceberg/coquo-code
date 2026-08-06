# 0121: Bounded Declarative Skills and ToolSet Restriction

- Status: Accepted
- Date: 2026-08-06
- Scope: Skill package format, source inventory, model discovery, instruction lifetime, and tool restriction

## Context

Leonervis already freezes provider-visible tools per Turn and progressively promotes MCP contracts, but it had no reusable procedural guidance package. Loading arbitrary code, scanning compatibility directories, or treating Skill metadata as permission would collapse the established separation between model guidance, Host policy, and execution. A useful first Skill boundary therefore needs deterministic source identity, bounded text loading, explicit model discovery, and restrictions that can only reduce existing authority.

## Decision

One Skill is exactly `<name>/SKILL.md` with strict UTF-8/LF YAML frontmatter and a nonblank bounded Markdown body. Manifest v1 accepts only `manifest-version`, `name`, `description`, and optional `allowed-tools`. Names match `[a-z][a-z0-9-]{0,63}` and must equal the package directory. Loading uses `yaml.safe_load`, rejects symbolic links, unknown fields, malformed YAML, CRLF, drift, and file/frontmatter/inventory bounds, and fingerprints the exact normalized metadata plus complete body.

The Host loads exactly three roots without ancestor or compatibility scanning: workspace-local `.leonervis-code/skills`, project-shared `.agents/skills`, and user `${XDG_CONFIG_HOME:-~/.config}/leonervis-code/skills`. Priority is workspace-local, project-shared, then user. Lower-priority same-name packages remain visible as shadowed candidates. `skills list`, `skills show`, and `skills doctor` inspect this inventory without a provider call, Session creation, or Action Audit.

Every prepared Turn pins one immutable SkillInventorySnapshot, and its identity participates in Effective Context and ActionLease identity. `skill_search` searches only active name and description metadata in that snapshot. `skill_load` requires an exact same-Turn discovered name and fingerprint, reloads the inventory before returning content, and stale-rejects any change. Both calls must be isolated in their assistant response. A successful load returns a closed JSON ToolResult containing source, fingerprint, optional allowed tools, and the complete bounded instructions without an absolute path.

Loaded instructions are procedural untrusted data, not system authority. Their lifetime follows ordinary causal history: the complete `skill_load` ToolUse/ToolResult pair remains effective while retained in Effective Context. Cross-Turn restrictions are derived only from successful Host-produced load results still present in effective history. A compaction summary mentioning a Skill cannot reactivate it after the pair is removed.

Missing `allowed-tools` inherits the current ToolSet. An empty list removes all ordinary Host and MCP action tools. A nonempty list intersects only action tools already present. Task, lifecycle, and discovery controls remain available, and restriction cannot promote MCP, add an unavailable tool, grant permission, approve an action, enable search, or bypass sandboxing and audit. A restriction creates a later immutable ToolSet epoch and replacement ActionLease under the existing transition boundary.

## Compatibility and contracts

The canonical tool order adds `skill_search` and `skill_load` after `tool_search` and `tool_promote`. Built-in Registry generation advances to 3, system prompt advances to v38, and provider adapter contract advances to v39. Effective Context full and compacted representations advance from v9/v10 to v11/v12 by adding `skill_inventory_id`; legacy versions remain valid for replay. Skill ToolUse and ToolResult use the existing Session turn schema, so no Session record migration is required. PyYAML is the only new runtime dependency and is used solely through `safe_load`.

## Explicit non-goals

- dynamic Python imports, executable Skill code, package installation, hot reload inside a Turn, or marketplace behavior;
- Skill resources, forked/subagent execution, automatic selection, or implicit activation from assistant prose or compact summaries;
- ancestor directory scanning or compatibility aliases for other agents' Skill roots;
- allowing Skill metadata to promote tools, weaken permissions, or become system/project authority.

## Verification

Deterministic tests cover source precedence and shadow visibility, strict format and symlink rejection, bounded CLI inspection without Session creation, canonical tool schema projection, same-Turn search/load binding, complete instruction persistence, ToolSet intersection, replacement epochs, cross-Turn restriction replay, and removal when compaction excludes the exact load pair. The complete offline release gate remains required.
