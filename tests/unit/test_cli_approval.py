from __future__ import annotations

from dataclasses import replace
import io
from pathlib import Path
from threading import Thread
import time

import pytest

from leonervis_code.cli.approval import TerminalApprovalBroker
from leonervis_code.cli.main import main, terminal_approval_handler
from leonervis_code.cli.repl import run_repl
from leonervis_code.core.action_coordinator import ApprovalResolution, HumanApprovalRequest
from leonervis_code.core.approval_preview import (
    ApprovalPreviewKind,
    build_file_change_preview,
    build_metadata_preview,
)
from leonervis_code.core.actions import ActionIdentity, ActionLease, ActionPrecondition
from leonervis_code.core.contracts import AssistantText, ToolArguments, ToolUse
from leonervis_code.core.cancellation import TurnCancellation
from leonervis_code.core.permissions import (
    ApprovalMode,
    PermissionAction,
    PermissionMode,
    PermissionDecision,
    PermissionReason,
    PermissionResult,
)
from leonervis_code.providers.request_context import RequestTokenCount, RequestTokenCountMethod
from leonervis_code.session import ProjectSession


class ToolProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    def count_input_tokens(self, _request):
        return RequestTokenCount(100, RequestTokenCountMethod.ESTIMATED)

    def respond(self, request):
        self.requests.append(request)
        self.calls += 1
        if self.calls == 1:
            return ToolUse(
                "write-1",
                "write_file",
                ToolArguments.from_mapping(
                    {"path": "note.txt", "content": "secret model content\n"}
                ),
            )
        return AssistantText("finished")


class NoReadInput(io.StringIO):
    def readline(self, *_args, **_kwargs):
        raise AssertionError("one-shot approval must not read stdin")


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def common(tmp_path: Path, provider: ToolProvider) -> dict:
    return {
        "cwd": tmp_path,
        "environment": {},
        "user_profile_path": tmp_path / "user.json",
        "project_profile_path": tmp_path / "project.json",
        "provider_factory": lambda route, *, environment: provider,
    }


def real_args(*tail: str) -> list[str]:
    return [
        "--model",
        "custom/model",
        "--provider-protocol",
        "openai-compatible",
        "--base-url",
        "http://127.0.0.1:11434/v1",
        *tail,
    ]


def approval_request() -> HumanApprovalRequest:
    identity = ActionIdentity(
        request_id="12345678-1234-4234-9234-123456789abc",
        tool_use_id="write-1",
        tool_name="write_file",
        arguments=ToolArguments.from_mapping(
            {"path": "note.txt", "content": "secret model content\n"}
        ),
        action=PermissionAction.WORKSPACE_CREATE,
        workspace_fingerprint=f"v1-{'1' * 64}",
        lease=ActionLease(
            "22345678-1234-4234-9234-123456789abc",
            "32345678-1234-4234-9234-123456789abc",
            0,
            f"ctx-v1-{'2' * 64}",
        ),
        precondition=ActionPrecondition.path_absent(),
    )
    return HumanApprovalRequest(
        identity,
        PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_CREATE,
        ),
    )


@pytest.mark.parametrize(
    ("input_text", "expected"),
    [
        ("y\n", ApprovalResolution.ACCEPT),
        ("yes\n", ApprovalResolution.ACCEPT),
        ("\n", ApprovalResolution.REJECT),
        ("n\n", ApprovalResolution.REJECT),
        ("no\n", ApprovalResolution.REJECT),
        ("c\n", ApprovalResolution.CANCEL),
        ("cancel\n", ApprovalResolution.CANCEL),
        ("", ApprovalResolution.CANCEL),
        ("bad\nwrong\nmaybe\n", ApprovalResolution.CANCEL),
    ],
)
def test_terminal_approval_handler_has_bounded_explicit_resolutions(
    input_text: str, expected: ApprovalResolution
) -> None:
    stdin = io.StringIO(input_text)
    stdout = io.StringIO()

    resolution = terminal_approval_handler(stdin, stdout)(approval_request())

    assert resolution == expected
    presentation = stdout.getvalue()
    assert "workspace-create write_file" in presentation
    assert "path='note.txt'" in presentation
    assert "bytes=21" in presentation
    assert "secret model content" not in presentation
    assert presentation.count("Please answer") <= 3


