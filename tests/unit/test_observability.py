from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from coquo.agent.tool_events import (
    AssistantResponseTextDeltaReceived,
    ProviderInvocationFinished,
    ProviderInvocationOutcome,
    ProviderInvocationStarted,
)
from coquo.observability import (
    ObservationContext,
    ObservationEvidence,
    ObservationPhase,
    ObservationRetentionPolicy,
    ObservationStream,
    ObservationSource,
    diagnose_observation_events,
    filter_observation_events,
    merge_observation_events,
    observation_event_json,
    project_background_items,
    project_child_records,
    project_session_records,
    project_task_records,
    project_team_records,
)
from coquo.session import ProjectSession


def _id() -> str:
    return str(uuid4())


def _record(record_type: str, sequence: int, timestamp: str, **values: object):
    return SimpleNamespace(
        record_type=record_type,
        sequence=sequence,
        occurred_at=timestamp,
        **values,
    )


def test_observation_context_accepts_event_parent_and_preserves_trace() -> None:
    session_id = _id()
    parent = "obs-v1-" + "a" * 64
    context = ObservationContext.new(session_id=session_id, parent_event_id=parent)
    child_id = _id()

    child = context.child(child_run_id=child_id)

    assert child.trace_id == context.trace_id
    assert child.session_id == session_id
    assert child.child_run_id == child_id
    assert child.parent_event_id == parent
    with pytest.raises(ValueError, match="parent event ID is invalid"):
        ObservationContext.new(parent_event_id=_id())


def test_project_session_creates_one_process_local_turn_trace(tmp_path, monkeypatch) -> None:
    session = ProjectSession.open(
        tmp_path,
        environment={},
        user_profile_path=tmp_path / "user.json",
        project_profile_path=tmp_path / "project.json",
    )
    captured = []
    monkeypatch.setattr(
        session._runtime,
        "run_turn",
        lambda request: captured.append(request.observation_context) or "observed",
    )
    try:
        assert session.prompt("inspect") == "observed"
    finally:
        session.close()

    assert len(captured) == 1
    context = captured[0]
    assert context is not None
    assert context.session_id == session.session_id
    assert context.trace_id == context.turn_id


def test_projection_is_deterministic_bounded_and_does_not_copy_bodies() -> None:
    session_id = _id()
    child_id = _id()
    context = ObservationContext(
        trace_id=session_id,
        session_id=session_id,
        child_run_id=child_id,
    )
    records = (
        _record(
            "child_run_header",
            1,
            "2026-08-20T00:00:00.000001Z",
            child_run_id=child_id,
            parent_session_id=session_id,
            objective="PRIVATE OBJECTIVE",
        ),
        _record(
            "child_run_handoff_published",
            2,
            "2026-08-20T00:00:01.000001Z",
            child_run_id=child_id,
            parent_session_id=session_id,
            body="PRIVATE HANDOFF",
            handoff_sha256="b" * 64,
        ),
    )

    first = project_child_records(child_id, records, context=context)
    second = project_child_records(child_id, records, context=context)

    assert first == second
    assert first[0].phase is ObservationPhase.CREATED
    assert first[1].phase is ObservationPhase.OBSERVED
    assert first[0].evidence is ObservationEvidence.HOST_OBSERVED
    assert first[1].evidence is ObservationEvidence.UNTRUSTED
    assert first[1].parent_event_id == first[0].event_id
    encoded = "\n".join(observation_event_json(event) for event in first)
    assert "PRIVATE OBJECTIVE" not in encoded
    assert "PRIVATE HANDOFF" not in encoded


def test_projection_uses_durable_delegation_timestamps() -> None:
    child_id = _id()
    session_id = _id()
    records = (
        SimpleNamespace(
            record_type="child_run_delegated",
            sequence=1,
            child_run_id=child_id,
            parent_session_id=session_id,
            parent_tool_use_id="tool-1",
            delegated_at="2026-08-20T00:00:00.000001Z",
        ),
        SimpleNamespace(
            record_type="child_run_admitted",
            sequence=2,
            child_run_id=child_id,
            parent_session_id=session_id,
            admitted_at="2026-08-20T00:00:01.000001Z",
        ),
    )
    events = project_child_records(
        child_id,
        records,
        context=ObservationContext(trace_id=session_id, session_id=session_id),
    )

    assert [event.record_type for event in events] == [
        "child_run_delegated",
        "child_run_admitted",
    ]


