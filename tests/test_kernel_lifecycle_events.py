from __future__ import annotations

from unchain.kernel import ModelTurnResult, ToolCall
from unchain.kernel.results import build_legacy_run_bundle
from unchain.kernel.state import RunState


def _state_for_lifecycle_payloads() -> RunState:
    state = RunState()
    state.seed_messages([{"role": "user", "content": "hello"}])
    state.transcript.append({"role": "assistant", "content": "done"})
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-test"
    state.provider_state.max_context_window_tokens = 100
    state.token_state.consumed_tokens = 30
    state.token_state.input_tokens = 10
    state.token_state.output_tokens = 20
    state.token_state.last_turn_tokens = 25
    state.iteration = 3
    state.run_status = "running"
    return state


def test_kernel_lifecycle_events_build_run_payloads_from_state_and_turn():
    from unchain.kernel.lifecycle_events import (
        build_final_message_payload,
        build_iteration_completed_payload,
        build_iteration_started_payload,
        build_max_iterations_decision_payload,
        build_response_received_payload,
        build_run_completed_payload,
        build_run_max_iterations_payload,
        build_run_started_payload,
    )

    state = _state_for_lifecycle_payloads()
    turn = ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "using tool"}],
        tool_calls=[ToolCall(call_id="call-1", name="demo", arguments={})],
        response_id="resp-1",
    )

    assert build_run_started_payload(state) == {
        "iteration": 3,
        "provider": "openai",
        "model": "gpt-test",
    }
    assert build_run_max_iterations_payload(state) == {
        "iteration": 3,
        "bundle": build_legacy_run_bundle(state, status="max_iterations"),
    }
    assert build_max_iterations_decision_payload(state, max_iterations=6) == {
        "iteration": 3,
        "max_iterations": 6,
        "consumed_tokens": 30,
    }
    assert build_iteration_started_payload(state) == {"iteration": 3}
    assert build_response_received_payload(state, turn) == {
        "iteration": 2,
        "response_id": "resp-1",
        "has_tool_calls": True,
        "status": "running",
        "bundle": build_legacy_run_bundle(state, status="running"),
    }
    assert build_iteration_completed_payload(state, has_tool_calls=True) == {
        "iteration": 2,
        "has_tool_calls": True,
    }
    assert build_final_message_payload(state) == {
        "iteration": 2,
        "content": "done",
    }
    assert build_run_completed_payload(state, status="completed") == {
        "iteration": 2,
        "status": "completed",
        "bundle": build_legacy_run_bundle(state, status="completed"),
    }
