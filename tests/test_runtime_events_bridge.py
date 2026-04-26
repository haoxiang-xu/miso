from __future__ import annotations

from datetime import datetime, timezone

from unchain.events import RuntimeEvent
from unchain.events.bridge import RuntimeEventBridge


def _id_factory():
    counter = {"value": 0}

    def next_id() -> str:
        counter["value"] += 1
        return f"evt-{counter['value']}"

    return next_id


def _clock() -> datetime:
    return datetime(2026, 4, 25, 12, 34, 56, 789000, tzinfo=timezone.utc)


def test_bridge_emits_session_started():
    bridge = RuntimeEventBridge(
        session_id="thread-1",
        root_run_id="run-root",
        id_factory=_id_factory(),
        clock=_clock,
    )

    event = bridge.emit_session_started({"model": "gpt-5"})

    assert isinstance(event, RuntimeEvent)
    assert event.to_dict() == {
        "schema_version": "v3",
        "event_id": "evt-1",
        "type": "session.started",
        "timestamp": "2026-04-25T12:34:56.789000Z",
        "session_id": "thread-1",
        "run_id": "run-root",
        "agent_id": "developer",
        "turn_id": None,
        "links": {
            "parent_run_id": None,
            "parent_event_id": None,
            "caused_by_event_id": None,
            "tool_call_id": None,
            "input_request_id": None,
            "channel_id": None,
            "team_id": None,
            "plan_id": None,
        },
        "visibility": "debug",
        "payload": {"model": "gpt-5"},
        "metadata": {},
    }


def test_bridge_normalizes_raw_events_and_updates_root_run_id():
    bridge = RuntimeEventBridge(
        session_id="thread-1",
        root_agent_id="developer",
        id_factory=_id_factory(),
        clock=_clock,
    )

    events = bridge.normalize(
        {
            "type": "run_started",
            "run_id": "run-root",
            "iteration": 0,
            "provider": "openai",
            "model": "gpt-5",
        }
    )
    delta = bridge.normalize(
        {
            "type": "token_delta",
            "run_id": "run-root",
            "iteration": 0,
            "delta": "hi",
        }
    )

    assert bridge.root_run_id == "run-root"
    assert events[0].event_id == "evt-1"
    assert events[0].type == "run.started"
    assert events[0].payload["model"] == "gpt-5"
    assert delta[0].type == "model.delta"
    assert delta[0].run_id == "run-root"


def test_bridge_records_dropped_unknown_events():
    bridge = RuntimeEventBridge(
        session_id="thread-1",
        root_run_id="run-root",
        id_factory=_id_factory(),
        clock=_clock,
    )

    assert bridge.normalize({"type": "unknown_event", "value": 1}) == []

    diagnostics = bridge.diagnostics()
    assert diagnostics["dropped_event_count"] == 1
    assert diagnostics["dropped_events"][0]["type"] == "unknown_event"


def test_bridge_emits_transport_failure_as_run_failed():
    bridge = RuntimeEventBridge(
        session_id="thread-1",
        root_run_id="run-root",
        id_factory=_id_factory(),
        clock=_clock,
    )

    event = bridge.emit_transport_failure("boom", code="stream_failed")

    assert event.type == "run.failed"
    assert event.run_id == "run-root"
    assert event.payload == {
        "status": "failed",
        "error": {
            "code": "stream_failed",
            "message": "boom",
        },
        "recoverable": False,
    }