def test_terminal_approval_keyboard_interrupt_cancels() -> None:
    class InterruptingInput(io.StringIO):
        def readline(self, *_args, **_kwargs):
            raise KeyboardInterrupt

    stdout = io.StringIO()

    assert (
        terminal_approval_handler(InterruptingInput(), stdout)(approval_request())
        == ApprovalResolution.CANCEL
    )
    assert stdout.getvalue().endswith("\n")


def test_terminal_approval_broker_preserves_exact_request_and_single_resolution() -> None:
    published = []
    broker = TerminalApprovalBroker(lambda turn_id, request: published.append((turn_id, request)))
    cancellation = TurnCancellation()
    request = approval_request()
    resolutions = []
    broker.activate(7, cancellation)
    thread = Thread(target=lambda: resolutions.append(broker(request)))

    thread.start()
    deadline = time.monotonic() + 1
    while not published and time.monotonic() < deadline:
        time.sleep(0.01)
    assert published == [(7, request)]
    assert broker.pending_request is request
    assert broker.resolve(ApprovalResolution.ACCEPT)
    assert not broker.resolve(ApprovalResolution.REJECT)
    thread.join(1)
    broker.deactivate(7)

    assert resolutions == [ApprovalResolution.ACCEPT]
    assert broker.pending_request is None


def test_one_shot_ask_never_reads_stdin_and_cancels_without_writing(tmp_path: Path) -> None:
    provider = ToolProvider()
    stdout = io.StringIO()
    stderr = io.StringIO()

    status = main(
        real_args(
            "--permission-mode",
            "workspace-write",
            "--approval",
            "ask",
            "prompt",
            "write it",
        ),
        stdin=NoReadInput(),
        stdout=stdout,
        stderr=stderr,
        **common(tmp_path, provider),
    )

    assert status == 0
    assert stdout.getvalue() == "finished\n"
    assert "Approval required" not in stdout.getvalue()
    assert not (tmp_path / "note.txt").exists()
    assert provider.requests[1].history[-1].content == "action approval cancelled"


def test_one_shot_auto_requires_explicit_write_capability_and_executes(tmp_path: Path) -> None:
    provider = ToolProvider()
    stdout = io.StringIO()
    stderr = io.StringIO()

    status = main(
        real_args(
            "--permission-mode",
            "workspace-write",
            "--approval",
            "auto",
            "prompt",
            "write it",
        ),
        stdin=NoReadInput(),
        stdout=stdout,
        stderr=stderr,
        **common(tmp_path, provider),
    )

    assert status == 0
    assert stdout.getvalue() == "finished\n"
    rendered = stderr.getvalue()
    assert "[context 1/24] input unknown + reserve 1.0k / unknown · unknown" in rendered
    assert "Token usage [1/24]: unknown" in rendered
    assert "[tool 1/32] write_file path='note.txt' content_bytes=21" in rendered
    assert "[tool 1/32] succeeded code=created" in rendered
    assert "Tool summary: requested=1 admitted=1 dispatched=1 succeeded=1" in rendered
    assert "Turn usage: 0 in / 0 out · known=0 unknown=2" in rendered
    assert "secret model content" not in rendered
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "secret model content\n"


