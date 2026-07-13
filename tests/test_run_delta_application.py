from __future__ import annotations

import copy
import json

from unchain.capabilities import (
    CapabilityOutcome,
    ContextTarget,
    CreateArtifactOp,
    DeleteMessagesOp,
    EmitEventOp,
    InsertMessagesOp,
    PatchMessageOp,
    RequestSuspendOp,
    RunDelta,
    SetRuntimeStateOp,
)
from unchain.kernel import BaseRuntimeHarness, KernelLoop, ModelTurnResult, ToolCall
from unchain.interaction.fyi import FyiChannel, FyiInjectionHarness
from unchain.runtime import build_runtime_loop
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
    assert state.suspend_state.payload == {
        "source": "hook",
        "context_version_id": state.latest_version_id,
    }
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

    result = build_runtime_loop(model_io=model_io).run(
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


def test_remote_tool_context_switches_to_complete_local_replay_instead_of_losing_delta():
    fyi_channel = FyiChannel()

    class ReplayModelIO:
        provider = "openai"

        def __init__(self):
            self.requests = []

        def fetch_turn(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                fyi_channel.post("keep the tool memory too")
                raw_call = {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-1",
                    "name": "delta_tool",
                    "arguments": "{}",
                    "status": "completed",
                }
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "delta_tool",
                            "arguments": "{}",
                        }
                    ],
                    tool_calls=[
                        ToolCall(
                            call_id="call-1",
                            name="delta_tool",
                            arguments={},
                        )
                    ],
                    response_id="resp-1",
                    provider_replay_frame={
                        "format": "openai.responses.v1",
                        "complete": True,
                        "items": [
                            *copy.deepcopy(request.messages),
                            {
                                "type": "reasoning",
                                "id": "rs-1",
                                "encrypted_content": "opaque-ciphertext",
                                "summary": [],
                            },
                            raw_call,
                        ],
                    },
                )
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
                response_id="resp-2",
            )

    model_io = ReplayModelIO()
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
                        reason="test_remote_tool_context",
                    ),
                ),
            ),
        )

    result = build_runtime_loop(
        model_io=model_io,
        harnesses=[FyiInjectionHarness(channel=fyi_channel)],
    ).run(
        [{"role": "user", "content": "start"}],
        provider="openai",
        model="gpt-5",
        toolkit=toolkit,
        max_iterations=3,
    )

    assert result.status == "completed"
    second_request = model_io.requests[1]
    assert second_request.previous_response_id is None
    assert {"role": "system", "content": "tool memory"} in second_request.messages
    assert sum(
        "keep the tool memory too" in str(item.get("content", ""))
        for item in second_request.messages
    ) == 1
    assert any(
        item.get("type") == "reasoning"
        and item.get("encrypted_content") == "opaque-ciphertext"
        for item in second_request.messages
    )
    assert any(
        item.get("type") == "function_call_output"
        and item.get("call_id") == "call-1"
        for item in second_request.messages
    )


def test_patch_and_delete_messages_update_model_context_without_touching_transcript():
    class StructuredHarness(BaseRuntimeHarness):
        def build_delta(self, context):
            return CapabilityOutcome(
                delta=RunDelta(
                    created_by="harness.context_editor",
                    context_ops=(
                        PatchMessageOp(
                            target=ContextTarget.MODEL_CONTEXT,
                            selector={"role": "system"},
                            patch={"content": "patched system"},
                            reason="test_patch_model_context",
                        ),
                        DeleteMessagesOp(
                            target=ContextTarget.MODEL_CONTEXT,
                            selector={"role": "assistant"},
                            reason="test_delete_model_context",
                        ),
                    ),
                ),
            )

    loop = KernelLoop(harnesses=[StructuredHarness(name="structured", phases=("before_model",))])
    state = loop.seed_state(
        [
            {"role": "system", "content": "original system"},
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": "old draft"},
        ]
    )

    loop.dispatch_phase(state, phase="before_model", event={"run_id": "run-1"})

    assert state.latest_messages() == [
        {"role": "system", "content": "patched system"},
        {"role": "user", "content": "start"},
    ]
    assert state.next_model_input == [
        {"role": "system", "content": "patched system"},
        {"role": "user", "content": "start"},
    ]
    assert state.transcript == [
        {"role": "system", "content": "original system"},
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "old draft"},
    ]


