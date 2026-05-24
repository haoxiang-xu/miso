from __future__ import annotations

import json

import pytest

from unchain.events import RuntimeEvent, RuntimeEventLinks


def test_runtime_event_links_serializes_all_reserved_keys():
    links = RuntimeEventLinks()

    assert links.to_dict() == {
        "parent_run_id": None,
        "parent_event_id": None,
        "caused_by_event_id": None,
        "tool_call_id": None,
        "input_request_id": None,
        "channel_id": None,
        "team_id": None,
        "plan_id": None,
    }


def test_runtime_event_to_dict_is_json_safe():
    event = RuntimeEvent(
        event_id="evt-1",
        type="tool.started",
        timestamp="2026-04-25T12:34:56.789Z",
        session_id="thread-1",
        run_id="run-1",
        agent_id="developer",
        turn_id="turn-0",
        links=RuntimeEventLinks(tool_call_id="call-1"),
        visibility="user",
        payload={"tool_name": "read_file", "count": 1, "raw": object()},
        metadata={"source": "test", "nested": {"value": object()}},
    )

    data = event.to_dict()

    assert data["schema_version"] == "v3"
    assert data["links"]["tool_call_id"] == "call-1"
    assert data["payload"]["tool_name"] == "read_file"
    assert isinstance(data["payload"]["raw"], str)
    assert isinstance(data["metadata"]["nested"]["value"], str)
    json.dumps(data)


def test_runtime_event_round_trips_from_dict():
    raw = {
        "schema_version": "v3",
        "event_id": "evt-1",
        "type": "model.delta",
        "timestamp": "2026-04-25T12:34:56.789Z",
        "session_id": "thread-1",
        "run_id": "run-1",
        "agent_id": "developer",
        "turn_id": "turn-0",
        "links": {"tool_call_id": "call-1"},
        "visibility": "user",
        "payload": {"kind": "text", "delta": "hi"},
        "metadata": {"provider": "openai"},
    }

    event = RuntimeEvent.from_dict(raw)

    assert event.type == "model.delta"
    assert event.links.tool_call_id == "call-1"
    assert event.payload == {"kind": "text", "delta": "hi"}
    assert event.to_dict()["links"]["team_id"] is None


def test_runtime_event_rejects_invalid_schema_version():
    with pytest.raises(ValueError, match="schema_version"):
        RuntimeEvent.from_dict(
            {
                "schema_version": "v2",
                "event_id": "evt-1",
                "type": "model.delta",
                "timestamp": "2026-04-25T12:34:56.789Z",
                "session_id": "thread-1",
                "run_id": "run-1",
                "agent_id": "developer",
                "visibility": "user",
            }
        )


def test_runtime_event_rejects_unknown_type_in_strict_mode():
    raw = {
        "schema_version": "v3",
        "event_id": "evt-1",
        "type": "team.started",
        "timestamp": "2026-04-25T12:34:56.789Z",
        "session_id": "thread-1",
        "run_id": "run-1",
        "agent_id": "developer",
        "visibility": "user",
    }

    with pytest.raises(ValueError, match="unknown event type"):
        RuntimeEvent.from_dict(raw)

    event = RuntimeEvent.from_dict(raw, strict=False)
    assert event.type == "team.started"
