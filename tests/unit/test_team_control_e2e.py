from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from coquo.child_run_store import ChildRunStore
from coquo.child_runtime import ChildRunExecutor
from coquo.child_supervisor import ChildRunSupervisor
from coquo.core.contracts import (
    AssistantText,
    ConversationRequest,
    ToolArguments,
    ToolResult,
    ToolUse,
)
from coquo.core.permissions import ApprovalMode
from coquo.session import ProjectSession
from coquo.team_records import TeamScheduleOutcome
from coquo.team_schedule import TeamScheduleService
from coquo.team_store import TeamStore


class ParentTeamProvider:
    """Drive a deterministic parent control sequence from prior ToolResults."""

    def __init__(self) -> None:
        self.step = 0
        self.requests: list[ConversationRequest] = []
        self.team_id: str | None = None
        self.member_ids: list[str] = []
        self.work_ids: list[str] = []
        self.schedule_run_id: str | None = None
        self.reply_id: str | None = None

    def respond(self, request: ConversationRequest):
        self.requests.append(request)
        result = next(
            (item for item in reversed(request.history) if isinstance(item, ToolResult)), None
        )
        payload = json.loads(result.content) if result is not None else {}
        step = self.step
        self.step += 1
        if step == 0:
            return self._tool("team_create", {"name": "Model Team"}, step)
        if step == 1:
            self.team_id = payload["team_id"]
            return self._tool("team_add_member", {"team_id": self.team_id, "name": "One"}, step)
        if step == 2:
            return self._tool("team_add_member", {"team_id": self.team_id, "name": "Two"}, step)
        if step == 3:
            self.member_ids.append(payload["member_id"])
            return self._tool(
                "team_work_create",
                {
                    "team_id": self.team_id,
                    "title": "First",
                    "objective": "Inspect the first bounded target",
                    "dependency_ids": [],
                },
                step,
            )
        if step == 4:
            self.work_ids.append(payload["work_item_id"])
            return self._tool(
                "team_work_create",
                {
                    "team_id": self.team_id,
                    "title": "Second",
                    "objective": "Inspect the second bounded target",
                    "dependency_ids": [],
                },
                step,
            )
        if step == 5:
            self.work_ids.append(payload["work_item_id"])
            return self._tool(
                "team_schedule_start",
                {"team_id": self.team_id, "max_assignments": 2, "max_parallel": 2},
                step,
            )
        if step == 6:
            self.schedule_run_id = payload["schedule_run_id"]
            return AssistantText("Team schedule started.")
        if step == 7:
            return self._tool(
                "team_schedule_wait",
                {
                    "team_id": self.team_id,
                    "schedule_run_id": self.schedule_run_id,
                    "timeout_seconds": 30,
                },
                step,
            )
        if step == 8:
            return self._tool("team_status", {"team_id": self.team_id}, step)
        if step == 9:
            self.reply_id = payload["unread_reply_ids"][0]["message_id"]
            return self._tool(
                "team_message_show",
                {"team_id": self.team_id, "message_id": self.reply_id},
                step,
            )
        if step == 10:
            return self._tool(
                "team_message_read",
                {"team_id": self.team_id, "message_id": self.reply_id},
                step,
            )
        if step == 11:
            return self._tool(
                "team_work_review",
                {
                    "team_id": self.team_id,
                    "work_item_id": self.work_ids[0],
                    "decision": "complete",
                    "note": "Verified the first reply and handoff.",
                    "message_id": self.reply_id,
                },
                step,
            )
        return AssistantText("Team review completed.")

    @staticmethod
    def _tool(name: str, values: dict[str, object], step: int) -> ToolUse:
        return ToolUse(f"team-e2e-{step}", name, ToolArguments.from_mapping(values))