def test_repl_ask_shows_candidate_diff_but_live_activity_stays_redacted(tmp_path: Path) -> None:
    provider = ToolProvider()
    stdin = TtyStringIO("write it\ny\n/exit\n")
    stdout = TtyStringIO()
    session = ProjectSession.open(
        tmp_path,
        model="custom/model",
        custom_protocol="openai-compatible",
        custom_base_url="http://127.0.0.1:11434/v1",
        environment={},
        provider_factory=lambda route, *, environment: provider,
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
        permission_mode=PermissionMode.WORKSPACE_WRITE,
        approval_mode=ApprovalMode.ASK,
        approval_handler=terminal_approval_handler(stdin, stdout),
    )
    try:
        status = run_repl(
            session,
            stdin=stdin,
            stdout=stdout,
            version="0.test",
            cwd=tmp_path,
            color=False,
        )
    finally:
        session.close()

    assert status == 0
    rendered = stdout.getvalue()
    assert "Approval required: workspace-create write_file path='note.txt' bytes=21" in rendered
    assert "[tool 1/32] write_file path='note.txt' content_bytes=21" in rendered
    assert "[tool 1/32] succeeded code=created" in rendered
    assert "Prepared candidate (21 bytes):" in rendered
    assert "--- /dev/null" in rendered
    assert "+++ b/note.txt" in rendered
    assert "+secret model content" in rendered
    assert rendered.count("secret model content") == 1
    assert "Approve this exact action? [y/N/c]:" in rendered
    assert "finished" in rendered
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "secret model content\n"


def test_invalid_permission_and_approval_flags_are_argparse_errors(capsys) -> None:
    with pytest.raises(SystemExit) as permission:
        main(["--permission-mode", "root", "prompt", "hello"])
    assert permission.value.code == 2
    assert "invalid choice" in capsys.readouterr().err

    with pytest.raises(SystemExit) as approval:
        main(["--approval", "always", "prompt", "hello"])
    assert approval.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_terminal_command_approval_shows_exact_argv_cwd_and_timeout() -> None:
    identity = ActionIdentity(
        request_id="12345678-1234-4234-9234-123456789abc",
        tool_use_id="command-1",
        tool_name="run_command",
        arguments=ToolArguments.from_mapping(
            {
                "argv": ["uv", "run", "pytest", "tests/unit"],
                "cwd": ".",
                "timeout_seconds": 60,
            }
        ),
        action=PermissionAction.DANGEROUS,
        workspace_fingerprint=f"v1-{'1' * 64}",
        lease=ActionLease(
            "22345678-1234-4234-9234-123456789abc",
            "32345678-1234-4234-9234-123456789abc",
            0,
            f"ctx-v1-{'2' * 64}",
        ),
        precondition=ActionPrecondition.none(),
    )
    request = HumanApprovalRequest(
        identity,
        PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_DANGEROUS,
        ),
        build_metadata_preview(
            action_digest=identity.digest,
            kind=ApprovalPreviewKind.COMMAND,
        ),
    )
    stdout = io.StringIO()

    assert (
        terminal_approval_handler(io.StringIO("y\n"), stdout)(request) == ApprovalResolution.ACCEPT
    )
    rendered = stdout.getvalue()
    assert "dangerous run_command" in rendered
    assert "argv=('uv', 'run', 'pytest', 'tests/unit')" in rendered
    assert "cwd='.'" in rendered
    assert "timeout=60s" in rendered
    assert "without shell parsing or rollback" in rendered
    assert "Host filesystem read-only" in rendered
    assert "workspace writable" in rendered
    assert "socket creation denied" in rendered
    assert "PWD" not in rendered


def test_terminal_mkdir_approval_shows_only_relative_path() -> None:
    identity = ActionIdentity(
        request_id="12345678-1234-4234-9234-123456789abc",
        tool_use_id="mkdir-1",
        tool_name="mkdir",
        arguments=ToolArguments.from_mapping({"path": "src/pkg"}),
        action=PermissionAction.WORKSPACE_CREATE,
        workspace_fingerprint=f"v1-{'1' * 64}",
        lease=ActionLease(
            "22345678-1234-4234-9234-123456789abc",
            "32345678-1234-4234-9234-123456789abc",
            0,
            f"ctx-v1-{'2' * 64}",
        ),
        precondition=ActionPrecondition.path_absent(),
    )
    request = HumanApprovalRequest(
        identity,
        PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_CREATE,
        ),
        build_metadata_preview(
            action_digest=identity.digest,
            kind=ApprovalPreviewKind.DIRECTORY_CREATE,
        ),
    )
    stdout = io.StringIO()

    assert (
        terminal_approval_handler(io.StringIO("y\n"), stdout)(request) == ApprovalResolution.ACCEPT
    )
    rendered = stdout.getvalue()
    assert "Approval required: workspace-create mkdir path='src/pkg'" in rendered
    assert "bytes=" not in rendered
    assert "target absent" in rendered
    assert "/root/" not in rendered


