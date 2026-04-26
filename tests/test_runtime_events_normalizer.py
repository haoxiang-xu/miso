from __future__ import annotations

from unchain.events.normalizer import RuntimeEventNormalizerContext, normalize_raw_event


def test_normalizes_model_and_tool_events():
    context = RuntimeEventNormalizerContext(
        session_id="thread-1",
        root_run_id="run-root",
        root_agent_id="developer",
    )

    model_events = normalize_raw_event(
        {
            "type": "token_delta",
            "run_id": "run-root",
            "iteration": 2,
            "provider": "openai",
            "delta": "hello",
            "accumulated_text": "hello",
        },
        context=context,
    )
    tool_events = normalize_raw_event(
        {
            "type": "tool_result",
            "run_id": "run-root",
            "iteration": 2,
            "tool_name": "read_file",
            "call_id": "call-1",
            "result": {"content": "abc"},
        },
        context=context,
    )

    assert len(model_events) == 1
    assert model_events[0].type == "model.delta"
    assert model_events[0].turn_id == "run-root:turn-2"
    assert model_events[0].payload == {
        "kind": "text",
        "delta": "hello",
        "accumulated_text": "hello",
    }
    assert model_events[0].metadata["provider"] == "openai"

    assert len(tool_events) == 1
    assert tool_events[0].type == "tool.completed"
    assert tool_events[0].links.tool_call_id == "call-1"
    assert tool_events[0].payload["status"] == "success"
    assert tool_events[0].payload["result"] == {"content": "abc"}


def test_tool_started_preserves_confirmation_id():
    context = RuntimeEventNormalizerContext(
        session_id="thread-1",
        root_run_id="run-root",
        root_agent_id="developer",
    )

    events = normalize_raw_event(
        {
            "type": "tool_call",
            "run_id": "run-root",
            "iteration": 1,
            "tool_name": "write_file",
            "call_id": "call-1",
            "confirmation_id": "confirm-1",
            "requires_confirmation": True,
        },
        context=context,
    )

    assert events[0].type == "tool.started"
    assert events[0].payload["confirmation_id"] == "confirm-1"
    assert events[0].payload["requires_confirmation"] is True


def test_normalizes_human_input_request_and_resolution():
    context = RuntimeEventNormalizerContext(
        session_id="thread-1",
        root_run_id="run-root",
        root_agent_id="developer",
    )

    requested = normalize_raw_event(
        {
            "type": "human_input_requested",
            "run_id": "run-root",
            "iteration": 1,
            "request_id": "input-1",
            "kind": "selection",
            "title": "Choose",
            "question": "Pick one",
            "selection_mode": "single",
            "options": [{"label": "A", "value": "a"}],
        },
        context=context,
    )
    resolved = normalize_raw_event(
        {
            "type": "tool_confirmed",
            "run_id": "run-root",
            "iteration": 1,
            "call_id": "call-1",
            "confirmation_id": "input-1",
            "user_response": {"value": "a"},
        },
        context=context,
    )

    assert requested[0].type == "input.requested"
    assert requested[0].links.input_request_id == "input-1"
    assert requested[0].payload["kind"] == "selection"
    assert requested[0].payload["interact_type"] == "single"

    assert resolved[0].type == "input.resolved"
    assert resolved[0].links.input_request_id == "input-1"
    assert resolved[0].links.tool_call_id == "call-1"
    assert resolved[0].payload == {
        "decision": "approved",
        "response": {"value": "a"},
    }


def test_normalizes_subagent_lifecycle_with_parent_link():
    context = RuntimeEventNormalizerContext(
        session_id="thread-1",
        root_run_id="run-root",
        root_agent_id="developer",
    )

    started = normalize_raw_event(
        {
            "type": "subagent_started",
            "run_id": "run-root",
            "root_run_id": "run-root",
            "child_run_id": "run-child",
            "subagent_id": "developer.worker.1",
            "parent_id": "developer",
            "mode": "worker",
            "template": "worker",
            "lineage": ["developer", "developer.worker.1"],
        },
        context=context,
    )
    completed = normalize_raw_event(
        {
            "type": "subagent_completed",
            "run_id": "run-root",
            "root_run_id": "run-root",
            "child_run_id": "run-child",
            "subagent_id": "developer.worker.1",
            "parent_id": "developer",
            "mode": "worker",
            "template": "worker",
            "lineage": ["developer", "developer.worker.1"],
            "status": "completed",
        },
        context=context,
    )

    assert started[0].type == "run.started"
    assert started[0].run_id == "run-child"
    assert started[0].agent_id == "developer.worker.1"
    assert started[0].links.parent_run_id == "run-root"
    assert started[0].payload["mode"] == "worker"

    assert completed[0].type == "run.completed"
    assert completed[0].run_id == "run-child"
    assert completed[0].links.parent_run_id == "run-root"
    assert completed[0].payload["status"] == "completed"


def test_unknown_raw_event_returns_no_draft():
    context = RuntimeEventNormalizerContext(
        session_id="thread-1",
        root_run_id="run-root",
        root_agent_id="developer",
    )

    assert normalize_raw_event({"type": "unknown_event"}, context=context) == []