def test_parent_model_orchestrates_team_and_replays_all_ledgers(tmp_path: Path) -> None:
    parent_provider = ParentTeamProvider()
    session = ProjectSession.open(
        tmp_path,
        environment={},
        fake_provider_factory=lambda: parent_provider,
        approval_mode=ApprovalMode.AUTO,
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    try:
        assert session.prompt("Build and start a bounded Team wave") == "Team schedule started."
        assert session.prompt("Wait and review the first reply") == "Team review completed."
        assert parent_provider.team_id is not None
        assert parent_provider.schedule_run_id is not None
        assert all(
            set(parent_provider.requests[index].enabled_tool_names or ())
            >= {"team_create", "team_schedule_start", "team_work_review"}
            for index in (0, 5, 11)
        )

        team = session.inspect_team(parent_provider.team_id)
        assert len(team.assignments) == 2
        assert all(assignment.phase.value == "terminal_observed" for assignment in team.assignments)
        assert all(assignment.child_outcome == "completed" for assignment in team.assignments)
        assert team.work_items[0].status.value == "completed"
        assert team.work_items[1].status.value == "review"
        assert any(
            record.record_type == "team_message_delivered_to_parent"
            for record in session._writer.state.records
        )

        dependent = session.create_team_work(
            parent_provider.team_id,
            "Dependent",
            "Inspect the dependent target",
            tuple(parent_provider.work_ids),
        )
        idle = session.start_team_schedule(
            parent_provider.team_id, max_assignments=1, max_parallel=1
        )
        idle_notice = session.wait_team_schedule(parent_provider.team_id, idle.schedule_run_id, 30)
        assert idle_notice is not None and idle_notice.state is not None
        assert idle_notice.state.outcome is TeamScheduleOutcome.IDLE

        session.release_team_work(
            parent_provider.team_id, parent_provider.work_ids[1], "Not accepted"
        )
        next_run = session.start_team_schedule(
            parent_provider.team_id, max_assignments=1, max_parallel=1
        )
        next_notice = session.wait_team_schedule(
            parent_provider.team_id, next_run.schedule_run_id, 30
        )
        assert next_notice is not None and next_notice.state is not None
        assert next_notice.state.outcome is TeamScheduleOutcome.LIMIT_REACHED
        latest = session.inspect_team(parent_provider.team_id)
        second_assignment = next(
            item for item in latest.assignments if item.work_item_id == parent_provider.work_ids[1]
        )
        assert second_assignment.phase.value == "terminal_observed"
        assert second_assignment.reply_message_id is not None
        session.read_team_message(parent_provider.team_id, second_assignment.reply_message_id)
        session.complete_team_work(
            parent_provider.team_id,
            parent_provider.work_ids[1],
            "Host verified the second handoff",
        )
        dependent_run = session.start_team_schedule(
            parent_provider.team_id, max_assignments=1, max_parallel=1
        )
        dependent_notice = session.wait_team_schedule(
            parent_provider.team_id, dependent_run.schedule_run_id, 30
        )
        assert dependent_notice is not None and dependent_notice.state is not None
        assert dependent_notice.state.outcome is TeamScheduleOutcome.LIMIT_REACHED
        latest = session.inspect_team(parent_provider.team_id)
        dependent_assignment = next(
            item for item in latest.assignments if item.work_item_id == dependent.work_item_id
        )
        assert dependent_assignment.phase.value == "terminal_observed"
        assert (
            next(
                item for item in latest.work_items if item.work_item_id == dependent.work_item_id
            ).status.value
            == "review"
        )

        for assignment in latest.assignments:
            if assignment.reply_message_id is not None and any(
                message.message_id == assignment.reply_message_id
                and message.status.value == "unread"
                for message in latest.messages
            ):
                session.read_team_message(parent_provider.team_id, assignment.reply_message_id)
        session.complete_team_work(
            parent_provider.team_id, dependent.work_item_id, "Host verified handoff"
        )
        closed = session.close_team(parent_provider.team_id)
        assert closed.status.value == "closed"
    finally:
        session.close()


def test_schedule_failure_isolated_and_recovery_makes_no_provider_call(tmp_path: Path) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    try:
        team = session.create_team("Failure isolation")
        session.add_team_member(team.team_id, "One")
        session.add_team_member(team.team_id, "Two")
        session.create_team_work(team.team_id, "Fail", "fail this child")
        session.create_team_work(team.team_id, "Pass", "complete this child")

        class IsolatedExecutor:
            def __init__(self, child_run_id: str) -> None:
                self.child_run_id = child_run_id

            def run(self, child_run_id: str, *, cancellation=None) -> None:
                info = ChildRunStore(tmp_path).inspect(child_run_id)
                if "fail" not in info.objective:
                    ChildRunExecutor(tmp_path).run(child_run_id, cancellation=cancellation)
                    return
                store = ChildRunStore(tmp_path)
                lease = store.acquire_execution(child_run_id)
                execution_id = str(uuid4())
                try:
                    store.start_execution(
                        child_run_id,
                        child_session_id=info.child_session_id,
                        execution_id=execution_id,
                    )
                    store.finish_failed(
                        child_run_id,
                        execution_id=execution_id,
                        phase="running",
                        result_code="injected_failure",
                        message="deterministic child failure",
                    )
                finally:
                    lease.close()

        session._child_supervisor = ChildRunSupervisor(
            tmp_path,
            executor_factory=lambda child_id: IsolatedExecutor(child_id),
            worker_count=2,
            parent_session_id=session.session_id,
        )
        state = session.run_team_schedule(team.team_id, max_assignments=2, max_parallel=2)
        assert state.outcome is TeamScheduleOutcome.LIMIT_REACHED
        info = session.inspect_team(team.team_id)
        assert {item.child_outcome for item in info.assignments} == {"failed", "completed"}
        assert all(item.phase.value == "terminal_observed" for item in info.assignments)
        assert all(item.status.value == "review" for item in info.work_items)

        owner = session.session_id
        session.close()
        recovered_store = TeamStore(tmp_path)
        team_state = recovered_store.inspect(team.team_id)
        assert team_state.owner_session_id == owner
    finally:
        if not session._closed:
            session.close()


def test_abandoned_schedule_recovery_is_exact_and_provider_free(tmp_path: Path) -> None:
    owner = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    try:
        team = owner.create_team("Recovery")
        run = TeamScheduleService(tmp_path).start(team.team_id, max_assignments=1, max_parallel=1)
        run_id = run.schedule_run_id
        run.close()
        recovered = TeamScheduleService(tmp_path).recover(team.team_id, run_id)
        assert recovered.outcome is TeamScheduleOutcome.INTERRUPTED
        assert recovered.assignment_ids == ()
        replayed = TeamStore(tmp_path).inspect(team.team_id)
        assert replayed.schedules[-1].schedule_run_id == run_id
        assert replayed.schedules[-1].status.terminal
    finally:
        owner.close()