def test_terminal_move_approval_shows_only_two_relative_paths() -> None:
    identity = ActionIdentity(
        request_id="12345678-1234-4234-9234-123456789abc",
        tool_use_id="move-1",
        tool_name="move_file",
        arguments=ToolArguments.from_mapping({"source": "src/a.py", "destination": "dst/b.py"}),
        action=PermissionAction.WORKSPACE_MOVE,
        workspace_fingerprint=f"v1-{'1' * 64}",
        lease=ActionLease(
            "22345678-1234-4234-9234-123456789abc",
            "32345678-1234-4234-9234-123456789abc",
            0,
            f"ctx-v1-{'2' * 64}",
        ),
        precondition=ActionPrecondition.expected_state("3" * 64),
    )
    request = HumanApprovalRequest(
        identity,
        PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_MOVE,
        ),
        build_metadata_preview(
            action_digest=identity.digest,
            kind=ApprovalPreviewKind.FILE_MOVE,
            byte_count=123,
        ),
    )
    stdout = io.StringIO()

    assert (
        terminal_approval_handler(io.StringIO("y\n"), stdout)(request) == ApprovalResolution.ACCEPT
    )
    rendered = stdout.getvalue()
    assert (
        "Approval required: workspace-move move_file source='src/a.py' "
        "destination='dst/b.py'" in rendered
    )
    assert "/root/" not in rendered
    assert "333333" not in rendered
    assert "Prepared move: 123 bytes" in rendered


def test_terminal_copy_approval_shows_only_two_relative_paths() -> None:
    identity = ActionIdentity(
        request_id="12345678-1234-4234-9234-123456789abc",
        tool_use_id="copy-1",
        tool_name="copy_file",
        arguments=ToolArguments.from_mapping({"source": "src/a.bin", "destination": "dst/b.bin"}),
        action=PermissionAction.WORKSPACE_CREATE,
        workspace_fingerprint=f"v1-{'1' * 64}",
        lease=ActionLease(
            "22345678-1234-4234-9234-123456789abc",
            "32345678-1234-4234-9234-123456789abc",
            0,
            f"ctx-v1-{'2' * 64}",
        ),
        precondition=ActionPrecondition.expected_state("3" * 64),
    )
    request = HumanApprovalRequest(
        identity,
        PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_CREATE,
        ),
        build_metadata_preview(
            action_digest=identity.digest,
            kind=ApprovalPreviewKind.FILE_COPY,
            byte_count=456,
        ),
    )
    stdout = io.StringIO()

    assert (
        terminal_approval_handler(io.StringIO("y\n"), stdout)(request) == ApprovalResolution.ACCEPT
    )
    rendered = stdout.getvalue()
    assert (
        "Approval required: workspace-create copy_file source='src/a.bin' "
        "destination='dst/b.bin'" in rendered
    )
    assert "/root/" not in rendered
    assert "333333" not in rendered
    assert "Prepared copy: 456 bytes" in rendered


def test_terminal_delete_approval_shows_only_relative_path() -> None:
    identity = ActionIdentity(
        request_id="12345678-1234-4234-9234-123456789abc",
        tool_use_id="delete-1",
        tool_name="delete_file",
        arguments=ToolArguments.from_mapping({"path": "obsolete.txt"}),
        action=PermissionAction.WORKSPACE_DELETE,
        workspace_fingerprint=f"v1-{'1' * 64}",
        lease=ActionLease(
            "22345678-1234-4234-9234-123456789abc",
            "32345678-1234-4234-9234-123456789abc",
            0,
            f"ctx-v1-{'2' * 64}",
        ),
        precondition=ActionPrecondition.expected_state("3" * 64),
    )
    request = HumanApprovalRequest(
        identity,
        PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_DELETE,
        ),
        build_metadata_preview(
            action_digest=identity.digest,
            kind=ApprovalPreviewKind.FILE_DELETE,
            byte_count=789,
        ),
    )
    stdout = io.StringIO()

    assert (
        terminal_approval_handler(io.StringIO("y\n"), stdout)(request) == ApprovalResolution.ACCEPT
    )
    rendered = stdout.getvalue()
    assert "Approval required: workspace-delete delete_file path='obsolete.txt'" in rendered
    assert "/root/" not in rendered
    assert "333333" not in rendered
    assert "Permanent deletion: 789 bytes" in rendered


