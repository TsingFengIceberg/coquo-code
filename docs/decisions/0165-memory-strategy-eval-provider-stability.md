# ADR 0165: Bounded Memory and Strategy Evolution, Eval Runs, and Provider Stability

## Status

Accepted as one bounded implementation slice.

## Context

The Host already records content-free evolution traces and provides a complete
Workflow-to-Skill lifecycle.  Memory, Prompt, and Workflow targets could only
be proposed manually, while deterministic Eval cases had no durable run
registry or baseline/candidate gate.  Provider rounds exposed stream timing,
but long-running acceptance could not aggregate repeated attempts or separate
upstream waiting from local presentation backpressure.

## Decision

`MemoryEvolutionService` mines at least three repeated successful Memory traces
into a quarantined Memory candidate.  It links the candidate to the durable
Memory record through an append-only `memory_link` event and reuses the generic
EvolutionController for safety, independent validation/test metrics, approval,
activation, observation, rollback, and archival.  Activation confirms the
Memory record only after the Evolution candidate is activated; an uncertain
failure is reported and the candidate is rolled back where possible.  Memory
content remains untrusted evidence and cannot grant authority.

`StrategyEvolutionService` provides the same bounded mining and lifecycle for
Prompt and Workflow strategy candidates.  Generated text is declarative and
cannot change the canonical system prompt, ToolSet, PermissionGate, sandbox,
Child/Team controls, AgentLoop, or provider contracts.  A committed Host Turn
records independent Workflow, Memory, and Prompt traces; candidate generation
is best-effort telemetry and never changes Session commit truth.

`EvolutionStore` keeps event schema version `1` separate from candidate content
version by writing `candidate_version` for candidate records while accepting
legacy candidate events whose version was `1`.  This prevents a second
candidate version from making the event log unreadable.

`EvalPlatform` registers immutable bounded datasets with disjoint validation
and test splits, converts existing Host Eval output into closed `EvalGrade`
facts, persists content-free `EvalRun` metadata under `.coquo/evals/runs.jsonl`,
and compares baseline/candidate runs by per-case score, pass state, suite pass
rate, and mean score.  `gate()` fails closed on any regression or missing case;
the platform never invokes a Provider and does not store prompts, responses,
paths, or credentials.

`StreamSample` and `ProviderSoakReport` provide a deterministic local contract
for repeated real-provider acceptance.  Samples are classified as not-streamed,
first-delta wait, inter-delta gap, or healthy, without claiming a particular
network or vendor cause.  The acceptance script accepts at most eight explicit
repetitions and reports the bounded aggregate.  `FrontendEventQueue` records
content-free enqueue, drain, high-watermark, and blocked-put counts; the TTY
warns once when queue backpressure is observed.  Provider deltas still flush as
they arrive, and a Host queue warning never fabricates an upstream chunk.

## Security and recovery boundaries

- Evolution remains default-off and all generated data is untrusted.
- Memory activation and strategy activation require independent Eval success,
  static safety, and explicit approval.
- No generated candidate changes permissions, sandbox limits, tool schemas,
  provider retry policy, recursive delegation, or durable Session facts.
- Eval and soak reports are bounded and content-free beyond closed check names.
- Existing provider retry, cancellation, no-replay-after-delta, and Session
  atomic-commit rules remain unchanged.

## Verification

Deterministic tests cover repeated-trace thresholds, quarantine, lifecycle
rollback, event-version compatibility, Eval persistence and regression gates,
stream classification, soak aggregation, queue backpressure metrics, and CLI
surfaces.  The normal release gate remains `pytest`, Ruff, format, `uv lock
--check`, and `git diff --check`; real-provider soak remains an explicitly
authorized manual operation.

