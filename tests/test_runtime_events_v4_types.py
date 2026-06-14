from __future__ import annotations

from unchain.events_v4 import RuntimeEventLinksV4, RuntimeEventV4, RuntimeEventSurface


def test_runtime_event_v4_round_trips_surface_and_extended_links():
    event = RuntimeEventV4(
        event_id="evt-1",
        type="artifact.created",
        timestamp="2026-05-26T12:00:00Z",
        session_id="thread-1",
        run_id="run-1",
        agent_id="developer",
        turn_id=None,
        seq=7,
        links=RuntimeEventLinksV4(
            step_id="step-1",
            tool_call_id="call-1",
            interaction_id="confirm-1",
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
    assert raw["surface"] == {
        "slot": "run_summary",
        "scope": "run",
        "group": "files",
        "default_state": "expanded",
        "priority": 50,
    }
    assert RuntimeEventV4.from_dict(raw).to_dict() == raw


def test_runtime_event_v4_rejects_unknown_strict_event_type():
    raw = {
        "schema_version": "v4",
        "event_id": "evt-1",
        "type": "legacy.event",
        "timestamp": "2026-05-26T12:00:00Z",
        "session_id": "thread-1",
        "run_id": "run-1",
        "agent_id": "developer",
        "seq": 1,
    }

    try:
        RuntimeEventV4.from_dict(raw)
    except ValueError as exc:
        assert "unknown event type" in str(exc)
    else:
        raise AssertionError("expected unknown event type to be rejected")
