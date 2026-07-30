# 0080: Fail-closed Linux Command Sandbox

- Status: Accepted
- Date: 2026-07-30
- Scope: require OS-enforced containment for every production `run_command` execution

## Context

`run_command` already used direct argv, a closed environment, bounded output, timeouts, cancellation, process-group cleanup, PermissionGate, approval, and durable Action Audit. Those controls did not stop an approved process from writing outside the workspace, reading Host credentials, or opening network sockets. Approval is informed authorization, not process containment.

The primary design reference is Claude Code's platform-native sandbox pattern: Linux command execution uses bubblewrap-style filesystem isolation while macOS uses a separate platform mechanism. OpenClaw, Hermes, DeerFlow, and Claw-Code were reviewed as secondary comparisons for namespace probing, fail-closed reporting, container-based alternatives, and explicit sandbox status. Leonervis adopts the Linux bubblewrap shape independently and does not import reference code or create a runtime dependency on a reference repository.

## Decision

Every production `run_command` on Linux is wrapped by fixed `/usr/bin/bwrap` arguments. The Host root is mounted read-only, the exact workspace path is mounted read-write at the same absolute path, `/tmp` is a private tmpfs, `/dev` is minimal, and Host `/proc`, `/sys`, and `/run` are replaced by empty private tmpfs mounts. User, PID, IPC, and UTS namespaces are unshared, capabilities are dropped, further user namespaces are disabled, and the sandbox dies with its parent.

When the original absolute HOME is available, known credential and agent-state directories are replaced by empty tmpfs mounts and known sensitive files by a `/dev/null` bind. The command-visible HOME, TMP, UV cache, and XDG directories point into private `/tmp`. The existing closed environment allowlist remains in force, and provider credentials are not added.

Because this Host cannot reliably create a network namespace, Leonervis builds a libseccomp BPF filter that returns `EPERM` for `socket`, legacy `socketcall` when present, and `io_uring_setup`. Bubblewrap loads the filter for the sandboxed command after establishing its own namespaces. This denies IPv4, IPv6, Unix-domain, and other new sockets without requiring shell parsing or a Python pre-exec hook.

Bubblewrap's private `--info-fd` report is required as activation evidence, while `--block-fd` prevents the requested argv from starting until the Host validates that report and releases the gate. Missing Linux support, `/usr/bin/bwrap`, libseccomp, required syscalls, filter construction, descriptor setup, process spawn, or a valid activation report returns failed `command_sandbox_unavailable`; the requested argv is never retried directly on the Host. Model requests and approval cannot select, weaken, or disable this boundary.

Permission semantics remain orthogonal and unchanged: `run_command` is still `dangerous`, so read-only and workspace-write deny it, while danger-full-access uses ask or auto. Approval authorizes only the prepared action and never bypasses sandboxing. Direct argv, `shell=False`, closed stdin, the 1-to-300-second timeout, independent 32 KiB stdout/stderr retention, continuous drain, cancellation, and bounded TERM-to-KILL process-group cleanup remain in force. Bubblewrap's conventional `128 + signal` child status is normalized back to the existing typed signal observation; deliberate command exits in the reserved 129-to-192 range are consequently interpreted as signals.

## Compatibility

The model-visible tool name, order, input schema, provider projection, ToolArguments, permission action, ActionIdentity, Action Audit, Session records, and tool budgets do not change. Provider adapter contract remains v25 and Effective Context representations remain `ctx-v3`/`ctx-v4`.

The model-visible execution guarantee changes, so the canonical system prompt advances from v21 to v22 with fingerprint `v22-b0cfedb6ee5c835f0dfe874b396b63003a2f1a203fe5f214f0e2d000dfd8d08c`. The empty full-history identity becomes `ctx-v3-a28664ae5f5143fac7e7b5936d78cb59c31643eb1a07eb7f41d73167625d67f8`. Existing transcripts and compact checkpoints are not rewritten; resumed turns use the current prompt snapshot as usual.

## Invariants

- No production path executes requested argv when the sandbox is unavailable or unconfirmed.
- Host filesystem writes outside the workspace cannot persist; private `/tmp` writes disappear with the sandbox.
- Workspace write access does not imply Host HOME, runtime-state, kernel-view, or network access.
- Permission or approval cannot disable mount, environment, seccomp, timeout, output, cleanup, causality, or audit bounds.
- A sandboxed command may still irreversibly change workspace files before failure, timeout, or cancellation; no rollback is claimed.
- Sandbox setup and result reporting expose no raw internal exception or activation payload to the model.

## Non-goals

- macOS Seatbelt or Windows support;
- Docker, VM, remote, or container-daemon execution;
- domain or endpoint network allowlists, proxy mediation, DNS policy, or package-download mode;
- cgroups, CPU/memory/disk quotas, syscall allowlisting, or kernel-exploit resistance;
- interactive PTYs, shell-source parsing, automatic retry, workspace rollback, or hostile-concurrency transactions;
- a CLI/model escape hatch for unsandboxed Host execution.
