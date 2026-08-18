# ADR 0147: Persistent Child Background Runtime

## Status

Accepted for the durable Child background-runtime slice after deterministic
queue replay, lease, worker, orphan-recovery, CLI, and Session integration tests.

## Decision

Coquo adds a workspace-bound, restartable background runtime for admitted Child
Runs. The existing Child Run JSONL ledger remains the only authority for Child
admission, execution state, terminal outcome, and handoff evidence. The new
background ledger records only submission ownership and queue observation.

The runtime stores an append-only queue at:

```text
.coquo/child-runs/<workspace-fingerprint>/background/queue.jsonl
```

and protects it with a queue lock, one workspace worker lease, and an atomic
worker-state file. Queue events are schema-v1 `queued`, `claimed`, `heartbeat`,
`requeued`, and `terminal` records. Queue capacity is bounded at 32 pending
items, worker concurrency at four Child execution threads, queue records at
20,000, and queue bytes at 8 MiB. Files are regular, mode-restricted, no-follow
paths and every replay/sequence/UUID/record boundary failure is fail-closed.

`PersistentChildRunRuntime.start()` first durably enqueues a `ready` Child and
then launches `python -m coquo.background_worker` in a detached local process.
If launch fails, the queue item remains durable and the result explicitly
reports `launch_error`; it never claims that execution started. A second
worker cannot acquire the workspace lease. A worker may stop after a bounded
idle period, so this is restartable local processing rather than an unlimited
daemon or service fleet.

The worker reuses the existing `ChildRunExecutor` and therefore the existing
Provider binding, shared Agent runtime, permission envelope, tool boundaries,
Session writer, cancellation watcher, and Child execution lease. It does not
create a second AgentLoop, Provider manager, parent writer, Task ledger, Team
ledger, or model-visible tool surface. A heartbeat is written to both worker
state and the queue observation ledger; the queue remains bounded and durable.

Worker loss is reconciled conservatively:

1. A claimed Child already in a terminal Child state only receives a queue
   terminal observation.
2. A claimed `ready` Child is requeued only after a fresh Child execution lease
   proves that no executor still owns it.
3. A claimed `running` or `cancelling` Child obtains the Child recovery lease
   and is durably marked `interrupted`; it is never retried automatically.
4. Any lease conflict, malformed ledger, unknown state, append uncertainty, or
   failed recovery keeps the queue claim and reports diagnostics. The runtime
   does not guess whether an external process is alive or claim success from
   incomplete evidence.

The CLI exposes `child start`, `child worker`, and `child status`; `child wait`
polls the durable Child ledger rather than relying on an in-process supervisor,
and `child recover` reconciles both background claims and the existing Child
recovery service. The REPL exposes `/child status` and includes background
recovery observations in `/child recover`. `ProjectSession` uses the persistent
runtime by default when no in-process `ChildRunSupervisor` was explicitly
injected; injected supervisors remain supported for deterministic foreground
tests and callers that intentionally need process-local execution.

Team assignment starts reuse the same persistent Child runtime when no local
supervisor is injected. Team assignment and schedule ledgers remain the
authority for Team lifecycle and roster observation; the background queue is
only an execution submission/observation seam. The Task–Child–Team bridge does
not change its identity or evidence rules.

## Compatibility

The queue and worker-state files are new local schema-v1 artifacts. Existing
Child, Session, Team, Task, schedule, handoff, and Effective Context records are
not rewritten. The process-local `ChildRunSupervisor` API and its injected test
semantics remain compatible. Existing foreground `child run` and Team execution
commands continue to use the shared executor directly.

## Consequences

- A Host can submit a prepared Child, exit, restart, inspect queue/worker state,
  and recover with explicit evidence instead of losing the submission to a
  process-local queue.
- Worker PID, worker ID, heartbeat, active submissions, queue state, orphan
  candidates, and bounded diagnostics are observable without exposing Provider
  credentials or raw Child output.
- Queue durability does not make arbitrary execution exactly-once. A crash
  before or after an external side effect remains represented by Child lease,
  terminal, interruption, or recovery-required evidence; automatic retry is not
  introduced.
- Team schedule coordination is still bounded and local; this slice does not
  create a distributed scheduler, arbitrary Task DAG, recursive agents, or a
  permanent daemon fleet.

## Rejected alternatives

Making the queue the Child lifecycle authority would duplicate state and make
handoff evidence ambiguous. Reusing only process-local daemon threads would
lose queued work on Host exit. Automatically re-running a claimed running Child
could duplicate provider calls or filesystem effects, so running/cancelling
orphans are interrupted instead. A database, remote queue, service manager, or
distributed transaction coordinator would exceed the local single-user V0
boundary. Adding new model-visible tools would expose worker/process control
that belongs to the Host CLI and Session APIs.
