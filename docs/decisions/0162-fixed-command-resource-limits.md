# ADR 0162: Fixed Command Resource Limits

## Status

Accepted

## Date

2026-09-01

## Context

The Linux command sandbox already constrains workspace, network, process, and
output boundaries, but an admitted command could otherwise consume unbounded
CPU time, address space, file size, or file descriptors. Permission approval
must not be mistaken for a resource guarantee, and a Host that cannot install
the guarantee must not run the command without it.

## Decision

Immediately before releasing the sandbox activation gate and the user argv,
`run_command` applies fixed non-raiseable process limits with Linux
`resource.prlimit`: CPU seconds equal the requested bounded timeout, address
space at 2 GiB, individual file size at 256 MiB, and open file descriptors at
1024. The limits are Host constants and are not model-visible override fields.
They apply equally to `approval=ask` and `approval=auto`, and do not weaken the
existing bubblewrap/seccomp, workspace, environment, timeout, output, or
process-group cleanup constraints.

If the platform or `prlimit` path cannot establish every required limit, the
Host releases no user command and returns the structured
`command_resource_limits_unavailable` failure with `resource-limits-rejected`
observation. There is no unsandboxed or unlimited fallback. Failure guidance
points the operator to `/sandbox check` and explicitly states that the command
was not started.

## Compatibility and Recovery

The `run_command` request schema and Action Audit lifecycle remain unchanged.
Resource-limit rejection is a pre-spawn failure and therefore has no external
command effect; later timeout, cancellation, signal, or cleanup uncertainty is
still reported using the existing outcome and audit rules. Other operating
systems remain fail-closed because the production command sandbox is Linux-only.

## Verification

Unit tests assert all four exact limits, fail-closed behavior when the resource
API is unavailable, structured result and guidance rendering, and preservation
of direct argv semantics. Environment-dependent bubblewrap activation tests
continue to run when the target machine exposes the required capabilities and
are reported as skips rather than silently downgraded tests. The normal offline
release gate remains required.
