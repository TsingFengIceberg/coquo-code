# ADR 0150: Upstream Provider Error Facts and Safe Display

## Status

Accepted for the bounded Provider error-observability slice after deterministic
adapter, sanitization, presentation, and compatibility tests.

## Decision

Provider adapters normalize an upstream status failure into the existing
`ProviderFailure` while retaining only bounded diagnostic facts: an HTTP status
code in the 100–599 range, the standard parsed `error.code` and `error.type`, a
bounded printable upstream message, a safe request ID, and a numeric
`Retry-After` value when available. OpenAI-compatible Chat Completions and
Responses use the same normalizer; Anthropic supplies the same fields through
its adapter helper.

The Host classification remains separate from upstream facts. `kind`,
`diagnostic_code`, `retryable`, and the Host-authored message continue to drive
retry guidance, fallback eligibility, stopping, and failure truth. An unusual
status such as 3xx is displayed as observed even when no special classification
exists. The CLI renders status, type, code, message, and Retry-After as
separate diagnostic lines while retaining the existing request-ID trace.

Only parsed standard fields are retained. Complete response bodies, arbitrary
headers, authorization material, credential values, and unknown/non-JSON body
text are not copied into `ProviderFailure`, Session records, or terminal output.
Control characters and overlong values are rejected or bounded before display.

## Compatibility and recovery

The new fields are optional and default to `None`; existing failures and
transcripts remain readable without rewriting. No retry, fallback, delay,
resend, response-body persistence, telemetry, or provider wire behavior changes
are implied. A missing or malformed status/body simply leaves the corresponding
upstream field absent while preserving the existing normalized Host failure.

## Consequences

Operators can identify the actual upstream HTTP response and provider error
terminology without losing Coquo's conservative policy classification. The
bounded projection improves diagnosis for authentication, request, rate-limit,
redirect, and service errors while maintaining the no-secret and no-raw-body
boundary. Non-JSON failures remain intentionally less detailed than trusted
structured error envelopes.

## Verification

Deterministic tests cover OpenAI-compatible and Anthropic metadata extraction,
request IDs, Retry-After, 3xx and malformed/non-JSON inputs, length and terminal
character bounds, CLI rendering, and the absence of credential/raw-body data.
The release gate still excludes real Providers, network, credentials, and API
cost.
