from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from unchain.agent import Agent, InteractionModule, MemoryModule, ToolsModule
from unchain.input.human_input import ASK_USER_QUESTION_TOOL_NAME
from unchain.kernel import ModelTurnRequest
from unchain.kernel.provider_replay import ProviderReplayFrameError
from unchain.memory import JsonFileSessionStore, MemoryManager
from unchain.providers import AnthropicModelIO, OllamaModelIO, OpenAIModelIO
from unchain.runtime import build_runtime_loop
from unchain.tools.messages import coalesce_provider_tool_result_messages
from unchain.tools import Toolkit


class _FakeOpenAIStream:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        yield SimpleNamespace(type="response.completed", response=self._response)


class _SequencedResponses:
    def __init__(self, outputs_by_turn, captured_requests):
        self._outputs_by_turn = outputs_by_turn
        self._captured_requests = captured_requests

    def create(self, **kwargs):
        self._captured_requests.append(copy.deepcopy(kwargs))
        outputs = self._outputs_by_turn.pop(0)
        response = SimpleNamespace(
            id=f"resp_{len(self._captured_requests)}",
            output=copy.deepcopy(outputs),
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )
        return _FakeOpenAIStream(response)


def _openai_factory(outputs_by_turn, captured_requests):
    responses = _SequencedResponses(outputs_by_turn, captured_requests)

    class _Client:
        def __init__(self, api_key):
            self.api_key = api_key
            self.responses = responses

    return _Client


class _FakeAnthropicStream:
    def __init__(self, events):
        self._events = list(events)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._events)


def _anthropic_factory(events_by_turn, captured_requests):
    class _Messages:
        def stream(self, **kwargs):
            captured_requests.append(copy.deepcopy(kwargs))
            return _FakeAnthropicStream(events_by_turn.pop(0))

    class _Client:
        def __init__(self, api_key, **kwargs):
            self.api_key = api_key
            self.messages = _Messages()

    return _Client


def _anthropic_thinking_tool_events(*, signature: str = "opaque-signature"):
    return [
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(
                type="thinking",
                thinking="",
                signature="",
            ),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="thinking_delta", thinking="plan"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="signature_delta", signature=signature),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(
                type="tool_use",
                id="toolu_1",
                name="demo_tool",
                input={},
            ),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(
                type="input_json_delta",
                partial_json='{"x":2}',
            ),
        ),
        SimpleNamespace(type="content_block_stop", index=1),
        SimpleNamespace(
            type="message_delta",
            usage={"input_tokens": 2, "output_tokens": 3},
        ),
    ]


class _FakeOllamaResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = list(lines)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        return iter(self._lines)

    def read(self):
        return b""


def _ollama_stream_factory(lines_by_turn, captured_requests):
    def stream(method, url, **kwargs):
        captured_requests.append(
            {
                "method": method,
                "url": url,
                **copy.deepcopy(kwargs),
            }
        )
        return _FakeOllamaResponse(lines_by_turn.pop(0))

    return stream


