"""Fail-closed Linux command sandbox construction for ``run_command``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
import ctypes.util
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Protocol


DEFAULT_BUBBLEWRAP_PATH = Path("/usr/bin/bwrap")
SANDBOX_PRIVATE_HOME = "/tmp/coquo-home"
SANDBOX_PRIVATE_TMP = "/tmp"
SANDBOX_STATUS_MAX_BYTES = 4096
_SECCOMP_ACTION_ALLOW = 0x7FFF0000
_SECCOMP_ACTION_ERRNO = 0x00050000
_REQUIRED_BLOCKED_SYSCALLS = ("socket", "io_uring_setup")
_OPTIONAL_BLOCKED_SYSCALLS = ("socketcall",)
_REQUIRED_BUBBLEWRAP_OPTIONS = (
    b"--disable-userns",
    b"--block-fd",
    b"--info-fd",
    b"--seccomp",
)
_BUBBLEWRAP_HELP_TIMEOUT_SECONDS = 2.0
_BUBBLEWRAP_HELP_MAX_BYTES = 64 * 1024
_SENSITIVE_HOME_PATHS = (
    ".ssh",
    ".aws",
    ".azure",
    ".claude",
    ".codex",
    ".coquo",
    ".leonervis-code",
    ".docker",
    ".gnupg",
    ".kube",
    ".password-store",
    ".config/gh",
    ".config/gcloud",
    ".local/share/keyrings",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
)
_PRIVATE_ENVIRONMENT = {
    "HOME": SANDBOX_PRIVATE_HOME,
    "TEMP": SANDBOX_PRIVATE_TMP,
    "TMP": SANDBOX_PRIVATE_TMP,
    "TMPDIR": SANDBOX_PRIVATE_TMP,
    "UV_CACHE_DIR": "/tmp/coquo-uv-cache",
    "XDG_CACHE_HOME": "/tmp/coquo-xdg/cache",
    "XDG_CONFIG_HOME": "/tmp/coquo-xdg/config",
    "XDG_DATA_HOME": "/tmp/coquo-xdg/data",
    "XDG_STATE_HOME": "/tmp/coquo-xdg/state",
}
_PRIVATE_DIRECTORIES = tuple(dict.fromkeys(_PRIVATE_ENVIRONMENT.values()))


class CommandSandboxUnavailable(RuntimeError):
    """The required command sandbox could not be prepared safely."""


@dataclass(frozen=True)
class CommandSandboxDependencies:
    """Content-free readiness facts for the production Linux sandbox."""

    platform: str
    platform_supported: bool
    bubblewrap_path: str
    bubblewrap_available: bool
    seccomp_available: bool

    @property
    def ready(self) -> bool:
        return self.platform_supported and self.bubblewrap_available and self.seccomp_available


@dataclass(frozen=True)
class CommandSandboxLaunch:
    """One exact process launch plus descriptors owned by the Host."""

    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    pass_fds: tuple[int, ...] = ()
    close_after_spawn_fds: tuple[int, ...] = ()
    activation_read_fd: int | None = None
    activation_release_fd: int | None = None
    encodes_signals_as_exit_status: bool = False

    def close_after_spawn(self) -> None:
        for descriptor in self.close_after_spawn_fds:
            _close_fd(descriptor)

    def close_without_spawn(self) -> None:
        self.close_after_spawn()
        if self.activation_read_fd is not None:
            _close_fd(self.activation_read_fd)
        if self.activation_release_fd is not None:
            _close_fd(self.activation_release_fd)


class CommandSandbox(Protocol):
    """Construct the trusted executable boundary for one command."""

    def prepare_launch(
        self,
        *,
        workspace: Path,
        cwd: Path,
        argv: tuple[str, ...],
        environment: Mapping[str, str],
    ) -> CommandSandboxLaunch: ...


class LinuxBubblewrapCommandSandbox:
    """Create a read-only-host, workspace-write Linux bubblewrap launch."""

    def __init__(
        self,
        *,
        bubblewrap_path: Path = DEFAULT_BUBBLEWRAP_PATH,
        seccomp_filter_factory: Callable[[], int] | None = None,
        platform: str | None = None,
        masked_read_paths: tuple[Path, ...] = (),
        workspace_writable: bool = True,
    ) -> None:
        self._bubblewrap_path = Path(bubblewrap_path)
        self._seccomp_filter_factory = seccomp_filter_factory or _create_network_seccomp_filter
        self._platform = sys.platform if platform is None else platform
        self._masked_read_paths = tuple(Path(path) for path in masked_read_paths)
        if type(workspace_writable) is not bool:
            raise ValueError("workspace writable option must be boolean")
        self._workspace_writable = workspace_writable

    def prepare_launch(
        self,
        *,
        workspace: Path,
        cwd: Path,
        argv: tuple[str, ...],
        environment: Mapping[str, str],
    ) -> CommandSandboxLaunch:
        if self._platform != "linux":
            raise CommandSandboxUnavailable("command sandbox requires Linux")
        bubblewrap = self._validated_bubblewrap_path()
        original_home = _absolute_home(environment)
        sensitive_mounts = _sensitive_mounts(original_home, workspace)
        masked_read_mounts = _masked_read_mounts(self._masked_read_paths, workspace)

        seccomp_fd: int | None = None
        activation_read_fd: int | None = None
        activation_write_fd: int | None = None
        release_read_fd: int | None = None
        release_write_fd: int | None = None
        try:
            seccomp_fd = self._seccomp_filter_factory()
            if type(seccomp_fd) is not int or seccomp_fd < 0:
                raise CommandSandboxUnavailable("seccomp filter descriptor is invalid")
            activation_read_fd, activation_write_fd = os.pipe2(os.O_CLOEXEC)
            release_read_fd, release_write_fd = os.pipe2(os.O_CLOEXEC)
            sandbox_environment = _sandbox_environment(environment, cwd)
            command = [
                str(bubblewrap),
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
                "/",
                "/",
                "--dev",
                "/dev",
                "--tmpfs",
                "/proc",
                "--tmpfs",
                "/sys",
                "--tmpfs",
                "/run",
                "--tmpfs",
                "/tmp",
            ]
            command.extend(masked_read_mounts)
            command.extend(
                (
                    "--bind" if self._workspace_writable else "--ro-bind",
                    str(workspace),
                    str(workspace),
                )
            )
            command.extend(sensitive_mounts)
            for directory in _PRIVATE_DIRECTORIES:
                if directory != SANDBOX_PRIVATE_TMP:
                    command.extend(("--dir", directory))
            command.append("--clearenv")
            for key, value in sorted(sandbox_environment.items()):
                command.extend(("--setenv", key, value))
            command.extend(
                (
                    "--chdir",
                    str(cwd),
                    "--seccomp",
                    str(seccomp_fd),
                    "--block-fd",
                    str(release_read_fd),
                    "--info-fd",
                    str(activation_write_fd),
                    "--",
                    *argv,
                )
            )
            return CommandSandboxLaunch(
                argv=tuple(command),
                cwd=workspace,
                environment=dict(environment),
                pass_fds=(seccomp_fd, activation_write_fd, release_read_fd),
                close_after_spawn_fds=(seccomp_fd, activation_write_fd, release_read_fd),
                activation_read_fd=activation_read_fd,
                activation_release_fd=release_write_fd,
                encodes_signals_as_exit_status=True,
            )
        except CommandSandboxUnavailable:
            raise
        except (OSError, ValueError) as error:
            raise CommandSandboxUnavailable("command sandbox setup failed") from error
        finally:
            if sys.exc_info()[0] is not None:
                for descriptor in (
                    seccomp_fd,
                    activation_read_fd,
                    activation_write_fd,
                    release_read_fd,
                    release_write_fd,
                ):
                    if descriptor is not None:
                        _close_fd(descriptor)

    def inspect_dependencies(self) -> CommandSandboxDependencies:
        """Check required local dependencies without starting a requested command."""
        platform_supported = self._platform == "linux"
        bubblewrap_available = False
        seccomp_available = False
        if platform_supported:
            try:
                bubblewrap = self._validated_bubblewrap_path()
            except CommandSandboxUnavailable:
                pass
            else:
                bubblewrap_available = _bubblewrap_supports_required_options(bubblewrap)
            if bubblewrap_available:
                descriptor: int | None = None
                try:
                    descriptor = self._seccomp_filter_factory()
                    seccomp_available = type(descriptor) is int and descriptor >= 0
                except (CommandSandboxUnavailable, OSError, ValueError):
                    pass
                finally:
                    if descriptor is not None:
                        _close_fd(descriptor)
        return CommandSandboxDependencies(
            platform=self._platform,
            platform_supported=platform_supported,
            bubblewrap_path=str(self._bubblewrap_path),
            bubblewrap_available=bubblewrap_available,
            seccomp_available=seccomp_available,
        )

    def _validated_bubblewrap_path(self) -> Path:
        path = self._bubblewrap_path
        if not path.is_absolute():
            raise CommandSandboxUnavailable("bubblewrap path must be absolute")
        try:
            info = path.stat()
        except OSError as error:
            raise CommandSandboxUnavailable("bubblewrap is unavailable") from error
        if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
            raise CommandSandboxUnavailable("bubblewrap is unavailable")
        return path


def sandbox_activation_succeeded(data: bytes, *, read_error: bool) -> bool:
    """Validate bubblewrap's private activation report without exposing it."""
    if read_error or not data or len(data) > SANDBOX_STATUS_MAX_BYTES:
        return False
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(value, dict) and type(value.get("child-pid")) is int and value["child-pid"] > 0
    )


