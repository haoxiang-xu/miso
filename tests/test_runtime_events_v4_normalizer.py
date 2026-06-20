from __future__ import annotations

from unchain.events.normalizer import RuntimeEventNormalizerContext, normalize_raw_event


def _context():
    return RuntimeEventNormalizerContext(
        session_id="thread-1",
        root_run_id="run-root",
        root_agent_id="developer",
    )


def test_v4_normalizes_tool_call_to_step_started():
    events = normalize_raw_event(
        {
            "type": "tool_call",
            "run_id": "run-root",
            "iteration": 2,
            "tool_name": "write",
            "call_id": "call-1",
            "arguments": {"path": "a.py"},
        },
        context=_context(),
    )

    assert len(events) == 1
    event = events[0]
    assert event.type == "step.started"
    assert event.turn_id == "run-root:turn-2"
    assert event.links.step_id == "tool:call-1"
    assert event.links.tool_call_id == "call-1"
    assert event.surface.slot == "trace_inline"
    assert event.surface.scope == "turn"
    assert event.payload == {
        "step_id": "tool:call-1",
        "step_type": "tool",
        "tool_name": "write",
        "call_id": "call-1",
        "arguments": {"path": "a.py"},
    }


def test_v4_normalizes_code_diff_confirmation_to_interaction_requested():
    events = normalize_raw_event(
        {
            "type": "tool_confirmation_requested",
            "run_id": "run-root",
            "iteration": 1,
            "confirmation_id": "confirm-1",
            "tool_name": "write",
            "toolkit_id": "core",
            "call_id": "call-1",
            "arguments": {"path": "a.py"},
            "interact_type": "code_diff",
            "interact_config": {"unified_diff": "--- a.py\n+++ a.py"},
            "description": "Edit a.py",
        },
        context=_context(),
    )

    assert len(events) == 1
    event = events[0]
    assert event.type == "interaction.requested"
    assert event.links.interaction_id == "confirm-1"
    assert event.links.tool_call_id == "call-1"
    assert event.surface.slot == "trace_inline"
    assert event.payload["interaction_id"] == "confirm-1"
    assert event.payload["kind"] == "code_diff"
    assert event.payload["renderer"] == "code_diff"
    assert event.payload["blocking"] is True
    assert event.payload["target"] == {
        "tool_call_id": "call-1",
        "tool_name": "write",
        "toolkit_id": "core",
        "arguments": {"path": "a.py"},
    }
    assert event.payload["config"] == {"unified_diff": "--- a.py\n+++ a.py"}


def test_v4_normalizes_human_input_to_choice_interaction():
    events = normalize_raw_event(
        {
            "type": "human_input_requested",
            "run_id": "run-root",
            "iteration": 3,
            "request_id": "input-1",
            "kind": "selection",
            "title": "Choose",
            "question": "Pick one",
            "selection_mode": "single",
            "options": [{"label": "A", "value": "a"}],
        },
        context=_context(),
    )

    assert events[0].type == "interaction.requested"
    assert events[0].links.interaction_id == "input-1"
    assert events[0].payload["kind"] == "choice"
    assert events[0].payload["renderer"] == "single"
    assert events[0].payload["selection_mode"] == "single"
    assert events[0].payload["prompt"] == "Pick one"
    assert events[0].payload["options"] == [{"label": "A", "value": "a"}]
    assert events[0].payload["config"]["selection_mode"] == "single"


def test_v4_normalizes_tool_denied_to_interaction_resolved():
    events = normalize_raw_event(
        {
            "type": "tool_denied",
            "run_id": "run-root",
            "iteration": 1,
            "confirmation_id": "confirm-1",
            "call_id": "call-1",
            "reason": "no",
        },
        context=_context(),
    )

    assert events[0].type == "interaction.resolved"
    assert events[0].links.interaction_id == "confirm-1"
    assert events[0].payload == {
        "interaction_id": "confirm-1",
        "outcome": "denied",
        "response": None,
        "reason": "no",
    }


def test_v4_artifact_surface_uses_run_summary_for_workspace_change_set():
    artifact = {
        "schema_version": "unchain.artifact.v1",
        "artifact_id": "workspace_change_set:run-root",
        "kind": "workspace_change_set",
        "title": "Workspace changes",
        "snapshot": {
            "change_set_id": "wcs_run-root",
            "totals": {"files": 1},
        },
        "presentation": {
            "surface": "run_summary",
            "group": "files",
            "collapsed": False,
        },
    }

    events = normalize_raw_event(
        {
            "type": "artifact_created",
            "run_id": "run-root",
            "iteration": 4,
            "artifact_id": "workspace_change_set:run-root",
            "artifact": artifact,
        },
        context=_context(),
    )

    assert events[0].type == "artifact.created"
    assert events[0].turn_id == "run-root:turn-4"
    assert events[0].links.artifact_id == "workspace_change_set:run-root"
    assert events[0].links.workspace_change_set_id == "wcs_run-root"
    assert events[0].surface.slot == "run_summary"
    assert events[0].surface.scope == "run"
    assert events[0].surface.group == "files"
    assert events[0].surface.default_state == "expanded"
    assert events[0].payload == artifact
