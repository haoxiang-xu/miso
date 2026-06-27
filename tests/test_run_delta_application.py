from __future__ import annotations

import json

from unchain.capabilities import (
    CapabilityOutcome,
    ContextTarget,
    EmitEventOp,
    InsertMessagesOp,
    RequestSuspendOp,
    RunDelta,
    SetRuntimeStateOp,
)
from unchain.kernel import BaseRuntimeHarness, KernelLoop, ModelTurnResult, ToolCall
from unchain.tools import Toolkit


class _QueueModelIO:
    provider = "openai"

    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if not self.results:
            raise AssertionError("unexpected fetch_turn call")
        return self.results.pop(0)


def test_runtime_hook_structured_delta_updates_model_context_state_events_and_suspend():
    events = []

    class StructuredHarness(BaseRuntimeHarness):
        def build_delta(self, context):
            return CapabilityOutcome(
                delta=RunDelta(
                    created_by="harness.structured",
                    context_ops=(
                        InsertMessagesOp(
                            target=ContextTarget.MODEL_CONTEXT,
                            index=0,
                            messages=[{"role": "system", "content": "hook memory"}],
                            reason="test_hook_context",
                        ),
                        SetRuntimeStateOp(
                            path=("tool_exposure", "active"),
                            value=["read_file"],
                            reason="test_hook_state",
                        ),
                        EmitEventOp(
                            type="hook_delta_event",
                            payload={"source": "hook"},
                            reason="test_hook_event",
                        ),
                        RequestSuspendOp(
                            kind="checkpoint",
                            payload={"source": "hook"},
                            reason="test_hook_suspend",
                        ),
                    ),
                ),
            )

    loop = KernelLoop(harnesses=[StructuredHarness(name="structured", phases=("before_model",))])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    loop.dispatch_phase(
        state,
        phase="before_model",
        event={"callback": events.append, "run_id": "run-1"},
    )

    assert state.latest_messages() == [
        {"role": "system", "content": "hook memory"},
        {"role": "user", "content": "start"},
    ]
    assert state.transcript == [{"role": "user", "content": "start"}]
    assert state.component_state["tool_exposure"]["active"] == ["read_file"]
    assert state.suspend_state.signal_kind == "checkpoint"
    assert state.suspend_state.payload == {"source": "hook"}
    assert events == [
        {
            "type": "hook_delta_event",
            "run_id": "run-1",
            "iteration": 0,
            "source": "hook",
        }
    ]


def test_tool_structured_delta_uses_same_application_layer_for_next_model_context():
    events = []
    model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "delta_tool",
                        "arguments": "{}",
                    }
                ],
                tool_calls=[ToolCall(call_id="call-1", name="delta_tool", arguments={})],
                response_id="resp-1",
            ),
            ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
                response_id="resp-2",
            ),
        ]
    )
    toolkit = Toolkit()

    @toolkit.tool(name="delta_tool")
    def delta_tool() -> CapabilityOutcome:
        return CapabilityOutcome(
            value={"ok": True},
            delta=RunDelta(
                created_by="tool.delta_tool",
                context_ops=(
                    InsertMessagesOp(
                        target=ContextTarget.MODEL_CONTEXT,
                        index=0,
                        messages=[{"role": "system", "content": "tool memory"}],
                        reason="test_tool_context",
                    ),
                    SetRuntimeStateOp(
                        path=("tool_exposure", "active"),
                        value=["delta_tool"],
                        reason="test_tool_state",
                    ),
                    EmitEventOp(
                        type="tool_delta_event",
                        payload={"source": "tool"},
                        reason="test_tool_event",
                    ),
                ),
            ),
        )

    result = KernelLoop(model_io=model_io).run(
        [{"role": "user", "content": "start"}],
        provider="openai",
        model="gpt-5",
        payload={"store": False},
        toolkit=toolkit,
        callback=events.append,
        max_iterations=3,
        run_id="run-1",
    )

    second_request_messages = model_io.requests[1].messages
    tool_message = next(
        message
        for message in result.messages
        if message.get("type") == "function_call_output"
    )

    assert result.status == "completed"
    assert {"role": "system", "content": "tool memory"} in second_request_messages
    assert {"role": "system", "content": "tool memory"} not in result.messages
    assert json.loads(tool_message["output"]) == {"ok": True}
    assert any(event["type"] == "tool_delta_event" and event["source"] == "tool" for event in events)