def test_delivered_child_handoff_receipt_is_untrusted() -> None:
    session_id = _id()
    event = project_session_records(
        session_id,
        (
            _record(
                "child_handoff_delivered",
                1,
                "2026-08-20T00:00:00.000001Z",
                child_run_id=_id(),
                parent_session_id=session_id,
            ),
        ),
    )[0]
    assert event.evidence is ObservationEvidence.UNTRUSTED


def test_live_stream_is_bounded_content_free_and_correlated() -> None:
    session_id = _id()
    context = ObservationContext.new(session_id=session_id)
    stream = ObservationStream(
        source_id=session_id,
        context=context,
        retention=ObservationRetentionPolicy(max_events=2),
    )
    stream.publish(record_type="provider_started", status="started", summary="safe")
    stream.publish_prompt(SimpleNamespace(status="completed", tool_use_id="tool-id"))
    stream.publish(record_type="provider_finished", status="completed", summary="safe")
    events = stream.snapshot()
    assert len(events) == 2
    assert events[-1].trace_id == context.trace_id
    assert all("PRIVATE BODY" not in observation_event_json(event) for event in events)
    assert events[0].parent_event_id is not None


def test_live_stream_projects_provider_round_boundaries_without_response_content() -> None:
    session_id = _id()
    stream = ObservationStream(
        source_id=session_id,
        context=ObservationContext.new(session_id=session_id),
    )

    stream.publish_prompt(ProviderInvocationStarted(1, 24))
    stream.publish_prompt(
        ProviderInvocationFinished(
            1,
            24,
            ProviderInvocationOutcome.TOOL_REQUEST,
            2,
            elapsed_milliseconds=1250,
            delta_count=2,
            first_delta_milliseconds=100,
            max_delta_gap_milliseconds=850,
            retry_count=1,
        )
    )

    events = stream.snapshot()
    assert [event.record_type for event in events] == [
        "live_provider_invocation_started",
        "live_provider_invocation_finished",
    ]
    assert [event.status for event in events] == ["started", "tool-request"]
    assert "elapsed_ms=1250" in events[1].summary
    assert "delta_count=2" in events[1].summary
    assert "first_delta_ms=100" in events[1].summary
    assert "max_delta_gap_ms=850" in events[1].summary
    assert "retry_count=1" in events[1].summary
    assert all("response" not in observation_event_json(event) for event in events)
    assert all(event.to_mapping()["schema_version"] == 1 for event in events)


def test_live_stream_reports_delta_size_without_delta_content() -> None:
    session_id = _id()
    stream = ObservationStream(
        source_id=session_id,
        context=ObservationContext.new(session_id=session_id),
    )

    stream.publish_prompt(AssistantResponseTextDeltaReceived("Hello"))

    event = stream.snapshot()[0]
    assert event.summary.endswith("chars=5 bytes=5")
    assert "Hello" not in observation_event_json(event)


def test_live_stream_subscribers_receive_fifo_events_and_can_unsubscribe() -> None:
    session_id = _id()
    stream = ObservationStream(
        source_id=session_id,
        context=ObservationContext.new(session_id=session_id),
    )
    received = []
    unsubscribe = stream.subscribe(received.append)

    first = stream.publish(record_type="provider_started", status="started", summary="safe")
    second = stream.publish(record_type="provider_finished", status="completed", summary="safe")
    unsubscribe()
    stream.publish(record_type="turn_committed", status="completed", summary="safe")

    assert received == [first, second]
    assert [event.sequence for event in received] == [0, 1]


def test_queue_subscription_is_reset_when_stream_context_switches() -> None:
    first_session = _id()
    second_session = _id()
    stream = ObservationStream(
        source_id=first_session,
        context=ObservationContext.new(session_id=first_session),
    )
    subscription = stream.subscribe_queue(max_pending=4)
    stream.publish(record_type="old_event", status="completed", summary="old")
    old_epoch = stream.stream_epoch

    stream.set_context(ObservationContext.new(session_id=second_session))
    assert stream.stream_epoch == old_epoch + 1
    assert subscription.read(after=-1).events == ()

    event = stream.publish(record_type="new_event", status="started", summary="new")
    batch = subscription.read(after=-1)
    assert batch.stream_epoch == stream.stream_epoch
    assert batch.events == (event,)
    assert all(item.source_id == second_session for item in batch.events)


def test_live_stream_subscriber_failure_does_not_change_agent_observation() -> None:
    session_id = _id()
    stream = ObservationStream(
        source_id=session_id,
        context=ObservationContext.new(session_id=session_id),
    )
    received = []

    def broken(_event) -> None:
        raise RuntimeError("presentation failed")

    stream.subscribe(broken)
    stream.subscribe(received.append)
    event = stream.publish(record_type="provider_started", status="started", summary="safe")

    assert received == [event]
    assert stream.snapshot() == (event,)


