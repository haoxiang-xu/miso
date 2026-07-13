from __future__ import annotations

import json

import pytest

from unchain.input import ASK_USER_QUESTION_TOOL_NAME
from unchain.kernel import BaseRuntimeHarness, HarnessDelta, ModelTurnResult
from unchain.kernel.types import ToolCall
from unchain.memory import (
    ExecutionCheckpointPersistenceError,
    InMemorySessionStore,
    KernelMemoryRuntime,
)
from unchain.runtime import build_runtime_loop
from unchain.tools import Toolkit


class _QueueModelIO:
    provider = "openai"
    model = "gpt-5"

    def __init__(self, turns: list[ModelTurnResult]):
        self.turns = list(turns)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("unexpected model turn")
        return self.turns.pop(0)


def _ask_turn(call_id: str = "call-user") -> ModelTurnResult:
    arguments = {
        "title": "Choose stack",
        "question": "Which stack?",
        "selection_mode": "single",
        "options": [
            {"label": "React", "value": "react"},
            {"label": "Vue", "value": "vue"},
        ],
    }
    return ModelTurnResult(
        assistant_messages=[
            {
                "type": "function_call",
                "call_id": call_id,
                "name": ASK_USER_QUESTION_TOOL_NAME,
                "arguments": json.dumps(arguments),
            }
        ],
        tool_calls=[
            ToolCall(
                call_id=call_id,
                name=ASK_USER_QUESTION_TOOL_NAME,
                arguments=arguments,
            )
        ],
        response_id="resp-ask",
    )


def _tool_turn(call_id: str = "call-tool") -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[
            {
                "type": "function_call",
                "call_id": call_id,
                "name": "demo_tool",
                "arguments": "{}",
            }
        ],
        tool_calls=[ToolCall(call_id=call_id, name="demo_tool", arguments={})],
        response_id="resp-tool",
    )


def _final_turn(text: str = "done") -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": text}],
        tool_calls=[],
        final_text=text,
        response_id="resp-final",
    )


def _human_input_toolkit() -> Toolkit:
    toolkit = Toolkit()
    toolkit.register(
        lambda **_: {"error": "reserved"},
        name=ASK_USER_QUESTION_TOOL_NAME,
        parameters=[],
    )
    return toolkit


class _PhaseRecorder(BaseRuntimeHarness):
    def __init__(self, records: list[tuple[str, int, str, str]]):
        super().__init__(
            name="safe_wait_phase_recorder",
            phases=("on_suspend", "on_resume"),
            order=200,
        )
        self.records = records

    def build_delta(self, context):
        self.records.append(
            (
                context.phase,
                id(context.state),
                str(context.event.get("status") or ""),
                context.state.run_status,
            )
        )
        return None


class _LateSuspendContributor(BaseRuntimeHarness):
    def __init__(self):
        super().__init__(
            name="late_suspend_contributor",
            phases=("on_suspend",),
            order=10_000,
        )

    def build_delta(self, context):
        return HarnessDelta(
            created_by=self.name,
            state_updates={
                "workspace_change_state": {
                    "safe_point_status": str(context.event.get("status") or ""),
                }
            },
        )


