from __future__ import annotations

from unchain.input.human_input import HumanInputOption, HumanInputRequest
from unchain.kernel.state import RunState


def _state_with_result_fields() -> RunState:
    state = RunState()
    state.seed_messages([{"role": "user", "content": "hello"}])
    state.transcript.append({"role": "assistant", "content": "done"})
    state.last_continuation = {"type": "human_input_continuation", "request_id": "input-1"}
    state.tool_batch_state.human_input_request = HumanInputRequest(
        request_id="input-1",
        kind="selector",
        title="Choose",
        question="Pick one",
        selection_mode="single",
        options=[HumanInputOption(label="React", value="react")],
        allow_other=False,
    )
    state.token_state.consumed_tokens = 30
    state.token_state.input_tokens = 10
    state.token_state.output_tokens = 20
    state.token_state.last_turn_tokens = 12
    state.token_state.last_turn_input_tokens = 5
    state.token_state.last_turn_output_tokens = 7
    state.token_state.cache_read_input_tokens = 3
    state.token_state.cache_creation_input_tokens = 4
    state.provider_state.model = "gpt-test"
    state.provider_state.previous_response_id = "resp-1"
    state.provider_state.max_context_window_tokens = 48
    state.iteration = 2
    return state


def test_kernel_results_builds_run_result_and_legacy_bundle_from_state():
    from unchain.kernel.results import build_kernel_run_result, build_legacy_run_bundle

    state = _state_with_result_fields()

    result = build_kernel_run_result(state, status="awaiting_human_input")
    bundle = build_legacy_run_bundle(state, status="awaiting_human_input")
    state.transcript[0]["content"] = "mutated"
    state.last_continuation["request_id"] = "mutated"

    assert result.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "done"},
    ]
    assert result.status == "awaiting_human_input"
    assert result.continuation == {"type": "human_input_continuation", "request_id": "input-1"}
    assert result.human_input_request is not None
    assert result.human_input_request["request_id"] == "input-1"
    assert result.consumed_tokens == 30
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.last_turn_tokens == 12
    assert result.last_turn_input_tokens == 5
    assert result.last_turn_output_tokens == 7
    assert result.cache_read_input_tokens == 3
    assert result.cache_creation_input_tokens == 4
    assert result.previous_response_id == "resp-1"
    assert result.iteration == 2

    assert bundle == {
        "model": "gpt-test",
        "consumed_tokens": 30,
        "input_tokens": 10,
        "output_tokens": 20,
        "last_turn_tokens": 12,
        "last_turn_input_tokens": 5,
        "last_turn_output_tokens": 7,
        "max_context_window_tokens": 48,
        "context_window_used_pct": 25.0,
        "status": "awaiting_human_input",
        "human_input_request": result.human_input_request,
        "continuation": {"type": "human_input_continuation", "request_id": "input-1"},
        "previous_response_id": "resp-1",
        "iteration": 2,
    }
