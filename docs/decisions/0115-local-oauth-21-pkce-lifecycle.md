# 0115: Local OAuth 2.1 PKCE Lifecycle

## Status

Accepted.

## Context

Environment-owned bearer tokens are sufficient for static credentials but not for hosted MCP services that require delegated authorization and refresh. OAuth state, verifier, access token, refresh token, expiry, and client authentication must remain outside project configuration and all model-visible or durable conversation state.

## Decision

Add a user-only schema-v1 OAuth credential store at `${XDG_CONFIG_HOME:-~/.config}/leonervis-code/mcp-oauth.json`. It uses the MCP configuration path's symlink checks, private parent directory, mode `0600`, bounded strict JSON, atomic replacement, and no terminal rendering of token values. Records bind user/project server key and exact configuration revision; a server revision change makes prior authorization unavailable.

`mcp oauth begin` discovers one HTTPS protected-resource metadata document and one authorization-server metadata document, requires PKCE `S256`, creates high-entropy state and verifier, accepts only a loopback HTTP redirect URI, persists a ten-minute pending record, and prints only the authorization URL. `complete` requires the exact live state and server revision before exchanging the code. A confidential client secret is read from its configured environment name and sent only to the token endpoint. Successful Bearer tokens require bounded expiry and are persisted without being rendered.

The Streamable HTTP client resolves a current access token immediately before each request. An expired token with a refresh token performs one bounded refresh and atomically replaces the prior token revision; no refresh happens after configuration staleness, without a refresh token, or after a failed request. `status` exposes only configured/pending/authorized/expired and token revision. `logout` deletes pending and token state.

## Non-goals

- browser launching, a local callback listener, device authorization, dynamic client registration, token exchange, revocation, introspection, organization policy, or encrypted-at-rest claims;
- storing a static bearer value in MCP configuration or exposing access/refresh tokens to the model, Session, Action Audit, terminal, logs, or repository;
- retrying an authorization-code or refresh request after delivery uncertainty.

## Consequences

- A local user can complete a standards-bounded PKCE authorization flow without placing delegated credentials in a workspace.
- Token lifecycle is explicit and revision-bound; unsupported OAuth deployments fail closed rather than guessing endpoints or grant types.
