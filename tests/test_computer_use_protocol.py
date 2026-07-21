from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from unchain.kernel import ModelTurnRequest
from unchain.kernel.types import ToolCall
from unchain.providers import OpenAIModelIO
from unchain.providers.context_assembler import (
    _openai_segments,
    _rehydrate,
    _validate_tool_pairs,
)
from unchain.tools import Toolkit
from unchain.tools.messages import OllamaMessageBuilder, OpenAIMessageBuilder
from unchain.tools.tool import Tool


class _Stream:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self.events)


class _Responses:
    def __init__(self, captured, events):
        self.captured = captured
        self.events = events

    def create(self, **kwargs):
        self.captured.update(kwargs)
        return _Stream(self.events)


class _Client:
    def __init__(self, captured, events):
        self.responses = _Responses(captured, events)


def _computer_toolkit():
    def computer(actions=None, **kwargs):
        return {"ok": True}

    toolkit = Toolkit()
    toolkit.register(
        Tool.from_callable(
            computer,
            name="computer",
            provider_native_specs={"openai": {"type": "computer"}},
        )
    )
    return toolkit


def _completed_event(output):
    response = SimpleNamespace(
        id="resp_1",
        output=output,
        usage=SimpleNamespace(input_tokens=2, output_tokens=1),
    )
    return SimpleNamespace(type="response.completed", response=response)


def test_openai_computer_call_becomes_existing_tool_call_without_type_change():
    captured = {}
    output = [
        {
            "type": "computer_call",
            "call_id": "call_pc",
            "actions": [
                {"type": "click", "button": "left", "x": 10, "y": 20},
                {"type": "type", "text": "hello"},
            ],
            "status": "completed",
        }
    ]
    io = OpenAIModelIO(
        model="gpt-5.6",
        api_key="test",
        client_factory=lambda **kwargs: _Client(captured, [_completed_event(output)]),
    )
    result = io.fetch_turn(
        ModelTurnRequest(
            messages=[{"role": "user", "content": "go"}],
            toolkit=_computer_toolkit(),
            run_id="run-1",
        )
    )

    assert captured["tools"] == [{"type": "computer"}]
    assert result.tool_calls == [
        ToolCall(
            call_id="call_pc",
            name="computer",
            arguments={
                "provider": "openai",
                "protocol": "openai.responses.computer.v1",
                "actions": output[0]["actions"],
            },
        )
    ]
    assert result.assistant_messages == [
        {
            "type": "computer_call",
            "call_id": "call_pc",
            "actions": output[0]["actions"],
        }
    ]
    assert result.provider_replay_frame["items"][-1:] == output


def test_openai_computer_replay_strips_output_only_safety_metadata():
    captured = {}
    messages = [
        {
            "type": "computer_call",
            "id": "item_pc",
            "call_id": "call_pc",
            "action": None,
            "actions": [{"type": "screenshot"}],
            "pending_safety_checks": [],
            "status": "completed",
            "created_by": None,
        },
        {
            "type": "computer_call_output",
            "call_id": "call_pc",
            "output": {
                "type": "computer_screenshot",
                "image_url": "data:image/png;base64,aW1n",
                "detail": "original",
            },
        },
    ]
    io = OpenAIModelIO(
        model="gpt-5.6",
        api_key="test",
        client_factory=lambda **kwargs: _Client(
            captured,
            [_completed_event([{"type": "message", "role": "assistant", "content": []}])],
        ),
    )

    io.fetch_turn(
        ModelTurnRequest(
            messages=messages,
            toolkit=_computer_toolkit(),
            run_id="run-1",
        )
    )

    assert captured["input"][0] == {
        "type": "computer_call",
        "id": "item_pc",
        "call_id": "call_pc",
        "actions": [{"type": "screenshot"}],
    }
    assert captured["input"][1] == messages[1]


def test_openai_computer_call_with_pending_safety_checks_fails_closed():
    captured = {}
    output = [
        {
            "type": "computer_call",
            "call_id": "call_pc",
            "actions": [{"type": "click", "button": "left", "x": 10, "y": 20}],
            "pending_safety_checks": [
                {
                    "id": "safety_1",
                    "code": "malicious_instructions",
                    "message": "The page may contain untrusted instructions.",
                }
            ],
            "status": "completed",
        }
    ]
    io = OpenAIModelIO(
        model="gpt-5.6",
        api_key="test",
        client_factory=lambda **kwargs: _Client(captured, [_completed_event(output)]),
    )

    with pytest.raises(ValueError, match="require explicit user acknowledgement"):
        io.fetch_turn(
            ModelTurnRequest(
                messages=[{"role": "user", "content": "go"}],
                toolkit=_computer_toolkit(),
                run_id="run-1",
            )
        )


