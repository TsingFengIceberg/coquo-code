#!/usr/bin/env python3
"""Exercise the public offline CLI and compatibility paths used by release CI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def _run(workspace: Path, config: Path, *arguments: str) -> str:
    environment = dict(os.environ)
    environment["XDG_CONFIG_HOME"] = str(config)
    environment["UV_CACHE_DIR"] = "/tmp/coquo-uv-cache"
    result = subprocess.run(
        [sys.executable, "-m", "coquo", "-C", str(workspace), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"command failed: {arguments!r}")
    return result.stdout


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="coquo-release-smoke-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        config = root / "config"
        workspace.mkdir()
        config.mkdir()

        first = _run(workspace, config, "prompt", "Hello")
        if "Fake response: Hello" not in first:
            raise RuntimeError("fake CLI smoke did not produce the expected response")
        _run(workspace, config, "session", "show", "latest")
        second = _run(workspace, config, "--resume", "latest", "prompt", "Second")
        if "Fake response: Second" not in second:
            raise RuntimeError("Session resume smoke did not produce the expected response")

        memory_config = workspace / ".coquo" / "memory" / "config.json"
        memory_config.parent.mkdir(parents=True)
        memory_config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "enabled": True,
                    "recall": "on",
                    "write": "propose",
                    "tools": False,
                    "provider": "local",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        status = _run(workspace, config, "memory", "status")
        if "configured capture: explicit" not in status:
            raise RuntimeError("legacy memory configuration did not default capture to explicit")
        _run(workspace, config, "memory", "configure", "--capture", "conservative")
        persisted = json.loads(memory_config.read_text(encoding="utf-8"))
        if persisted.get("schema_version") != 3 or persisted.get("capture") != "conservative":
            raise RuntimeError("memory configuration did not migrate to schema v3")
    print("offline release smoke: passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"offline release smoke failed: {error}") from None
