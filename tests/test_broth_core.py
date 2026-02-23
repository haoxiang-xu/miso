import json
import importlib

import pytest

from miso import broth as Broth, response_format, tool, toolkit
from miso.broth import ProviderTurnResult, ToolCall


def test_observation_injected_into_last_tool_message_and_callback_events():
    agent = Broth()
    agent.provider = "ollama"

    observed_tool = tool(name="need_observe", func=lambda: {"value": 1}, observe=True, parameters=[])
    plain_tool = tool(name="plain_tool", func=lambda: {"value": 2}, observe=False, parameters=[])
    agent.toolkit = toolkit({
        observed_tool.name: observed_tool,
        plain_tool.name: plain_tool,
    })

    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        if kwargs["run_id"] == "observe":
            return ProviderTurnResult(
                assistant_messages=[{"role": "assistant", "content": "检查通过，继续下一步。"}],
                tool_calls=[],
                final_text="检查通过，继续下一步。",
                consumed_tokens=4,
            )

        state["turn"] += 1

        if state["turn"] == 1:
            return ProviderTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "call_1", "function": {"name": "need_observe", "arguments": "{}"}},
                            {"id": "call_2", "function": {"name": "plain_tool", "arguments": "{}"}},
                        ],
                    }
                ],
                tool_calls=[
                    ToolCall(call_id="call_1", name="need_observe", arguments={}),
                    ToolCall(call_id="call_2", name="plain_tool", arguments={}),
                ],
                final_text="",
                consumed_tokens=10,
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            consumed_tokens=7,
        )

    agent._fetch_once = fake_fetch_once

    events = []
    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "start"}],
        callback=events.append,
        max_iterations=3,
    )

    tool_messages = [msg for msg in messages if isinstance(msg, dict) and msg.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert bundle["consumed_tokens"] == 21

    last_tool_payload = json.loads(tool_messages[-1]["content"])
    assert last_tool_payload["observation"] == "检查通过，继续下一步。"

    event_types = [evt["type"] for evt in events]
    assert event_types.count("tool_call") == 2
    assert event_types.count("tool_result") == 2
    assert "observation" in event_types
    assert "final_message" in event_types


def test_response_format_parses_last_assistant_message():
    agent = Broth()
    agent.provider = "ollama"

    def fake_fetch_once(**kwargs):
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": '{"answer":"ok"}'}],
            tool_calls=[],
            final_text='{"answer":"ok"}',
        )

    agent._fetch_once = fake_fetch_once

    fmt = response_format(
        name="answer_format",
        schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
            },
            "required": ["answer"],
            "additionalProperties": False,
        },
    )

    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "give me json"}],
        response_format=fmt,
        max_iterations=1,
    )

    last_assistant = [msg for msg in messages if msg.get("role") == "assistant"][-1]
    assert json.loads(last_assistant["content"]) == {"answer": "ok"}
    assert bundle["consumed_tokens"] == 0


def test_merged_payload_overrides_only_known_default_keys():
    agent = Broth()
    agent.model = "gpt-4.1"

    merged = agent._merged_payload(
        {
            "temperature": 0.2,
            "max_output_tokens": 64,
            "not_allowed": "ignored",
        }
    )

    assert merged["temperature"] == 0.2
    assert merged["max_output_tokens"] == 64
    assert merged["top_p"] == 1
    assert "not_allowed" not in merged


def test_merged_payload_ignores_user_payload_when_model_has_no_defaults():
    agent = Broth()
    agent.model = "unknown-model"

    merged = agent._merged_payload({"temperature": 0.1, "max_output_tokens": 10})

    assert merged == {}


