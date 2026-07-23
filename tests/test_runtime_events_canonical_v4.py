from __future__ import annotations

from datetime import datetime, timezone

from unchain.events import (
    RuntimeEvent,
    RuntimeEventBridge,
    RuntimeEventLinks,
    RuntimeEventNormalizerContext,
    RuntimeEventSurface,
    normalize_raw_event,
)


def _clock() -> datetime:
    return datetime(2026, 5, 26, 12, 34, 56, 789000, tzinfo=timezone.utc)


def test_canonical_runtime_event_is_v4_shape():
    event = RuntimeEvent(
        event_id="evt-1",
        type="artifact.created",
        timestamp="2026-05-26T12:00:00Z",
        session_id="thread-1",
        run_id="run-1",
        agent_id="developer",
        seq=7,
        links=RuntimeEventLinks(
            step_id="step-1",
            artifact_id="workspace_change_set:run-1",
            workspace_change_set_id="wcs_run-1",
        ),
        surface=RuntimeEventSurface(
            slot="run_summary",
            scope="run",
            group="files",
            default_state="expanded",
            priority=50,
        ),
        payload={"ok": True},
    )

    raw = event.to_dict()

    assert raw["schema_version"] == "v4"
    assert raw["seq"] == 7
    assert raw["links"]["step_id"] == "step-1"
    assert raw["links"]["workspace_change_set_id"] == "wcs_run-1"
    assert raw["surface"]["slot"] == "run_summary"
    assert RuntimeEvent.from_dict(raw).to_dict() == raw


def test_canonical_bridge_normalizes_raw_events_to_v4_steps():
    counter = {"value": 0}

    def next_id() -> str:
        counter["value"] += 1
        return f"evt-{counter['value']}"

    bridge = RuntimeEventBridge(
        session_id="thread-1",
        root_agent_id="developer",
        id_factory=next_id,
        clock=_clock,
    )

    [event] = bridge.normalize(
        {
            "type": "tool_call",
            "run_id": "run-root",
            "iteration": 2,
            "tool_name": "write",
            "call_id": "call-1",
            "arguments": {"path": "a.py"},
        }
    )

    assert event.to_dict()["schema_version"] == "v4"
    assert event.type == "step.started"
    assert event.seq == 1
    assert event.links.step_id == "tool:call-1"
    assert event.surface.slot == "trace_inline"
    assert event.payload["step_type"] == "tool"


def test_canonical_normalizer_emits_interaction_requested():
    events = normalize_raw_event(
        {
            "type": "human_input_requested",
            "run_id": "run-root",
            "iteration": 3,
            "request_id": "input-1",
            "kind": "selection",
            "question": "Pick one",
            "selection_mode": "single",
            "options": [{"label": "A", "value": "a"}],
        },
        context=RuntimeEventNormalizerContext(
            session_id="thread-1",
            root_run_id="run-root",
            root_agent_id="developer",
        ),
    )

    assert events[0].type == "interaction.requested"
    assert events[0].links.interaction_id == "input-1"
    assert events[0].payload["renderer"] == "single"


def test_canonical_normalizer_maps_thread_and_return_handoff_lifecycle_to_child_runs():
    context = RuntimeEventNormalizerContext(
        session_id="thread-1",
        root_run_id="run-root",
        root_agent_id="developer",
    )
    cases = [
        (
            {
                "type": "agent_thread_spawned",
                "root_run_id": "run-root",
                "child_run_id": "run-thread",
                "subagent_id": "developer.researcher.1",
                "parent_id": "developer",
                "mode": "thread",
                "thread_id": "developer.researcher.1",
                "background": False,
            },
            "run.started",
            "running",
        ),
        (
            {
                "type": "agent_thread_completed",
                "root_run_id": "run-root",
                "child_run_id": "run-thread",
                "subagent_id": "developer.researcher.1",
                "status": "completed",
                "thread_id": "developer.researcher.1",
            },
            "run.completed",
            "completed",
        ),
        (
            {
                "type": "agent_thread_failed",
                "root_run_id": "run-root",
                "child_run_id": "run-thread-failed",
                "subagent_id": "developer.researcher.2",
                "status": "failed",
                "error": "child exploded",
            },
            "run.failed",
            "failed",
        ),
        (
            {
                "type": "subagent_return_handoff_started",
                "root_run_id": "run-root",
                "child_run_id": "run-return",
                "subagent_id": "developer.reviewer.1",
                "mode": "return_handoff",
            },
            "run.started",
            "running",
        ),
        (
            {
                "type": "subagent_return_handoff_completed",
                "root_run_id": "run-root",
                "child_run_id": "run-return",
                "subagent_id": "developer.reviewer.1",
                "status": "completed",
            },
            "run.completed",
            "completed",
        ),
        (
            {
                "type": "subagent_return_handoff_completed",
                "root_run_id": "run-root",
                "child_run_id": "run-return-failed",
                "subagent_id": "developer.reviewer.2",
                "status": "failed",
                "error": {"code": "return_failed", "message": "return exploded"},
            },
            "run.failed",
            "failed",
        ),
    ]

    for raw_event, expected_type, expected_status in cases:
        [event] = normalize_raw_event(raw_event, context=context)
        assert event.type == expected_type
        assert event.run_id == raw_event["child_run_id"]
        assert event.agent_id == raw_event["subagent_id"]
        assert event.links.parent_run_id == "run-root"
        assert event.payload["status"] == expected_status
        assert event.metadata["raw_type"] == raw_event["type"]

    [spawned] = normalize_raw_event(cases[0][0], context=context)
    assert spawned.payload["thread_id"] == "developer.researcher.1"
    assert spawned.payload["background"] is False

    [thread_failed] = normalize_raw_event(cases[2][0], context=context)
    assert thread_failed.payload["error"]["message"] == "child exploded"

    [return_failed] = normalize_raw_event(cases[-1][0], context=context)
    assert return_failed.payload["error"] == {
        "code": "return_failed",
        "message": "return exploded",
    }


