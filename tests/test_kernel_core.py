from miso.kernel import (
    AppendMessagesOp,
    BaseRuntimeHarness,
    HarnessDelta,
    InsertMessagesOp,
    KernelLoop,
    LegacyBrothModelIO,
    ModelTurnRequest,
    OpenAIModelIO,
)
from miso.kernel.types import ToolCall as KernelToolCall
from miso.tools import Toolkit
from miso.kernel.types import ModelTurnResult


def test_run_state_rebases_old_view_delta_onto_latest_version():
    loop = KernelLoop()
    state = loop.seed_state([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ])
    seed_version_id = state.latest_version_id

    state.apply_delta(
        HarnessDelta(
            created_by="append_assistant",
            ops=(AppendMessagesOp(messages=[{"role": "assistant", "content": "later"}]),),
        )
    )
    latest_before_rebase = state.latest_version_id

    rebased_version_id = state.apply_delta(
        HarnessDelta(
            created_by="insert_memory",
            base_version_id=seed_version_id,
            ops=(InsertMessagesOp(index=1, messages=[{"role": "system", "content": "memory"}]),),
        )
    )

    assert seed_version_id is not None
    assert latest_before_rebase is not None
    assert rebased_version_id is not None
    assert state.latest_messages() == [
        {"role": "system", "content": "sys"},
        {"role": "system", "content": "memory"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "later"},
    ]

    version = state.versions.get_version(rebased_version_id)
    assert version.parent_version_id == latest_before_rebase
    assert version.metadata["requested_base_version_id"] == seed_version_id
    assert version.metadata["applied_base_version_id"] == latest_before_rebase


class _AppendHarness(BaseRuntimeHarness):
    def __init__(self, *, name: str, order: int, text: str) -> None:
        super().__init__(name=name, phases=("before_model",), order=order)
        self._text = text

    def build_delta(self, context):
        return HarnessDelta.append(
            created_by=self.name,
            messages=[{"role": "system", "content": self._text}],
        )


def test_kernel_loop_applies_harnesses_in_order():
    loop = KernelLoop(
        harnesses=[
            _AppendHarness(name="late", order=20, text="second"),
            _AppendHarness(name="early", order=10, text="first"),
        ]
    )
    state = loop.seed_state([{"role": "user", "content": "start"}])

    loop.dispatch_phase(state, phase="before_model")

    assert state.latest_messages() == [
        {"role": "user", "content": "start"},
        {"role": "system", "content": "first"},
        {"role": "system", "content": "second"},
    ]


def test_legacy_broth_model_io_delegates_fetch_once_to_engine():
    bridge = LegacyBrothModelIO.from_config(provider="openai", model="gpt-5", api_key="test-key")
    seen = {}

    def fake_fetch_once(**kwargs):
        seen.update(kwargs)
        from miso.runtime import ProviderTurnResult

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "ok"}],
            tool_calls=[],
            final_text="ok",
            response_id="resp_1",
            consumed_tokens=7,
            input_tokens=4,
            output_tokens=3,
        )

    bridge.engine._fetch_once = fake_fetch_once
    loop = KernelLoop(model_io=bridge)
    state = loop.seed_state([{"role": "user", "content": "start"}], provider="openai", model="gpt-5")
    state.provider_state.previous_response_id = "prev_0"

    turn = loop.fetch_model_turn(
        state,
        payload={"temperature": 0.2},
        toolkit=Toolkit(),
        run_id="kernel-run",
        emit_stream=True,
    )

    assert turn.final_text == "ok"
    assert seen["messages"] == [{"role": "user", "content": "start"}]
    assert seen["payload"] == {"temperature": 0.2}
    assert seen["run_id"] == "kernel-run"
    assert seen["iteration"] == 0
    assert seen["emit_stream"] is True
    assert seen["previous_response_id"] == "prev_0"


def test_openai_model_io_builds_request_and_parses_text(monkeypatch):
    captured_kwargs = {}
    events = []

    class FakeOpenAIStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield type("Chunk", (), {
                "type": "response.completed",
                "response": type("Resp", (), {
                    "id": "resp_openai",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                    "usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7},
                })(),
            })()

    class FakeResponses:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeOpenAIStream()

    class FakeOpenAIClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.responses = FakeResponses()

    io = OpenAIModelIO(model="gpt-4.1", api_key="test-key", client_factory=FakeOpenAIClient)
    loop = KernelLoop(model_io=io)
    state = loop.seed_state([{"role": "user", "content": "hi"}], provider="openai", model="gpt-4.1")
    state.provider_state.previous_response_id = "prev_0"

    turn = loop.fetch_model_turn(
        state,
        payload={"temperature": 0.2},
        toolkit=Toolkit(),
        callback=events.append,
        run_id="kernel-openai",
    )

    assert isinstance(turn, ModelTurnResult)
    assert turn.final_text == "ok"
    assert turn.response_id == "resp_openai"
    assert turn.consumed_tokens == 7
    assert turn.input_tokens == 4
    assert turn.output_tokens == 3
    assert captured_kwargs["model"] == "gpt-4.1"
    assert captured_kwargs["previous_response_id"] == "prev_0"
    assert captured_kwargs["stream"] is True
    assert captured_kwargs["temperature"] == 0.2
    request_event = next(evt for evt in events if evt["type"] == "request_messages")
    assert request_event["provider"] == "openai"
    assert request_event["previous_response_id"] == "prev_0"