def test_openai_run_threads_previous_response_id_across_iterations():
    agent = Broth()
    agent.provider = "openai"

    seen_previous_ids = []
    seen_messages = []
    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        seen_previous_ids.append(kwargs.get("previous_response_id"))
        seen_messages.append(kwargs.get("messages"))
        state["turn"] += 1

        if state["turn"] == 1:
            return ProviderTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "missing_tool",
                        "status": "completed",
                        "arguments": "{}",
                    }
                ],
                tool_calls=[ToolCall(call_id="call_1", name="missing_tool", arguments={})],
                final_text="",
                response_id="resp_1",
                consumed_tokens=11,
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_2",
            consumed_tokens=7,
        )

    agent._fetch_once = fake_fetch_once

    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "start"}],
        previous_response_id="prev_0",
        max_iterations=3,
    )

    assert seen_previous_ids == ["prev_0", "resp_1"]
    assert isinstance(seen_messages[1], list)
    assert len(seen_messages[1]) == 1
    assert seen_messages[1][0].get("type") == "function_call_output"
    assert agent.last_response_id == "resp_2"
    assert [msg for msg in messages if msg.get("role") == "assistant"][-1]["content"] == "done"
    assert bundle["consumed_tokens"] == 18


def test_openai_run_emits_reasoning_event_and_tracks_reasoning_items():
    agent = Broth()
    agent.provider = "openai"

    def fake_fetch_once(**kwargs):
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_reasoning",
            reasoning_items=[
                {
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "Need to verify data shape first."},
                    ],
                }
            ],
            consumed_tokens=9,
        )

    agent._fetch_once = fake_fetch_once

    events = []
    _, bundle = agent.run(
        messages=[{"role": "user", "content": "start"}],
        callback=events.append,
        max_iterations=1,
    )

    reasoning_events = [evt for evt in events if evt["type"] == "reasoning"]
    assert len(reasoning_events) == 1
    assert reasoning_events[0]["response_id"] == "resp_reasoning"
    assert agent.last_response_id == "resp_reasoning"
    assert len(agent.last_reasoning_items) == 1
    assert bundle["consumed_tokens"] == 9


def test_merged_payload_respects_model_capability_allowed_payload_keys():
    agent = Broth()
    agent.model = "gpt-4.1"

    # Inject a key into defaults that is not allowed by model_capabilities.
    agent.default_payload["gpt-4.1"]["store"] = True

    merged = agent._merged_payload({"store": False, "temperature": 0.4})

    assert merged["temperature"] == 0.4
    assert "store" not in merged


def test_run_drops_previous_response_id_when_capability_disallows_it():
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-4.1"
    agent.model_capabilities["gpt-4.1"]["supports_previous_response_id"] = False

    seen_previous_ids = []

    def fake_fetch_once(**kwargs):
        seen_previous_ids.append(kwargs.get("previous_response_id"))
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_done",
        )

    agent._fetch_once = fake_fetch_once

    agent.run(
        messages=[{"role": "user", "content": "start"}],
        previous_response_id="prev_should_be_ignored",
        max_iterations=1,
    )

    assert seen_previous_ids == [None]

