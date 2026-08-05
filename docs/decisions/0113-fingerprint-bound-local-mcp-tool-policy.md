# 0113: Fingerprint-bound Local MCP Tool Policy

## Status

Accepted.

## Context

Classifying every MCP tool as `dangerous` is conservative but prevents a confined, genuinely read-only local tool from running in `read-only` mode. MCP annotations cannot safely grant authority: they are supplied by the executable being governed and do not bind configuration, protocol, or schema identity. A trust decision therefore needs a separate user-owned store and exact stale detection.

## Decision

Add schema-v1 user and project policy stores at `${XDG_CONFIG_HOME:-~/.config}/leonervis-code/mcp-tool-policies.json` and `.leonervis-code/mcp-tool-policies.json`. They use separate scope locks, symlink-safe path checks, bounded strict JSON, revision compare-and-swap, mode-0600 atomic replacement, and cross-scope qualified-name collision rejection. A rule stores no credential or server prose.

Each rule binds the qualified tool name, configured server name, server scope, configuration revision, remote tool name, negotiated protocol version, exact input-schema fingerprint, selected permission action, and policy revision. The only allowed actions for confined local stdio are `workspace-read` and `dangerous`. Exact matching yields `applied`; no rule yields `default`; any mismatch yields `stale` and falls back to `dangerous`. Titles, descriptions, annotations, output schemas, notifications, and result content never participate in authority.

Policy identity participates in quarantine catalog configuration identity. Candidate identity records the sanitized disposition, effective action, and policy revision. The effective action becomes the accepted contract's single `permission_actions` value, so a change propagates through contract, Registry, ToolSet, Effective Context, ActionLease, ActionIdentity precondition, catalog, and process-generation identity. The process sandbox remains read-only and socket-denied regardless of policy.

Standalone `mcp policy list|show|set|clear` manages rules. `set` first probes the current accepted quarantine catalog, resolves an exact qualified candidate, and requires the caller-supplied schema fingerprint to match before writing. It cannot create a rule for a guessed, rejected, missing, or stale candidate. `--replace` and `--if-revision` preserve explicit mutation and CAS semantics.

## Compatibility And Versions

The canonical system prompt advances from v35 to v36 with fingerprint `v36-0ab649c44e73ce244ef761512272188dd4540f46ed5243bcd61c2bbf63d9815d`. The empty full-context identity becomes `ctx-v9-97c4e14f393e36bfc0f7b17f6715ca84a0dde30771a46fd81da434b08f538693`; Effective Context representations remain `ctx-v9` and `ctx-v10`. Provider adapter remains v38 because wire schemas do not change. Existing Session, Task, Action Audit, Profile, MCP server configuration, Extension Contract, Registry, ToolSet, ToolArguments, ActionIdentity, ApprovalPreview, and compaction representations remain unchanged. The new policy file is independent and requires no transcript migration.

## Non-goals

- inferring network, write, destructive, or credential authority from schemas or annotations;
- per-argument policies, wildcard rules, executable signing, remote MCP trust, or organization-wide policy distribution;
- weakening sandbox, workspace, symlink, timeout, result, cleanup, approval, audit, or frozen-ToolSet boundaries;
- automatically promoting a trusted tool or treating policy as task-completion evidence.

## Consequences

- A user can explicitly run one exact confined read-only MCP contract under `read-only` while every missing or stale rule remains `dangerous`.
- Schema, protocol, server revision, or scope changes revoke the downgrade automatically through stale fallback.
- Permission authority remains Host-owned and inspectable instead of being delegated to untrusted MCP metadata.