def test_openai_stateless_reasoning_replay_preserves_order_and_encrypted_content():
    captured_requests = []
    outputs_by_turn = [
        [
            {
                "type": "reasoning",
                "id": "rs_1",
                "encrypted_content": "opaque-ciphertext",
                "summary": [],
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "demo_tool",
                "arguments": "{\"x\":1}",
                "status": "completed",
            },
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
                "status": "completed",
            }
        ],
    ]
    model_io = OpenAIModelIO(
        model="gpt-5",
        api_key="test-key",
        client_factory=_openai_factory(outputs_by_turn, captured_requests),
    )
    toolkit = Toolkit()
    tool_calls = {"count": 0}

    def demo_tool(x: int):
        tool_calls["count"] += 1
        return {"value": x + 1}

    toolkit.register(demo_tool, name="demo_tool")
    loop = build_runtime_loop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "call the tool"}],
        payload={"store": False, "reasoning": {"effort": "medium"}},
        provider="openai",
        model="gpt-5",
        toolkit=toolkit,
        max_iterations=3,
    )

    assert result.status == "completed"
    assert tool_calls["count"] == 1
    assert len(captured_requests) == 2
    assert "previous_response_id" not in captured_requests[1]
    assert "reasoning.encrypted_content" in captured_requests[0]["include"]
    second_types = [item.get("type") for item in captured_requests[1]["input"]]
    assert second_types[-3:] == [
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    replay_reasoning = captured_requests[1]["input"][-3]
    assert replay_reasoning["encrypted_content"] == "opaque-ciphertext"
    replay_call = captured_requests[1]["input"][-2]
    assert replay_call["id"] == "fc_1"
    assert replay_call["status"] == "completed"


def test_openai_request_trace_redacts_encrypted_reasoning_content():
    captured_requests = []
    events = []
    outputs_by_turn = [
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ok"}],
            }
        ]
    ]
    model_io = OpenAIModelIO(
        model="gpt-5",
        api_key="test-key",
        client_factory=_openai_factory(outputs_by_turn, captured_requests),
    )

    model_io.fetch_turn(
        SimpleNamespace(
            messages=[
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "encrypted_content": "do-not-log-this",
                }
            ],
            payload={"store": False},
            response_format=None,
            callback=events.append,
            verbose=False,
            run_id="trace-run",
            iteration=0,
            toolkit=Toolkit(),
            emit_stream=False,
            previous_response_id=None,
            openai_text_format=None,
        )
    )

    request_event = next(event for event in events if event["type"] == "request_messages")
    traced_value = request_event["messages"][0]["encrypted_content"]
    assert "do-not-log-this" not in traced_value
    assert "redacted encrypted_content" in traced_value
    assert captured_requests[0]["input"][0]["encrypted_content"] == "do-not-log-this"


def test_openai_reasoning_checkpoint_cold_restart_does_not_reexecute_tool(tmp_path):
    tool_calls = {"count": 0}

    def demo_tool(x: int):
        tool_calls["count"] += 1
        return {"value": x + 1}

    first_requests = []
    first_outputs = [
        [
            {
                "type": "reasoning",
                "id": "rs_cold",
                "encrypted_content": "cold-ciphertext",
                "summary": [],
            },
            {
                "type": "function_call",
                "id": "fc_cold",
                "call_id": "call_cold",
                "name": "demo_tool",
                "arguments": "{\"x\":2}",
                "status": "completed",
            },
        ]
    ]
    first_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
    first_agent = Agent(
        name="openai-cold-replay",
        provider="openai",
        model="gpt-5",
        modules=(
            ToolsModule(tools=(demo_tool,)),
            MemoryModule(memory=first_memory),
        ),
        model_io_factory=lambda spec, context: OpenAIModelIO(
            model="gpt-5",
            api_key="test-key",
            client_factory=_openai_factory(first_outputs, first_requests),
        ),
    )

    stopped = first_agent.run(
        "call the tool",
        payload={"store": False, "reasoning": {"effort": "medium"}},
        session_id="openai-cold-session",
        max_iterations=1,
    )

    assert stopped.status == "max_iterations"
    assert tool_calls["count"] == 1
    checkpoint = first_memory.store.load("openai-cold-session")["execution_checkpoint"]
    assert checkpoint["replay_frame"]["complete"] is True
    assert [item.get("type") for item in checkpoint["replay_frame"]["items"]][-3:] == [
        "reasoning",
        "function_call",
        "function_call_output",
    ]

    mismatched_requests = []

    def demo_tool_with_changed_schema(x: int, note: str = ""):
        del note
        tool_calls["count"] += 1
        return {"value": x + 1}

    mismatched_agent = Agent(
        name="openai-cold-replay",
        provider="openai",
        model="gpt-5",
        modules=(
            ToolsModule(tools=(demo_tool_with_changed_schema,)),
            MemoryModule(
                memory=MemoryManager(store=JsonFileSessionStore(tmp_path))
            ),
        ),
        model_io_factory=lambda spec, context: OpenAIModelIO(
            model="gpt-5",
            api_key="test-key",
            client_factory=_openai_factory(
                [
                    [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "must not run"}],
                        }
                    ]
                ],
                mismatched_requests,
            ),
        ),
    )
    with pytest.raises(ProviderReplayFrameError, match="tool schema"):
        mismatched_agent.run(
            [],
            payload={"store": False, "reasoning": {"effort": "medium"}},
            session_id="openai-cold-session",
            max_iterations=1,
        )
    assert mismatched_requests == []
    assert tool_calls["count"] == 1

    resumed_requests = []
    resumed_outputs = [
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
                "status": "completed",
            }
        ]
    ]
    resumed_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
    resumed_agent = Agent(
        name="openai-cold-replay",
        provider="openai",
        model="gpt-5",
        modules=(
            ToolsModule(tools=(demo_tool,)),
            MemoryModule(memory=resumed_memory),
        ),
        model_io_factory=lambda spec, context: OpenAIModelIO(
            model="gpt-5",
            api_key="test-key",
            client_factory=_openai_factory(resumed_outputs, resumed_requests),
        ),
    )

    completed = resumed_agent.run(
        [],
        payload={"store": False, "reasoning": {"effort": "medium"}},
        session_id="openai-cold-session",
        max_iterations=1,
    )

    assert completed.status == "completed"
    assert tool_calls["count"] == 1
    assert "previous_response_id" not in resumed_requests[0]
    assert resumed_requests[0]["input"][-3]["encrypted_content"] == "cold-ciphertext"
    assert "execution_checkpoint" not in resumed_memory.store.load("openai-cold-session")


