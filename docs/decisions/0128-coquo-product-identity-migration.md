# ADR 0128: Coquo Product Identity Migration

- Status: Accepted
- Date: 2026-08-09

## Context

The project began under the coined name Leonervis Code. That identity now spans the Python distribution and import package, two console entry points, workspace and XDG state directories, environment variables, MCP client metadata, HTTP user agents, temporary-file prefixes, terminal branding, model-visible prompts, durable identity domains, tests, and documentation. The project remains a single-user pre-alpha prototype at version 0.1.0, so retaining a permanent public compatibility layer would add more ambiguity and maintenance cost than value.

The replacement name is Coquo, from Latin *coquo* (classically written *coquō*), “I cook.” The name connects preparing software from requirements, context, tools, and model decisions with the existing coding-agent purpose without constraining the product to one subsystem such as permissions, Tasks, MCP, or Skills.

## Decision

Adopt `Coquo` as the sole display name and `coquo` as the canonical repository name, Python distribution, Python import package, console command, module entry point, workspace state directory, and XDG application directory. Use `COQUO_` for product-owned environment variables, `coquo` for MCP client metadata, and `coquo/<version>` for HTTP user-agent identity. Replace the LEO terminal and README mark with a deterministic COQ mark while retaining bounded rendering, narrow-terminal fallback, the established warm palette, and `NO_COLOR` behavior.

This is an intentional clean break. New installations expose only `coquo` and `python -m coquo`; they do not expose `leonervis-code`, `leonervis`, or `python -m leonervis_code`. New runtime state is written only under `<workspace>/.coquo`, `${XDG_CONFIG_HOME}/coquo`, and `${XDG_CACHE_HOME}/coquo`. Coquo neither reads nor silently migrates legacy `.leonervis-code` or XDG `leonervis-code` state. Legacy data is not deleted. The repository continues to ignore `.leonervis-code`, and command sandbox and independent-review boundaries continue masking both old and new state names so abandoned sensitive data cannot become visible merely because the product was renamed.

Reset product-owned fingerprint domain separators from `leonervis-*` to `coquo-*`. Existing Leonervis Session, Task, Action, ToolSet, Skill, Hook, and Effective Context identities are outside the Coquo runtime compatibility contract. The underlying record structures and representation algorithms do not otherwise change, so their structural version numbers remain unchanged. Advance the canonical system prompt to v44, provider adapter contract to v45, and built-in Tool Registry/source generation to 6 because the model-visible role, provider projection identity, and built-in source identity change. Exact golden fingerprints are regenerated only from reviewed canonical inputs.

Do not rewrite ADRs 0001-0127 or Git history. They remain truthful historical records written under the former product name. Current entry-point documentation and the consolidated implemented-foundations narrative use Coquo; historical references may retain Leonervis when needed to explain lineage or a protected legacy path. Learning submodules remain untouched.

The tracked source migration is completed before the GitHub repository and local checkout directory are renamed. After the code passes its release gate and is pushed, rename the remote repository to `coquo`, update `origin`, and move the local checkout only while no process is using it.

## Consequences

- Product, package, command, state, terminal, Provider, MCP, and documentation identities become one short name.
- Existing local profiles, Sessions, Tasks, OAuth state, Hook configuration, Skill candidates, and caches are not automatically available to Coquo.
- No compatibility import package or deprecated command alias remains to confuse packaging or future diagnostics.
- Legacy state remains ignored and masked until the owner explicitly archives or removes it.
- Accepted historical ADRs continue to contain the former name by design, so a repository-wide zero-match check is inappropriate; current source, tests, build products, and entry-point docs use an explicit residual-name allowlist instead.
