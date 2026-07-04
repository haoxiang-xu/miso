from __future__ import annotations

from typing import Any

from unchain.kernel.lifecycle_events import (
    build_final_message_payload,
    build_run_completed_payload,
    build_run_max_iterations_payload,
)
from unchain.kernel.state import RunState


def _state_for_run_outcome() -> RunState:
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
    return state


def test_finish_completed_run_emits_lifecycle_events_finalizes_and_returns_result():
    from unchain.kernel.run_outcomes import finish_completed_run

    state = _state_for_run_outcome()
    events: list[tuple[Any, str, str, dict[str, Any]]] = []
    finalizing: list[dict[str, Any]] = []

    result = finish_completed_run(
        state,
        callback="callback",
        run_id="run-1",
        emit_event=lambda callback, name, run_id, **payload: events.append(
            (callback, name, run_id, payload)
        ),
        dispatch_run_finalizing=lambda _state, **payload: finalizing.append(payload),
    )

    assert result.status == "completed"
    assert result.iteration == 3
    assert events == [
        ("callback", "final_message", "run-1", build_final_message_payload(state)),
        (
            "callback",
            "run_completed",
            "run-1",
            build_run_completed_payload(state, status="completed"),
        ),
    ]
    assert finalizing == [
        {
            "callback": "callback",
            "run_id": "run-1",
            "iteration": 2,
            "status": "completed",
        }
    ]


def test_finish_max_iterations_run_finalizes_and_controls_event_emission():
    from unchain.kernel.run_outcomes import finish_max_iterations_run

    state = _state_for_run_outcome()
    emitted_events: list[tuple[Any, str, str, dict[str, Any]]] = []
    emitted_finalizing: list[dict[str, Any]] = []

    emitted_result = finish_max_iterations_run(
        state,
        callback="callback",
        run_id="run-1",
        emit_run_max_iterations=True,
        emit_event=lambda callback, name, run_id, **payload: emitted_events.append(
            (callback, name, run_id, payload)
        ),
        dispatch_run_finalizing=lambda _state, **payload: emitted_finalizing.append(payload),
    )

    assert emitted_result.status == "max_iterations"
    assert state.run_status == "max_iterations"
    assert emitted_events == [
        (
            "callback",
            "run_max_iterations",
            "run-1",
            build_run_max_iterations_payload(state),
        )
    ]
    assert emitted_finalizing == [
        {
            "callback": "callback",
            "run_id": "run-1",
            "iteration": 3,
            "status": "max_iterations",
        }
    ]

    silent_state = _state_for_run_outcome()
    silent_events: list[tuple[Any, str, str, dict[str, Any]]] = []
    silent_finalizing: list[dict[str, Any]] = []

    silent_result = finish_max_iterations_run(
        silent_state,
        callback="callback",
        run_id="run-2",
        emit_run_max_iterations=False,
        emit_event=lambda callback, name, run_id, **payload: silent_events.append(
            (callback, name, run_id, payload)
        ),
        dispatch_run_finalizing=lambda _state, **payload: silent_finalizing.append(payload),
    )

    assert silent_result.status == "max_iterations"
    assert silent_state.run_status == "max_iterations"
    assert silent_events == []
    assert silent_finalizing == [
        {
            "callback": "callback",
            "run_id": "run-2",
            "iteration": 3,
            "status": "max_iterations",
        }
    ]