def test_openai_fetch_once_forces_stream_true(monkeypatch):
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-4.1"
    agent.api_key = "test-key"
    agent.default_payload.setdefault("gpt-4.1", {})["stream"] = False

    captured_kwargs = {}

    class FakeOpenAIStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield type("Chunk", (), {
                "type": "response.completed",
                "response": type("Resp", (), {
                    "id": "resp_stream_test",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
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

    broth_module = importlib.import_module("miso.broth")
    monkeypatch.setattr(broth_module, "OpenAI", FakeOpenAIClient)

    turn = agent._openai_fetch_once(
        messages=[{"role": "user", "content": "hi"}],
        payload={"stream": False},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_stream",
        iteration=0,
        toolkit=toolkit(),
        emit_stream=False,
        previous_response_id=None,
    )

    assert captured_kwargs["stream"] is True
    assert turn.final_text == "ok"


def test_ollama_fetch_once_forces_stream_true(monkeypatch):
    agent = Broth()
    agent.provider = "ollama"

    captured_request = {}

    class FakeHTTPXStream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield json.dumps({
                "message": {"content": "ok"},
                "done": True,
            })

        def read(self):
            return b""

    def fake_httpx_stream(method, url, json, timeout):
        captured_request["method"] = method
        captured_request["url"] = url
        captured_request["json"] = json
        captured_request["timeout"] = timeout
        return FakeHTTPXStream()

    broth_module = importlib.import_module("miso.broth")
    monkeypatch.setattr(broth_module.httpx, "stream", fake_httpx_stream)

    turn = agent._ollama_fetch_once(
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

    assert captured_request["json"]["stream"] is True
    assert turn.final_text == "ok"


# ── Context-window tracking tests ──────────────────────────────────────

def test_max_context_window_tokens_from_capabilities():
    """max_context_window_tokens property reads from model_capabilities.json."""
    a = Broth()
    a.model = "gpt-5"
    assert a.max_context_window_tokens == 1047576
    a.model = "claude-opus-4"
    assert a.max_context_window_tokens == 200000
    a.model = "deepseek-r1:14b"
    assert a.max_context_window_tokens == 128000
    # Unknown model falls back to 0
    a.model = "unknown-model-xyz"
    assert a.max_context_window_tokens == 0


def test_max_context_window_tokens_user_override():
    """User-set max_context_window_tokens takes precedence over model default."""
    a = Broth()
    a.model = "gpt-5"
    assert a.max_context_window_tokens == 1047576  # default from capabilities

    a.max_context_window_tokens = 50000
    assert a.max_context_window_tokens == 50000  # user override

    # Resetting to None falls back to model default
    a.max_context_window_tokens = None
    assert a.max_context_window_tokens == 1047576


def test_bundle_contains_context_window_used_pct():
    """context_window_used_pct = last turn's tokens / max_context_window_tokens (not cumulative)."""
    a = Broth()
    a.provider = "ollama"
    a.model = "deepseek-r1:14b"  # max_context_window_tokens = 128000

    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        state["turn"] += 1
        if state["turn"] == 1:
            # First turn: tool call, 2000 tokens
            return ProviderTurnResult(
                assistant_messages=[{
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "c1", "function": {"name": "t1", "arguments": "{}"}}],
                }],
                tool_calls=[ToolCall(call_id="c1", name="t1", arguments={})],
                final_text="",
                consumed_tokens=2000,
            )
        # Second turn: final answer, 6400 tokens (conversation grew)
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            consumed_tokens=6400,
        )

    from miso import tool, toolkit
    t1 = tool(name="t1", func=lambda: "ok", parameters=[])
    a.toolkit = toolkit({"t1": t1})
    a._fetch_once = fake_fetch_once

    _, bundle = a.run([{"role": "user", "content": "hello"}])
    assert bundle["consumed_tokens"] == 8400      # cumulative: 2000 + 6400
    assert bundle["max_context_window_tokens"] == 128000
    assert bundle["context_window_used_pct"] == 5.0  # last turn: 6400/128000*100
    assert "context_window_tokens" not in bundle


def test_consumed_tokens_accumulated_across_runs():
    """agent.consumed_tokens accumulates across multiple run() calls."""
    a = Broth()
    a.provider = "ollama"
    a.model = "deepseek-r1:14b"
    call_count = 0

    def fake_fetch_once(**kwargs):
        nonlocal call_count
        call_count += 1
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": f"resp{call_count}"}],
            tool_calls=[],
            final_text=f"resp{call_count}",
            consumed_tokens=100,
        )

    a._fetch_once = fake_fetch_once

    assert a.consumed_tokens == 0

    a.run([{"role": "user", "content": "first"}])
    assert a.consumed_tokens == 100
    assert a.last_consumed_tokens == 100

    a.run([{"role": "user", "content": "second"}])
    assert a.consumed_tokens == 200
    assert a.last_consumed_tokens == 100  # last run only


def test_context_window_used_pct_zero_for_unknown_model():
    """When max_context_window_tokens is 0 (unknown model), pct should be 0."""
    a = Broth()
    a.provider = "ollama"
    a.model = "unknown-model"

    def fake_fetch_once(**kwargs):
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "ok"}],
            tool_calls=[],
            final_text="ok",
            consumed_tokens=10,
        )

    a._fetch_once = fake_fetch_once
    _, bundle = a.run([{"role": "user", "content": "hi"}])
    assert bundle["max_context_window_tokens"] == 0
    assert bundle["context_window_used_pct"] == 0.0


