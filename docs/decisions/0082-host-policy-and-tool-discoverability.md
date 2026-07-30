# 0082: Host Policy and Tool Discoverability

- Status: Accepted
- Date: 2026-07-30
- Scope: make canonical tool and permission policy facts easier to inspect without changing runtime authority

## Context

The initial Host workbench catalog showed all tools and current availability, but users still had to consult source or long-form documentation for one tool's arguments and major hard boundaries. Permission and approval remained visible only as current status rather than as the actual action-by-action `PermissionGate` matrix. Static slash completion stopped at common second-level commands, and obvious typing mistakes produced only generic unknown-command output.

## Decision

`/tools catalog <tool-name>` resolves only exact names from canonical `TOOL_CATALOG`. It renders the canonical position and input-schema argument shapes together with the established permission class, current policy availability, and one bounded Host-maintained hard-bound summary. This is documentation over trusted local contracts, not tool execution or a substitute for runtime validation. Existing `/tools catalog`, `/tools`, and `/tools details` semantics remain unchanged.

`/permissions` evaluates the production pure `PermissionGate` for workspace read, create, overwrite, move, delete, and dangerous actions. With no arguments it uses current process policy. Optional permission mode and approval mode create a read-only preview labelled as not applied. Sandbox dependency readiness is shown as a separate execution prerequisite and never changes the policy decision. `/help policy` consolidates the permission ceiling, approval interaction, and mandatory command-sandbox relationship. These commands cannot mutate policy, grant approval, invoke a tool, call a provider, or write Session state.

Slash completion adds exact candidates for canonical tool names, permission-mode and approval-mode pairs, Action Audit status/tool filters, and existing common subcommands. Unknown top-level commands, provider/session subcommands, and tool names use one bounded `difflib.get_close_matches` result at a conservative threshold. Suggestions are presentation text only; the Host never rewrites, resubmits, or dispatches the candidate.

## Compatibility

This is Host-only discovery behavior. The canonical system prompt remains v22, provider adapter contract remains v25, and the model-visible 21-tool names, order, descriptions, schemas, ToolArguments v1, PermissionGate decisions, approval identities, Action Audit records, Session records, compaction records, and Effective Context representations and identities remain unchanged.

## Invariants

- Tool detail accepts only an exact canonical tool name and performs no tool action.
- Permission previews use the real PermissionGate but never install their selected mode or approval value.
- Auto approval remains independent from capability and never bypasses hard tool checks.
- Suggestions require one close bounded candidate and never alter or execute user input.
- Slash inspection output never enters model history.
- Every canonical tool must have one Host hard-bound summary, enforced by deterministic catalog coverage.
- Real ProjectSession discovery leaves transcript bytes, history, usage, Action Audit, and Session metadata unchanged.

## Non-goals

- changing policy during a REPL;
- granting reusable approvals or authorizing future actions;
- replacing tool implementation errors with catalog text;
- fuzzy tool dispatch, command aliases, autocorrection, or shell completion;
- changing model-visible tool descriptions or schemas.
