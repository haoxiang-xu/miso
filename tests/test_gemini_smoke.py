import os
import json
import importlib
from unittest.mock import MagicMock

import pytest

from miso.runtime import Broth
from miso.tools import Toolkit
from miso.runtime import ProviderTurnResult, ToolCall


def _last_assistant_text(messages):
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                return "".join(parts).strip()
            return (content or "").strip()
    return ""


# ── live smoke test (skipped unless env vars set) ───────────────────────────

def test_gemini_smoke():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    model = os.environ.get("GEMINI_MODEL")
    if not api_key or not model:
        pytest.skip("GEMINI_API_KEY/GOOGLE_API_KEY or GEMINI_MODEL not set")

    a = Broth()
    a.provider = "gemini"
    a.api_key = api_key
    a.model = model

    messages = [{"role": "user", "content": "Reply with OK only."}]
    messages_out, bundle = a.run(
        messages=messages,
        payload={"max_output_tokens": 32},
        max_iterations=1,
    )

    assert _last_assistant_text(messages_out) != ""
    assert isinstance(bundle.get("consumed_tokens"), int)


# ── unit test: _gemini_fetch_once streaming text ────────────────────────────

def test_gemini_fetch_once_streams_text(monkeypatch):
    a = Broth()
    a.provider = "gemini"
    a.api_key = "fake-key"
    a.model = "gemini-2.5-pro"

    captured_kwargs = {}

    # ── lightweight fakes for Gemini streaming ──

    class FakePart:
        def __init__(self, text=None, function_call=None):
            self.text = text
            self.function_call = function_call

    class FakeContent:
        def __init__(self, parts):
            self.parts = parts

    class FakeCandidate:
        def __init__(self, content):
            self.content = content

    class FakeUsageMeta:
        prompt_token_count = 8
        tool_use_prompt_token_count = 1
        candidates_token_count = 4
        thoughts_token_count = 3
        total_token_count = 16

    class FakeChunk:
        def __init__(self, candidates, usage_metadata=None):
            self.candidates = candidates
            self.usage_metadata = usage_metadata or FakeUsageMeta()

    class FakeModels:
        def generate_content_stream(self, **kwargs):
            captured_kwargs.update(kwargs)
            yield FakeChunk(
                candidates=[FakeCandidate(FakeContent([FakePart(text="hello")]))]
            )
            yield FakeChunk(
                candidates=[FakeCandidate(FakeContent([FakePart(text=" world")]))]
            )

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.models = FakeModels()

    # Patch google_genai in broth module
    broth_module = importlib.import_module("miso.runtime.providers")
    fake_genai = MagicMock()
    fake_genai.Client = FakeClient
    monkeypatch.setattr(broth_module, "google_genai", fake_genai)

    turn = a._gemini_fetch_once(
        messages=[{"role": "user", "parts": [{"text": "hi"}]}],
        payload={},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_gemini",
        iteration=0,
        toolkit=Toolkit(),
        emit_stream=False,
    )

    assert captured_kwargs["model"] == "gemini-2.5-pro"
    assert turn.final_text == "hello world"
    assert turn.consumed_tokens == 16
    assert turn.input_tokens == 9
    assert turn.output_tokens == 7
    assert turn.tool_calls == []


# ── unit test: _gemini_fetch_once parses tool calls ─────────────────────────

