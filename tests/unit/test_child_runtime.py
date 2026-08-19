from __future__ import annotations

from coquo.child_runtime import (
    CHILD_TOOL_NAMES,
    RECURSIVE_CHILD_ROLE_CONTRACT_VERSION,
    build_child_role_prompt,
    build_child_runtime_spec_from_binding,
    child_role_prompt_fingerprint,
    child_tool_set,
    provider_binding_from_session,
    recursive_child_role_prompt_fingerprint,
)
from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import ChildRunStore
from coquo.core.contracts import AssistantText
from coquo.providers.fake import ScriptedFakeProvider
from coquo.session_records import BindingSnapshot
from coquo.session_store import SessionStore
from coquo.tools.team_control import TEAM_CONTROL_TOOL_NAMES


def test_child_tool_set_is_fixed_and_ordered() -> None:
    snapshot = child_tool_set()
    assert snapshot.names == CHILD_TOOL_NAMES
    assert set(snapshot.names).isdisjoint(TEAM_CONTROL_TOOL_NAMES)
    assert snapshot.names == (
        "read_file",
        "glob",
        "grep",
        "list_directory",
        "read_file_lines",
        "stat_path",
        "list_tree",
        "grep_regex",
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "compare_files",
        "git_blame",
        "git_refs",
        "json_query",
        "checksum_file",
        "archive_list",
    )
    assert snapshot.snapshot_id == child_tool_set().snapshot_id


def test_child_role_contract_is_deterministic_and_bounded() -> None:
    fingerprint = child_role_prompt_fingerprint()
    prompt = build_child_role_prompt("Inspect README.md", "42345678-1234-4234-9234-123456789abc")
    assert len(fingerprint) == 64
    assert fingerprint == child_role_prompt_fingerprint()
    assert "Inspect README.md" in prompt
    assert "read-only workspace tools" in prompt


def test_recursive_child_role_contract_is_explicit_and_distinct() -> None:
    prompt = build_child_role_prompt(
        "Inspect the delegated fixture",
        "42345678-1234-4234-9234-123456789abc",
        delegation_allowed=True,
    )
    assert recursive_child_role_prompt_fingerprint() != child_role_prompt_fingerprint()
    assert "at most one read-only Grandchild" in prompt
    assert "depth two" in prompt
    assert '"delegation_allowed":true' in prompt
    assert f'"role_contract_version":{RECURSIVE_CHILD_ROLE_CONTRACT_VERSION}' in prompt


def test_child_binding_projection_has_no_credential_value() -> None:
    binding = BindingSnapshot.fake()
    projected = provider_binding_from_session(binding)
    assert projected["provider_id"] == "fake"
    assert "credential_value" not in projected
    assert all(value != "secret-token" for value in projected.values())


def test_child_runtime_spec_freezes_read_only_contract() -> None:
    binding = BindingSnapshot.fake()
    spec = build_child_runtime_spec_from_binding(
        child_run_id="42345678-1234-4234-9234-123456789abc",
        parent_session_id="52345678-1234-4234-9234-123456789abc",
        child_session_id="62345678-1234-4234-9234-123456789abc",
        objective="Inspect files",
        binding=binding,
    )
    assert spec.permission_mode == "read-only"
    assert spec.approval_mode == "auto"
    assert spec.tool_names == CHILD_TOOL_NAMES
    assert spec.provider_binding["credential_env"] is None


def test_recursive_child_runtime_spec_uses_recursive_role_contract() -> None:
    binding = BindingSnapshot.fake()
    spec = build_child_runtime_spec_from_binding(
        child_run_id="42345678-1234-4234-9234-123456789abc",
        parent_session_id="52345678-1234-4234-9234-123456789abc",
        child_session_id="62345678-1234-4234-9234-123456789abc",
        objective="Inspect files",
        binding=binding,
        delegation_allowed=True,
    )
    assert spec.role_contract_version == RECURSIVE_CHILD_ROLE_CONTRACT_VERSION
    assert spec.delegation_allowed is True


def test_child_executor_runs_one_turn_with_independent_session(tmp_path) -> None:
    from coquo.child_runtime import ChildRunExecutor

    session_store = SessionStore(tmp_path)
    parent_writer = session_store.create(BindingSnapshot.fake())
    parent_id = parent_writer.session_id
    latest_before = (session_store.root / "latest.json").read_bytes()
    parent_writer.release()
    child_store = ChildRunStore(tmp_path)
    info = child_store.create("Inspect files", parent_session=parent_id)
    parent = session_store.inspect(parent_id)
    spec = build_child_runtime_spec_from_binding(
        child_run_id=info.child_run_id,
        parent_session_id=parent_id,
        child_session_id="12345678-1234-4234-9234-123456789abc",
        objective=info.objective,
        binding=parent.binding,
    )
    child_store.prepare(
        info.child_run_id,
        runtime_spec=spec,
        session_store=session_store,
        binding=parent.binding,
    )

    def provider():
        return ScriptedFakeProvider([AssistantText("child result")])

    result = ChildRunExecutor(tmp_path, fake_provider_factory=provider).run(info.child_run_id)
    assert result.status is ChildRunStatus.COMPLETED
    assert result.execution_id is not None
    assert result.session_record_sequence is not None
    assert session_store.inspect("latest").session_id == parent_id
    assert (session_store.root / "latest.json").read_bytes() == latest_before
    child = session_store.inspect(spec.child_session_id)
    assert child.session_id == spec.child_session_id
    assert child.turn_count == 1
