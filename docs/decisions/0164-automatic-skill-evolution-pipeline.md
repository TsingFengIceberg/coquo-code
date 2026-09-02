# ADR 0164: Automatic Workflow-to-Skill Evolution Pipeline

## Status

Accepted

## Context

ADR 0163 introduced a Host-owned evolution control plane, but a generic
candidate record alone did not make the requested Skill self-evolution path
observable or discoverable. Repeated successful workflows need a deterministic
path from committed evidence to a reusable Skill without allowing model output
to grant tools, change permissions, or affect the Turn that produced the
evidence.

## Decision

Add `SkillEvolutionService` as a Host-only orchestration layer for the complete
bounded Workflow-to-Skill lifecycle:

1. A successfully committed Host workflow records a bounded `EvolutionTrace`
   containing only ordered `tool:outcome` facts and provenance.
2. Repeated traces are grouped by normalized summary and exact workflow
   sequence. At least three successful traces are required by default.
3. The Host deterministically derives a versioned workflow fingerprint and a
   declarative Skill candidate with trace provenance and an `allowed-tools`
   intersection.
4. The generated package is stored under `.coquo/skill-candidates/v1/` and
   remains quarantined; it is not part of the active Skill inventory.
5. Static checks reject secrets, protected runtime instructions, invalid tools,
   missing tool restrictions, and oversized content. Candidate and safety facts
   remain untrusted evidence.
6. Evaluation compares caller-supplied baseline and candidate metrics using
   independent validation and test-set identifiers. The controller does not
   claim to have run a benchmark that the Host did not run.
7. A candidate must pass safety and evaluation and then receive explicit human
   approval before activation. Generic `skills install` cannot bypass this
   gate.
8. Host installation writes the exact package and lock through the existing
   Skill import boundary. The current prepared Turn keeps its frozen inventory;
   only a later Turn can discover the new package with `skill_search` and load
   it with the exact returned fingerprint.
9. Usage observations update bounded metrics. An active candidate can be rolled
   back, which revokes the exact installed package after drift checks; inactive
   records can be deprecated or archived without deleting audit provenance.

The generated Skill is declarative, untrusted guidance. Its `allowed-tools`
metadata can only reduce the tools already present in the current ToolSet. It
cannot grant write, command, network, MCP, Child, Team, PermissionGate,
sandbox, Action Audit, or AgentLoop authority. Child runtimes do not run this
automatic mining path and receive no Evolution Skill capability.

Automatic generation is enabled only when the Host evolution mode is `propose`
or `supervised`; it never installs or activates a candidate automatically.
`off` still permits normal trace accounting but produces no automatic Skill
candidate. Evolution state is independent of Session history, Memory records,
and provider credentials.

## Compatibility and recovery

The pipeline uses the existing Evolution and Skill candidate append-only logs,
record-local provenance, inventory fingerprints, and import locks. Existing
legacy traces without a workflow remain readable and simply cannot form an
automatic workflow pattern. A failed candidate creation, installation,
activation, or rollback reports the partial state and never claims success.
The current Turn is never hot-reloaded; a process restart or later Turn is the
discovery boundary.

## Verification

Deterministic tests cover the three-trace threshold, candidate quarantine,
static safety, independent evaluation, approval gating, generic-install
rejection, installation and next-Turn inventory discovery, usage observation,
exact rollback, and archive retention. CLI tests exercise the same lifecycle
without a Provider call or credential.

## Non-goals

- automatic evolution of Memory, the canonical system prompt, PermissionGate,
  sandbox, ToolSet definitions, Child/Team controls, or AgentLoop policy;
- execution of generated code, scripts, dependencies, or remote downloads;
- claiming that metric comparison is a full benchmark runner when only metrics
  were supplied;
- automatic approval, activation, retry, commit, or push.