def test_anthropic_thinking_signature_is_preserved_only_in_provider_replay():
    captured_requests = []
    events_by_turn = [_anthropic_thinking_tool_events()]
    model_io = AnthropicModelIO(
        model="claude-sonnet-4",
        api_key="test-key",
        client_factory=_anthropic_factory(events_by_turn, captured_requests),
    )

    turn = model_io.fetch_turn(
        ModelTurnRequest(
            messages=[{"role": "user", "content": "call the tool"}],
            toolkit=Toolkit(),
        )
    )

    assert turn.reasoning_items == [{"type": "thinking", "text": "plan"}]
    assert turn.assistant_messages == [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "demo_tool",
                    "input": {"x": 2},
                }
            ],
        }
    ]
    raw_assistant = turn.provider_replay_frame["items"][-1]
    assert [block["type"] for block in raw_assistant["content"]] == [
        "thinking",
        "tool_use",
    ]
    assert raw_assistant["content"][0] == {
        "type": "thinking",
        "thinking": "plan",
        "signature": "opaque-signature",
    }


def test_anthropic_missing_thinking_signature_fails_before_tool_execution():
    model_io = AnthropicModelIO(
        model="claude-sonnet-4",
        api_key="test-key",
        client_factory=_anthropic_factory(
            [_anthropic_thinking_tool_events(signature="")],
            [],
        ),
    )

    with pytest.raises(ProviderReplayFrameError, match="missing the replay signature"):
        model_io.fetch_turn(
            ModelTurnRequest(
                messages=[{"role": "user", "content": "call the tool"}],
                toolkit=Toolkit(),
            )
        )


def test_anthropic_two_turn_tool_loop_replays_signature_and_coalesced_result():
    captured_requests = []
    events_by_turn = [
        _anthropic_thinking_tool_events(),
        [
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(type="text_delta", text="done"),
            ),
            SimpleNamespace(type="content_block_stop", index=0),
        ],
    ]
    model_io = AnthropicModelIO(
        model="claude-sonnet-4",
        api_key="test-key",
        client_factory=_anthropic_factory(events_by_turn, captured_requests),
    )
    toolkit = Toolkit()
    tool_calls = {"count": 0}

    def demo_tool(x: int):
        tool_calls["count"] += 1
        return {"value": x + 1}

    toolkit.register(demo_tool, name="demo_tool")
    result = build_runtime_loop(model_io=model_io).run(
        [{"role": "user", "content": "call the tool"}],
        provider="anthropic",
        model="claude-sonnet-4",
        toolkit=toolkit,
        max_iterations=3,
    )

    assert result.status == "completed"
    assert tool_calls["count"] == 1
    assert len(captured_requests) == 2
    raw_assistant = captured_requests[1]["messages"][-2]
    assert raw_assistant["role"] == "assistant"
    assert raw_assistant["content"][0]["signature"] == "opaque-signature"
    tool_result_message = captured_requests[1]["messages"][-1]
    assert tool_result_message["role"] == "user"
    assert tool_result_message["content"][0]["type"] == "tool_result"
    assert "opaque-signature" not in json.dumps(result.messages)


