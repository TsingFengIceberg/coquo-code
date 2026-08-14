from __future__ import annotations

import hashlib
from io import StringIO

from coquo.cli.approval import render_approval_request, terminal_approval_handler
from coquo.core.action_coordinator import ApprovalResolution
from coquo.core.delegation_approval import (
    DelegationApprovalIdentity,
    DelegationApprovalPreview,
    DelegationApprovalRequest,
    delegation_decision_sha256,
)
from coquo.core.permissions import ApprovalMode


def request() -> DelegationApprovalRequest:
    objective = "Inspect the failing tests"
    identity = DelegationApprovalIdentity(
        parent_session_id="12345678-1234-4234-9234-123456789abc",
        context_id="ctx-v21-" + "a" * 64,
        tool_use_id="child-tool-1",
        objective_sha256=hashlib.sha256(objective.encode()).hexdigest(),
        route_fingerprint="route-v1-" + "b" * 64,
        child_tool_set_id="toolset-v1-" + "c" * 64,
        max_provider_invocations=24,
        max_tool_requests=32,
        max_output_tokens=4096,
        deadline_seconds=300,
        depth=1,
        approval_mode=ApprovalMode.ASK,
    )
    preview = DelegationApprovalPreview(
        objective=objective,
        provider_id="anthropic",
        profile_name="reviewer",
        model="claude-test",
        tool_names=("read_file", "grep"),
        max_provider_invocations=24,
        max_tool_requests=32,
        max_output_tokens=4096,
        deadline_seconds=300,
        spawn_number=2,
    )
    return DelegationApprovalRequest(identity, preview)


def test_delegation_identity_and_decision_are_deterministic() -> None:
    first = request()
    assert first.identity.digest == request().identity.digest
    assert delegation_decision_sha256(first.identity, "accepted") == (
        delegation_decision_sha256(request().identity, "accepted")
    )
    assert delegation_decision_sha256(first.identity, "accepted") != (
        delegation_decision_sha256(first.identity, "rejected")
    )


def test_delegation_preview_is_informed_and_terminal_resolves() -> None:
    approval = request()
    rendered = render_approval_request(approval, color=False)
    for expected in (
        approval.preview.objective,
        "reviewer / claude-test",
        "read_file, grep",
        "one Turn",
        "spawn 2/4",
        "process-local",
        "additional Provider cost",
    ):
        assert expected in rendered
    stdin = StringIO("y\n")
    stdout = StringIO()
    assert terminal_approval_handler(stdin, stdout)(approval) is ApprovalResolution.ACCEPT
    assert "Approve this exact delegation" in stdout.getvalue()
