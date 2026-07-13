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