def _bubblewrap_supports_required_options(path: Path) -> bool:
    """Check the fixed binary's bounded help surface before reporting it usable."""
    try:
        completed = subprocess.run(
            (str(path), "--help"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=_BUBBLEWRAP_HELP_TIMEOUT_SECONDS,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = completed.stdout
    return (
        completed.returncode == 0
        and len(output) <= _BUBBLEWRAP_HELP_MAX_BYTES
        and all(option in output for option in _REQUIRED_BUBBLEWRAP_OPTIONS)
    )


def _sandbox_environment(environment: Mapping[str, str], cwd: Path) -> dict[str, str]:
    result = {
        key: value
        for key, value in environment.items()
        if key not in _PRIVATE_ENVIRONMENT and key not in {"PWD"}
    }
    result.update(_PRIVATE_ENVIRONMENT)
    result["PWD"] = str(cwd)
    return result


def _absolute_home(environment: Mapping[str, str]) -> Path | None:
    value = environment.get("HOME")
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        return None
    return Path(value)


def _sensitive_mounts(home: Path | None, workspace: Path) -> list[str]:
    if home is None:
        return []
    mounts: list[str] = []
    for relative in _SENSITIVE_HOME_PATHS:
        target = home.joinpath(*relative.split("/"))
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise CommandSandboxUnavailable("sensitive path could not be inspected") from error
        if workspace == target or workspace.is_relative_to(target):
            raise CommandSandboxUnavailable("workspace conflicts with a sensitive path")
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            mounts.extend(("--tmpfs", str(target)))
        else:
            mounts.extend(("--ro-bind", "/dev/null", str(target)))
    return mounts


def _masked_read_mounts(paths: tuple[Path, ...], workspace: Path) -> list[str]:
    mounts: list[str] = []
    for target in paths:
        if not target.is_absolute():
            raise CommandSandboxUnavailable("masked read path must be absolute")
        try:
            info = target.lstat()
        except OSError as error:
            raise CommandSandboxUnavailable("masked read path could not be inspected") from error
        if target == workspace or target.is_relative_to(workspace):
            raise CommandSandboxUnavailable("masked read path conflicts with workspace")
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            mounts.extend(("--tmpfs", str(target)))
        else:
            mounts.extend(("--ro-bind", "/dev/null", str(target)))
    return mounts


def _create_network_seccomp_filter() -> int:
    library_name = ctypes.util.find_library("seccomp")
    if library_name is None:
        raise CommandSandboxUnavailable("libseccomp is unavailable")
    try:
        library = ctypes.CDLL(library_name, use_errno=True)
    except OSError as error:
        raise CommandSandboxUnavailable("libseccomp is unavailable") from error

    try:
        library.seccomp_init.argtypes = (ctypes.c_uint32,)
        library.seccomp_init.restype = ctypes.c_void_p
        library.seccomp_syscall_resolve_name.argtypes = (ctypes.c_char_p,)
        library.seccomp_syscall_resolve_name.restype = ctypes.c_int
        library.seccomp_rule_add.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
        )
        library.seccomp_rule_add.restype = ctypes.c_int
        library.seccomp_export_bpf.argtypes = (ctypes.c_void_p, ctypes.c_int)
        library.seccomp_export_bpf.restype = ctypes.c_int
        library.seccomp_release.argtypes = (ctypes.c_void_p,)
        library.seccomp_release.restype = None
    except AttributeError as error:
        raise CommandSandboxUnavailable("libseccomp API is unavailable") from error

    context = library.seccomp_init(_SECCOMP_ACTION_ALLOW)
    if not context:
        raise CommandSandboxUnavailable("seccomp filter initialization failed")
    descriptor: int | None = None
    try:
        blocked: list[int] = []
        for name in _REQUIRED_BLOCKED_SYSCALLS:
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number < 0:
                raise CommandSandboxUnavailable("required seccomp syscall is unavailable")
            blocked.append(number)
        for name in _OPTIONAL_BLOCKED_SYSCALLS:
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number >= 0:
                blocked.append(number)
        action = _SECCOMP_ACTION_ERRNO | errno.EPERM
        for number in blocked:
            if library.seccomp_rule_add(context, action, number, 0) != 0:
                raise CommandSandboxUnavailable("seccomp rule installation failed")
        descriptor = os.memfd_create("coquo-command-seccomp", os.MFD_CLOEXEC)
        if library.seccomp_export_bpf(context, descriptor) != 0:
            raise CommandSandboxUnavailable("seccomp filter export failed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except (AttributeError, OSError) as error:
        raise CommandSandboxUnavailable("seccomp filter setup failed") from error
    finally:
        library.seccomp_release(context)
        if sys.exc_info()[0] is not None and descriptor is not None:
            _close_fd(descriptor)


def _close_fd(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass
