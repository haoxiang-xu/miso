import json
import importlib

from miso import agent as Agent, response_format, tool, toolkit
from miso.agent import ProviderTurnResult, ToolCall


def test_observation_injected_into_last_tool_message_and_callback_events():
    agent = Agent()
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
    agent = Agent()
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
    agent = Agent()
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
    agent = Agent()
    agent.model = "unknown-model"

    merged = agent._merged_payload({"temperature": 0.1, "max_output_tokens": 10})

    assert merged == {}


def test_openai_run_threads_previous_response_id_across_iterations():
    agent = Agent()
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
    agent = Agent()
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
    agent = Agent()
    agent.model = "gpt-4.1"

    # Inject a key into defaults that is not allowed by model_capabilities.
    agent.default_payload["gpt-4.1"]["store"] = True

    merged = agent._merged_payload({"store": False, "temperature": 0.4})

    assert merged["temperature"] == 0.4
    assert "store" not in merged


def test_run_drops_previous_response_id_when_capability_disallows_it():
    agent = Agent()
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
    agent = Agent()
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

    agent_module = importlib.import_module("miso.agent")
    monkeypatch.setattr(agent_module, "OpenAI", FakeOpenAIClient)

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
    agent = Agent()
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

    agent_module = importlib.import_module("miso.agent")
    monkeypatch.setattr(agent_module.httpx, "stream", fake_httpx_stream)

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
