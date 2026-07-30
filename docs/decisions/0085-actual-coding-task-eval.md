# 0085: Actual Coding Task Eval

- Status: Accepted
- Date: 2026-07-30
- Scope: score real coding outcomes with protected inputs and Host-private tests, with explicit opt-in for a real-provider attempt

## Context

ADR 0084 proves fixed Host trajectories with a scripted fake provider, but it deliberately does not measure whether a model can inspect a small project, modify the correct production file, preserve requirements and tests, and satisfy cases it was not shown. A useful second stage must score actual workspace outcomes instead of requiring one exact tool trajectory or trusting final assistant prose.

The feature must preserve the existing security boundary. Hidden tests cannot be placed in the task workspace before the model runs. Scoring cannot execute an arbitrary candidate command, bypass `RunCommandTool`, mutate the submitted workspace, or silently invoke provider credentials and API spend. A real-provider attempt must be unmistakably opt-in and isolated from the caller's project.

## Decision

Add the versioned `coding-task-v1` suite with two immutable Python tasks: `inventory-validation` and `slug-normalization`. Each fixture defines a prompt, exact initial files, the only mutable production path, protected README/test paths, and a Host-private `unittest` module. The CLI adds `eval task list`, `eval task prepare TASK OUTPUT`, `eval task score TASK WORKSPACE [--format text|json]`, and `eval task run TASK --real-provider [--output PATH] [--format text|json]` while preserving the existing `eval list/run` surface.

Preparation requires an absent output path and writes no private test. Scoring reads the candidate without following symlinks, caps each declared file at 1 MiB, caps declared bytes at 4 MiB, and caps the observed tree at 100 entries. It compares the complete non-Session workspace shape and protected-file SHA-256 identities. Declared files are copied into a fresh temporary scoring workspace; only there does the Host add the hidden module. Visible and hidden suites run separately through fixed `/usr/bin/python3 -m unittest discover` argv under the production `RunCommandTool` sandbox. The original candidate receives no test cache or hidden content.

`task run` requires both the literal `--real-provider` acknowledgement and an explicit profile, profile ID, or model route. The Host creates a new task directory and opens the ordinary ProjectSession with fixed `danger-full-access + auto`; this is an interaction policy inside a disposable bounded workspace, not a relaxation of tool hard limits. Every model tool still crosses AgentLoop validation, PermissionGate, execution, tool ledger, and Action Audit. `run_command` remains fail closed behind bubblewrap/seccomp. Tool events use stderr and the stable score uses stdout. A retained `--output` path is reported separately; otherwise the attempt directory is removed after scoring.

The regular command sandbox exposes most Host files read-only. To prevent the agent from reading built-in hidden-test source through `run_command`, the Eval-specific command tool asks bubblewrap to mask the current Leonervis source checkout before rebinding the task workspace. In an installed layout without a source checkout, it masks the evaluator module and bytecode cache. Hidden tests are instantiated only after the provider Session has closed. This masking option is additive to `LinuxBubblewrapCommandSandbox`; ordinary `run_command` construction supplies no masked paths and retains its existing behavior.

Seven real-attempt checks cover provider-turn completion, exactly one committed turn, no uncertain Action Audit tail, workspace shape, protected files, visible tests, and hidden tests. Offline score uses the final four checks. Stable JSON excludes candidate paths, provider final text, prompts, timestamps, Session/action/tool IDs, and command stream content. Exit status is 0 for a full pass, 1 for a scored failure, and 2 for invalid selection or forbidden option composition.

## Compatibility

This slice adds Host CLI and evaluation behavior only. The canonical system prompt remains v23, adapter contract remains v26, current Effective Context representations remain `ctx-v5`/`ctx-v6`, and all 21 model-visible tool names, order, definitions, budgets, ToolArguments v1, Session records, Action Audit records, and compaction records remain unchanged. The optional sandbox masking argument defaults to no additional masks, so ordinary command execution is source compatible and behaviorally unchanged.

## Invariants

- Preparing a task never writes hidden tests into the task workspace.
- Scoring never writes to the candidate workspace and never executes candidate-selected argv.
- Every declared file is copied through a no-follow descriptor path; intermediate or final symlinks do not escape the candidate root.
- Protected inputs and the complete allowed workspace shape are scored independently from test results.
- Visible and hidden tests execute only through the fail-closed production command sandbox.
- A real-provider attempt requires explicit dual intent: `--real-provider` plus a profile/model selector.
- Real attempts use a newly created task workspace; `-C`, resume, and caller-selected permission/approval modes are rejected.
- Hidden tests are created only after the agent Session closes, and evaluator source is masked from agent commands.
- Provider final text cannot establish a pass and is excluded from stable reports.

## Non-goals

- arbitrary repositories, user-authored fixtures, external benchmark downloads, or package installation;
- prescribing one ideal tool sequence or penalizing harmless planning differences;
- model rankings, weighted scores, pass thresholds, retries, majority voting, latency, or cost comparison;
- treating two small tasks as evidence of broad coding capability or generalization;
- running a real provider without separate user authorization for credentials, network, endpoint access, and cost;
- changing model-visible prompts, tools, permission semantics, Session representations, or ordinary command sandbox behavior to improve scores.
