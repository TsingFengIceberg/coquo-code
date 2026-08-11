from __future__ import annotations

import ctypes.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

import pytest

import coquo.tools.command_sandbox as sandbox_module
from coquo.core.contracts import ToolArguments, ToolUse
from coquo.tools.command_sandbox import (
    CommandSandboxUnavailable,
    LinuxBubblewrapCommandSandbox,
    SANDBOX_PRIVATE_HOME,
    sandbox_activation_succeeded,
)
from coquo.tools.run_command import (
    RunCommandExecutionStatus,
    RunCommandOutcome,
    RunCommandTool,
)


def _filter_fd() -> int:
    return os.memfd_create("test-seccomp", os.MFD_CLOEXEC)


def _request(argv: list[str]) -> ToolUse:
    return ToolUse(
        "sandbox-command",
        "run_command",
        ToolArguments.from_mapping({"argv": argv, "cwd": ".", "timeout_seconds": 10}),
    )


def _payload(result) -> dict[str, object]:
    return json.loads(result.tool_result.content)


def _close_launch(launch) -> None:
    launch.close_without_spawn()


def test_bubblewrap_launch_has_fixed_mount_namespace_environment_and_seccomp_order(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    (home / ".ssh").mkdir(parents=True)
    (home / ".coquo").mkdir()
    (home / ".leonervis-code").mkdir()
    (home / ".netrc").write_text("secret", encoding="utf-8")
    bwrap = tmp_path / "bwrap"
    bwrap.write_text("", encoding="utf-8")
    bwrap.chmod(0o755)
    sandbox = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=bwrap,
        seccomp_filter_factory=_filter_fd,
        platform="linux",
    )

    launch = sandbox.prepare_launch(
        workspace=workspace,
        cwd=workspace,
        argv=("/usr/bin/printf", "hello"),
        environment={"HOME": str(home), "PATH": "/usr/bin", "SECRET": "excluded"},
    )
    try:
        argv = launch.argv
        assert argv[0] == str(bwrap)
        assert argv[1:11] == (
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--disable-userns",
            "--cap-drop",
            "ALL",
            "--ro-bind",
        )
        assert _subsequence(argv, ("--ro-bind", "/", "/"))
        assert _subsequence(argv, ("--bind", str(workspace), str(workspace)))
        assert _subsequence(argv, ("--tmpfs", "/proc"))
        assert _subsequence(argv, ("--tmpfs", "/sys"))
        assert _subsequence(argv, ("--tmpfs", "/run"))
        assert _subsequence(argv, ("--tmpfs", "/tmp"))
        assert _subsequence(argv, ("--tmpfs", str(home / ".ssh")))
        assert _subsequence(argv, ("--tmpfs", str(home / ".coquo")))
        assert _subsequence(argv, ("--tmpfs", str(home / ".leonervis-code")))
        assert _subsequence(argv, ("--ro-bind", "/dev/null", str(home / ".netrc")))
        assert _subsequence(argv, ("--setenv", "HOME", SANDBOX_PRIVATE_HOME))
        assert _subsequence(argv, ("--setenv", "PWD", str(workspace)))
        assert argv[-3:] == ("--", "/usr/bin/printf", "hello")
        assert _subsequence(argv, ("--block-fd", str(launch.pass_fds[2])))
        assert len(launch.pass_fds) == 3
        assert launch.activation_read_fd is not None
        assert launch.activation_release_fd is not None
        assert launch.encodes_signals_as_exit_status
        assert launch.environment["SECRET"] == "excluded"
    finally:
        _close_launch(launch)


def test_bubblewrap_can_bind_the_workspace_read_only_for_host_verification(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bwrap = tmp_path / "bwrap"
    bwrap.write_text("", encoding="utf-8")
    bwrap.chmod(0o755)
    sandbox = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=bwrap,
        seccomp_filter_factory=_filter_fd,
        platform="linux",
        workspace_writable=False,
    )

    launch = sandbox.prepare_launch(
        workspace=workspace,
        cwd=workspace,
        argv=("/usr/bin/true",),
        environment={"PATH": "/usr/bin"},
    )
    try:
        assert _subsequence(launch.argv, ("--ro-bind", str(workspace), str(workspace)))
        assert not _subsequence(launch.argv, ("--bind", str(workspace), str(workspace)))
    finally:
        _close_launch(launch)


