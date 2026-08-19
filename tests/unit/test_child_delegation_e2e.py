from __future__ import annotations

import json
from uuid import uuid4

from coquo.background_runtime import BackgroundQueueItem, BackgroundSubmission, utc_now
from coquo.child_run_store import ChildRunStore
from coquo.child_runtime import CHILD_TOOL_NAMES, ChildRunExecutor
from coquo.child_supervisor import ChildRunSupervisor
from coquo.core.contracts import (
    AssistantText,
    ConversationRequest,
    ToolArguments,
    ToolResult,
    ToolUse,
)
from coquo.core.permissions import ApprovalMode
from coquo.providers.fake import ScriptedFakeProvider
from coquo.session import ProjectSession
from coquo.session_store import SessionStore
from coquo.tools.child_control import CHILD_CONTROL_TOOL_NAMES


class DelegatingParentProvider(ScriptedFakeProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.step = 0
        self.child_ids: list[str] = []

    def respond(self, request: ConversationRequest):
        self._received_requests.append(request)
        if self.step == 0:
            response = ToolUse(
                "spawn-1",
                "child_spawn",
                ToolArguments.from_mapping({"objective": "Inspect README scope"}),
            )
        elif self.step == 1:
            self.child_ids.append(_last_child_id(request, "spawn-1"))
            response = ToolUse(
                "spawn-2",
                "child_spawn",
                ToolArguments.from_mapping({"objective": "Inspect Git status"}),
            )
        elif self.step == 2:
            self.child_ids.append(_last_child_id(request, "spawn-2"))
            response = ToolUse(
                "parent-read",
                "read_file",
                ToolArguments.from_mapping({"path": "README.md"}),
            )
        elif self.step == 3:
            response = ToolUse(
                "wait-1",
                "child_wait",
                ToolArguments.from_mapping(
                    {"child_run_id": self.child_ids[0], "timeout_seconds": 30}
                ),
            )
        elif self.step == 4:
            response = ToolUse(
                "wait-2",
                "child_wait",
                ToolArguments.from_mapping(
                    {"child_run_id": self.child_ids[1], "timeout_seconds": 30}
                ),
            )
        else:
            response = AssistantText("Parent and both Children completed.")
        self.step += 1
        return response


def _last_child_id(request: ConversationRequest, tool_use_id: str) -> str:
    result = next(
        item
        for item in reversed(request.history)
        if isinstance(item, ToolResult) and item.tool_use_id == tool_use_id
    )
    value = json.loads(result.content)["child_run_id"]
    assert isinstance(value, str)
    return value


def test_parent_delegates_two_children_works_and_replays_three_sessions(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    parent_provider = DelegatingParentProvider()
    session = ProjectSession.open(
        tmp_path,
        environment={},
        fake_provider_factory=lambda: parent_provider,
        approval_mode=ApprovalMode.AUTO,
    )
    parent_id = session._writer.session_id
    try:
        assert session.prompt("Delegate two independent inspections") == (
            "Parent and both Children completed."
        )
        assert len(parent_provider.child_ids) == 2
        first_request = parent_provider.received_requests[0]
        assert first_request.enabled_tool_names is not None
        assert first_request.enabled_tool_names[-16:-12] == CHILD_CONTROL_TOOL_NAMES
        committed = session._writer.state.records[-1]
        assert tuple(entry.tool_use_id for entry in committed.tool_ledger.entries) == (
            "spawn-1",
            "spawn-2",
            "parent-read",
            "wait-1",
            "wait-2",
        )
        assert len(session._writer.state.child_handoff_deliveries) == 2
    finally:
        session.close()

    sessions = SessionStore(tmp_path)
    parent = sessions.inspect(parent_id)
    assert parent.turn_count == 1
    store = ChildRunStore(tmp_path)
    for child_id in parent_provider.child_ids:
        state = store.replay_state(child_id)
        assert state.completed is not None
        assert state.delegated is not None
        assert state.admitted is not None
        assert state.admitted.tool_names == CHILD_TOOL_NAMES
        assert set(state.admitted.tool_names).isdisjoint(CHILD_CONTROL_TOOL_NAMES)
        assert state.session_bound is not None
        child_session = sessions.inspect(state.session_bound.child_session_id)
        assert child_session.turn_count == 1


class RecursiveParentProvider(ScriptedFakeProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.step = 0
        self.root_child_id: str | None = None

    def respond(self, request: ConversationRequest):
        self._received_requests.append(request)
        if self.step == 0:
            response = ToolUse(
                "root-spawn",
                "child_spawn",
                ToolArguments.from_mapping({"objective": "Delegate one bounded inspection"}),
            )
        elif self.step == 1:
            self.root_child_id = _last_child_id(request, "root-spawn")
            response = ToolUse(
                "root-wait",
                "child_wait",
                ToolArguments.from_mapping(
                    {"child_run_id": self.root_child_id, "timeout_seconds": 30}
                ),
            )
        else:
            response = AssistantText("Recursive Child completed.")
        self.step += 1
        return response


class RecursiveRootChildProvider(ScriptedFakeProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.step = 0
        self.grandchild_id: str | None = None
        self.last_request: ConversationRequest | None = None

    def respond(self, request: ConversationRequest):
        self._received_requests.append(request)
        self.last_request = request
        assert request.enabled_tool_names is not None
        assert set(CHILD_CONTROL_TOOL_NAMES).issubset(request.enabled_tool_names)
        if self.step == 0:
            response = ToolUse(
                "grand-spawn",
                "child_spawn",
                ToolArguments.from_mapping({"objective": "Inspect one fixture"}),
            )
        else:
            self.grandchild_id = _last_child_id(request, "grand-spawn")
            response = AssistantText("Root Child received the Grandchild result.")
        self.step += 1
        return response


class RecursiveGrandchildProvider(ScriptedFakeProvider):
    def respond(self, request: ConversationRequest):
        self._received_requests.append(request)
        assert request.enabled_tool_names is not None
        assert set(request.enabled_tool_names).isdisjoint(CHILD_CONTROL_TOOL_NAMES)
        return AssistantText("Grandchild observed the fixture.")


def test_read_only_child_can_delegate_one_grandchild_and_grandchild_cannot_recurse(
    tmp_path, monkeypatch
) -> None:
    parent_provider = RecursiveParentProvider()
    root_provider = RecursiveRootChildProvider()
    grandchild_provider = RecursiveGrandchildProvider()
    providers = {
        "root": root_provider,
        "grandchild": grandchild_provider,
    }

    class InjectedExecutor:
        def __init__(self, workspace) -> None:
            self.workspace = workspace

        def run(self, child_run_id: str, **kwargs):
            info = ChildRunStore(self.workspace).inspect(child_run_id)
            depth = info.delegated.depth if info.delegated is not None else 1
            provider = providers["root" if depth == 1 else "grandchild"]
            return ChildRunExecutor(
                self.workspace,
                fake_provider_factory=lambda: provider,
            ).run(child_run_id, **kwargs)

    class InlinePersistentRuntime:
        def __init__(self, workspace, *, parent_session_id=None) -> None:
            self.workspace = workspace
            self.parent_session_id = parent_session_id

        def start(self, child_run_id: str):
            InjectedExecutor(self.workspace).run(child_run_id)
            return BackgroundSubmission(
                item=BackgroundQueueItem(
                    submission_id=str(uuid4()),
                    child_run_id=child_run_id,
                    state="completed",
                    queued_at=utc_now(),
                    terminal_child_status=ChildRunStore(self.workspace)
                    .inspect(child_run_id)
                    .status.value,
                ),
                worker_pid=None,
                worker_started=False,
            )

    monkeypatch.setattr("coquo.child_runtime.ChildRunExecutor", InjectedExecutor)
    monkeypatch.setattr(
        "coquo.background_runtime.PersistentChildRunRuntime", InlinePersistentRuntime
    )
    session = ProjectSession.open(
        tmp_path,
        environment={},
        fake_provider_factory=lambda: parent_provider,
        approval_mode=ApprovalMode.AUTO,
    )
    parent_id = session._writer.session_id
    supervisor = ChildRunSupervisor(
        tmp_path,
        executor_factory=lambda child_id: InjectedExecutor(tmp_path),
        parent_session_id=parent_id,
    )
    session._child_supervisor = supervisor
    try:
        assert session.prompt("Run one recursive read-only inspection") == (
            "Recursive Child completed."
        )
        assert parent_provider.root_child_id is not None
        assert root_provider.grandchild_id is not None
        root_state = ChildRunStore(tmp_path).replay_state(parent_provider.root_child_id)
        grand_state = ChildRunStore(tmp_path).replay_state(root_provider.grandchild_id)
        assert root_state.delegated is not None
        assert root_state.delegated.depth == 1
        assert grand_state.delegated is not None
        assert grand_state.delegated.depth == 2
        assert grand_state.delegated.parent_child_run_id == parent_provider.root_child_id
        assert grand_state.delegated.root_child_run_id == parent_provider.root_child_id
        assert grand_state.delegated.schema_version == 2
        assert root_state.admitted is not None
        assert root_state.admitted.role_contract_version == 4
        assert grand_state.admitted is not None
        assert grand_state.admitted.role_contract_version == 1
    finally:
        session.close()