def test_run_projects_multimodal_seed_messages_to_openai_blocks():
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-5"

    captured_messages = []

    def fake_fetch_once(**kwargs):
        captured_messages.append(kwargs.get("messages"))
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "ok"}],
            tool_calls=[],
            final_text="ok",
            response_id="resp_mm_1",
        )

    agent._fetch_once = fake_fetch_once

    agent.run(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "read both"},
                    {"type": "image", "source": {"type": "url", "url": "https://example.com/a.jpg"}},
                    {
                        "type": "pdf",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": "JVBERi0xLjQK",
                        },
                    },
                ],
            }
        ],
        max_iterations=1,
    )

    assert len(captured_messages) == 1
    first_request = captured_messages[0]
    assert isinstance(first_request, list)
    blocks = first_request[0]["content"]
    assert [b["type"] for b in blocks] == ["input_text", "input_image", "input_file"]
    assert blocks[0]["text"] == "read both"
    assert blocks[1]["image_url"] == "https://example.com/a.jpg"
    assert blocks[2]["file_data"] == "JVBERi0xLjQK"
    assert blocks[2]["filename"] == "document.pdf"


def test_run_projects_multimodal_seed_messages_to_anthropic_blocks():
    agent = Broth()
    agent.provider = "anthropic"
    agent.model = "claude-sonnet-4"

    captured_messages = []

    def fake_fetch_once(**kwargs):
        captured_messages.append(kwargs.get("messages"))
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "ok"}],
            tool_calls=[],
            final_text="ok",
        )

    agent._fetch_once = fake_fetch_once

    agent.run(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "check"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgoAAAANSUhEUgAA",
                        },
                    },
                    {"type": "pdf", "source": {"type": "url", "url": "https://example.com/a.pdf"}},
                ],
            }
        ],
        max_iterations=1,
    )

    assert len(captured_messages) == 1
    first_request = captured_messages[0]
    blocks = first_request[0]["content"]
    assert [b["type"] for b in blocks] == ["text", "image", "document"]
    assert blocks[1]["source"]["type"] == "base64"
    assert blocks[2]["source"]["type"] == "url"


def test_run_rejects_unsupported_multimodal_input_for_text_only_model():
    agent = Broth()
    agent.provider = "ollama"
    agent.model = "deepseek-r1:14b"

    with pytest.raises(ValueError, match="does not support input modality 'image'"):
        agent.run(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "image", "source": {"type": "url", "url": "https://example.com/a.jpg"}},
                    ],
                }
            ],
            max_iterations=1,
        )


def test_run_rejects_disallowed_source_type_from_capabilities():
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-5"
    agent.model_capabilities["gpt-5"]["input_source_types"] = {
        "image": ["url"],
        "pdf": ["url", "base64"],
    }

    with pytest.raises(ValueError, match="source.type 'base64'"):
        agent.run(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "iVBORw0KGgoAAAANSUhEUgAA",
                            },
                        }
                    ],
                }
            ],
            max_iterations=1,
        )


def test_canonicalize_compat_provider_native_input_blocks():
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-5"

    canonical = agent._canonicalize_seed_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "native"},
                    {"type": "input_image", "image_url": "https://example.com/a.jpg"},
                    {"type": "input_file", "file_url": "https://example.com/a.pdf"},
                ],
            }
        ]
    )
    blocks = canonical[0]["content"]
    assert [b["type"] for b in blocks] == ["text", "image", "pdf"]

    projected_openai = agent._project_canonical_to_openai(canonical)
    assert [b["type"] for b in projected_openai[0]["content"]] == ["input_text", "input_image", "input_file"]

    agent.provider = "anthropic"
    canonical_from_anthropic = agent._canonicalize_seed_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "native"},
                    {"type": "image", "source": {"type": "url", "url": "https://example.com/a.jpg"}},
                    {"type": "document", "source": {"type": "url", "url": "https://example.com/a.pdf"}},
                ],
            }
        ]
    )
    projected_anthropic = agent._project_canonical_to_anthropic(canonical_from_anthropic)
    assert [b["type"] for b in projected_anthropic[0]["content"]] == ["text", "image", "document"]
