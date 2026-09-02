# ADR 0166: Host-gated Browser Actions in the AgentLoop

## Status

Accepted as one bounded implementation slice.

## Context

The repository already had a credential-free, origin-allowlisted Browser
automation contract, but it was not reachable from a normal model/tool turn.
Advertising a Browser tool without a configured backend would let a model ask
for an action that the Host could not execute, while bypassing the existing
permission and audit path would create a second authority boundary.

## Decision

`ProjectSession` accepts an explicit Host-owned `BrowserAutomation` runtime.
Only a Session with that runtime receives the closed `browser_action` contract;
ordinary Sessions and all Child Sessions keep it out of their ToolSet. The
contract contains one action per request: `navigate`, `click`, `fill`,
`extract_text`, or `screenshot`, with action-specific optional fields and no
additional properties.

Browser requests are parsed at the catalog boundary and prepared as a normal
Host `network-read` Action. They use the existing PermissionGate,
ActionCoordinator, approval, Hook, lease, cancellation, and Action Audit
paths. The injected Browser runtime remains responsible for its own
credential-free URL, origin, selector, step, timeout, and output constraints;
the Session only dispatches the already parsed action and serializes a bounded
`evidence: "untrusted"` observation. Browser actions never execute in a Child,
create or delegate work, access MCP or Skills, run commands, or alter the
workspace.

Session shutdown closes the injected runtime. Browser backend failures are
reported as failed Tool results and audited; no automatic retry or claim of
successful page state is made. Browser configuration is an explicit Host/API
injection in this slice; no CLI flag or implicit Playwright dependency is
added.

## Security and recovery boundaries

- Browser page text, screenshots, URLs, and backend claims are untrusted data.
- The Host never exposes credentials, raw network responses, or browser state
  in the model-visible contract or observation payload.
- Existing workspace, permission, approval, lease, cancellation, causality,
  output, and durable commit boundaries remain authoritative.
- A missing or closed runtime fails closed and does not fabricate a Browser
  result; an uncertain backend effect is not retried automatically.

## Verification

Deterministic tests cover action-specific contract validation, dynamic ToolSet
advertisement, Session-to-AgentLoop dispatch, PermissionGate/Action Audit
integration, untrusted observation serialization, Child rejection, URL and
step policy failures, and runtime cleanup. The normal release gate remains
`pytest`, Ruff, format, `uv lock --check`, and `git diff --check`; real browser
backends remain outside offline tests.
