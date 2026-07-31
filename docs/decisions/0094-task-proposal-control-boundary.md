# 0094: Task Proposal Control Boundary

## Status

Accepted.

## Context

Durable Tasks are Host-owned state machines above ordinary AgentLoop Turns. Existing `/task` commands are human/operator controls, while model participation currently relies on bounded Task framing and terminal text protocols. A future model-facing Task surface must not make the model generate slash commands, dispatch Task coordination through filesystem Action handling, mutate `TaskStore` before its owning Session Turn commits, or expose every ordinary tool during planning and reflection.

Claw-Code provides a useful interaction reference by exposing Task and Agent tools directly to the model and by selecting tool subsets for specialized agents. Its reviewed Task registry and Agent runner are separate in-memory paths, however, so Leonervis retains its existing durable Task/Stage/Turn/Action hierarchy and introduces only a narrower proposal boundary.

## Decision

Human `/task` commands remain operator adapters. The foreground Driver remains a Host orchestration adapter. Future model Task tools enter a separate proposal adapter. All three may reuse Task validation and state-machine rules, but a model proposal is never an operator command, permission grant, acceptance fact, Driver instruction, or direct `TaskStore` mutation.

`ConversationRequest` gains an optional immutable `enabled_tool_names` tuple. `None` means the complete canonical catalog when tools are allowed; an explicit tuple selects an exact validated subset while provider projection preserves global canonical order. A disabled request cannot carry a subset. `PreparedAgentTurn` pins the subset with its existing Effective Context snapshot, and AgentLoop rejects any provider request for a tool outside that set before dispatch. Anthropic count/create and OpenAI-compatible estimate/create projections use the same exact subset.

AgentLoop gains a dedicated Task-control dispatcher separate from `ActionDispatcher`. A registered Task-control call must be the only tool call in its assistant response, requires a proposal sink, and forces the next provider invocation to be text-only. It still participates in ordinary tool-use/result causality, the shared Turn budget, the durable Session transcript, and the Host tool ledger, but it does not receive an Action lease or create an Action Audit merely for proposing Task coordination.

The internal immutable `TaskControlProposal` binds one closed proposal kind to canonical Task ID, Stage ID and number, the pinned Effective Context ID, exact tool-use ID, and bounded canonical `ToolArguments`. A successful Task-control dispatch must return exactly one matching proposal; an unsuccessful result cannot carry one. AgentLoop publishes that proposal to the Host sink only after the complete Session Turn commit succeeds. A commit failure publishes nothing.

Recovery does not trust assistant prose or ToolResult content. Given an already committed Turn and the Host-known Task/Stage/context binding, it reconstructs a proposal only when the Turn contains exactly one matching Task-control call and the durable Host tool ledger records that same tool-use ID and name as succeeded.

This slice deliberately adds no public Task tool definition. Concrete plan, reflection, blocker, and completion tools remain later slices, so ordinary CLI and model behavior stay unchanged while their shared lower boundary becomes testable.

## Compatibility And Versions

The provider adapter contract advances to v27 because ordinary provider requests can now project an exact canonical tool subset. The canonical system prompt remains v26 after review because no new model-visible tool or Task behavior is enabled. The 21-tool catalog, canonical order and schemas remain unchanged.

Effective Context representations remain `ctx-v5`/`ctx-v6`. The complete canonical catalog still participates in committed context identity; a request-local subset is pinned execution policy, like the existing final text-only projection, and does not rewrite historical context identity. ToolArguments v1, Session records, Task records, Action Audit, compaction records, ordinary 8/32/24 budgets, permission semantics, and existing transcripts remain unchanged.

## Consequences

- Models will be able to enter a structured Task proposal path without generating `/task` commands or receiving operator authority.
- Planning, reflection, execution, and review can later receive different exact tool sets without creating a second AgentLoop.
- A Task-control proposal cannot be mixed with sibling calls or followed by more actions in the same Turn.
- Proposal publication follows Session durability, and recovery requires Host ledger evidence rather than model claims.
- Concrete Task tools, Task-record mutation, Driver migration, ordinary-prompt Task creation, and terminal UX changes remain out of scope.
