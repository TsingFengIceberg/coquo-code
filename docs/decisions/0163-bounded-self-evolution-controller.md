# ADR 0163: Bounded Self-Evolution Controller

## Status

Accepted

## Context

Coquo already records durable Session, Task, Child, Team, memory, Skill, and
Provider observations, and it already has deterministic offline Eval runners.
Those pieces did not form a controlled improvement loop.  Automatically
changing a running Agent's prompt, tools, permissions, sandbox, retry policy,
or AgentLoop would weaken the Host authority boundary.

## Decision

Add a local Host-owned `EvolutionController` backed by the bounded append-only
`.coquo/evolution/events.jsonl` log.  It implements nine explicit stages:

1. record content-bounded, provenance-bearing traces;
2. assess outcomes with deterministic or named grader facts;
3. group repeated target/outcome/summary patterns;
4. create versioned candidates for memory, Skill, Prompt, or Workflow;
5. reject possible secrets and protected runtime-boundary changes;
6. compare baseline and candidate metrics on independent validation and test
   set identifiers;
7. require evaluation success and a later explicit approval before activation;
8. record bounded active-candidate observations and support rollback;
9. deprecate and archive inactive candidates under an operator-supplied cutoff.

The persistent Host switch is `off | propose | supervised`.  `off` records
traces only; the other modes permit quarantined candidate creation.  No mode
automatically activates a candidate, and candidate text, trace summaries,
grader labels, and metrics remain untrusted task data.  Candidate versions are
monotonic per target, only one candidate per target is active, and rollback
restores the newest prior evaluated, safety-passed candidate when one exists.

The controller is an audit and release surface, not a second AgentLoop.  It
does not invoke a model, execute tools, alter Session transcripts, grant
permissions, or mutate Provider, sandbox, ToolSet, Child, Team, or workflow
runtime policy.  Model-visible system prompt and tool contracts are unchanged.

## Compatibility and recovery

Evolution state is workspace-local and independently replayable.  Unknown,
oversized, malformed, or over-limit events fail closed.  Existing memory,
Skill, Session, Task, and Eval stores remain authoritative for their own data;
the controller stores only references and bounded summaries.  A candidate is
never considered active merely because a model or grader claims success.

## Verification

Unit tests cover default-off behavior, trace assessment and pattern grouping,
provenance, independent-set enforcement, secret and protected-boundary
rejection, evaluation gating, activation, observation, rollback, and archive
lifecycle.  CLI smoke coverage exercises the same lifecycle through
`coquo evolution` without a Provider call.