def test_human_input_callback_runs_only_after_checkpoint_then_resumes_same_state():
    session_id = "safe-human-input"
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    records: list[tuple[str, int, str, str]] = []
    events: list[dict] = []
    model_io = _QueueModelIO([_ask_turn(), _final_turn("React selected")])
    loop = build_runtime_loop(
        model_io=model_io,
        memory_runtime=runtime,
        harnesses=[_PhaseRecorder(records), _LateSuspendContributor()],
    )

    def provide_input(request):
        checkpoint = store.load(session_id)["execution_checkpoint"]
        assert checkpoint["status"] == "awaiting_human_input"
        assert checkpoint["continuation"]["call_id"] == request.request_id
        assert checkpoint["workspace_change_state"] == {
            "safe_point_status": "awaiting_human_input"
        }
        assert records == [
            ("on_suspend", records[0][1], "awaiting_human_input", "awaiting_human_input")
        ]
        return {
            "request_id": request.request_id,
            "selected_values": ["react"],
        }

    result = loop.run(
        [{"role": "user", "content": "pick one"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=_human_input_toolkit(),
        max_iterations=3,
        on_human_input=provide_input,
        callback=events.append,
    )

    assert result.status == "completed"
    assert [record[0] for record in records] == ["on_suspend", "on_resume"]
    assert records[0][1] == records[1][1]
    assert records[1][3] == "running"
    assert "execution_checkpoint" not in store.load(session_id)
    assert len(model_io.requests) == 2
    assert sum(event["type"] == "human_input_requested" for event in events) == 1
    tool_result_events = [event for event in events if event["type"] == "tool_result"]
    assert len(tool_result_events) == 1
    assert tool_result_events[0]["call_id"] == "call-user"
    assert tool_result_events[0]["result"]["selected_values"] == ["react"]


def test_human_input_checkpoint_failure_prevents_blocking_callback():
    class _DroppingStore:
        def load(self, session_id):
            del session_id
            return {}

        def save(self, session_id, state):
            del session_id, state

    callbacks: list[str] = []
    model_io = _QueueModelIO([_ask_turn()])
    loop = build_runtime_loop(
        model_io=model_io,
        memory_runtime=KernelMemoryRuntime.from_config(store=_DroppingStore()),
    )

    with pytest.raises(ExecutionCheckpointPersistenceError, match="verification failed"):
        loop.run(
            [{"role": "user", "content": "pick one"}],
            session_id="dropped-human-input",
            provider="openai",
            model="gpt-5",
            toolkit=_human_input_toolkit(),
            on_human_input=lambda request: callbacks.append(request.request_id),
        )

    assert callbacks == []
    assert len(model_io.requests) == 1


def test_human_input_callback_exception_leaves_durable_suspension_for_retry():
    session_id = "failed-human-input-callback"
    store = InMemorySessionStore()
    records: list[tuple[str, int, str, str]] = []
    model_io = _QueueModelIO([_ask_turn()])
    loop = build_runtime_loop(
        model_io=model_io,
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
        harnesses=[_PhaseRecorder(records)],
    )

    def fail_callback(_request):
        raise RuntimeError("input UI unavailable")

    with pytest.raises(RuntimeError, match="input UI unavailable"):
        loop.run(
            [{"role": "user", "content": "pick one"}],
            session_id=session_id,
            provider="openai",
            model="gpt-5",
            toolkit=_human_input_toolkit(),
            on_human_input=fail_callback,
        )

    checkpoint = store.load(session_id)["execution_checkpoint"]
    assert checkpoint["status"] == "awaiting_human_input"
    assert checkpoint["continuation"]["call_id"] == "call-user"
    assert [record[0] for record in records] == ["on_suspend"]
    assert records[0][3] == "awaiting_human_input"
    assert len(model_io.requests) == 1


def test_human_input_event_exception_happens_after_durable_suspension():
    session_id = "failed-human-input-event-callback"
    store = InMemorySessionStore()
    loop = build_runtime_loop(
        model_io=_QueueModelIO([_ask_turn()]),
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )

    def fail_event_callback(event):
        if event["type"] != "human_input_requested":
            return
        assert store.load(session_id)["execution_checkpoint"]["status"] == (
            "awaiting_human_input"
        )
        raise RuntimeError("input event sink unavailable")

    with pytest.raises(RuntimeError, match="input event sink unavailable"):
        loop.run(
            [{"role": "user", "content": "pick one"}],
            session_id=session_id,
            provider="openai",
            model="gpt-5",
            toolkit=_human_input_toolkit(),
            callback=fail_event_callback,
        )

    checkpoint = store.load(session_id)["execution_checkpoint"]
    assert checkpoint["status"] == "awaiting_human_input"
    assert checkpoint["continuation"]["call_id"] == "call-user"


def test_public_human_input_resume_emits_one_tool_result_event():
    loop = build_runtime_loop(model_io=_QueueModelIO([_ask_turn(), _final_turn()]))
    toolkit = _human_input_toolkit()
    suspended = loop.run(
        [{"role": "user", "content": "pick one"}],
        provider="openai",
        model="gpt-5",
        toolkit=toolkit,
    )
    events: list[dict] = []

    resumed = loop.resume_human_input(
        conversation=suspended.messages,
        continuation=suspended.continuation,
        response={"request_id": "call-user", "selected_values": ["react"]},
        toolkit=toolkit,
        callback=events.append,
    )

    assert resumed.status == "completed"
    tool_result_events = [event for event in events if event["type"] == "tool_result"]
    assert len(tool_result_events) == 1
    assert tool_result_events[0]["iteration"] == 0
    assert tool_result_events[0]["call_id"] == "call-user"


def test_max_iterations_callback_waits_for_on_suspend_safe_point_without_memory():
    order: list[str] = []
    safe_point: list[tuple[str, str]] = []

    class _MaxWaitRecorder(BaseRuntimeHarness):
        def __init__(self):
            super().__init__(name="max_wait_recorder", phases=("on_suspend",), order=100)

        def build_delta(self, context):
            order.append("safe_point")
            safe_point.append((str(context.event.get("status")), context.state.run_status))
            return None

    model_io = _QueueModelIO([_tool_turn(), _final_turn()])
    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="demo_tool")
    loop = build_runtime_loop(model_io=model_io, harnesses=[_MaxWaitRecorder()])

    def on_event(event):
        if event["type"] == "run_max_iterations":
            order.append("event")

    def approve(_payload):
        order.append("callback")
        assert safe_point == [("max_iterations", "max_iterations")]
        return {"approved": True, "extra_iterations": 1}

    result = loop.run(
        [{"role": "user", "content": "use the tool"}],
        provider="openai",
        model="gpt-5",
        toolkit=toolkit,
        max_iterations=1,
        on_max_iterations=approve,
        callback=on_event,
    )

    assert result.status == "completed"
    assert order == ["safe_point", "event", "callback"]
    assert len(model_io.requests) == 2


def test_max_iterations_callback_observes_durable_checkpoint_before_wait():
    session_id = "safe-max-iterations"
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    model_io = _QueueModelIO([_tool_turn(), _final_turn()])
    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="demo_tool")
    loop = build_runtime_loop(model_io=model_io, memory_runtime=runtime)

    def approve(_payload):
        snapshot = store.load_with_revision(session_id)
        checkpoint = snapshot.state["execution_checkpoint"]
        assert checkpoint["status"] == "max_iterations"
        assert checkpoint["base_session_revision"] == 0
        assert snapshot.revision == 1
        return {"approved": True, "extra_iterations": 1}

    result = loop.run(
        [{"role": "user", "content": "use the tool"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=toolkit,
        max_iterations=1,
        on_max_iterations=approve,
    )

    assert result.status == "completed"
    final_snapshot = store.load_with_revision(session_id)
    assert "execution_checkpoint" not in final_snapshot.state
    assert final_snapshot.revision == 2


def test_max_iterations_event_exception_happens_after_durable_checkpoint():
    session_id = "failed-max-event-callback"
    store = InMemorySessionStore()
    callbacks: list[dict] = []
    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="demo_tool")
    loop = build_runtime_loop(
        model_io=_QueueModelIO([_tool_turn()]),
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )

    def fail_event_callback(event):
        if event["type"] != "run_max_iterations":
            return
        assert store.load(session_id)["execution_checkpoint"]["status"] == (
            "max_iterations"
        )
        raise RuntimeError("max event sink unavailable")

    with pytest.raises(RuntimeError, match="max event sink unavailable"):
        loop.run(
            [{"role": "user", "content": "use the tool"}],
            session_id=session_id,
            provider="openai",
            model="gpt-5",
            toolkit=toolkit,
            max_iterations=1,
            on_max_iterations=lambda payload: callbacks.append(payload),
            callback=fail_event_callback,
        )

    checkpoint = store.load(session_id)["execution_checkpoint"]
    assert checkpoint["status"] == "max_iterations"
    assert callbacks == []


def test_max_iterations_safe_point_failure_prevents_blocking_callback():
    callbacks: list[dict] = []

    class _FailingSafePoint(BaseRuntimeHarness):
        def __init__(self):
            super().__init__(name="failing_safe_point", phases=("on_suspend",), order=100)

        def build_delta(self, context):
            assert context.event.get("status") == "max_iterations"
            raise RuntimeError("safe point failed")

    model_io = _QueueModelIO([_tool_turn()])
    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="demo_tool")
    loop = build_runtime_loop(model_io=model_io, harnesses=[_FailingSafePoint()])

    with pytest.raises(RuntimeError, match="safe point failed"):
        loop.run(
            [{"role": "user", "content": "use the tool"}],
            provider="openai",
            model="gpt-5",
            toolkit=toolkit,
            max_iterations=1,
            on_max_iterations=lambda payload: callbacks.append(payload),
        )

    assert callbacks == []
    assert len(model_io.requests) == 1