def test_anthropic_parallel_tool_results_are_one_user_message():
    coalesced = coalesce_provider_tool_result_messages(
        "anthropic",
        [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "one"}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_2", "content": "two"}
                ],
            },
        ],
    )

    assert len(coalesced) == 1
    assert [block["tool_use_id"] for block in coalesced[0]["content"]] == [
        "toolu_1",
        "toolu_2",
    ]


def test_anthropic_thinking_checkpoint_cold_restart_preserves_signature(tmp_path):
    tool_calls = {"count": 0}

    def demo_tool(x: int):
        tool_calls["count"] += 1
        return {"value": x + 1}

    first_requests = []
    first_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
    first_agent = Agent(
        name="anthropic-cold-replay",
        provider="anthropic",
        model="claude-sonnet-4",
        modules=(
            ToolsModule(tools=(demo_tool,)),
            MemoryModule(memory=first_memory),
        ),
        model_io_factory=lambda spec, context: AnthropicModelIO(
            model="claude-sonnet-4",
            api_key="test-key",
            client_factory=_anthropic_factory(
                [_anthropic_thinking_tool_events()],
                first_requests,
            ),
        ),
    )

    stopped = first_agent.run(
        "call the tool",
        session_id="anthropic-cold-session",
        max_iterations=1,
    )

    assert stopped.status == "max_iterations"
    assert tool_calls["count"] == 1
    checkpoint = first_memory.store.load("anthropic-cold-session")[
        "execution_checkpoint"
    ]
    assert checkpoint["replay_frame"]["complete"] is True
    replay_assistant = checkpoint["replay_frame"]["items"][-2]
    assert replay_assistant["content"][0]["signature"] == "opaque-signature"

    resumed_requests = []
    resumed_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
    resumed_agent = Agent(
        name="anthropic-cold-replay",
        provider="anthropic",
        model="claude-sonnet-4",
        modules=(
            ToolsModule(tools=(demo_tool,)),
            MemoryModule(memory=resumed_memory),
        ),
        model_io_factory=lambda spec, context: AnthropicModelIO(
            model="claude-sonnet-4",
            api_key="test-key",
            client_factory=_anthropic_factory(
                [
                    [
                        SimpleNamespace(
                            type="content_block_delta",
                            index=0,
                            delta=SimpleNamespace(type="text_delta", text="done"),
                        ),
                        SimpleNamespace(type="content_block_stop", index=0),
                    ]
                ],
                resumed_requests,
            ),
        ),
    )

    completed = resumed_agent.run(
        [],
        session_id="anthropic-cold-session",
        max_iterations=1,
    )

    assert completed.status == "completed"
    assert tool_calls["count"] == 1
    replayed_assistant = resumed_requests[0]["messages"][-2]
    assert replayed_assistant["content"][0]["signature"] == "opaque-signature"
    assert "execution_checkpoint" not in resumed_memory.store.load(
        "anthropic-cold-session"
    )


