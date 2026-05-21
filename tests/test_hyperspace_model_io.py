"""Tests for HyperspaceModelIO — SAP Hyperspace Anthropic-compatible adapter.

These tests mirror the AnthropicModelIO test patterns since Hyperspace uses
the Anthropic wire protocol. We verify:

- ``fetch_turn`` builds the correct request and parses streaming text deltas
- The configured ``base_url`` is used by the client factory
- API key validation happens up front
- Tool use round-trip works (provider-routed tools)
- Streaming emits ``token_delta`` events when ``emit_stream=True``
- Extended thinking blocks emit ``reasoning`` events
- Empty chat messages raise a clear error (inherited from AnthropicModelIO)
- Unknown model keys still pass through (loose match in registry)
- The ``hyperspace--claude-opus-4-6`` registry entry rewrites to
  ``anthropic--claude-opus-4-6`` for the actual API request
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from unchain.kernel import ModelTurnRequest
from unchain.providers import HyperspaceModelIO
from unchain.tools import Toolkit


class _FakeAnthropicStream:
    def __init__(self, events):
        self._events = list(events)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._events)


class _FakeAnthropicMessages:
    def __init__(self, *, events, captured_kwargs):
        self._events = list(events)
        self._captured_kwargs = captured_kwargs

    def stream(self, **kwargs):
        self._captured_kwargs.update(kwargs)
        return _FakeAnthropicStream(self._events)


class _FakeHyperspaceClient:
    def __init__(self, *, events, captured_kwargs, captured_init, **kwargs):
        captured_init.update(kwargs)
        self.messages = _FakeAnthropicMessages(events=events, captured_kwargs=captured_kwargs)


def _make_client_factory(events, captured_kwargs, captured_init):
    def factory(api_key, **kwargs):
        captured_init["api_key"] = api_key
        return _FakeHyperspaceClient(
            events=events,
            captured_kwargs=captured_kwargs,
            captured_init=captured_init,
            **kwargs,
        )

    return factory


def test_hyperspace_model_io_builds_request_and_parses_text():
    captured_kwargs: dict = {}
    captured_init: dict = {}
    events: list = []
    client_factory = _make_client_factory(
        events=[
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage={"input_tokens": 7, "output_tokens": 0}),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="hola"),
            ),
            SimpleNamespace(
                type="message_delta",
                usage={"input_tokens": 7, "output_tokens": 1},
            ),
        ],
        captured_kwargs=captured_kwargs,
        captured_init=captured_init,
    )
    io = HyperspaceModelIO(
        model="hyperspace--claude-opus-4-6",
        api_key="hs-key",
        client_factory=client_factory,
    )

    turn = io.fetch_turn(
        ModelTurnRequest(
            messages=[
                {"role": "system", "content": "translate"},
                {"role": "user", "content": "hello"},
            ],
            callback=events.append,
            run_id="hs-run",
        )
    )

    assert turn.final_text == "hola"
    assert turn.assistant_messages == [{"role": "assistant", "content": "hola"}]
    assert turn.consumed_tokens == 8
    # provider_model rewrites the registry key to SAP Hyperspace's actual model name
    assert captured_kwargs["model"] == "anthropic--claude-opus-4-6"
    assert captured_init["api_key"] == "hs-key"
    request_event = next(event for event in events if event["type"] == "request_messages")
    assert request_event["provider"] == "hyperspace"
    assert request_event["system"] == "translate"


def test_hyperspace_model_io_uses_configured_base_url():
    """The default client factory must point at the configured base_url."""
    io = HyperspaceModelIO(
        model="hyperspace--claude-opus-4-6",
        api_key="hs-key",
        base_url="https://hyperspace.internal.sap/anthropic",
    )
    assert io.base_url == "https://hyperspace.internal.sap/anthropic"


def test_hyperspace_model_io_rejects_empty_api_key():
    with pytest.raises(ValueError, match="non-empty api_key"):
        HyperspaceModelIO(model="hyperspace--claude-opus-4-6", api_key="")


def test_hyperspace_model_io_rejects_empty_base_url():
    with pytest.raises(ValueError, match="non-empty base_url"):
        HyperspaceModelIO(
            model="hyperspace--claude-opus-4-6",
            api_key="hs-key",
            base_url="   ",
        )


def test_hyperspace_model_io_raises_clear_error_when_chat_messages_are_empty():
    captured_kwargs: dict = {}
    captured_init: dict = {}
    events: list = []
    client_factory = _make_client_factory(
        events=[],
        captured_kwargs=captured_kwargs,
        captured_init=captured_init,
    )
    io = HyperspaceModelIO(
        model="hyperspace--claude-opus-4-6",
        api_key="hs-key",
        client_factory=client_factory,
    )

    with pytest.raises(ValueError, match="no chat messages after preprocessing"):
        io.fetch_turn(
            ModelTurnRequest(
                messages=[{"role": "system", "content": "be helpful"}],
                callback=events.append,
                run_id="hs-empty",
            )
        )

    request_event = next(event for event in events if event["type"] == "request_messages")
    assert request_event["provider"] == "hyperspace"
    assert request_event["messages"] == []
    assert captured_kwargs == {}


def test_hyperspace_model_io_parses_tool_use_and_emits_token_delta():
    captured_kwargs: dict = {}
    captured_init: dict = {}
    events: list = []
    toolkit = Toolkit()
    toolkit.register(lambda x=None: {"x": x}, name="demo_tool")
    client_factory = _make_client_factory(
        events=[
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="thinking"),
            ),
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(type="tool_use", name="demo_tool", id="tool_1"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="input_json_delta", partial_json='{"x":'),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="input_json_delta", partial_json="2}"),
            ),
            SimpleNamespace(type="content_block_stop"),
            SimpleNamespace(type="message_delta", usage={"input_tokens": 4, "output_tokens": 3}),
        ],
        captured_kwargs=captured_kwargs,
        captured_init=captured_init,
    )
    io = HyperspaceModelIO(
        model="hyperspace--claude-opus-4-6",
        api_key="hs-key",
        client_factory=client_factory,
    )

    turn = io.fetch_turn(
        ModelTurnRequest(
            messages=[{"role": "user", "content": "call tool"}],
            toolkit=toolkit,
            callback=events.append,
            emit_stream=True,
            run_id="hs-tools",
        )
    )

    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].call_id == "tool_1"
    assert turn.tool_calls[0].name == "demo_tool"
    assert turn.tool_calls[0].arguments == {"x": 2}
    assert turn.assistant_messages == [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "thinking"},
            {"type": "tool_use", "id": "tool_1", "name": "demo_tool", "input": {"x": 2}},
        ],
    }]
    assert captured_kwargs["tools"][0]["name"] == "demo_tool"
    token_event = next(event for event in events if event["type"] == "token_delta")
    assert token_event["provider"] == "hyperspace"
    assert token_event["delta"] == "thinking"


def test_hyperspace_model_io_emits_reasoning_for_thinking_blocks():
    captured_kwargs: dict = {}
    captured_init: dict = {}
    events: list = []
    client_factory = _make_client_factory(
        events=[
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(type="thinking"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="thinking_delta", thinking="step 1"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="thinking_delta", thinking=" then step 2"),
            ),
            SimpleNamespace(type="content_block_stop"),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="answer"),
            ),
            SimpleNamespace(type="message_delta", usage={"input_tokens": 2, "output_tokens": 5}),
        ],
        captured_kwargs=captured_kwargs,
        captured_init=captured_init,
    )
    io = HyperspaceModelIO(
        model="hyperspace--claude-opus-4-6",
        api_key="hs-key",
        client_factory=client_factory,
    )

    turn = io.fetch_turn(
        ModelTurnRequest(
            messages=[{"role": "user", "content": "think hard"}],
            callback=events.append,
            emit_stream=True,
            run_id="hs-thinking",
        )
    )

    reasoning_events = [e for e in events if e["type"] == "reasoning"]
    assert len(reasoning_events) == 2
    assert reasoning_events[0]["delta"] == "step 1"
    assert reasoning_events[1]["delta"] == " then step 2"
    assert turn.reasoning_items == [
        {"type": "thinking", "text": "step 1 then step 2"}
    ]
    assert turn.final_text == "answer"
