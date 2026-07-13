from __future__ import annotations

from typing import Any

from unchain.kernel.lifecycle_events import (
    build_max_iterations_decision_payload,
    build_run_max_iterations_payload,
)
from unchain.kernel.state import RunState


def _state(iteration: int = 3) -> RunState:
    state = RunState()
    state.seed_messages([{"role": "user", "content": "hello"}])
    state.iteration = iteration
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-test"
    state.token_state.consumed_tokens = 12
    return state


def test_max_iterations_boundary_allows_run_when_under_budget():
    from unchain.kernel.run_limits import resolve_max_iterations_boundary

    events: list[dict[str, Any]] = []
    outcome = resolve_max_iterations_boundary(
        _state(iteration=2),
        effective_max=3,
        on_max_iterations=None,
        callback="callback",
        run_id="run-1",
        emit_event=lambda callback, event_type, run_id, **payload: events.append(
            {"callback": callback, "type": event_type, "run_id": run_id, **payload}
        ),
    )

    assert outcome.should_finish is False
    assert outcome.effective_max == 3
    assert outcome.emit_run_max_iterations_on_finish is False
    assert events == []


def test_max_iterations_boundary_finishes_without_approval_callback():
    from unchain.kernel.run_limits import resolve_max_iterations_boundary

    events: list[dict[str, Any]] = []
    outcome = resolve_max_iterations_boundary(
        _state(iteration=3),
        effective_max=3,
        on_max_iterations=None,
        callback="callback",
        run_id="run-1",
        emit_event=lambda callback, event_type, run_id, **payload: events.append(
            {"callback": callback, "type": event_type, "run_id": run_id, **payload}
        ),
    )

    assert outcome.should_finish is True
    assert outcome.effective_max == 3
    assert outcome.emit_run_max_iterations_on_finish is True
    assert events == []


def test_max_iterations_boundary_emits_before_approval_callback_and_extends_budget():
    from unchain.kernel.run_limits import resolve_max_iterations_boundary

    state = _state(iteration=3)
    approval_payloads: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    outcome = resolve_max_iterations_boundary(
        state,
        effective_max=3,
        on_max_iterations=lambda payload: approval_payloads.append(payload)
        or {
            "approved": True,
            "extra_iterations": 2,
        },
        callback="callback",
        run_id="run-1",
        emit_event=lambda callback, event_type, run_id, **payload: events.append(
            {"callback": callback, "type": event_type, "run_id": run_id, **payload}
        ),
    )

    assert approval_payloads == [
        build_max_iterations_decision_payload(state, max_iterations=3)
    ]
    assert events == [
        {
            "callback": "callback",
            "type": "run_max_iterations",
            "run_id": "run-1",
            **build_run_max_iterations_payload(state),
        }
    ]
    assert outcome.should_finish is False
    assert outcome.effective_max == 5
    assert outcome.emit_run_max_iterations_on_finish is False


def test_max_iterations_boundary_denial_finishes_without_duplicate_event():
    from unchain.kernel.run_limits import resolve_max_iterations_boundary

    state = _state(iteration=3)
    outcome = resolve_max_iterations_boundary(
        state,
        effective_max=3,
        on_max_iterations=lambda _payload: {"approved": False},
        callback=None,
        run_id="run-1",
        emit_event=lambda *_args, **_kwargs: None,
    )

    assert outcome.should_finish is True
    assert outcome.effective_max == 3
    assert outcome.emit_run_max_iterations_on_finish is False
