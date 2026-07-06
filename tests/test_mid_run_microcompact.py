from __future__ import annotations

import copy
import json
from typing import Any

from unchain.kernel.harness import HarnessContext
from unchain.kernel.microcompact import (
    MidRunMicrocompactConfig,
    MidRunMicrocompactHarness,
)
from unchain.kernel.state import RunState
from unchain.kernel.types import ToolCall
from unchain.tools.toolkit import Toolkit


def _call(call_id: str, name: str = "demo_tool") -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments={})


def _function_call(call: ToolCall) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call.call_id,
        "name": call.name,
        "arguments": "{}",
    }


def _function_call_output(call: ToolCall, blob: str) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": call.call_id,
        "output": json.dumps({"blob": blob}),
    }


def _state_with_two_openai_tool_turns(
    *,
    max_context_window_tokens: int,
) -> tuple[RunState, ToolCall, ToolCall, str, str]:
    old_call = _call("call_old")
    new_call = _call("call_new")
    old_output = json.dumps({"blob": "old-" * 400})
    new_output = json.dumps({"blob": "new-" * 400})
    messages = [
        {"role": "user", "content": "run two tools"},
        _function_call(old_call),
        {
            "type": "function_call_output",
            "call_id": old_call.call_id,
            "output": old_output,
        },
        _function_call(new_call),
        {
            "type": "function_call_output",
            "call_id": new_call.call_id,
            "output": new_output,
        },
    ]
    state = RunState(transcript=messages)
    state.next_model_input = [dict(message) for message in messages]
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-4.1"
    state.provider_state.max_context_window_tokens = max_context_window_tokens
    state.session_state.session_id = "session-a"
    return state, old_call, new_call, old_output, new_output


def _configure_runtime_state(state: RunState, *, max_context_window_tokens: int) -> None:
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-4.1"
    state.provider_state.max_context_window_tokens = max_context_window_tokens
    state.session_state.session_id = "session-a"


def test_mid_run_microcompact_compacts_old_tool_turn_only():
    state, old_call, new_call, _old_output, new_output = _state_with_two_openai_tool_turns(
        max_context_window_tokens=200,
    )
    harness = MidRunMicrocompactHarness(
        config=MidRunMicrocompactConfig(
            trigger_context_ratio=0.01,
            trigger_remaining_tokens=10_000,
            keep_recent_completed_turns=1,
            compact_current_batch=False,
            min_savings_chars=10,
            max_compacted_result_chars=180,
            preview_chars=24,
        )
    )
    context = HarnessContext(
        state=state,
        phase="after_tool_batch",
        event={
            "toolkit": Toolkit(),
            "tool_calls": [new_call],
        },
    )

    delta = harness.build_delta(context)
    assert delta is not None
    state.apply_delta(delta)

    transcript_old = json.loads(state.transcript[2]["output"])
    next_input_old = json.loads(state.next_model_input[2]["output"])
    assert transcript_old["compacted"] is True
    assert transcript_old["reason"] == "mid_run_microcompact"
    assert transcript_old["call_id"] == old_call.call_id
    assert next_input_old == transcript_old
    assert state.transcript[4]["output"] == new_output
    assert state.next_model_input[4]["output"] == new_output
    assert state.optimizer_state["mid_run_microcompact"]["compacted_count"] == 1


def test_mid_run_microcompact_replaces_active_message_version_without_next_input():
    state, old_call, new_call, _old_output, new_output = _state_with_two_openai_tool_turns(
        max_context_window_tokens=200,
    )
    state.seed_messages(state.transcript, created_by="test.seed")
    state.next_model_input = None
    harness = MidRunMicrocompactHarness(
        config=MidRunMicrocompactConfig(
            trigger_context_ratio=0.01,
            trigger_remaining_tokens=10_000,
            keep_recent_completed_turns=1,
            compact_current_batch=False,
            min_savings_chars=10,
            max_compacted_result_chars=180,
            preview_chars=24,
        )
    )
    context = HarnessContext(
        state=state,
        phase="after_tool_batch",
        event={
            "toolkit": Toolkit(),
            "tool_calls": [new_call],
        },
    )

    delta = harness.build_delta(context)
    assert delta is not None
    state.apply_delta(delta)

    latest_messages = state.latest_messages()
    latest_old = json.loads(latest_messages[2]["output"])
    transcript_old = json.loads(state.transcript[2]["output"])
    assert latest_old["compacted"] is True
    assert latest_old["reason"] == "mid_run_microcompact"
    assert latest_old["call_id"] == old_call.call_id
    assert latest_old == transcript_old
    assert latest_messages[4]["output"] == new_output


