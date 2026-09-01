from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "real_provider_acceptance.py"
_SPEC = importlib.util.spec_from_file_location("coquo_real_provider_acceptance", SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_ACCEPTANCE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ACCEPTANCE)


def _arguments(**overrides: object) -> argparse.Namespace:
    values = {
        "profile": "test-profile",
        "workspace": None,
        "allow_network": True,
        "allow_credentials": True,
        "allow_cost": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_real_acceptance_requires_all_explicit_acknowledgements(monkeypatch) -> None:
    monkeypatch.setenv("COQUO_REAL_PROVIDER_ACCEPT", "1")
    with pytest.raises(SystemExit, match="--allow-cost"):
        _ACCEPTANCE._validate_options(_arguments(allow_cost=False))


def test_real_acceptance_requires_process_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("COQUO_REAL_PROVIDER_ACCEPT", raising=False)
    with pytest.raises(SystemExit, match="COQUO_REAL_PROVIDER_ACCEPT"):
        _ACCEPTANCE._validate_options(_arguments())


def test_real_acceptance_rejects_endpoint_or_credential_as_profile(monkeypatch) -> None:
    monkeypatch.setenv("COQUO_REAL_PROVIDER_ACCEPT", "1")
    with pytest.raises(SystemExit, match="bounded name"):
        _ACCEPTANCE._validate_options(_arguments(profile="https://gateway.example/v1"))


def test_real_acceptance_rejects_existing_caller_fixture(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COQUO_REAL_PROVIDER_ACCEPT", "1")
    (tmp_path / _ACCEPTANCE.READONLY_FIXTURE_NAME).write_text("keep me", encoding="utf-8")
    with pytest.raises(SystemExit, match="refusing to overwrite caller data"):
        _ACCEPTANCE._validate_options(_arguments(workspace=tmp_path))


def test_real_acceptance_uses_and_cleans_exclusive_fixture(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COQUO_REAL_PROVIDER_ACCEPT", "1")
    monkeypatch.setattr(_ACCEPTANCE, "_prepare_memory", lambda _workspace: None)
    monkeypatch.setattr(
        _ACCEPTANCE,
        "_scenario",
        lambda name, workspace, profile, prompt, expected: {
            "name": name,
            "status": "passed",
        },
    )
    assert _ACCEPTANCE.run(_arguments(workspace=tmp_path)) == 0
    assert not (tmp_path / _ACCEPTANCE.READONLY_FIXTURE_NAME).exists()


def test_real_acceptance_scenario_reports_bounded_success(monkeypatch, tmp_path: Path) -> None:
    class Completed:
        returncode = 0
        stdout = "REAL_PROVIDER_ACCEPTANCE_OK\n"
        stderr = ""

    monkeypatch.setattr(_ACCEPTANCE, "_run_command", lambda *_args: Completed())
    result = _ACCEPTANCE._scenario(
        "basic",
        tmp_path,
        "test-profile",
        "Reply with exactly REAL_PROVIDER_ACCEPTANCE_OK.",
        ("REAL_PROVIDER_ACCEPTANCE_OK",),
    )
    assert result["status"] == "passed"
    assert result["expected_tokens_missing"] == []


def test_real_acceptance_scenario_extracts_content_free_stream_metrics(
    monkeypatch, tmp_path: Path
) -> None:
    class Completed:
        returncode = 0
        stdout = "REAL_PROVIDER_ACCEPTANCE_OK\n"
        stderr = (
            '{"record_type":"live_provider_invocation_finished",'
            '"summary":"live_provider_invocation_finished elapsed_ms=1250 '
            'delta_count=2 first_delta_ms=100 max_delta_gap_ms=850 retry_count=1"}\n'
        )

    monkeypatch.setattr(_ACCEPTANCE, "_run_command", lambda *_args: Completed())
    result = _ACCEPTANCE._scenario(
        "basic",
        tmp_path,
        "test-profile",
        "Reply with exactly REAL_PROVIDER_ACCEPTANCE_OK.",
        ("REAL_PROVIDER_ACCEPTANCE_OK",),
    )

    assert result["stream_metrics"] == [
        {
            "elapsed_ms": 1250,
            "delta_count": 2,
            "first_delta_ms": 100,
            "max_delta_gap_ms": 850,
            "retry_count": 1,
        }
    ]


def test_real_acceptance_host_memory_setup_is_offline(tmp_path: Path) -> None:
    _ACCEPTANCE._prepare_memory(tmp_path)
    environment = dict(os.environ)
    environment["UV_CACHE_DIR"] = "/tmp/coquo-uv-cache"
    status = subprocess.run(
        [
            _ACCEPTANCE.sys.executable,
            "-m",
            "coquo",
            "-C",
            str(tmp_path),
            "memory",
            "status",
        ],
        cwd=SCRIPT_PATH.parents[1],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert status.returncode == 0
    assert "effective recall: on" in status.stdout
    assert "records: 1" in status.stdout