def test_bubblewrap_masks_eval_sources_before_rebinding_nested_workspace(tmp_path: Path) -> None:
    source_checkout = tmp_path / "source"
    workspace = source_checkout / "task"
    workspace.mkdir(parents=True)
    hidden_file = tmp_path / "hidden.py"
    hidden_file.write_text("private", encoding="utf-8")
    bwrap = tmp_path / "bwrap"
    bwrap.write_text("", encoding="utf-8")
    bwrap.chmod(0o755)
    sandbox = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=bwrap,
        seccomp_filter_factory=_filter_fd,
        platform="linux",
        masked_read_paths=(source_checkout, hidden_file),
    )

    launch = sandbox.prepare_launch(
        workspace=workspace,
        cwd=workspace,
        argv=("/usr/bin/true",),
        environment={"PATH": "/usr/bin"},
    )
    try:
        argv = launch.argv
        assert _subsequence(argv, ("--tmpfs", str(source_checkout)))
        assert _subsequence(argv, ("--ro-bind", "/dev/null", str(hidden_file)))
        assert argv.index(str(source_checkout)) < argv.index(str(workspace))
    finally:
        _close_launch(launch)


def test_bubblewrap_rejects_masked_read_path_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    hidden = workspace / "hidden.py"
    hidden.write_text("private", encoding="utf-8")
    bwrap = tmp_path / "bwrap"
    bwrap.write_text("", encoding="utf-8")
    bwrap.chmod(0o755)
    sandbox = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=bwrap,
        seccomp_filter_factory=_filter_fd,
        platform="linux",
        masked_read_paths=(hidden,),
    )

    with pytest.raises(CommandSandboxUnavailable, match="conflicts with workspace"):
        sandbox.prepare_launch(
            workspace=workspace,
            cwd=workspace,
            argv=("/usr/bin/true",),
            environment={"PATH": "/usr/bin"},
        )


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_unsupported_platform_fails_closed(tmp_path: Path, platform: str) -> None:
    sandbox = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=tmp_path / "missing",
        seccomp_filter_factory=_filter_fd,
        platform=platform,
    )

    with pytest.raises(CommandSandboxUnavailable, match="requires Linux"):
        sandbox.prepare_launch(
            workspace=tmp_path,
            cwd=tmp_path,
            argv=("/usr/bin/true",),
            environment={},
        )


def test_missing_bwrap_and_seccomp_setup_failure_fail_before_launch(tmp_path: Path) -> None:
    missing = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=tmp_path / "missing",
        seccomp_filter_factory=_filter_fd,
        platform="linux",
    )
    with pytest.raises(CommandSandboxUnavailable, match="bubblewrap is unavailable"):
        missing.prepare_launch(
            workspace=tmp_path,
            cwd=tmp_path,
            argv=("/usr/bin/true",),
            environment={},
        )

    bwrap = tmp_path / "bwrap"
    bwrap.write_text("", encoding="utf-8")
    bwrap.chmod(0o755)

    def unavailable_filter() -> int:
        raise CommandSandboxUnavailable("libseccomp is unavailable")

    unavailable = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=bwrap,
        seccomp_filter_factory=unavailable_filter,
        platform="linux",
    )
    with pytest.raises(CommandSandboxUnavailable, match="libseccomp is unavailable"):
        unavailable.prepare_launch(
            workspace=tmp_path,
            cwd=tmp_path,
            argv=("/usr/bin/true",),
            environment={},
        )


def test_dependency_inspection_is_content_free_and_does_not_spawn(tmp_path: Path) -> None:
    bwrap = tmp_path / "bwrap"
    bwrap.write_text("", encoding="utf-8")
    bwrap.chmod(0o755)
    ready = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=bwrap,
        seccomp_filter_factory=_filter_fd,
        platform="linux",
    ).inspect_dependencies()
    missing = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=tmp_path / "missing",
        seccomp_filter_factory=_filter_fd,
        platform="linux",
    ).inspect_dependencies()

    assert ready.ready
    assert ready.bubblewrap_available
    assert ready.seccomp_available
    assert not missing.ready
    assert not missing.bubblewrap_available
    assert not missing.seccomp_available


def test_run_command_reports_unavailable_without_host_fallback(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    sandbox = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=tmp_path / "missing",
        seccomp_filter_factory=_filter_fd,
        platform="linux",
    )
    tool = RunCommandTool(tmp_path, command_sandbox=sandbox)
    prepared = tool.prepare(
        _request([sys.executable, "-c", "from pathlib import Path; Path('must-not-exist').touch()"])
    )

    result = tool.execute_detailed(prepared)

    assert result.outcome == RunCommandOutcome.FAILED
    assert result.result_code == "command_sandbox_unavailable"
    assert result.observation.status == RunCommandExecutionStatus.SANDBOX_REJECTED
    assert _payload(result)["status"] == "sandbox-rejected"
    assert not marker.exists()