def test_background_projection_filters_and_diagnosis_are_read_only() -> None:
    child_id = _id()
    submission_id = _id()
    item = SimpleNamespace(
        submission_id=submission_id,
        child_run_id=child_id,
        state="claimed",
        queued_at="2020-01-01T00:00:00Z",
        claimed_at="2020-01-01T00:00:00Z",
        heartbeat_at="2020-01-01T00:00:00Z",
        worker_id=_id(),
        lease_id=_id(),
        terminal_child_status=None,
    )
    events = project_background_items((item,), trace_id=child_id)
    assert events[0].source is ObservationSource.BACKGROUND
    assert filter_observation_events(events, status="claimed") == events
    findings = diagnose_observation_events(events, stale_after_seconds=1)
    assert any(item.code == "stale-background-lease" for item in findings)


def test_merge_links_roots_across_existing_durable_ids() -> None:
    session_id = _id()
    task_id = _id()
    team_id = _id()
    direct_child_id = _id()
    team_child_id = _id()
    stage_id = _id()
    tool_use_id = "tool-child-1"
    context = ObservationContext(trace_id=session_id, session_id=session_id)

    session_events = project_session_records(
        session_id,
        (
            _record("session_header", 1, "2026-08-20T00:00:00.000001Z", session_id=session_id),
            _record(
                "task_admission_resolved",
                2,
                "2026-08-20T00:00:01.000001Z",
                task_id=task_id,
            ),
            _record(
                "child_delegation_decided",
                3,
                "2026-08-20T00:00:02.000001Z",
                parent_session_id=session_id,
                tool_use_id=tool_use_id,
            ),
            _record(
                "team_control_decided",
                4,
                "2026-08-20T00:00:03.000001Z",
                parent_session_id=session_id,
                target_team_id=team_id,
            ),
        ),
        context=context,
    )
    task_events = project_task_records(
        task_id,
        (
            _record(
                "task_header",
                1,
                "2026-08-20T00:00:04.000001Z",
                task_id=task_id,
                owner_session_id=session_id,
            ),
            _record(
                "stage_delegated",
                2,
                "2026-08-20T00:00:05.000001Z",
                stage_id=stage_id,
                child_run_id=direct_child_id,
                team_id=team_id,
            ),
        ),
        context=context.child(task_id=task_id),
    )
    team_events = project_team_records(
        team_id,
        (
            _record(
                "team_header",
                1,
                "2026-08-20T00:00:06.000001Z",
                team_id=team_id,
                owner_session_id=session_id,
            ),
            _record(
                "team_assignment_created",
                2,
                "2026-08-20T00:00:07.000001Z",
                team_id=team_id,
                assignment_id=_id(),
                child_run_id=team_child_id,
            ),
        ),
        context=context.child(team_id=team_id),
    )
    direct_child_events = project_child_records(
        direct_child_id,
        (
            _record(
                "child_run_header",
                1,
                "2026-08-20T00:00:08.000001Z",
                child_run_id=direct_child_id,
                parent_session_id=session_id,
            ),
            _record(
                "child_run_delegated",
                2,
                "2026-08-20T00:00:09.000001Z",
                child_run_id=direct_child_id,
                parent_session_id=session_id,
                parent_tool_use_id=tool_use_id,
            ),
        ),
        context=context.child(child_run_id=direct_child_id),
    )
    team_child_events = project_child_records(
        team_child_id,
        (
            _record(
                "child_run_header",
                1,
                "2026-08-20T00:00:10.000001Z",
                child_run_id=team_child_id,
                parent_session_id=session_id,
            ),
        ),
        context=context.child(child_run_id=team_child_id),
    )

    merged = merge_observation_events(
        (session_events, task_events, team_events, direct_child_events, team_child_events)
    )
    by_source = {(event.source, event.source_id, event.sequence): event for event in merged}

    assert (
        by_source[(ObservationSource.TASK, task_id, 1)].parent_event_id
        == session_events[1].event_id
    )
    assert (
        by_source[(ObservationSource.TEAM, team_id, 1)].parent_event_id == task_events[1].event_id
    )
    assert (
        by_source[(ObservationSource.CHILD, direct_child_id, 1)].parent_event_id
        == task_events[1].event_id
    )
    assert (
        by_source[(ObservationSource.CHILD, team_child_id, 1)].parent_event_id
        == team_events[1].event_id
    )
    assert {event.trace_id for event in merged} == {session_id}
