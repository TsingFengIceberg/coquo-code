from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def run_cli(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    return subprocess.run(
        [sys.executable, "-m", "coquo", *arguments],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=environment,
        text=True,
    )


def test_module_entry_persists_profile_lifecycle_across_processes(tmp_path) -> None:
    added = run_cli(
        tmp_path,
        "provider",
        "add",
        "local-dev",
        "--provider",
        "custom",
        "--model",
        "Qwen/Qwen3.5",
        "--protocol",
        "openai-compatible",
        "--base-url",
        "http://127.0.0.1:11434",
    )
    assert added.returncode == 0
    assert added.stdout == "Saved provider profile local-dev.\n"
    assert added.stderr == ""

    selected = run_cli(tmp_path, "provider", "use", "local-dev")
    assert selected.returncode == 0
    assert selected.stdout == "Using provider profile local-dev at project scope.\n"
    assert selected.stderr == ""

    listed = run_cli(tmp_path, "provider", "list")
    assert listed.returncode == 0
    assert listed.stdout == "local-dev *: custom/Qwen/Qwen3.5\n"
    assert listed.stderr == ""

    shown = run_cli(tmp_path, "provider", "show", "local-dev")
    assert shown.returncode == 0
    assert "base URL: http://127.0.0.1:11434/v1" in shown.stdout
    assert "credential: not required" in shown.stdout

    cleared = run_cli(tmp_path, "provider", "clear")
    assert cleared.returncode == 0
    assert cleared.stdout == "Cleared project active provider profile.\n"

    removed = run_cli(tmp_path, "provider", "remove", "local-dev")
    assert removed.returncode == 0
    assert removed.stdout == "Removed provider profile local-dev.\n"


def test_module_entry_profile_configuration_never_renders_key_value(tmp_path) -> None:
    environment = dict(os.environ)
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    environment["VENDOR_API_KEY"] = "secret-must-not-render"
    added = subprocess.run(
        [
            sys.executable,
            "-m",
            "coquo",
            "provider",
            "add",
            "vendor",
            "--provider",
            "custom",
            "--model",
            "vendor/model",
            "--protocol",
            "openai-compatible",
            "--base-url",
            "https://gateway.example/v1",
            "--api-key-env",
            "VENDOR_API_KEY",
        ],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=environment,
        text=True,
    )
    assert added.returncode == 0

    shown = subprocess.run(
        [sys.executable, "-m", "coquo", "provider", "show", "vendor"],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=environment,
        text=True,
    )
    assert shown.returncode == 0
    assert "credential: configured" in shown.stdout
    assert "VENDOR_API_KEY" not in shown.stdout
    assert "secret-must-not-render" not in shown.stdout
    assert "secret-must-not-render" not in shown.stderr


def test_module_entry_profile_native_search_defaults_and_explicit_adapter(tmp_path) -> None:
    builtin = run_cli(
        tmp_path,
        "provider",
        "add",
        "dashscope-search",
        "--provider",
        "dashscope",
        "--model",
        "qwen-plus",
    )
    assert builtin.returncode == 0
    shown_builtin = run_cli(tmp_path, "provider", "show", "dashscope-search")
    assert shown_builtin.returncode == 0
    assert "native search: available; enabled by default" in shown_builtin.stdout
    assert "native search adapter: dashscope-enable-search-v1" in shown_builtin.stdout
    assert "native search source: built-in" in shown_builtin.stdout

    custom = run_cli(
        tmp_path,
        "provider",
        "add",
        "custom-no-search",
        "--provider",
        "custom",
        "--model",
        "vendor/model",
        "--protocol",
        "openai-compatible",
        "--base-url",
        "https://gateway.example/v1",
    )
    assert custom.returncode == 0
    shown_custom = run_cli(tmp_path, "provider", "show", "custom-no-search")
    assert shown_custom.returncode == 0
    assert "native search: unavailable" in shown_custom.stdout
    assert "native search adapter: <none>" in shown_custom.stdout
    assert "native search source: unavailable" in shown_custom.stdout

    explicit = run_cli(
        tmp_path,
        "provider",
        "add",
        "custom-search",
        "--provider",
        "custom",
        "--model",
        "vendor/search-model",
        "--protocol",
        "openai-compatible",
        "--base-url",
        "https://gateway.example/v1",
        "--native-search-adapter",
        "openai-chat-web-search-options-v1",
    )
    assert explicit.returncode == 0
    shown_explicit = run_cli(tmp_path, "provider", "show", "custom-search")
    assert shown_explicit.returncode == 0
    assert "native search: available; enabled by default" in shown_explicit.stdout
    assert "native search adapter: openai-chat-web-search-options-v1" in shown_explicit.stdout
    assert "native search source: profile" in shown_explicit.stdout

    stored = json.loads((tmp_path / "xdg" / "coquo" / "providers.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == 5
    stored_profiles = list(stored["profiles"].values())
    custom_search = next(
        profile for profile in stored_profiles if profile["name"] == "custom-search"
    )
    assert custom_search["native_search_adapter"] == "openai-chat-web-search-options-v1"
    assert custom_search["native_search_manifest"] is None


def test_deepseek_v4_flash_profile_defaults_to_responses_and_native_search(tmp_path) -> None:
    added = run_cli(
        tmp_path,
        "provider",
        "add",
        "deepseek-flash",
        "--provider",
        "deepseek",
        "--model",
        "deepseek-v4-flash",
        "--api-key-env",
        "DEEPSEEK_TEST_KEY",
    )

    assert added.returncode == 0
    shown = run_cli(tmp_path, "provider", "show", "deepseek-flash")
    assert shown.returncode == 0
    assert "protocol: openai_responses" in shown.stdout
    assert "native search: available; enabled by default" in shown.stdout
    assert "native search adapter: openai-responses-web-search-v1" in shown.stdout


def test_module_entry_accepts_bounded_native_search_manifest(tmp_path) -> None:
    manifest_path = tmp_path / "native-search.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "future-vendor-search-v1",
                "request": {
                    "extra_body": {"enable_search": True},
                    "server_tool": None,
                },
                "response": {"citation_format": "openai-url-annotations"},
            }
        ),
        encoding="utf-8",
    )

    added = run_cli(
        tmp_path,
        "provider",
        "add",
        "manifest-search",
        "--provider",
        "custom",
        "--model",
        "vendor/model",
        "--protocol",
        "openai-compatible",
        "--base-url",
        "https://gateway.example/v1",
        "--native-search-manifest",
        str(manifest_path),
    )
    assert added.returncode == 0
    shown = run_cli(tmp_path, "provider", "show", "manifest-search")
    assert shown.returncode == 0
    assert "native search adapter: custom-manifest-v1" in shown.stdout
    assert "native search source: custom-manifest" in shown.stdout
    assert "native search manifest: future-vendor-search-v1" in shown.stdout
    assert "native search manifest digest: sha256:" in shown.stdout

    stored = json.loads((tmp_path / "xdg" / "coquo" / "providers.json").read_text(encoding="utf-8"))
    stored_profile = next(iter(stored["profiles"].values()))
    assert stored_profile["native_search_adapter"] is None
    assert stored_profile["native_search_manifest"]["id"] == "future-vendor-search-v1"


def test_module_entry_rejects_unsafe_native_search_manifest(tmp_path) -> None:
    manifest_path = tmp_path / "unsafe-native-search.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "unsafe-search-v1",
                "request": {
                    "extra_body": {"authorization": "must-not-be-stored"},
                    "server_tool": None,
                },
                "response": {"citation_format": "none"},
            }
        ),
        encoding="utf-8",
    )

    rejected = run_cli(
        tmp_path,
        "provider",
        "add",
        "unsafe-search",
        "--provider",
        "custom",
        "--model",
        "vendor/model",
        "--protocol",
        "openai-compatible",
        "--base-url",
        "https://gateway.example/v1",
        "--native-search-manifest",
        str(manifest_path),
    )
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert "cannot contain credential field: authorization" in rejected.stderr
    assert "must-not-be-stored" not in rejected.stderr
