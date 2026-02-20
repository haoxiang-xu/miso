import os
import json
import importlib
import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from miso import broth as Broth, toolkit
from miso.broth import ProviderTurnResult, ToolCall


def _last_assistant_text(messages):
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, list):
                # Anthropic-style content blocks
                parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                return "".join(parts).strip()
            return (content or "").strip()
    return ""


# ── live smoke test (skipped unless env vars set) ───────────────────────────

def test_anthropic_smoke():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("ANTHROPIC_MODEL")
    if not api_key or not model:
        pytest.skip("ANTHROPIC_API_KEY or ANTHROPIC_MODEL not set")

    a = Broth()
    a.provider = "anthropic"
    a.api_key = api_key
    a.model = model

    messages = [{"role": "user", "content": "Reply with OK only."}]
    messages_out, bundle = a.run(
        messages=messages,
        payload={"max_tokens": 32},
        max_iterations=1,
    )

    assert _last_assistant_text(messages_out) != ""
    assert isinstance(bundle.get("consumed_tokens"), int)


# ── unit test: _anthropic_fetch_once forces stream & returns text ───────────

def test_anthropic_fetch_once_forces_stream_true(monkeypatch):
    a = Broth()
    a.provider = "anthropic"
    a.api_key = "sk-test-fake"
    a.model = "claude-sonnet-4"

    captured_kwargs = {}

    # ── lightweight fakes for anthropic streaming ──

    class FakeContentBlockStart:
        type = "content_block_start"

        class content_block:
            type = "text"

    class FakeContentBlockDelta:
        type = "content_block_delta"

        class delta:
            type = "text_delta"
            text = "hello"

    class FakeContentBlockStop:
        type = "content_block_stop"

    class FakeMessageStart:
        type = "message_start"

        class message:
            usage = {"input_tokens": 10}

    class FakeMessageDelta:
        type = "message_delta"

        class usage:
            output_tokens = 5

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            yield FakeMessageStart()
            yield FakeContentBlockStart()
            yield FakeContentBlockDelta()
            yield FakeContentBlockStop()
            yield FakeMessageDelta()

    class FakeMessages:
        def stream(self, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeStream()

    class FakeAnthropicClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.messages = FakeMessages()

    broth_module = importlib.import_module("miso.broth")
    monkeypatch.setattr(broth_module, "Anthropic", FakeAnthropicClient)

    turn = a._anthropic_fetch_once(
        messages=[{"role": "user", "content": "hi"}],
        payload={},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_stream",
        iteration=0,
        toolkit=toolkit(),
        emit_stream=False,
    )

    assert captured_kwargs["stream"] is True
    assert turn.final_text == "hello"
    assert turn.consumed_tokens > 0


# ── unit test: _anthropic_fetch_once parses tool_use blocks ─────────────────

def test_anthropic_fetch_once_parses_tool_calls(monkeypatch):
    a = Broth()
    a.provider = "anthropic"
    a.api_key = "sk-test-fake"
    a.model = "claude-sonnet-4"

    class FakeMessageStart:
        type = "message_start"

        class message:
            usage = {"input_tokens": 20}

    class FakeToolBlockStart:
        type = "content_block_start"

        class content_block:
            type = "tool_use"
            id = "call_abc"
            name = "get_weather"

    class FakeToolInputDelta:
        type = "content_block_delta"

        class delta:
            type = "input_json_delta"
            partial_json = '{"city": "Tokyo"}'

    class FakeToolBlockStop:
        type = "content_block_stop"

    class FakeMessageDelta:
        type = "message_delta"

        class usage:
            output_tokens = 12

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            yield FakeMessageStart()
            yield FakeToolBlockStart()
            yield FakeToolInputDelta()
            yield FakeToolBlockStop()
            yield FakeMessageDelta()

    class FakeMessages:
        def stream(self, **kwargs):
            return FakeStream()

    class FakeAnthropicClient:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    broth_module = importlib.import_module("miso.broth")
    monkeypatch.setattr(broth_module, "Anthropic", FakeAnthropicClient)

    turn = a._anthropic_fetch_once(
        messages=[{"role": "user", "content": "What's the weather?"}],
        payload={},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_tool",
        iteration=0,
        toolkit=toolkit(),
        emit_stream=False,
    )

    assert len(turn.tool_calls) == 1
    tc = turn.tool_calls[0]
    assert tc.name == "get_weather"
    assert tc.call_id == "call_abc"
    assert tc.arguments == {"city": "Tokyo"}
    assert turn.consumed_tokens > 0


# ── unit test: dated model resolves to undated config ───────────────────────

def test_dated_model_resolves_to_base_config():
    a = Broth()
    a.model = "claude-opus-4-20250514"

    # Should resolve to claude-opus-4 entry
    provider = a._model_capability("provider")
    assert provider == "anthropic"

    supports_tools = a._model_capability("supports_tools")
    assert supports_tools is True

    # Default payload should also resolve
    merged = a._merged_payload(None)
    assert "max_tokens" in merged
    assert merged["max_tokens"] == 4096


def test_dated_model_46_resolves():
    a = Broth()
    a.model = "claude-sonnet-4.6-20260101"

    provider = a._model_capability("provider")
    assert provider == "anthropic"

    merged = a._merged_payload(None)
    assert "max_tokens" in merged
    assert merged["max_tokens"] == 8192