def test_gemini_fetch_once_parses_tool_calls(monkeypatch):
    a = Broth()
    a.provider = "gemini"
    a.api_key = "fake-key"
    a.model = "gemini-2.5-pro"

    class FakeFnCall:
        name = "get_weather"
        args = {"city": "Tokyo"}

    class FakePart:
        def __init__(self, text=None, function_call=None):
            self.text = text
            self.function_call = function_call

    class FakeContent:
        def __init__(self, parts):
            self.parts = parts

    class FakeCandidate:
        def __init__(self, content):
            self.content = content

    class FakeUsageMeta:
        prompt_token_count = 10
        candidates_token_count = 8
        thoughts_token_count = 7
        total_token_count = 25

    class FakeChunk:
        def __init__(self, candidates, usage_metadata=None):
            self.candidates = candidates
            self.usage_metadata = usage_metadata or FakeUsageMeta()

    class FakeModels:
        def generate_content_stream(self, **kwargs):
            yield FakeChunk(
                candidates=[FakeCandidate(FakeContent([
                    FakePart(function_call=FakeFnCall()),
                ]))]
            )

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    broth_module = importlib.import_module("miso.runtime.providers")
    fake_genai = MagicMock()
    fake_genai.Client = FakeClient
    monkeypatch.setattr(broth_module, "google_genai", fake_genai)

    turn = a._gemini_fetch_once(
        messages=[{"role": "user", "parts": [{"text": "weather?"}]}],
        payload={},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_tool",
        iteration=0,
        toolkit=Toolkit(),
        emit_stream=False,
    )

    assert len(turn.tool_calls) == 1
    tc = turn.tool_calls[0]
    assert tc.name == "get_weather"
    assert tc.arguments == {"city": "Tokyo"}
    assert turn.consumed_tokens == 25
    assert turn.input_tokens == 10
    assert turn.output_tokens == 15


# ── unit test: model capabilities resolve ───────────────────────────────────

def test_gemini_model_resolves():
    a = Broth()
    a.model = "gemini-2.5-pro"

    provider = a._model_capability("provider")
    assert provider == "gemini"

    supports_tools = a._model_capability("supports_tools")
    assert supports_tools is True

    merged = a._merged_payload(None)
    assert "max_output_tokens" in merged


def test_gemini_flash_model_resolves():
    a = Broth()
    a.model = "gemini-2.5-flash"

    provider = a._model_capability("provider")
    assert provider == "gemini"

    merged = a._merged_payload(None)
    assert "max_output_tokens" in merged
    assert merged["max_output_tokens"] == 65536


# ── unit test: canonical message projection ─────────────────────────────────

def test_gemini_projection_basic():
    a = Broth()
    a.provider = "gemini"
    a.model = "gemini-2.5-pro"

    canonical = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]

    projected = a._project_canonical_to_gemini(canonical)

    # System messages are stripped (handled separately)
    assert len(projected) == 2

    # User message
    assert projected[0]["role"] == "user"
    assert projected[0]["parts"] == [{"text": "Hello"}]

    # Assistant -> model
    assert projected[1]["role"] == "model"
    assert projected[1]["parts"] == [{"text": "Hi there"}]


def test_gemini_projection_image():
    a = Broth()
    a.provider = "gemini"
    a.model = "gemini-2.5-pro"

    canonical = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe this image"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "iVBORw0KGgoAAAA",
                    },
                },
            ],
        }
    ]

    projected = a._project_canonical_to_gemini(canonical)
    assert len(projected) == 1
    parts = projected[0]["parts"]
    assert len(parts) == 2
    assert parts[0] == {"text": "describe this image"}
    assert parts[1] == {
        "inline_data": {
            "mime_type": "image/png",
            "data": "iVBORw0KGgoAAAA",
        }
    }


def test_gemini_projection_pdf_url():
    a = Broth()
    a.provider = "gemini"
    a.model = "gemini-2.5-pro"

    canonical = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "summarize"},
                {"type": "pdf", "source": {"type": "url", "url": "https://example.com/a.pdf"}},
            ],
        }
    ]

    projected = a._project_canonical_to_gemini(canonical)
    parts = projected[0]["parts"]
    assert parts[1] == {
        "file_data": {
            "file_uri": "https://example.com/a.pdf",
            "mime_type": "application/pdf",
        }
    }


# ── unit test: tool result message format ───────────────────────────────────

