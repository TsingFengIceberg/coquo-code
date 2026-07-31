# 0095: Model-visible Task Coordination Tools

## Status

Accepted.

## Context

ADR 0094 established a proposal-only boundary between model requests and the Host-owned durable Task state machine, but deliberately exposed no concrete model tool and left Task Stages dependent on terminal text markers such as `TASK_PLAN_JSON`, `TASK_REFLECTION_JSON`, and `TASK_COMPLETION_PROPOSAL`. Text parsing is weaker than a closed tool schema, does not give provider adapters one uniform representation, and makes it harder to bind a recovered proposal to an exact successful tool call.

The model must be able to propose plans, reflections, blockers, and completion without generating human `/task` commands or receiving authority to mutate Task state directly. Planning and reflection also need narrower capabilities than execution. Any integration must preserve ordinary AgentLoop causality, Session-first durability, Task acceptance, PermissionGate, Action Audit, Stage budgets, and compatibility with already committed text-protocol Stages.

## Decision

The canonical catalog adds four proposal-only tools after the existing 21 ordinary tools:

1. `task_propose_plan(steps)` submits 1-32 bounded Stage objectives.
2. `task_report_reflection(recommendation, summary, next_objective)` submits one closed reflection recommendation.
3. `task_report_blocker(category, summary)` records a bounded information, permission, human-evidence, external-condition, or other blocker.
4. `task_propose_completion()` states only that the current Task appears complete.

These tools are not filesystem Actions. They receive no Action lease, do not pass through PermissionGate, and create no Action Audit record. They still use ordinary ToolUse/ToolResult causality, consume the shared Turn request budget, appear in the durable Session transcript and Host tool ledger, must be the only call in their assistant response, and force the continuation to final text only.

Ordinary prompts expose only the original 21 tools. Task Stage exposure is exact and preserves canonical order:

- planning exposes bounded read/Git observation tools plus `task_propose_plan` and `task_report_blocker`;
- reflection exposes only `task_report_reflection` and `task_report_blocker`;
- execution and correction expose all 21 ordinary tools plus `task_report_blocker` and `task_propose_completion`.

The Host binds each control call to the active Task ID, Stage ID and number, pinned Effective Context ID, tool-use ID, and canonical arguments. The proposal sink only retains this pending immutable value. Durable application order is `stage_started`, complete Session Turn append and fsync, `stage_committed`, then the corresponding Task proposal record. A provider, dispatch, Session commit, or Stage commit failure cannot publish a durable Task proposal.

New plan, reflection, completion, and blocker records preserve the proposal tool-use ID. IDs are unique across the complete Task transcript. Reapplying the same ID and same canonical payload is idempotent; a changed payload or a different proposal for the same Stage is rejected. A blocker makes the current Task blocked and stops the foreground Driver with `model-blocked`, but grants no permission, supplies no missing evidence, and does not terminate or complete the Task.

Recovery reads the exact committed Session Turn rather than assistant claims. It accepts one proposal only when the Stage-linked Turn contains one terminal compatible control call and the durable Host ledger records the same tool-use ID and name as succeeded. If no structured control call exists, the historical terminal text protocols remain readable for already committed and interrupted Stages. New Task framing instructs models to use the structured tools and not emit those legacy markers.

## Compatibility And Versions

The canonical system prompt advances to v27 because Task Stage behavior and tool instructions are model-visible. The provider adapter contract advances to v28 because Anthropic and OpenAI-compatible count/create projections now include four additional closed schemas and exact Stage subsets.

The complete canonical catalog contains 25 definitions, while ordinary ProjectSession prompts continue to expose 21. Effective Context representations remain `ctx-v5` and `ctx-v6`; the catalog content changes the current identity rather than introducing another representation version. The no-project-instructions empty full-context identity becomes `ctx-v5-63362449120e69a39d2a03b22c8c1937ee66d2fd67d065d4e3ccfd3466d88aa7`.

New records use `task_plan_proposed` schema v3, `task_completion_proposed` schema v2, `task_reflection_recorded` schema v2, and `task_blocker_recorded` schema v1. Plan v1/v2, completion v1, and reflection v1 remain readable without transcript rewriting. ToolArguments v1, Session records, Action Audit records, compaction records, Task Stage records, ordinary 8/32/24 budgets, acceptance policy, and workspace hard boundaries remain unchanged.

## Consequences

- Models can participate in durable Task coordination through typed proposals without operating human slash commands.
- Planning and reflection have least-capability tool surfaces, while execution continues to reuse the ordinary tool and permission path.
- Task proposal durability is downstream of the complete committed Session Turn and exact Stage evidence.
- Structured blockers are visible and resumable facts but cannot manufacture authority or acceptance.
- Historical text-protocol Task Stages remain recoverable, while new model-visible behavior uses closed tool schemas.
- Task creation from an ordinary prompt, background execution, automatic reviewer spending, parallel Stages, subagents, and model control over Task lifecycle commands remain out of scope.
