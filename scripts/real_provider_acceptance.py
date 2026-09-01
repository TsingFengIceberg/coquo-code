#!/usr/bin/env python3
"""Run a small, explicitly authorized real-Provider acceptance suite.

The suite is intentionally a script rather than an automatic pytest test:
network, credentials, and API cost must be acknowledged independently for
each invocation.  It never prints environment values or writes a report file.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MAX_OUTPUT_CHARS = 4096
COMMAND_TIMEOUT_SECONDS = 90
MAX_STREAM_METRICS = 32
STREAM_METRIC_PATTERN = re.compile(
    r"elapsed_ms=(?P<elapsed>none|[0-9]+) "
    r"delta_count=(?P<delta_count>[0-9]+) "
    r"first_delta_ms=(?P<first>none|[0-9]+) "
    r"max_delta_gap_ms=(?P<gap>none|[0-9]+) "
    r"retry_count=(?P<retry>[0-9]+)"
)
MEMORY_CONTENT = (
    "Acceptance memory code is AURORA-4821 and the deployment window is Thursday 11:15."
)
READONLY_FIXTURE_NAME = "acceptance-readonly.txt"
READONLY_FIXTURE_CONTENT = "REAL_PROVIDER_ACCEPTANCE_READONLY_OK\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run opt-in real Provider acceptance scenarios without storing secrets."
    )
    parser.add_argument("--profile", required=True, help="an existing named Coquo Provider profile")
    parser.add_argument(
        "--workspace",
        type=Path,
        help="existing workspace to use; otherwise a temporary workspace is created",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="acknowledge that scenarios may contact the configured endpoint",
    )
    parser.add_argument(
        "--allow-credentials",
        action="store_true",
        help="acknowledge that the profile's configured credential environment variable may be read",
    )
    parser.add_argument(
        "--allow-cost",
        action="store_true",
        help="acknowledge that the Provider may charge for the requests",
    )
    return parser


def _validate_options(arguments: argparse.Namespace) -> None:
    if not PROFILE_NAME.fullmatch(arguments.profile):
        raise SystemExit("profile must be a bounded name, not an endpoint or credential value")
    missing = [
        name
        for name, enabled in (
            ("--allow-network", arguments.allow_network),
            ("--allow-credentials", arguments.allow_credentials),
            ("--allow-cost", arguments.allow_cost),
        )
        if not enabled
    ]
    if missing:
        raise SystemExit(
            "refusing real Provider acceptance without explicit acknowledgements: "
            + ", ".join(missing)
        )
    if os.environ.get("COQUO_REAL_PROVIDER_ACCEPT") != "1":
        raise SystemExit("set COQUO_REAL_PROVIDER_ACCEPT=1 for this one explicitly authorized run")
    if arguments.workspace is not None:
        workspace = arguments.workspace.resolve()
        if workspace.is_symlink() or not workspace.is_dir():
            raise SystemExit("--workspace must be an existing non-symlink directory")
        fixture = workspace / READONLY_FIXTURE_NAME
        if fixture.exists() or fixture.is_symlink():
            raise SystemExit(
                f"--workspace already contains the acceptance fixture {READONLY_FIXTURE_NAME!r}; "
                "refusing to overwrite caller data"
            )


def _run_command(workspace: Path, profile: str, prompt: str) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "coquo",
        "-C",
        str(workspace),
        "--profile",
        profile,
        "--permission-mode",
        "read-only",
        "--approval",
        "auto",
        "--events",
        "ndjson",
        "prompt",
        prompt,
    ]
    return subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ),
        capture_output=True,
        check=False,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )


def _run_host(workspace: Path, *arguments: str) -> str:
    command = [sys.executable, "-m", "coquo", "-C", str(workspace), *arguments]
    result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ),
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Host setup command failed")
    return result.stdout


def _snippet(value: str) -> str:
    bounded = value.strip()
    if len(bounded) <= MAX_OUTPUT_CHARS:
        return bounded
    return bounded[:MAX_OUTPUT_CHARS] + "...[truncated]"


def _parse_stream_metrics(stderr: str) -> list[dict[str, int | None]]:
    """Extract only bounded Host stream facts from content-free NDJSON events."""
    metrics: list[dict[str, int | None]] = []
    for line in stderr.splitlines():
        if len(metrics) >= MAX_STREAM_METRICS:
            break
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        if record.get("record_type") != "live_provider_invocation_finished":
            continue
        summary = record.get("summary")
        if not isinstance(summary, str):
            continue
        match = STREAM_METRIC_PATTERN.search(summary)
        if match is None:
            continue

        def number(name: str) -> int | None:
            value = match.group(name)
            return None if value == "none" else int(value)

        metrics.append(
            {
                "elapsed_ms": number("elapsed"),
                "delta_count": int(match.group("delta_count")),
                "first_delta_ms": number("first"),
                "max_delta_gap_ms": number("gap"),
                "retry_count": int(match.group("retry")),
            }
        )
    return metrics


def _scenario(
    name: str, workspace: Path, profile: str, prompt: str, expected: tuple[str, ...]
) -> dict[str, object]:
    started = time.monotonic()
    try:
        result = _run_command(workspace, profile, prompt)
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "status": "timeout",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    output = result.stdout
    missing = tuple(token for token in expected if token not in output)
    stream_metrics = _parse_stream_metrics(result.stderr)
    return {
        "name": name,
        "status": "passed" if result.returncode == 0 and not missing else "failed",
        "returncode": result.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "expected_tokens_missing": list(missing),
        "stream_metrics": stream_metrics,
        "stdout": _snippet(output),
        "stderr": _snippet(result.stderr),
    }


def _prepare_memory(workspace: Path) -> None:
    _run_host(
        workspace,
        "memory",
        "configure",
        "--enable",
        "--recall",
        "on",
        "--write",
        "off",
        "--retrieval",
        "semantic",
        "--no-tools",
    )
    raw = _run_host(workspace, "memory", "add", MEMORY_CONTENT)
    record = json.loads(raw)
    memory_id = record.get("memory_id")
    if not isinstance(memory_id, str) or not memory_id:
        raise RuntimeError("memory setup did not return a memory ID")
    _run_host(workspace, "memory", "confirm", memory_id)


def run(arguments: argparse.Namespace) -> int:
    _validate_options(arguments)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if arguments.workspace is None:
        temporary = tempfile.TemporaryDirectory(prefix="coquo-real-acceptance-")
        workspace = Path(temporary.name)
    else:
        workspace = arguments.workspace.resolve()
    fixture = workspace / READONLY_FIXTURE_NAME
    fixture_created = False
    try:
        try:
            with fixture.open("x", encoding="utf-8") as stream:
                stream.write(READONLY_FIXTURE_CONTENT)
            fixture_created = True
        except FileExistsError:
            raise RuntimeError(
                f"acceptance fixture {READONLY_FIXTURE_NAME!r} appeared before setup; "
                "refusing to overwrite caller data"
            ) from None
        _prepare_memory(workspace)
        scenarios = (
            _scenario(
                "basic-final-response",
                workspace,
                arguments.profile,
                "Reply with exactly REAL_PROVIDER_ACCEPTANCE_OK and nothing else.",
                ("REAL_PROVIDER_ACCEPTANCE_OK",),
            ),
            _scenario(
                "bounded-read-only-tool",
                workspace,
                arguments.profile,
                f"You must call read_file on {READONLY_FIXTURE_NAME}, then reply with the exact token REAL_PROVIDER_READ_OK. Do not modify anything.",
                ("REAL_PROVIDER_READ_OK",),
            ),
            _scenario(
                "long-term-memory-recall",
                workspace,
                arguments.profile,
                "Do not read files or use tools. From long-term memory, report the acceptance memory code and deployment window exactly.",
                ("AURORA-4821", "Thursday 11:15"),
            ),
        )
        report = {
            "suite": "coquo-real-provider-acceptance-v1",
            "profile": arguments.profile,
            "workspace": "temporary" if temporary is not None else "caller-provided",
            "scenarios": scenarios,
            "status": "passed"
            if all(item["status"] == "passed" for item in scenarios)
            else "failed",
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if report["status"] == "passed" else 1
    finally:
        if fixture_created:
            try:
                fixture.unlink()
            except OSError:
                pass
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    try:
        raise SystemExit(run(_parser().parse_args()))
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"acceptance setup failed: {error}") from None