def test_gemini_tool_result_format():
    a = Broth()
    a.provider = "gemini"
    a.api_key = "fake-key"
    a.model = "gemini-2.5-pro"

    def fake_fetch_once(**kwargs):
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
        )

    a._fetch_once = fake_fetch_once

    tool_calls = [ToolCall(call_id="c1", name="get_weather", arguments={"city": "SF"})]

    tk = Toolkit()
    from miso.tools import Tool
    t = Tool(name="get_weather", description="Get weather", parameters=[], func=lambda city: {"temp": 72})
    tk.register(t)
    a.add_toolkit(tk)

    result_messages, _ = a._execute_tool_calls(
        tool_calls=tool_calls,
        run_id="r1",
        iteration=0,
        callback=None,
    )

    assert len(result_messages) == 1
    msg = result_messages[0]
    assert msg["role"] == "user"
    assert "parts" in msg
    fn_response = msg["parts"][0]["function_response"]
    assert fn_response["name"] == "get_weather"
    assert isinstance(fn_response["response"], dict)


# ── unit test: run() end-to-end with fake Gemini ───────────────────────────

def test_gemini_run_end_to_end(monkeypatch):
    a = Broth()
    a.provider = "gemini"
    a.api_key = "fake-key"
    a.model = "gemini-2.5-pro"

    captured_events = []

    def callback(event):
        captured_events.append(event)

    def fake_fetch_once(**kwargs):
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "Hello!"}],
            tool_calls=[],
            final_text="Hello!",
            consumed_tokens=10,
            input_tokens=6,
            output_tokens=4,
        )

    a._fetch_once = fake_fetch_once

    messages_out, bundle = a.run(
        messages=[{"role": "user", "content": "hi"}],
        max_iterations=1,
        callback=callback,
    )

    assert _last_assistant_text(messages_out) == "Hello!"
    assert bundle["consumed_tokens"] == 10
    assert bundle["input_tokens"] == 6
    assert bundle["output_tokens"] == 4

    event_types = [e["type"] for e in captured_events]
    assert "run_started" in event_types
    assert "final_message" in event_types
    assert "response_received" in event_types
    assert "run_completed" in event_types
    response_received = next(e for e in captured_events if e["type"] == "response_received")
    assert response_received["has_tool_calls"] is False
    assert response_received["bundle"] == bundle
    run_completed = next(e for e in captured_events if e["type"] == "run_completed")
    assert run_completed["bundle"] == bundle


# ── unit test: streaming callback events ───────────────────────────────────

def test_gemini_streaming_callback(monkeypatch):
    a = Broth()
    a.provider = "gemini"
    a.api_key = "fake-key"
    a.model = "gemini-2.5-pro"

    class FakePart:
        def __init__(self, text=None):
            self.text = text
            self.function_call = None

    class FakeContent:
        def __init__(self, parts):
            self.parts = parts

    class FakeCandidate:
        def __init__(self, content):
            self.content = content

    class FakeUsageMeta:
        prompt_token_count = 4
        candidates_token_count = 3
        thoughts_token_count = 3
        total_token_count = 10

    class FakeChunk:
        def __init__(self, candidates):
            self.candidates = candidates
            self.usage_metadata = FakeUsageMeta()

    class FakeModels:
        def generate_content_stream(self, **kwargs):
            yield FakeChunk([FakeCandidate(FakeContent([FakePart("tok1")]))])
            yield FakeChunk([FakeCandidate(FakeContent([FakePart("tok2")]))])

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    broth_module = importlib.import_module("miso.runtime.providers")
    fake_genai = MagicMock()
    fake_genai.Client = FakeClient
    monkeypatch.setattr(broth_module, "google_genai", fake_genai)

    deltas = []

    def cb(event):
        if event.get("type") == "token_delta":
            deltas.append(event["delta"])

    turn = a._gemini_fetch_once(
        messages=[{"role": "user", "parts": [{"text": "hi"}]}],
        payload={},
        response_format=None,
        callback=cb,
        verbose=False,
        run_id="stream_test",
        iteration=0,
        toolkit=Toolkit(),
        emit_stream=True,
    )

    assert deltas == ["tok1", "tok2"]
    assert turn.final_text == "tok1tok2"
    assert turn.consumed_tokens == 10
    assert turn.input_tokens == 4
    assert turn.output_tokens == 6
