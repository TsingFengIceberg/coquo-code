# 0090: Structured Task Acceptance and Independent Review

- Status: Accepted
- Date: 2026-07-31

## Context

ADR 0089 deliberately made model completion a proposal and required human evidence before a Task could close. A free-form string contract is durable and understandable, but it cannot tell the Host which facts are deterministic, which files an independent reviewer may inspect, or which verification source is authorized to satisfy a criterion. Treating reviewer prose or a test command as ordinary executor evidence would also let the same model path grade its own claims without a separate context and would risk allowing verification commands to modify the workspace.

## Decision

New Tasks may append one record-local schema-v1 `task_acceptance_contract` before their first Stage. It defines at most 16 bounded criteria with a `human`, `path-exists`, `path-unchanged`, `command-succeeds`, `action-audit-certain`, or `independent-reviewer` kind, plus a `manual` or `auto-verified` completion policy. The existing header retains the criterion descriptions for stable discovery. Header-only Tasks replay unchanged as `human + manual`; no legacy transcript is rewritten.

Each kind has exactly one authorized verification source. Human criteria accept only explicit `/task verify` evidence. Path, digest, command, and Action Audit criteria accept only `/task verify host` observations. Reviewer criteria accept only `/task review` verdicts. Every attempted Host or reviewer check appends a schema-v1 `task_acceptance_checked` fact with `passed`, `failed`, `needs-human`, or `error`; only a passed check appends the existing acceptance-verification record with its matching source. All check and verification records remain bound to the current completion Stage, so later Stage work invalidates them for completion without deleting history.

Deterministic Host verification uses no model. `path-exists` performs bounded no-follow type observation. `path-unchanged` compares the current bounded regular-file SHA-256 with the baseline captured when the Task is created. `action-audit-certain` rejects any uncertain lifecycle in the owner Session. `command-succeeds` reuses `RunCommandTool`, bubblewrap, seccomp, timeout, output, environment, and process-cleanup constraints, but mounts the workspace read-only. A sandbox that cannot be established fails closed; the verifier never falls back to direct Host execution.

Independent review reuses the currently selected provider endpoint, credential configuration, and model route, but not the executor's Session history. The Host sends one dedicated `ConversationRequest` with a separate versioned reviewer system prompt, `allow_tools=false`, the Task objective, structured criteria, bounded Host facts, and only the regular-file paths explicitly declared by reviewer criteria. `.git`, `.leonervis-code`, and `.env*` components are forbidden. The response must be one exact JSON object containing every requested criterion once with `passed`, `failed`, or `needs-human`; malformed responses append error checks but no verification. Review token usage is tracked separately from ordinary Turns and compaction and is not added to the executor Session transcript.

`manual` preserves ADR 0089: after all current criteria are verified, the user still runs `/task complete`. `auto-verified` lets the Host append `completed` only when the latest committed execution Stage has a current model completion proposal and every criterion has matching current verification. Neither the model proposal nor a reviewer verdict can grant permission, approve an Action, execute a tool, or bypass Task/Turn budgets.

## Compatibility

`task_header`, Task Stage v1/v2, Session records, Action Audit, ToolArguments, all 21 tool schemas, and provider adapter contract v26 remain unchanged. New `task_acceptance_contract` and `task_acceptance_checked` records use schema v1; `task_acceptance_verified` remains schema v1 and expands its closed source enum. The canonical Task Stage JSON now includes the structured contract, completion policy, and verification sources. The canonical system prompt advances to v25, so the empty no-instructions full-context identity changes while Effective Context representation remains `ctx-v5`/`ctx-v6`.

## Invariants

- A completion proposal is necessary but never sufficient for Task completion.
- A criterion can be satisfied only by its declared verification source.
- Verification and checks are causally bound to the current completion Stage.
- Reviewer context is separate, no-tools, path-allowlisted, and absent from executor Session history.
- Host command checks are workspace-read-only and fail closed without bubblewrap and seccomp.
- `auto-verified` changes only the final Host transition; it grants no Action authority.
- Legacy Tasks replay without rewriting and retain human/manual behavior.

## Non-goals

- a second credential, vendor, or model requirement for review;
- multi-agent debate, reviewer tool use, hidden repository exploration, or general codebase indexing;
- arbitrary shell predicates, writable test setup, network checks, flaky-test retries, or background verification;
- semantic proof that a reviewer is unbiased merely because its context is separate;
- automatic planning, autonomous Task creation, unattended Stage continuation, or workflow scheduling.