def test_terminal_delete_directory_approval_shows_only_relative_path() -> None:
    identity = ActionIdentity(
        request_id="12345678-1234-4234-9234-123456789abc",
        tool_use_id="rmdir-1",
        tool_name="delete_directory",
        arguments=ToolArguments.from_mapping({"path": "build/empty"}),
        action=PermissionAction.WORKSPACE_DELETE,
        workspace_fingerprint=f"v1-{'1' * 64}",
        lease=ActionLease(
            "22345678-1234-4234-9234-123456789abc",
            "32345678-1234-4234-9234-123456789abc",
            0,
            f"ctx-v1-{'2' * 64}",
        ),
        precondition=ActionPrecondition.expected_state("3" * 64),
    )
    request = HumanApprovalRequest(
        identity,
        PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_DELETE,
        ),
        build_metadata_preview(
            action_digest=identity.digest,
            kind=ApprovalPreviewKind.DIRECTORY_DELETE,
        ),
    )
    stdout = io.StringIO()

    assert (
        terminal_approval_handler(io.StringIO("y\n"), stdout)(request) == ApprovalResolution.ACCEPT
    )
    rendered = stdout.getvalue()
    assert "Approval required: workspace-delete delete_directory path='build/empty'" in rendered
    assert "/root/" not in rendered
    assert "333333" not in rendered
    assert "Permanent empty-directory deletion" in rendered


def test_terminal_patch_approval_shows_path_without_edit_content() -> None:
    identity = ActionIdentity(
        request_id="12345678-1234-4234-9234-123456789abc",
        tool_use_id="patch-1",
        tool_name="patch_file",
        arguments=ToolArguments.from_mapping(
            {
                "path": "src/app.py",
                "edits": [{"old_text": "secret-before", "new_text": "secret-after"}],
            }
        ),
        action=PermissionAction.WORKSPACE_OVERWRITE,
        workspace_fingerprint=f"v1-{'1' * 64}",
        lease=ActionLease(
            "22345678-1234-4234-9234-123456789abc",
            "32345678-1234-4234-9234-123456789abc",
            0,
            f"ctx-v1-{'2' * 64}",
        ),
        precondition=ActionPrecondition.expected_state("3" * 64),
    )
    request = HumanApprovalRequest(
        identity,
        PermissionResult(
            PermissionDecision.ASK,
            PermissionReason.APPROVAL_REQUIRED_WORKSPACE_OVERWRITE,
        ),
    )
    stdout = io.StringIO()

    assert (
        terminal_approval_handler(io.StringIO("y\n"), stdout)(request) == ApprovalResolution.ACCEPT
    )
    rendered = stdout.getvalue()
    assert "Approval required: workspace-overwrite patch_file path='src/app.py'" in rendered
    assert "secret-before" not in rendered
    assert "secret-after" not in rendered


def test_terminal_approval_renders_bound_diff_and_escapes_terminal_controls() -> None:
    request = approval_request()
    after = "after\x1b[31m\u202eevil\n"
    identity = replace(
        request.identity,
        arguments=ToolArguments.from_mapping({"path": "note.txt", "content": after}),
    )
    preview = build_file_change_preview(
        action_digest=identity.digest,
        path="note.txt",
        before=b"before\n",
        after=after.encode(),
    )
    request = HumanApprovalRequest(identity, request.permission_result, preview)
    stdout = io.StringIO()

    assert (
        terminal_approval_handler(io.StringIO("y\n"), stdout, color=True)(request)
        == ApprovalResolution.ACCEPT
    )
    rendered = stdout.getvalue()
    assert "-before" in rendered
    assert "+after\\x1b[31m\\u202eevil" in rendered
    assert "\x1b[31mafter" not in rendered
    assert "\u202e" not in rendered
    assert "Approve this exact action?" in rendered