def test_ollama_thinking_tool_loop_replays_thinking_without_semantic_leak():
    captured_requests = []
    lines_by_turn = [
        [
            json.dumps(
                {
                    "message": {"role": "assistant", "thinking": "plan", "content": ""},
                    "done": False,
                }
            ),
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_ollama",
                                "function": {
                                    "name": "demo_tool",
                                    "arguments": {"x": 3},
                                },
                            }
                        ],
                    },
                    "done": False,
                    "prompt_eval_count": 2,
                    "eval_count": 3,
                }
            ),
        ],
        [
            json.dumps(
                {
                    "message": {"role": "assistant", "content": "done"},
                    "done": True,
                    "prompt_eval_count": 3,
                    "eval_count": 1,
                }
            )
        ],
    ]
    model_io = OllamaModelIO(
        model="qwen3",
        stream_factory=_ollama_stream_factory(lines_by_turn, captured_requests),
    )
    toolkit = Toolkit()
    calls = {"count": 0}

    def demo_tool(x: int):
        calls["count"] += 1
        return {"value": x + 1}

    toolkit.register(demo_tool, name="demo_tool")
    result = build_runtime_loop(model_io=model_io).run(
        [{"role": "user", "content": "call the tool"}],
        provider="ollama",
        model="qwen3",
        toolkit=toolkit,
        max_iterations=3,
    )

    assert result.status == "completed"
    assert calls["count"] == 1
    replayed_assistant = captured_requests[1]["json"]["messages"][-2]
    assert replayed_assistant["thinking"] == "plan"
    assert replayed_assistant["tool_calls"][0]["id"] == "call_ollama"
    assert "plan" not in json.dumps(result.messages)


def test_openai_reasoning_human_suspend_cold_resume_uses_checkpoint_frame(tmp_path):
    first_requests = []
    ask_arguments = json.dumps(
        {
            "title": "Choose stack",
            "question": "Which stack?",
            "selection_mode": "single",
            "options": [
                {"label": "React", "value": "react"},
                {"label": "Vue", "value": "vue"},
            ],
        }
    )
    first_outputs = [
        [
            {
                "type": "reasoning",
                "id": "rs_human",
                "encrypted_content": "human-ciphertext",
                "summary": [],
            },
            {
                "type": "function_call",
                "id": "fc_human",
                "call_id": "call_human",
                "name": ASK_USER_QUESTION_TOOL_NAME,
                "arguments": ask_arguments,
                "status": "completed",
            },
        ]
    ]
    first_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
    first_agent = Agent(
        name="openai-human-cold-replay",
        provider="openai",
        model="gpt-5",
        modules=(InteractionModule(), MemoryModule(memory=first_memory)),
        model_io_factory=lambda spec, context: OpenAIModelIO(
            model="gpt-5",
            api_key="test-key",
            client_factory=_openai_factory(first_outputs, first_requests),
        ),
    )

    suspended = first_agent.run(
        "ask me",
        payload={"store": False, "reasoning": {"effort": "medium"}},
        session_id="openai-human-cold-session",
    )

    assert suspended.status == "awaiting_human_input"
    checkpoint = first_memory.store.load("openai-human-cold-session")[
        "execution_checkpoint"
    ]
    assert checkpoint["status"] == "awaiting_human_input"
    assert checkpoint["replay_frame"]["items"][-2]["encrypted_content"] == (
        "human-ciphertext"
    )

    resumed_requests = []
    resumed_outputs = [
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "React it is"}],
                "status": "completed",
            }
        ]
    ]
    resumed_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
    resumed_agent = Agent(
        name="openai-human-cold-replay",
        provider="openai",
        model="gpt-5",
        modules=(InteractionModule(), MemoryModule(memory=resumed_memory)),
        model_io_factory=lambda spec, context: OpenAIModelIO(
            model="gpt-5",
            api_key="test-key",
            client_factory=_openai_factory(resumed_outputs, resumed_requests),
        ),
    )

    completed = resumed_agent.resume_human_input(
        response={"request_id": "call_human", "selected_values": ["react"]},
        payload={"store": False, "reasoning": {"effort": "medium"}},
        session_id="openai-human-cold-session",
    )

    assert completed.status == "completed"
    assert "previous_response_id" not in resumed_requests[0]
    assert [item.get("type") for item in resumed_requests[0]["input"]][-3:] == [
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert "human-ciphertext" not in json.dumps(completed.messages)
    assert "execution_checkpoint" not in resumed_memory.store.load(
        "openai-human-cold-session"
    )
