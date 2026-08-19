# ADR 0148: Bounded Recursive Child Delegation and Host-owned Workflow Orchestration

## Status

Accepted for the conservative multi-agent expansion after deterministic
recursive Child, Task bridge, workflow, replay, and prompt-contract tests.

## Decision

Leonervis permits one explicitly bounded recursive read-only capability. A
Host Session may delegate a depth-one read-only Child with the fixed
`read-only-explorer-v1` capability. That Child may create at most one depth-two
Grandchild. A depth-two Grandchild cannot delegate again. The Host remains the
owner of Child admission, permission ceilings, budgets, cancellation, durable
state, execution leases, handoff delivery, and recovery.

Recursive lineage is recorded in the Child and Session ledgers. Depth-two
delegation uses record-local schema v2 and carries both
`parent_child_run_id` and `root_child_run_id`; legacy depth-one records remain
readable. The recursive role prompt has its own contract version and
fingerprint. Its enabled model-visible tools are the fixed read-only Child
ToolSet plus the Host-provided Child controls. The Grandchild receives only the
fixed read-only ToolSet and no Child controls.

No Child, including a recursive Child, receives Task, Team, Skill, Hook, MCP,
write, shell, network, or integration controls. A role prompt or handoff is
untrusted task evidence and cannot grant capability. A depth, lineage,
capability, prompt fingerprint, owner, or schema mismatch fails closed.

The Host also exposes a high-level workflow skeleton with the fixed roles
Architect, Explorer, Executor, Reviewer, and Integrator. It reuses the existing
Task and Task–Child–Team bridge identities rather than duplicating their
execution or ledgers. The Host advances the phases and persists a bounded
workflow snapshot. Explorer, Executor, and Reviewer packets are typed evidence
and must be marked untrusted. Reviewer `passed` advances to integration,
`rejected` requires rework, and `unknown` enters recovery-required. Accepting
integration is an explicit Host decision only; the workflow never writes files,
merges, commits, pushes, invokes a Provider, or silently creates a Child, Team,
or Task.

## Compatibility and recovery

Existing depth-one Child records and ordinary read-only role prompts remain
valid. New recursive admission is represented by the new role contract and
lineage fields without rewriting old JSONL. Unknown process outcomes, missing
handoffs, invalid evidence, stale identities, failed durable saves, or workflow
phase inconsistencies stop progress and require Host recovery. No automatic
retry, cleanup, merge, commit, or push is implied.

## Consequences

The feature is observable through Child Run depth, parent/root lineage,
role-prompt fingerprints, enabled tool lists, separate Child Run handoffs, and
workflow phase/verdict inspection. The design intentionally stops short of a
general autonomous agent tree or automatic role scheduler; those require a
separate decision with stronger resource, cancellation, and recovery evidence.

## Verification

Deterministic tests cover recursive prompt/spec identity, schema-v2 lineage,
root Child control exposure, Grandchild control removal, one-level recursion,
legacy replay, workflow phase transitions, untrusted Reviewer evidence, and
recovery-required outcomes. The full repository release gate remains the
authoritative completion check; no real Provider, network, credential, commit,
or push operation is part of this ADR.
