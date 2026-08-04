# 0106: Bounded Fetch, Structured Read, and Controlled Transfer Tools

## Status

Accepted.

## Context

Leonervis could search the public web, inspect common workspace and Git state, and mutate individual files, but it could not retrieve one selected page, compare two files directly, inspect blame or refs, query structured JSON, hash a large file, inspect an archive without extraction, move a directory tree, or install a public download. Asking `run_command` to emulate these operations would bypass purpose-specific argument schemas and informed approval, while its fail-closed sandbox deliberately denies network sockets.

The new capabilities must remain ordinary bounded tools. Network access must not become a generic HTTP client, structured reads must not become arbitrary code execution, archive observation must not imply extraction safety, and directory/download mutation must preserve PermissionGate, exact-state revalidation, Action Audit, durable ToolResult causality, and ordinary Turn budgets.

## Decision

Append nine ordinary model-visible tools in this canonical order after `web_search`: `web_fetch`, `compare_files`, `git_blame`, `git_refs`, `json_query`, `checksum_file`, `archive_list`, `move_directory`, and `download_file`. Existing tool order is preserved. All provider adapters select definitions by canonical name rather than relying on shifted numeric positions, and Anthropic Messages, OpenAI-compatible Chat Completions, and OpenAI Responses project the same provider-neutral schemas.

`compare_files` reads two no-symlink UTF-8 regular files of at most 1 MiB each and returns at most 64 KiB of unified diff. `git_blame` observes at most 200 lines from current `HEAD` for one literal workspace path. `git_refs` observes current `HEAD`, local branches, and tags only, rejects repositories over 200 refs, and accepts no revision or free-form Git argument. `json_query` parses at most 1 MiB of strict UTF-8 JSON, rejects duplicate keys and non-finite numbers, resolves one RFC 6901 pointer with at most 128 segments, and returns at most 32 KiB. `checksum_file` streams SHA-256 for one no-symlink regular file up to 256 MiB. `archive_list` lists metadata for ZIP and uncompressed TAR files up to 64 MiB, at most 1,000 entries and 32 KiB output; it reports unsafe paths, encryption, and links but never extracts or verifies that extraction would be safe. Compressed TAR formats are rejected in this first version.

Share one standard-library HTTP GET transport between `web_fetch` and `download_file`. It accepts only HTTP and HTTPS on ports 80 and 443, rejects URL credentials, resolves every hostname before each request, rejects the entire resolution set if any address is non-public, connects to a validated pinned address while preserving the original hostname for HTTP Host and TLS SNI/certificate validation, and repeats the full validation for at most five redirects. It sends a fixed User-Agent, requests identity encoding, sends no body, credentials, cookies, authentication, proxy configuration, or model-selected headers, and rejects compressed responses.

`web_fetch(url, format)` is a `network-read` action. It waits at most 20 seconds, retains at most 512 KiB, accepts supported HTML, plain text, JSON, and XML media types, strips script/style/template-like HTML content with the standard-library parser, performs no JavaScript, and returns at most 64 KiB of text or deterministic simple Markdown. `download_file(url, path)` is one new `network-write` action rather than separate network and write grants. It waits at most 30 seconds, retains at most 16 MiB, rechecks the destination before and after network I/O, atomically creates or replaces one regular workspace file, preserves overwrite mode, and returns the installed byte count and SHA-256. `network-write` is denied in `read-only` and `workspace-write`; only `danger-full-access` may ask or auto-allow it.

`move_directory(source, destination)` uses Linux `renameat2(RENAME_NOREPLACE)` to atomically move one existing directory tree to one missing same-filesystem destination. It rejects symlinked parents, descendant destinations, replacement, cross-filesystem movement, stale source/destination state, and platforms without atomic no-replace support. It does not copy, merge, recursively delete, or create parent directories.

Routine live tool summaries redact URLs and JSON pointers while showing relative paths, format, line ranges, and byte/count metadata. Ask-mode approval shows the exact URL and destination or source/destination paths because informed authorization requires them. `web_fetch`, `move_directory`, and `download_file` use dedicated ApprovalPreview v3 kinds. Timeout or transport uncertainty and post-rename/install durability uncertainty remain explicit partial outcomes; the system prompt forbids automatic retry when side effects or remote delivery may already have occurred.

All production network behavior is behind an injectable transport. Deterministic tests use fake transports and make no real request, consume no credential, and incur no provider or search cost.

## Compatibility And Versions

The canonical system prompt advances from v32 to v33 with fingerprint `v33-ecefd82d8e51e9542404288e5911fcd8355e1179d1f9c13c2b3ec09492fac4f5`. The provider adapter contract advances from v35 to v36 because native tool projections change. The current empty no-instructions full-context identity becomes `ctx-v7-d9d80c3188613943154a2c3f8df40062d52ff14fdb19b3b8628d557e81e13c95`; Effective Context representations remain `ctx-v7`/`ctx-v8`. ApprovalPreview advances from v2 to v3. ToolArguments remains v1, ActionIdentity remains v1, and Session, Task, Action Audit, compaction, provider-owned history, profile, and usage schemas remain unchanged. Existing transcripts and checkpoints replay without rewriting.

## Non-goals

- generic HTTP methods, request bodies, custom headers, authentication, cookies, proxies, private-network access, browser automation, JavaScript, screenshots, or rendered DOM;
- automatic web-search-to-fetch chaining, crawling, caching, retry, fan-out, ranking, or content trust;
- archive extraction, compressed TAR support, malware detection, decompression-ratio analysis, or archive signature verification;
- arbitrary Git revisions, remote refs, free-form Git commands, linked-worktree support, or untracked-content history;
- cross-filesystem directory copy/delete fallback, destination merge/replacement, rollback, or hostile-concurrency guarantees;
- download resume, checksum expectation input, executable quarantine, MIME-based filename selection, or background transfer.

## Consequences

- The model can inspect and transfer common coding artifacts without receiving generic shell networking or arbitrary HTTP authority.
- Search remains discovery and fetch remains one exact selected resource, with distinct approval and audit provenance.
- Purpose-specific output limits and structured errors make omitted data, stale state, partial effects, and unsupported formats explicit.
- Future browser, MCP, archive extraction, or richer download work must define separate trust and execution boundaries instead of widening these tools silently.
