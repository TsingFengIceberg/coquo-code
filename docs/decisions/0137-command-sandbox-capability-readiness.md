# ADR 0137: Command Sandbox Capability Readiness

- Status: Accepted
- Date: 2026-08-14

## Context

ADR 0080 requires every production `run_command` to use a fixed bubblewrap
boundary that includes `--disable-userns`, `--block-fd`, `--info-fd`, and
`--seccomp`. Dependency inspection previously checked only that
`/usr/bin/bwrap` was an executable regular file and that a seccomp filter could
be created. An older bubblewrap could therefore be reported as ready even
though activation would fail closed on an unsupported required option.

Real sandbox integration tests also selected themselves from executable and
library presence alone. On such a Host they ran, then failed because the
production boundary correctly refused to start the requested command. A
separate Action/Session causality test unnecessarily depended on the same Host
integration despite already having a direct, test-only command sandbox.

## Decision

Dependency inspection invokes only the fixed bubblewrap binary with `--help`,
using a closed environment, a two-second timeout, and a 64 KiB output bound.
Bubblewrap is reported usable only when the command succeeds and its help
surface contains every option required by ADR 0080. Seccomp readiness is
checked only after this capability check passes.

Production launch arguments do not change. In particular, Coquo does not
remove `--disable-userns` to accommodate an older binary and never falls back
to unsandboxed Host execution. `/status`, `/permissions`, and `/sandbox check`
therefore report an old or otherwise incompatible bubblewrap as unavailable
before a user command is attempted.

Tests that exercise the real sandbox run only when the production dependency
and activation probe succeeds. They remain active on capable Hosts. Tests of
Action Audit, tool causality, and Session commit use the existing explicit
test-only direct sandbox so their result does not depend on the Host's
bubblewrap version.

## Compatibility

This is Host-only readiness and test isolation. Tool definitions and order,
PermissionGate behavior, approval, Action Audit, Session schemas, system prompt
v45, provider adapter contract v46, and Effective Context v23/v24 do not
change. Existing transcripts and configuration require no migration.

## Invariants

- Missing required bubblewrap capabilities never produce a ready status.
- Capability probing never executes model- or user-supplied argv.
- Production execution retains every ADR 0080 mount, namespace, seccomp,
  activation, timeout, output, and cleanup boundary.
- Environment-dependent integration tests are skipped only when the exact
  production sandbox cannot activate; deterministic Host causality remains
  covered independently.

## Alternatives rejected

- Remove `--disable-userns` on old bubblewrap: this weakens the accepted
  namespace boundary to make a Host appear compatible.
- Treat executable presence as readiness: this preserves the false-positive
  status and defers a known incompatibility until action execution.
- Skip all real sandbox tests unconditionally: capable development and release
  Hosts must continue proving the actual OS boundary.
