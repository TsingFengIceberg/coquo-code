# ADR 0126: Durable Hook Observation and Audit

- Status: Accepted
- Date: 2026-08-07

## Context

ADR 0125 introduced one frozen declarative `before_action_authorization` policy event. That boundary can prevent or tighten an action, but it cannot observe the terminal action outcome, explain which Turn or Task lifecycle rules matched, or prove after restart which exact HookSet was evaluated. A separate free-form Hook log would weaken atomicity and make Session and Task state disagree with its audit trail. Executable post-action handlers would also create a second side-effect path outside PermissionGate, Action Audit, sandboxing, and Tool hard constraints.

## Decision

Advance Hook configuration and HookSetSnapshot to v2. Retain strict v1 configuration reads by decoding absent `action_outcomes` as an empty matcher and write only v2 thereafter. Add `after_action`, `turn_committed`, `turn_failed`, `task_stage_started`, `task_stage_committed`, `task_stage_failed`, `task_blocked`, and `task_terminated`. Only `before_action_authorization` may use `deny` or `require_ask`. Every observation event supports only `continue` or `advisory`; lifecycle events reject all action matchers, while `after_action` may additionally match one or more closed terminal outcomes.

Represent each evaluation as a bounded immutable `HookAuditEntry`: event, exact HookSet ID, safe subject ID, canonical matched Hook IDs and effects, deterministic aggregate result, and only content-free action metadata when applicable. Hook messages, Tool arguments, file content, credentials, callback bodies, and arbitrary result content are excluded. Action entries and one terminal Turn entry are embedded in `turn_committed` v10 or `turn_failed` v3. The five observed Task record types embed their matching ledger in `stage_started|committed|failed` v3 and `task_blocker_recorded|task_terminated` v2. Older versions remain readable and may contain no Hook audit.

Successful action evaluations are retained in the candidate Turn and committed with its final Turn record. If the Turn fails after actions ran, ProjectSession carries those content-free entries into `turn_failed` before appending its final failure observation. Task lifecycle evaluation uses one exact current Hook snapshot per durable transition and is appended atomically with that Task record. Advisory text may be exposed through a typed transient `HookLifecycleObserved` terminal event, but the text is not copied into the audit ledger. After-action advisory text may be appended to the model-visible ToolResult without changing the authoritative Tool or Action Audit outcome.

Provide read-only bounded projections through standalone `hooks evaluations [session]` and `hooks task <task-id>`, plus REPL `/hooks evaluations [count]` and `/hooks task <task-id> [count]`. These commands strictly replay existing records, expose no Hook messages or Tool arguments, invoke no provider, and mutate no Session, Task, Action Audit, or configuration. `hooks add` accepts an explicit event and optional after-action outcome matcher; new rules remain disabled by default.

Advance the system prompt to v42 and provider adapter contract to v43 because the model-visible Hook boundary changes. HookSet identities become `hooks-v2`. Full and compacted Effective Context become v19/v20 and retain v17/v18 as strict legacy Hook-v1 representations. The Tool Registry remains generation 5 because no model-visible Tool schema is added.

## Consequences

- Hook evaluation facts survive restart at the same durability boundary as the Turn or Task transition they describe.
- Observation cannot alter an authoritative action result, grant permission, approve an action, retry work, or execute a callback.
- Configuration changes between lifecycle transitions are explicit because each audit entry records its exact HookSet identity.
- Read-only inspection is bounded and content-free; operational messages remain transient or in their normal ToolResult history rather than being duplicated into audit metadata.
- Shell, HTTP, model, subagent, background, result-rewrite, argument-mutation, automatic-retry, and rollback Hook handlers remain out of scope.