def test_patch_and_delete_messages_update_conversation_transcript():
    class StructuredHarness(BaseRuntimeHarness):
        def build_delta(self, context):
            return CapabilityOutcome(
                delta=RunDelta(
                    created_by="harness.conversation_editor",
                    context_ops=(
                        PatchMessageOp(
                            target=ContextTarget.CONVERSATION,
                            selector=0,
                            patch={"content": "patched user"},
                            reason="test_patch_conversation",
                        ),
                        DeleteMessagesOp(
                            target=ContextTarget.CONVERSATION,
                            selector={"role": "assistant"},
                            reason="test_delete_conversation",
                        ),
                    ),
                ),
            )

    loop = KernelLoop(harnesses=[StructuredHarness(name="structured", phases=("before_model",))])
    state = loop.seed_state(
        [
            {"role": "user", "content": "original user"},
            {"role": "assistant", "content": "old reply"},
        ]
    )

    loop.dispatch_phase(state, phase="before_model", event={"run_id": "run-1"})

    assert state.transcript == [{"role": "user", "content": "patched user"}]
    assert state.latest_messages() == [{"role": "user", "content": "patched user"}]
    assert state.next_model_input is None


def test_create_artifact_op_upserts_and_emits_events_from_hook():
    events = []
    calls = {"count": 0}

    class ArtifactHarness(BaseRuntimeHarness):
        def build_delta(self, context):
            calls["count"] += 1
            revision = calls["count"]
            return CapabilityOutcome(
                delta=RunDelta(
                    created_by="harness.artifact_writer",
                    context_ops=(
                        CreateArtifactOp(
                            artifact={
                                "schema_version": "unchain.artifact.v1",
                                "artifact_id": "stable-report",
                                "kind": "markdown",
                                "title": "Report",
                                "revision": revision,
                                "snapshot": {"markdown": f"revision {revision}"},
                            },
                            reason="test_artifact_upsert",
                        ),
                    ),
                ),
            )

    loop = KernelLoop(harnesses=[ArtifactHarness(name="artifact", phases=("before_model",))])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    loop.dispatch_phase(
        state,
        phase="before_model",
        event={"callback": events.append, "run_id": "run-1"},
    )
    loop.dispatch_phase(
        state,
        phase="before_model",
        event={"callback": events.append, "run_id": "run-1"},
    )

    artifact_events = [event for event in events if event["type"].startswith("artifact_")]
    assert [event["type"] for event in artifact_events] == ["artifact_created", "artifact_updated"]
    assert artifact_events[0]["artifact_id"] == "stable-report"
    assert artifact_events[0]["created_by"] == "harness.artifact_writer"
    assert artifact_events[0]["reason"] == "test_artifact_upsert"
    assert state.artifacts == [artifact_events[1]["artifact"]]
    assert state.artifacts[0]["revision"] == 2


def test_tool_create_artifact_op_uses_delta_application_for_artifact_events():
    events = []
    model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "delta_artifact_tool",
                        "arguments": "{}",
                    }
                ],
                tool_calls=[ToolCall(call_id="call-1", name="delta_artifact_tool", arguments={})],
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

    @toolkit.tool(name="delta_artifact_tool")
    def delta_artifact_tool() -> CapabilityOutcome:
        return CapabilityOutcome(
            value={"ok": True},
            delta=RunDelta(
                created_by="tool.delta_artifact_tool",
                context_ops=(
                    CreateArtifactOp(
                        artifact={
                            "schema_version": "unchain.artifact.v1",
                            "artifact_id": "tool-report",
                            "kind": "markdown",
                            "title": "Tool Report",
                            "revision": 1,
                            "snapshot": {"markdown": "from tool"},
                        },
                        reason="test_tool_artifact",
                    ),
                ),
            ),
        )

    result = build_runtime_loop(model_io=model_io).run(
        [{"role": "user", "content": "start"}],
        provider="openai",
        model="gpt-5",
        payload={"store": False},
        toolkit=toolkit,
        callback=events.append,
        max_iterations=3,
        run_id="run-1",
    )

    artifact_event = next(event for event in events if event["type"] == "artifact_created")

    assert result.status == "completed"
    assert artifact_event["artifact_id"] == "tool-report"
    assert artifact_event["tool_name"] == "delta_artifact_tool"
    assert artifact_event["call_id"] == "call-1"
    assert artifact_event["created_by"] == "tool.delta_artifact_tool"
    assert artifact_event["reason"] == "test_tool_artifact"
