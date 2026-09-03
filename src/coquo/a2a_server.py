"""Opt-in local process entry point for the Coquo A2A Provider."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import uvicorn

from coquo.a2a_provider import create_app
from coquo.tenant import TenantRegistry


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coquo-a2a",
        description="Expose one read-only Coquo workspace through standard A2A JSON-RPC.",
    )
    parser.add_argument(
        "--workspace", required=True, help="explicit workspace visible to the Agent"
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback)")
    parser.add_argument("--port", default=18751, type=_port, help="bind port")
    parser.add_argument(
        "--public-url",
        help="public base URL advertised in AgentCard (default: http://HOST:PORT)",
    )
    parser.add_argument(
        "--fixture-provider",
        action="store_true",
        help="run the deterministic offline fixture Provider; it is not model-backed evidence",
    )
    parser.add_argument(
        "--profile",
        help="named Coquo Provider profile for model-backed A2A Tasks",
    )
    parser.add_argument(
        "--model",
        help="direct Coquo model route, optionally overriding --profile for this process",
    )
    parser.add_argument(
        "--bearer-token-env",
        help="environment variable containing an optional bearer token for task routes",
    )
    parser.add_argument(
        "--enforce-tenants",
        action="store_true",
        help="require A2A task tenants to be configured for this workspace",
    )
    arguments = parser.parse_args(argv)
    if not arguments.fixture_provider and not arguments.profile and not arguments.model:
        parser.error(
            "select --fixture-provider, --profile, or --model; no implicit Provider route is used"
        )
    workspace = Path(arguments.workspace).resolve()
    if not workspace.is_dir():
        parser.error("workspace must be an existing directory")
    public_url = arguments.public_url or f"http://{arguments.host}:{arguments.port}"
    bearer_token = None
    if arguments.bearer_token_env:
        bearer_token = os.environ.get(arguments.bearer_token_env)
        if not bearer_token:
            parser.error(f"missing bearer token environment variable: {arguments.bearer_token_env}")
    app = create_app(
        workspace,
        public_url,
        profile=arguments.profile,
        model=arguments.model,
        fixture_provider=arguments.fixture_provider,
        bearer_token=bearer_token,
        tenant_registry=TenantRegistry(workspace) if arguments.enforce_tenants else None,
    )
    if arguments.fixture_provider:
        print(
            "coquo-a2a: deterministic fixture Provider selected; no model or network evidence is produced",
            file=sys.stderr,
        )
    uvicorn.run(app, host=arguments.host, port=arguments.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