def test_openai_model_io_parses_function_calls_without_completed_event():
    class FakeOpenAIStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield type("Chunk", (), {
                "type": "response.created",
                "response": type("Resp", (), {"id": "resp_partial"})(),
            })()
            yield type("Chunk", (), {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "demo_tool",
                    "arguments": "{}",
                    "status": "completed",
                },
            })()

    class FakeResponses:
        def create(self, **kwargs):
            return FakeOpenAIStream()

    class FakeOpenAIClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.responses = FakeResponses()

    io = OpenAIModelIO(model="gpt-4.1", api_key="test-key", client_factory=FakeOpenAIClient)
    request = ModelTurnRequest(
        messages=[{"role": "user", "content": "hi"}],
        toolkit=Toolkit(),
    )
    turn = io.fetch_turn(request)

    assert request.messages == [{"role": "user", "content": "hi"}]
    assert turn.response_id == "resp_partial"
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "demo_tool"
    assert turn.tool_calls[0].call_id == "call_1"


class _FakeModelIO:
    def __init__(self, result: ModelTurnResult) -> None:
        self.result = result
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        return self.result


class _RecordingHarness(BaseRuntimeHarness):
    def __init__(self, *, name: str, phases: tuple[str, ...], sink: list[tuple[str, dict]]) -> None:
        super().__init__(name=name, phases=phases, order=10)
        self._sink = sink

    def build_delta(self, context):
        self._sink.append((context.phase, context.event_payload()))
        return None


def test_step_once_runs_before_after_model_and_before_commit_without_tool_calls():
    events = []
    model_io = _FakeModelIO(
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_1",
            consumed_tokens=7,
            input_tokens=4,
            output_tokens=3,
        )
    )
    loop = KernelLoop(
        model_io=model_io,
        harnesses=[
            _RecordingHarness(name="before", phases=("before_model",), sink=events),
            _RecordingHarness(name="after", phases=("after_model",), sink=events),
            _RecordingHarness(name="commit", phases=("before_commit",), sink=events),
        ],
    )
    state = loop.seed_state([{"role": "user", "content": "start"}], provider="openai", model="gpt-4.1")
    state.provider_state.previous_response_id = "prev_0"

    turn = loop.step_once(state, payload={"temperature": 0.2}, toolkit=Toolkit(), run_id="step-run")

    assert turn.final_text == "done"
    assert [phase for phase, _ in events] == ["before_model", "after_model", "before_commit"]
    assert state.iteration == 1
    assert state.last_model_turn == turn
    assert state.pending_tool_calls == []
    assert state.provider_state.previous_response_id == "resp_1"
    assert state.token_state.consumed_tokens == 7
    assert state.token_state.input_tokens == 4
    assert state.token_state.output_tokens == 3
    assert state.latest_messages() == [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "done"},
    ]
    assert state.transcript == [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "done"},
    ]
    assert model_io.requests[0].previous_response_id == "prev_0"
    assert model_io.requests[0].run_id == "step-run"


def test_step_once_dispatches_tool_phases_when_model_returns_tool_calls():
    events = []
    tool_call = KernelToolCall(call_id="call_1", name="demo_tool", arguments={"x": 1})
    model_io = _FakeModelIO(
        ModelTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "demo_tool",
                    "arguments": "{\"x\": 1}",
                }
            ],
            tool_calls=[tool_call],
            final_text="",
            response_id="resp_tool",
        )
    )
    loop = KernelLoop(
        model_io=model_io,
        harnesses=[
            _RecordingHarness(name="after", phases=("after_model",), sink=events),
            _RecordingHarness(name="tool", phases=("on_tool_call",), sink=events),
            _RecordingHarness(name="batch", phases=("after_tool_batch",), sink=events),
        ],
    )
    state = loop.seed_state([{"role": "user", "content": "start"}], provider="openai", model="gpt-4.1")

    loop.step_once(state, toolkit=Toolkit())

    assert [phase for phase, _ in events] == ["after_model", "on_tool_call", "after_tool_batch"]
    tool_event = next(payload for phase, payload in events if phase == "on_tool_call")
    assert tool_event["tool_call"].name == "demo_tool"
    assert tool_event["tool_call_index"] == 0
    assert len(tool_event["tool_calls"]) == 1
    assert state.iteration == 1
    assert len(state.pending_tool_calls) == 1
    assert state.pending_tool_calls[0].name == "demo_tool"