def test_bubblewrap_runtime_setup_failure_is_unavailable_without_host_fallback(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-exist"
    fake_bwrap = tmp_path / "bwrap"
    fake_bwrap.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_bwrap.chmod(0o755)
    sandbox = LinuxBubblewrapCommandSandbox(
        bubblewrap_path=fake_bwrap,
        seccomp_filter_factory=_filter_fd,
        platform="linux",
    )
    tool = RunCommandTool(tmp_path, command_sandbox=sandbox)
    prepared = tool.prepare(
        _request([sys.executable, "-c", "from pathlib import Path; Path('must-not-exist').touch()"])
    )

    result = tool.execute_detailed(prepared)

    assert result.result_code == "command_sandbox_unavailable"
    assert result.observation.status == RunCommandExecutionStatus.SANDBOX_REJECTED
    assert not marker.exists()


def test_invalid_or_missing_activation_report_is_rejected() -> None:
    assert sandbox_activation_succeeded(b'{"child-pid":12}\n', read_error=False)
    assert not sandbox_activation_succeeded(b"", read_error=False)
    assert not sandbox_activation_succeeded(b'{"child-pid":0}\n', read_error=False)
    assert not sandbox_activation_succeeded(b'{"child-pid":12}\n', read_error=True)


@pytest.mark.skipif(
    sys.platform != "linux"
    or shutil.which("bwrap") is None
    or ctypes.util.find_library("seccomp") is None,
    reason="Linux bubblewrap and libseccomp are required",
)
def test_real_sandbox_inspection_verifies_fixed_activation_probe(tmp_path: Path) -> None:
    inspection = RunCommandTool(tmp_path).inspect_sandbox(verify_activation=True)

    assert inspection.dependencies.ready
    assert inspection.activation_verified is True
    assert inspection.available
    assert inspection.result_code == "command_succeeded"


@pytest.mark.skipif(
    sys.platform != "linux"
    or shutil.which("bwrap") is None
    or ctypes.util.find_library("seccomp") is None,
    reason="Linux bubblewrap and libseccomp are required",
)
def test_real_sandbox_allows_workspace_write_but_blocks_host_write_and_network(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = Path("/etc/coquo-command-sandbox-test")
    code = (
        "from pathlib import Path; import socket; "
        "Path('inside.txt').write_text('inside', encoding='utf-8'); "
        f"outside=Path({str(outside)!r}); "
        "\ntry: outside.write_text('outside', encoding='utf-8')\n"
        "except OSError as error: print('outside-denied', error.errno)\n"
        "try: socket.socket()\n"
        "except OSError as error: print('socket', error.errno)"
    )
    tool = RunCommandTool(workspace, environment={"PATH": "/usr/bin", "HOME": str(tmp_path)})

    result = tool.execute_detailed(tool.prepare(_request([sys.executable, "-c", code])))
    data = _payload(result)

    assert result.result_code == "command_succeeded"
    assert (workspace / "inside.txt").read_text(encoding="utf-8") == "inside"
    assert not outside.exists()
    assert "outside-denied" in data["stdout"]["text"]
    assert "socket 1\n" in data["stdout"]["text"]


@pytest.mark.skipif(
    sys.platform != "linux"
    or shutil.which("bwrap") is None
    or ctypes.util.find_library("seccomp") is None,
    reason="Linux bubblewrap and libseccomp are required",
)
def test_real_read_only_sandbox_prevents_verifier_workspace_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = workspace / "protected.txt"
    protected.write_text("before\n", encoding="utf-8")
    sandbox = LinuxBubblewrapCommandSandbox(workspace_writable=False)
    tool = RunCommandTool(
        workspace,
        environment={"PATH": "/usr/bin"},
        command_sandbox=sandbox,
    )
    code = "from pathlib import Path; Path('protected.txt').write_text('after\\n')"

    result = tool.execute_detailed(tool.prepare(_request(["/usr/bin/python3", "-c", code])))

    assert result.result_code == "command_exited_nonzero"
    assert result.observation.exit_code != 0
    assert protected.read_text(encoding="utf-8") == "before\n"


@pytest.mark.skipif(
    sys.platform != "linux"
    or shutil.which("bwrap") is None
    or ctypes.util.find_library("seccomp") is None,
    reason="Linux bubblewrap and libseccomp are required",
)
def test_real_sandbox_masks_sensitive_home_and_kernel_runtime_views(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = workspace / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_test").write_text("private", encoding="utf-8")
    (home / ".netrc").write_text("credential", encoding="utf-8")
    code = (
        "from pathlib import Path; import os; "
        f"home=Path({str(home)!r}); "
        "print('HOME', os.environ['HOME']); "
        "print('ssh', list((home/'.ssh').iterdir())); "
        "\ntry: print('netrc', (home/'.netrc').read_bytes())\n"
        "except OSError as error: print('netrc-denied', error.errno)\n"
        "print('runtime', [len(list(Path(p).iterdir())) for p in ('/proc','/sys','/run')])"
    )
    tool = RunCommandTool(workspace, environment={"PATH": "/usr/bin", "HOME": str(home)})

    result = tool.execute_detailed(tool.prepare(_request([sys.executable, "-c", code])))
    data = _payload(result)

    assert result.result_code == "command_succeeded"
    assert f"HOME {SANDBOX_PRIVATE_HOME}\n" in data["stdout"]["text"]
    assert "ssh []\n" in data["stdout"]["text"]
    assert "netrc-denied" in data["stdout"]["text"]
    assert "runtime [0, 0, 0]\n" in data["stdout"]["text"]


@pytest.mark.skipif(
    sys.platform != "linux"
    or shutil.which("bwrap") is None
    or ctypes.util.find_library("seccomp") is None,
    reason="Linux bubblewrap and libseccomp are required",
)
def test_real_sandbox_masks_source_parent_and_rebinds_task_workspace(tmp_path: Path) -> None:
    source_checkout = tmp_path / "source"
    workspace = source_checkout / "task"
    workspace.mkdir(parents=True)
    secret = source_checkout / "hidden-tests.py"
    secret.write_text("private", encoding="utf-8")
    sandbox = LinuxBubblewrapCommandSandbox(masked_read_paths=(source_checkout,))
    tool = RunCommandTool(
        workspace,
        environment={"PATH": "/usr/bin"},
        command_sandbox=sandbox,
    )
    code = (
        "from pathlib import Path; "
        f"print('hidden', Path({str(secret)!r}).exists()); "
        "Path('result.txt').write_text('visible', encoding='utf-8')"
    )

    result = tool.execute_detailed(tool.prepare(_request(["/usr/bin/python3", "-c", code])))
    data = _payload(result)

    assert result.result_code == "command_succeeded"
    assert data["stdout"]["text"] == "hidden False\n"
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "visible"


@pytest.mark.skipif(
    sys.platform != "linux"
    or shutil.which("bwrap") is None
    or ctypes.util.find_library("seccomp") is None,
    reason="Linux bubblewrap and libseccomp are required",
)
def test_real_sandbox_timeout_cleans_descendants_before_they_can_write_late(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    child = "import pathlib,time; time.sleep(2); pathlib.Path('late.txt').write_text('late')"
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(10)"
    )
    tool = RunCommandTool(workspace, environment={"PATH": "/usr/bin", "HOME": str(tmp_path)})

    result = tool.execute_detailed(
        tool.prepare(
            ToolUse(
                "sandbox-command",
                "run_command",
                ToolArguments.from_mapping(
                    {"argv": [sys.executable, "-c", parent], "cwd": ".", "timeout_seconds": 1}
                ),
            )
        )
    )

    assert result.result_code == "command_timed_out"
    assert result.observation.cleanup_complete
    time.sleep(1.5)
    assert not (workspace / "late.txt").exists()


@pytest.mark.skipif(
    sys.platform != "linux"
    or shutil.which("bwrap") is None
    or ctypes.util.find_library("seccomp") is None,
    reason="Linux bubblewrap and libseccomp are required",
)
def test_real_sandbox_restores_bubblewrap_encoded_signal_status(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = RunCommandTool(workspace, environment={"PATH": "/usr/bin", "HOME": str(tmp_path)})

    result = tool.execute_detailed(
        tool.prepare(
            _request(
                [
                    sys.executable,
                    "-c",
                    "import os,signal; os.kill(os.getpid(), signal.SIGTERM)",
                ]
            )
        )
    )

    assert result.result_code == "command_signaled"
    assert result.outcome == RunCommandOutcome.PARTIAL
    assert result.observation.signal == 15
    assert result.observation.exit_code is None


def test_libseccomp_absence_is_structured(monkeypatch) -> None:
    monkeypatch.setattr(sandbox_module.ctypes.util, "find_library", lambda name: None)

    with pytest.raises(CommandSandboxUnavailable, match="libseccomp is unavailable"):
        sandbox_module._create_network_seccomp_filter()


def _subsequence(values: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    width = len(expected)
    return any(
        values[index : index + width] == expected for index in range(len(values) - width + 1)
    )