def test_canonical_normalizer_preserves_batch_handoff_and_thread_close_steps():
    context = RuntimeEventNormalizerContext(
        session_id="thread-1",
        root_run_id="run-root",
        root_agent_id="developer",
    )

    [batch_started] = normalize_raw_event(
        {
            "type": "subagent_batch_started",
            "run_id": "run-root",
            "iteration": 2,
            "batch_id": "batch-1",
            "subagent_id": "developer",
            "parent_id": "developer",
            "task_count": 3,
        },
        context=context,
    )
    [batch_joined] = normalize_raw_event(
        {
            "type": "subagent_batch_joined",
            "run_id": "run-root",
            "iteration": 2,
            "batch_id": "batch-1",
            "subagent_id": "developer",
            "parent_id": "developer",
            "completed_count": 2,
        },
        context=context,
    )

    assert batch_started.type == "step.started"
    assert batch_joined.type == "step.completed"
    assert batch_started.links.step_id == batch_joined.links.step_id == "agent-batch:batch-1"
    assert batch_started.payload["task_count"] == 3
    assert batch_joined.payload["completed_count"] == 2
    assert batch_started.payload["step_type"] == "agent_orchestration"

    [handoff] = normalize_raw_event(
        {
            "type": "subagent_handoff",
            "root_run_id": "run-root",
            "child_run_id": "run-handoff",
            "subagent_id": "developer.specialist.1",
            "parent_id": "developer",
            "reason": "Needs specialist ownership",
        },
        context=context,
    )
    assert handoff.type == "step.completed"
    assert handoff.run_id == "run-handoff"
    assert handoff.links.parent_run_id == "run-root"
    assert handoff.payload["operation"] == "handoff"
    assert handoff.payload["reason"] == "Needs specialist ownership"

    [closed] = normalize_raw_event(
        {
            "type": "agent_thread_closed",
            "run_id": "run-root",
            "iteration": 3,
            "thread_id": "developer.researcher.1",
            "subagent_id": "developer.researcher.1",
            "reason": "inspected",
        },
        context=context,
    )
    assert closed.type == "step.completed"
    assert closed.payload["operation"] == "agent_thread_close"
    assert closed.payload["status"] == "closed"


def test_canonical_bridge_keeps_supported_agent_events_out_of_dropped_diagnostics():
    bridge = RuntimeEventBridge(
        session_id="thread-1",
        root_run_id="run-root",
        root_agent_id="developer",
        clock=_clock,
    )
    raw_events = [
        {
            "type": "subagent_started",
            "root_run_id": "run-root",
            "child_run_id": "run-worker",
            "subagent_id": "developer.worker.1",
            "batch_id": "batch-1",
        },
        {
            "type": "subagent_batch_started",
            "run_id": "run-root",
            "batch_id": "batch-1",
            "task_count": 1,
        },
        {
            "type": "subagent_batch_joined",
            "run_id": "run-root",
            "batch_id": "batch-1",
            "completed_count": 1,
        },
        {
            "type": "subagent_handoff",
            "root_run_id": "run-root",
            "child_run_id": "run-handoff",
            "subagent_id": "developer.specialist.1",
        },
        {
            "type": "agent_thread_spawned",
            "root_run_id": "run-root",
            "child_run_id": "run-thread",
            "subagent_id": "developer.researcher.1",
            "thread_id": "developer.researcher.1",
        },
        {
            "type": "subagent_return_handoff_started",
            "root_run_id": "run-root",
            "child_run_id": "run-return",
            "subagent_id": "developer.reviewer.1",
        },
        {
            "type": "agent_thread_closed",
            "run_id": "run-root",
            "thread_id": "developer.researcher.1",
        },
    ]

    for raw_event in raw_events:
        assert bridge.normalize(raw_event)

    assert bridge.diagnostics()["dropped_event_count"] == 0
    [worker_started] = bridge.normalize(raw_events[0])
    assert worker_started.payload["batch_id"] == "batch-1"
