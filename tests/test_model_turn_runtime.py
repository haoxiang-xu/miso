from __future__ import annotations

from unchain.kernel import ModelTurnResult
from unchain.kernel.state import RunState
from unchain.retry import RetryConfig
from unchain.tools import Toolkit


class _RecordingModelIO:
    def __init__(self, result: ModelTurnResult) -> None:
        self.result = result
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        return self.result


def test_provider_model_turn_runtime_builds_request_and_applies_turn_state():
    from unchain.providers.model_turn_runtime import apply_model_turn_result, fetch_model_turn

    turn = ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "done"}],
        tool_calls=[],
        final_text="done",
        response_id="resp-1",
        consumed_tokens=7,
        input_tokens=4,
        output_tokens=3,
        cache_read_input_tokens=2,
        cache_creation_input_tokens=1,
    )
    model_io = _RecordingModelIO(turn)
    state = RunState()
    state.seed_messages([{"role": "user", "content": "original"}])
    state.next_model_input = [{"role": "user", "content": "resume-only"}]
    state.provider_state.previous_response_id = "prev-1"
    state.provider_state.use_previous_response_chain = True
    state.token_state.consumed_tokens = 10
    state.token_state.input_tokens = 6
    state.token_state.output_tokens = 4
    state.token_state.cache_read_input_tokens = 3
    state.token_state.cache_creation_input_tokens = 2

    fetched = fetch_model_turn(
        model_io=model_io,
        retry_config=RetryConfig(max_retries=0),
        state=state,
        payload={"temperature": 0.2},
        toolkit=Toolkit(),
        run_id="run-1",
    )
    apply_model_turn_result(state, fetched)

    request = model_io.requests[0]
    assert request.messages == [{"role": "user", "content": "resume-only"}]
    assert request.payload == {"temperature": 0.2}
    assert request.previous_response_id == "prev-1"
    assert request.run_id == "run-1"

    assert state.provider_state.previous_response_id == "resp-1"
    assert state.next_model_input is None
    assert state.token_state.consumed_tokens == 17
    assert state.token_state.input_tokens == 10
    assert state.token_state.output_tokens == 7
    assert state.token_state.cache_read_input_tokens == 5
    assert state.token_state.cache_creation_input_tokens == 3
    assert state.token_state.last_turn_tokens == 7
    assert state.token_state.last_turn_input_tokens == 4
    assert state.token_state.last_turn_output_tokens == 3
    assert state.token_state.last_turn_cache_read_input_tokens == 2
    assert state.token_state.last_turn_cache_creation_input_tokens == 1