def test_openai_computer_replay_rehydrates_raw_checkpoint_semantics():
    first_raw_call = {
        "type": "computer_call",
        "id": "item_1",
        "call_id": "call_1",
        "action": None,
        "actions": [{"type": "screenshot"}],
        "pending_safety_checks": [],
        "status": "completed",
    }
    first_wire_call = {
        "type": "computer_call",
        "id": "item_1",
        "call_id": "call_1",
        "actions": [{"type": "screenshot"}],
    }
    first_output = {
        "type": "computer_call_output",
        "call_id": "call_1",
        "output": {
            "type": "computer_screenshot",
            "image_url": "data:image/png;base64,aW1n",
            "detail": "original",
        },
    }
    second_raw_call = {
        "type": "computer_call",
        "id": "item_2",
        "call_id": "call_2",
        "action": None,
        "actions": [{"type": "wait"}],
        "pending_safety_checks": [],
        "status": "completed",
    }
    second_output = {
        "type": "computer_call_output",
        "call_id": "call_2",
        "output": {
            "type": "computer_screenshot",
            "image_url": "data:image/png;base64,aW1nMg==",
            "detail": "original",
        },
    }
    replay_items = [
        {
            "type": "reasoning",
            "id": "reasoning_1",
            "encrypted_content": "ciphertext-1",
            "summary": [],
        },
        first_wire_call,
        first_output,
        {
            "type": "reasoning",
            "id": "reasoning_2",
            "encrypted_content": "ciphertext-2",
            "summary": [],
            "status": "completed",
        },
        second_raw_call,
        second_output,
    ]
    target = [first_raw_call, first_output, second_raw_call, second_output]

    rehydrated = _rehydrate("openai", target, _openai_segments(replay_items))

    assert [item["type"] for item in rehydrated] == [
        "reasoning",
        "computer_call",
        "computer_call_output",
        "reasoning",
        "computer_call",
        "computer_call_output",
    ]

    mutated_target = copy.deepcopy(target)
    mutated_target[2]["actions"] = [
        {"type": "click", "button": "left", "x": 1, "y": 2}
    ]
    with pytest.raises(ValueError, match="mutated ambiguously"):
        _rehydrate("openai", mutated_target, _openai_segments(replay_items))


def test_openai_computer_result_uses_native_screenshot_output():
    message = OpenAIMessageBuilder().build_tool_result_message(
        tool_call=ToolCall("call_pc", "computer", {}),
        tool_result={
            "content_blocks": [
                {"type": "text", "text": "done"},
                {"type": "image", "media_type": "image/png", "data_b64": "aW1n"},
            ]
        },
    )
    assert message == {
        "type": "computer_call_output",
        "call_id": "call_pc",
        "output": {
            "type": "computer_screenshot",
            "image_url": "data:image/png;base64,aW1n",
            "detail": "original",
        },
    }


def test_openai_computer_failure_without_screenshot_terminates_turn():
    with pytest.raises(ValueError, match="computer_turn_terminated"):
        OpenAIMessageBuilder().build_tool_result_message(
            tool_call=ToolCall("call_pc", "computer", {}),
            tool_result={"ok": False, "error": "denied"},
        )


def test_ollama_computer_result_is_tool_message_plus_user_image():
    messages = OllamaMessageBuilder().build_tool_result_messages(
        tool_call=ToolCall("call_pc", "computer", {}),
        tool_result={
            "content_blocks": [
                {"type": "text", "text": "done"},
                {"type": "image", "media_type": "image/png", "data_b64": "aW1n"},
            ]
        },
    )
    assert messages[0]["role"] == "tool"
    assert messages[1] == {
        "role": "user",
        "content": "Current computer screenshot after the action batch.",
        "images": ["aW1n"],
    }


def test_openai_replay_pairs_computer_call_and_output():
    messages = [
        {
            "type": "computer_call",
            "call_id": "call_pc",
            "actions": [{"type": "screenshot"}],
        },
        {
            "type": "computer_call_output",
            "call_id": "call_pc",
            "output": {
                "type": "computer_screenshot",
                "image_url": "data:image/png;base64,aW1n",
                "detail": "original",
            },
        },
    ]
    segments = _openai_segments(messages)
    assert [segment.semantic["type"] for segment in segments] == [
        "computer_call",
        "computer_call_output",
    ]
    _validate_tool_pairs("openai", messages)
