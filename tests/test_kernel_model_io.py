import base64
import json
from types import SimpleNamespace

from unchain.input import media
from unchain.kernel import ModelTurnRequest
from unchain.providers import AnthropicModelIO, OllamaModelIO
from unchain.tools import get_provider_message_builder
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


class _FakeAnthropicClient:
    def __init__(self, *, events, captured_kwargs, **kwargs):
        self.messages = _FakeAnthropicMessages(events=events, captured_kwargs=captured_kwargs)


def _anthropic_text_client_factory(captured_kwargs, *, text="ok"):
    def client_factory(api_key, **kwargs):
        return _FakeAnthropicClient(
            events=[
                SimpleNamespace(
                    type="message_start",
                    message=SimpleNamespace(usage={"input_tokens": 1, "output_tokens": 0}),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="text_delta", text=text),
                ),
                SimpleNamespace(
                    type="message_delta",
                    usage={"input_tokens": 1, "output_tokens": 1},
                ),
            ],
            captured_kwargs=captured_kwargs,
        )

    return client_factory


class _FakeOllamaResponse:
    def __init__(self, *, lines, captured_kwargs):
        self.status_code = 200
        self._lines = list(lines)
        self._captured_kwargs = captured_kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        for line in self._lines:
            yield line

    def read(self):
        return b""


def test_anthropic_model_io_builds_request_and_parses_text():
    captured_kwargs = {}
    events = []
    client_factory = lambda api_key, **kwargs: _FakeAnthropicClient(
        events=[
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage={"input_tokens": 5, "output_tokens": 0}),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="hello claude"),
            ),
            SimpleNamespace(
                type="message_delta",
                usage={"input_tokens": 5, "output_tokens": 2},
            ),
        ],
        captured_kwargs=captured_kwargs,
    )
    io = AnthropicModelIO(model="claude-3-7-sonnet", api_key="test-key", client_factory=client_factory)

    turn = io.fetch_turn(
        ModelTurnRequest(
            messages=[
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "hi"},
            ],
            callback=events.append,
            run_id="anthropic-run",
        )
    )

    assert turn.final_text == "hello claude"
    assert turn.assistant_messages == [{"role": "assistant", "content": "hello claude"}]
    assert turn.consumed_tokens == 7
    assert captured_kwargs["model"] == "claude-3-7-sonnet"
    assert captured_kwargs["messages"] == [{
        "role": "user",
        "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
    }]
    assert captured_kwargs["system"] == [
        {"type": "text", "text": "be helpful", "cache_control": {"type": "ephemeral"}}
    ]
    request_event = next(event for event in events if event["type"] == "request_messages")
    assert request_event["provider"] == "anthropic"
    assert request_event["system"] == "be helpful"


def test_anthropic_model_io_parses_tool_use_and_emits_token_delta():
    captured_kwargs = {}
    events = []
    toolkit = Toolkit()
    toolkit.register(lambda x=None: {"x": x}, name="demo_tool")
    client_factory = lambda api_key, **kwargs: _FakeAnthropicClient(
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
                delta=SimpleNamespace(type="input_json_delta", partial_json="1}"),
            ),
            SimpleNamespace(type="content_block_stop"),
            SimpleNamespace(type="message_delta", usage={"input_tokens": 3, "output_tokens": 2}),
        ],
        captured_kwargs=captured_kwargs,
    )
    io = AnthropicModelIO(model="claude-3-7-sonnet", api_key="test-key", client_factory=client_factory)

    turn = io.fetch_turn(
        ModelTurnRequest(
            messages=[{"role": "user", "content": "call tool"}],
            toolkit=toolkit,
            callback=events.append,
            emit_stream=True,
            run_id="anthropic-tools",
        )
    )

    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].call_id == "tool_1"
    assert turn.tool_calls[0].arguments == {"x": 1}
    assert turn.assistant_messages == [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "thinking"},
            {"type": "tool_use", "id": "tool_1", "name": "demo_tool", "input": {"x": 1}},
        ],
    }]
    assert captured_kwargs["tools"][0]["name"] == "demo_tool"
    token_event = next(event for event in events if event["type"] == "token_delta")
    assert token_event["provider"] == "anthropic"
    assert token_event["delta"] == "thinking"