def test_mid_run_microcompact_keeps_all_results_in_newest_completed_turn():
    old_call = _call("call_old")
    new_call = _call("call_new")
    old_output = json.dumps({"blob": "old-" * 400})
    new_output_1 = json.dumps({"blob": "new-one-" * 300})
    new_output_2 = json.dumps({"blob": "new-two-" * 300})
    messages = [
        {"role": "user", "content": "run tools"},
        _function_call(old_call),
        {
            "type": "function_call_output",
            "call_id": old_call.call_id,
            "output": old_output,
        },
        _function_call(new_call),
        {
            "type": "function_call_output",
            "call_id": new_call.call_id,
            "output": new_output_1,
        },
        {
            "type": "function_call_output",
            "call_id": new_call.call_id,
            "output": new_output_2,
        },
    ]
    state = RunState(transcript=messages)
    state.next_model_input = [dict(message) for message in messages]
    _configure_runtime_state(state, max_context_window_tokens=200)
    harness = MidRunMicrocompactHarness(
        config=MidRunMicrocompactConfig(
            trigger_context_ratio=0.01,
            trigger_remaining_tokens=10_000,
            keep_recent_completed_turns=1,
            compact_current_batch=False,
            min_savings_chars=10,
            max_compacted_result_chars=180,
            preview_chars=24,
        )
    )
    context = HarnessContext(
        state=state,
        phase="after_tool_batch",
        event={
            "toolkit": Toolkit(),
            "tool_calls": [],
        },
    )

    delta = harness.build_delta(context)
    assert delta is not None
    state.apply_delta(delta)

    old_payload = json.loads(state.transcript[2]["output"])
    assert old_payload["compacted"] is True
    assert state.transcript[4]["output"] == new_output_1
    assert state.transcript[5]["output"] == new_output_2
    assert state.next_model_input[4]["output"] == new_output_1
    assert state.next_model_input[5]["output"] == new_output_2
    assert state.optimizer_state["mid_run_microcompact"]["compacted_count"] == 1


def test_mid_run_microcompact_protects_idless_current_gemini_result():
    old_call = _call("call_old", name="old_tool")
    current_call = _call("call_current", name="gemini_tool")
    old_response = {"blob": "old-" * 400}
    current_response = {"blob": "current-" * 400}
    messages = [
        {
            "role": "user",
            "parts": [{"text": "run gemini tools"}],
        },
        {
            "role": "model",
            "parts": [
                {
                    "function_call": {
                        "id": old_call.call_id,
                        "name": old_call.name,
                        "args": {},
                    }
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "id": old_call.call_id,
                        "name": old_call.name,
                        "response": old_response,
                    }
                }
            ],
        },
        {
            "role": "model",
            "parts": [
                {
                    "function_call": {
                        "name": current_call.name,
                        "args": {},
                    }
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "name": current_call.name,
                        "response": current_response,
                    }
                }
            ],
        },
    ]
    state = RunState(transcript=messages)
    state.next_model_input = copy.deepcopy(messages)
    state.provider_state.provider = "gemini"
    state.provider_state.model = "gemini-2.5"
    state.provider_state.max_context_window_tokens = 200
    state.session_state.session_id = "session-a"
    harness = MidRunMicrocompactHarness(
        config=MidRunMicrocompactConfig(
            trigger_context_ratio=0.01,
            trigger_remaining_tokens=10_000,
            keep_recent_completed_turns=0,
            compact_current_batch=False,
            min_savings_chars=10,
            max_compacted_result_chars=180,
            preview_chars=24,
        )
    )
    context = HarnessContext(
        state=state,
        phase="after_tool_batch",
        event={
            "toolkit": Toolkit(),
            "tool_calls": [current_call],
        },
    )

    delta = harness.build_delta(context)
    assert delta is not None
    state.apply_delta(delta)

    old_payload = state.transcript[2]["parts"][0]["function_response"]["response"]
    current_payload = state.transcript[4]["parts"][0]["function_response"]["response"]
    current_next_input_payload = state.next_model_input[4]["parts"][0]["function_response"]["response"]
    assert old_payload["compacted"] is True
    assert old_payload["reason"] == "mid_run_microcompact"
    assert current_payload == current_response
    assert current_next_input_payload == current_response
    assert state.optimizer_state["mid_run_microcompact"]["compacted_count"] == 1


def test_mid_run_microcompact_noops_when_context_pressure_is_low():
    state, _old_call, new_call, _old_output, _new_output = _state_with_two_openai_tool_turns(
        max_context_window_tokens=1_000_000,
    )
    harness = MidRunMicrocompactHarness(
        config=MidRunMicrocompactConfig(
            trigger_context_ratio=0.99,
            trigger_remaining_tokens=1,
            keep_recent_completed_turns=1,
            compact_current_batch=False,
            min_savings_chars=10_000,
            max_compacted_result_chars=180,
            preview_chars=24,
        )
    )
    context = HarnessContext(
        state=state,
        phase="after_tool_batch",
        event={
            "toolkit": Toolkit(),
            "tool_calls": [new_call],
        },
    )

    assert harness.build_delta(context) is None