def test_anthropic_model_io_raises_clear_error_when_chat_messages_are_empty():
    captured_kwargs = {}
    events = []
    client_factory = lambda api_key, **kwargs: _FakeAnthropicClient(
        events=[],
        captured_kwargs=captured_kwargs,
    )
    io = AnthropicModelIO(model="claude-3-7-sonnet", api_key="test-key", client_factory=client_factory)

    try:
        io.fetch_turn(
            ModelTurnRequest(
                messages=[{"role": "system", "content": "be helpful"}],
                callback=events.append,
                run_id="anthropic-empty",
            )
        )
        assert False, "expected AnthropicModelIO to reject empty chat messages"
    except ValueError as exc:
        assert "no chat messages after preprocessing" in str(exc)

    request_event = next(event for event in events if event["type"] == "request_messages")
    assert request_event["provider"] == "anthropic"
    assert request_event["messages"] == []
    assert request_event["system"] == "be helpful"
    assert captured_kwargs == {}


def test_anthropic_model_io_maps_sonnet_4_alias_to_provider_model():
    captured_kwargs = {}
    client_factory = lambda api_key, **kwargs: _FakeAnthropicClient(
        events=[
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage={"input_tokens": 1, "output_tokens": 0}),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="ok"),
            ),
            SimpleNamespace(
                type="message_delta",
                usage={"input_tokens": 1, "output_tokens": 1},
            ),
        ],
        captured_kwargs=captured_kwargs,
    )
    io = AnthropicModelIO(model="claude-sonnet-4", api_key="test-key", client_factory=client_factory)

    turn = io.fetch_turn(
        ModelTurnRequest(messages=[{"role": "user", "content": "hi"}])
    )

    assert turn.final_text == "ok"
    assert captured_kwargs["model"] == "claude-sonnet-4-20250514"


def test_anthropic_model_io_translates_canonical_media_blocks(tmp_path):
    png_bytes = b"png-bytes"
    pdf_bytes = b"%PDF-1.4\npdf-bytes\n"
    png_path = tmp_path / "x.png"
    pdf_path = tmp_path / "x.pdf"
    png_path.write_bytes(png_bytes)
    pdf_path.write_bytes(pdf_bytes)

    captured_kwargs = {}
    io = AnthropicModelIO(
        model="claude-3-7-sonnet",
        api_key="test-key",
        client_factory=_anthropic_text_client_factory(captured_kwargs),
    )

    turn = io.fetch_turn(
        ModelTurnRequest(
            messages=[{
                "role": "user",
                "content": [
                    media.from_file(png_path),
                    media.from_file(pdf_path),
                    {"type": "pdf", "source": {"type": "file_id", "file_id": "file_pdf"}},
                    {"type": "text", "text": "read these"},
                ],
            }]
        )
    )

    assert turn.final_text == "ok"
    assert captured_kwargs["messages"] == [{
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(png_bytes).decode("ascii"),
                },
            },
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(pdf_bytes).decode("ascii"),
                },
            },
            {
                "type": "document",
                "source": {"type": "file", "file_id": "file_pdf"},
            },
            {"type": "text", "text": "read these", "cache_control": {"type": "ephemeral"}},
        ],
    }]


def test_anthropic_model_io_translates_openai_native_image_blocks():
    captured_kwargs = {}
    io = AnthropicModelIO(
        model="claude-3-7-sonnet",
        api_key="test-key",
        client_factory=_anthropic_text_client_factory(captured_kwargs),
    )

    io.fetch_turn(
        ModelTurnRequest(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": "data:image/png;base64,aW1n"},
                    {"type": "input_image", "image_url": "https://example.com/image.png"},
                    {"type": "input_text", "text": "describe"},
                ],
            }]
        )
    )

    assert captured_kwargs["messages"][0]["content"] == [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "aW1n"},
        },
        {
            "type": "image",
            "source": {"type": "url", "url": "https://example.com/image.png"},
        },
        {"type": "text", "text": "describe", "cache_control": {"type": "ephemeral"}},
    ]


def test_anthropic_model_io_translates_openai_native_file_blocks():
    captured_kwargs = {}
    io = AnthropicModelIO(
        model="claude-3-7-sonnet",
        api_key="test-key",
        client_factory=_anthropic_text_client_factory(captured_kwargs),
    )

    io.fetch_turn(
        ModelTurnRequest(
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "filename": "report.pdf",
                        "file_data": "data:application/pdf;base64,JVBERg==",
                    },
                    {"type": "input_file", "file_url": "https://example.com/report.pdf"},
                    {"type": "input_file", "file_id": "file_123"},
                    {"type": "input_text", "text": "summarize"},
                ],
            }]
        )
    )

    assert captured_kwargs["messages"][0]["content"] == [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERg=="},
        },
        {
            "type": "document",
            "source": {"type": "url", "url": "https://example.com/report.pdf"},
        },
        {
            "type": "document",
            "source": {"type": "file", "file_id": "file_123"},
        },
        {"type": "text", "text": "summarize", "cache_control": {"type": "ephemeral"}},
    ]


def test_ollama_model_io_builds_request_and_parses_text():
    captured_kwargs = {}

    def stream_factory(method, url, **kwargs):
        captured_kwargs["method"] = method
        captured_kwargs["url"] = url
        captured_kwargs.update(kwargs)
        return _FakeOllamaResponse(
            lines=[
                json.dumps({
                    "message": {"content": "hello ollama"},
                    "prompt_eval_count": 4,
                    "eval_count": 3,
                    "done": True,
                })
            ],
            captured_kwargs=captured_kwargs,
        )

    io = OllamaModelIO(model="qwen3", stream_factory=stream_factory)
    events = []
    turn = io.fetch_turn(
        ModelTurnRequest(
            messages=[{"role": "user", "content": "hi"}],
            callback=events.append,
            run_id="ollama-run",
        )
    )

    assert turn.final_text == "hello ollama"
    assert turn.assistant_messages == [{"role": "assistant", "content": "hello ollama"}]
    assert turn.consumed_tokens == 7
    assert captured_kwargs["method"] == "POST"
    assert captured_kwargs["url"].endswith("/api/chat")
    assert captured_kwargs["json"]["messages"] == [{"role": "user", "content": "hi"}]
    request_event = next(event for event in events if event["type"] == "request_messages")
    assert request_event["provider"] == "ollama"


def test_ollama_model_io_parses_tool_calls():
    toolkit = Toolkit()
    toolkit.register(lambda x=None: {"x": x}, name="demo_tool")

    def stream_factory(method, url, **kwargs):
        del method, url, kwargs
        return _FakeOllamaResponse(
            lines=[
                json.dumps({
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "demo_tool",
                                    "arguments": {"x": 1},
                                },
                            }
                        ],
                    },
                    "prompt_eval_count": 2,
                    "eval_count": 1,
                })
            ],
            captured_kwargs={},
        )

    io = OllamaModelIO(model="qwen3", stream_factory=stream_factory)
    turn = io.fetch_turn(
        ModelTurnRequest(
            messages=[{"role": "user", "content": "use tool"}],
            toolkit=toolkit,
        )
    )

    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].call_id == "call_1"
    assert turn.tool_calls[0].name == "demo_tool"
    assert turn.tool_calls[0].arguments == {"x": 1}
    assert turn.assistant_messages == [{
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "function": {
                    "name": "demo_tool",
                    "arguments": {"x": 1},
                },
            }
        ],
    }]


def test_ollama_model_io_omits_tools_when_model_capability_disables_tools():
    toolkit = Toolkit()
    toolkit.register(lambda x=None: {"x": x}, name="demo_tool")
    captured_kwargs = {}

    def stream_factory(method, url, **kwargs):
        del method, url
        captured_kwargs.update(kwargs)
        return _FakeOllamaResponse(
            lines=[
                json.dumps({
                    "message": {"content": "no tools"},
                    "done": True,
                })
            ],
            captured_kwargs=captured_kwargs,
        )

    io = OllamaModelIO(
        model="deepseek-r1:14b",
        stream_factory=stream_factory,
        model_capabilities={
            "deepseek-r1:14b": {
                "provider": "ollama",
                "supports_tools": False,
            },
        },
    )
    events = []
    turn = io.fetch_turn(
        ModelTurnRequest(
            messages=[{"role": "user", "content": "use tool"}],
            toolkit=toolkit,
            callback=events.append,
        )
    )

    assert turn.final_text == "no tools"
    assert "tools" not in captured_kwargs["json"]
    assert "tool_choice" not in captured_kwargs["json"]
    request_event = next(event for event in events if event["type"] == "request_messages")
    assert "tool_names" not in request_event


def test_provider_message_builders_cover_anthropic_and_ollama_shapes():
    tool_call = SimpleNamespace(call_id="call_1", name="demo_tool")
    anthropic_message = get_provider_message_builder("anthropic").build_tool_result_message(
        tool_call=tool_call,
        tool_result={"ok": True},
    )
    ollama_message = get_provider_message_builder("ollama").build_tool_result_message(
        tool_call=tool_call,
        tool_result={"ok": True},
    )

    assert anthropic_message == {
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": json.dumps({"ok": True}, ensure_ascii=False),
        }],
    }
    assert ollama_message == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": json.dumps({"ok": True}, ensure_ascii=False),
    }
