import json
import importlib
from pathlib import Path

import httpx
import openai
import pytest

from miso.memory import MemoryManager
from miso.runtime import Broth
from miso.schemas import ResponseFormat
from miso.tools import tool, Toolkit
from miso.toolkits import WorkspaceToolkit
from miso.runtime import ProviderTurnResult, ToolCall
from miso.workspace import build_pin_record, save_workspace_pins


def test_observation_injected_into_last_tool_message_and_callback_events():
    agent = Broth()
    agent.provider = "ollama"

    observed_tool = tool(name="need_observe", func=lambda: {"value": 1}, observe=True, parameters=[])
    plain_tool = tool(name="plain_tool", func=lambda: {"value": 2}, observe=False, parameters=[])
    agent.toolkit = Toolkit({
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
                input_tokens=1,
                output_tokens=3,
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
                input_tokens=6,
                output_tokens=4,
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            consumed_tokens=7,
            input_tokens=5,
            output_tokens=2,
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
    assert bundle["input_tokens"] == 12
    assert bundle["output_tokens"] == 9

    last_tool_payload = json.loads(tool_messages[-1]["content"])
    assert last_tool_payload["observation"] == "检查通过，继续下一步。"

    event_types = [evt["type"] for evt in events]
    assert event_types.count("tool_call") == 2
    assert event_types.count("tool_result") == 2
    assert "observation" in event_types
    assert "final_message" in event_types
    assert event_types.count("response_received") == 2
    response_events = [evt for evt in events if evt["type"] == "response_received"]
    assert response_events[0]["has_tool_calls"] is True
    assert response_events[0]["bundle"]["model"] == "gpt-5"
    assert response_events[0]["bundle"]["consumed_tokens"] == 10
    assert response_events[0]["bundle"]["input_tokens"] == 6
    assert response_events[0]["bundle"]["output_tokens"] == 4
    assert response_events[1]["has_tool_calls"] is False
    assert response_events[1]["bundle"]["model"] == "gpt-5"
    assert response_events[1]["bundle"]["consumed_tokens"] == 21
    assert response_events[1]["bundle"]["input_tokens"] == 12
    assert response_events[1]["bundle"]["output_tokens"] == 9
    run_completed = next(evt for evt in events if evt["type"] == "run_completed")
    assert run_completed["bundle"] == bundle


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

    fmt = ResponseFormat(
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
        payload={"store": True},
        max_iterations=3,
    )

    assert seen_previous_ids == ["prev_0", "resp_1"]
    assert isinstance(seen_messages[1], list)
    assert len(seen_messages[1]) == 1
    assert seen_messages[1][0].get("type") == "function_call_output"
    assert agent.last_response_id == "resp_2"
    assert [msg for msg in messages if msg.get("role") == "assistant"][-1]["content"] == "done"
    assert bundle["consumed_tokens"] == 18


def test_openai_run_drops_previous_response_id_when_store_disabled():
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
        payload={"store": False},
        max_iterations=3,
    )

    assert seen_previous_ids == [None, None]
    assert isinstance(seen_messages[1], list)
    assert len(seen_messages[1]) > 1
    assert any(item.get("type") == "function_call_output" for item in seen_messages[1])
    assert agent.last_response_id == "resp_2"
    assert [msg for msg in messages if msg.get("role") == "assistant"][-1]["content"] == "done"
    assert bundle["consumed_tokens"] == 18


def test_openai_run_falls_back_when_previous_response_not_found():
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

        if state["turn"] == 2:
            response = httpx.Response(
                400,
                request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
            )
            raise openai.BadRequestError(
                message=(
                    "Error code: 400 - {'error': {'message': "
                    "\"Previous response with id 'resp_1' not found.\", "
                    "'type': 'invalid_request_error', 'param': 'previous_response_id', "
                    "'code': 'previous_response_not_found'}}"
                ),
                response=response,
                body={
                    "error": {
                        "message": "Previous response with id 'resp_1' not found.",
                        "type": "invalid_request_error",
                        "param": "previous_response_id",
                        "code": "previous_response_not_found",
                    }
                },
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_2",
            consumed_tokens=7,
        )

    agent._fetch_once = fake_fetch_once

    events = []
    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "start"}],
        previous_response_id="prev_0",
        payload={"store": True},
        callback=events.append,
        max_iterations=3,
    )

    assert seen_previous_ids == ["prev_0", "resp_1", None]
    assert isinstance(seen_messages[2], list)
    assert len(seen_messages[2]) > 1
    assert any(item.get("type") == "function_call_output" for item in seen_messages[2])
    fallback_events = [evt for evt in events if evt["type"] == "previous_response_fallback"]
    assert len(fallback_events) == 1
    assert fallback_events[0]["previous_response_id"] == "resp_1"
    assert agent.last_response_id == "resp_2"
    assert [msg for msg in messages if msg.get("role") == "assistant"][-1]["content"] == "done"
    assert bundle["consumed_tokens"] == 18


def test_run_memory_hooks_tolerate_legacy_monkey_patches_without_memory_namespace():
    manager = MemoryManager()
    original_prepare = manager.prepare_messages
    original_commit = manager.commit_messages

    def legacy_prepare_messages(session_id, incoming, *, max_context_window_tokens, model, summary_generator=None):
        return original_prepare(
            session_id,
            incoming,
            max_context_window_tokens=max_context_window_tokens,
            model=model,
            summary_generator=summary_generator,
        )

    def legacy_commit_messages(session_id, full_conversation, *, model=None, long_term_extractor=None):
        return original_commit(
            session_id,
            full_conversation,
            model=model,
            long_term_extractor=long_term_extractor,
        )

    manager.prepare_messages = legacy_prepare_messages  # type: ignore[method-assign]
    manager.commit_messages = legacy_commit_messages  # type: ignore[method-assign]

    agent = Broth(memory_manager=manager)
    agent.provider = "openai"

    def fake_fetch_once(**kwargs):
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_legacy_memory",
            consumed_tokens=5,
        )

    agent._fetch_once = fake_fetch_once

    events = []
    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "hello"}],
        session_id="legacy-memory-session",
        memory_namespace="shared-user",
        callback=events.append,
        max_iterations=1,
    )

    prepare_events = [event for event in events if event["type"] == "memory_prepare"]
    commit_events = [event for event in events if event["type"] == "memory_commit"]

    assert len(prepare_events) == 1
    assert prepare_events[0]["applied"] is True
    assert len(commit_events) == 1
    assert commit_events[0]["applied"] is True
    assert [msg for msg in messages if msg.get("role") == "assistant"][-1]["content"] == "done"
    assert bundle["consumed_tokens"] == 5


def test_workspace_pin_messages_inject_after_existing_system_messages_when_memory_store_is_reused(tmp_path):
    manager = MemoryManager()
    agent = Broth(memory_manager=manager)

    file_path = tmp_path / "demo.py"
    file_path.write_text(
        "def target():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    pin = build_pin_record(
        path=file_path.resolve(),
        lines=file_path.read_text(encoding="utf-8").splitlines(keepends=True),
        start=1,
        end=2,
        start_with="def target():",
    )
    save_workspace_pins(manager.store, "pin-order-session", {}, [pin])

    injected = agent._inject_workspace_pin_messages(
        messages=[
            {"role": "system", "content": "Base instructions"},
            {"role": "system", "content": "[MEMORY SUMMARY]\nrecent facts"},
            {"role": "user", "content": "latest question"},
        ],
        session_id="pin-order-session",
    )

    assert [msg["content"] for msg in injected[:4]] == [
        "Base instructions",
        "[MEMORY SUMMARY]\nrecent facts",
        injected[2]["content"],
        injected[3]["content"],
    ]
    assert injected[2]["content"].startswith("[PINNED SUMMARY]")
    assert injected[3]["content"].startswith("[PINNED CONTENT]")
    assert injected[4] == {"role": "user", "content": "latest question"}


def test_broth_workspace_pins_persist_across_runs_without_memory_manager(tmp_path):
    agent = Broth()
    agent.provider = "ollama"
    tk = WorkspaceToolkit(workspace_root=tmp_path)
    agent.add_toolkit(tk)

    file_path = Path(tmp_path) / "demo.py"
    file_path.write_text(
        "before\n"
        "def target():\n"
        "    return 1\n"
        "after\n",
        encoding="utf-8",
    )

    first_run_state = {"turn": 0}

    def first_fetch_once(**kwargs):
        del kwargs
        first_run_state["turn"] += 1
        if first_run_state["turn"] == 1:
            return ProviderTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "pin_file_context",
                                    "arguments": json.dumps(
                                        {
                                            "path": "demo.py",
                                            "start": 2,
                                            "end": 3,
                                            "start_with": "def target():",
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="call_1",
                        name="pin_file_context",
                        arguments={
                            "path": "demo.py",
                            "start": 2,
                            "end": 3,
                            "start_with": "def target():",
                        },
                    )
                ],
                final_text="",
                consumed_tokens=8,
            )
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "pinned"}],
            tool_calls=[],
            final_text="pinned",
            consumed_tokens=4,
        )

    agent._fetch_once = first_fetch_once
    agent.run(
        messages=[
            {"role": "system", "content": "Base instructions"},
            {"role": "user", "content": "pin that function"},
        ],
        session_id="no-memory-pin-session",
        max_iterations=2,
    )

    file_path.write_text(
        "intro\n"
        "before\n"
        "def target():\n"
        "    return 2\n"
        "after\n",
        encoding="utf-8",
    )

    captured_requests = []

    def second_fetch_once(**kwargs):
        captured_requests.append(kwargs["messages"])
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            consumed_tokens=5,
        )

    agent._fetch_once = second_fetch_once
    agent.run(
        messages=[
            {"role": "system", "content": "Base instructions"},
            {"role": "user", "content": "use the pinned context"},
        ],
        session_id="no-memory-pin-session",
        max_iterations=1,
    )

    request_messages = captured_requests[0]
    assert request_messages[0] == {"role": "system", "content": "Base instructions"}
    assert request_messages[1]["content"].startswith("[PINNED SUMMARY]")
    assert "current=lines=3-4" in request_messages[1]["content"]
    assert request_messages[2]["content"].startswith("[PINNED CONTENT]")
    assert "return 2" in request_messages[2]["content"]
    assert request_messages[3] == {"role": "user", "content": "use the pinned context"}


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

    broth_module = importlib.import_module("miso.runtime.providers")
    monkeypatch.setattr(broth_module, "OpenAI", FakeOpenAIClient)

    turn = agent._openai_fetch_once(
        messages=[{"role": "user", "content": "hi"}],
        payload={"stream": False},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_stream",
        iteration=0,
        toolkit=Toolkit(),
        emit_stream=False,
        previous_response_id=None,
    )

    assert captured_kwargs["stream"] is True
    assert turn.final_text == "ok"


def test_openai_fetch_once_emits_request_messages_with_tool_names(monkeypatch):
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-4.1"
    agent.api_key = "test-key"

    captured_kwargs = {}
    events = []

    class FakeOpenAIStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield type("Chunk", (), {
                "type": "response.completed",
                "response": type("Resp", (), {
                    "id": "resp_tools_test",
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

    broth_module = importlib.import_module("miso.runtime.providers")
    monkeypatch.setattr(broth_module, "OpenAI", FakeOpenAIClient)

    echo_tool = tool(name="echo_tool", func=lambda: {"ok": True}, parameters=[])
    turn = agent._openai_fetch_once(
        messages=[{"role": "user", "content": "hi"}],
        payload={},
        response_format=None,
        callback=events.append,
        verbose=False,
        run_id="run_tools",
        iteration=0,
        toolkit=Toolkit({"echo_tool": echo_tool}),
        emit_stream=False,
        previous_response_id=None,
    )

    request_event = next(evt for evt in events if evt["type"] == "request_messages")
    assert request_event["tool_names"] == ["echo_tool"]
    assert captured_kwargs["tools"][0]["name"] == "echo_tool"
    assert turn.final_text == "ok"


def test_openai_fetch_once_places_response_format_under_text_config(monkeypatch):
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-4.1"
    agent.api_key = "test-key"

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
                    "id": "resp_text_format_test",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"answer":"ok"}'}],
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

    broth_module = importlib.import_module("miso.runtime.providers")
    monkeypatch.setattr(broth_module, "OpenAI", FakeOpenAIClient)

    fmt = ResponseFormat(
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

    turn = agent._openai_fetch_once(
        messages=[{"role": "user", "content": "hi"}],
        payload={},
        response_format=fmt,
        callback=None,
        verbose=False,
        run_id="run_text_format",
        iteration=0,
        toolkit=Toolkit(),
        emit_stream=False,
        previous_response_id=None,
    )

    assert "response_format" not in captured_kwargs
    assert captured_kwargs["text"]["format"] == fmt.to_openai()
    assert turn.final_text == '{"answer":"ok"}'


def test_openai_fetch_once_accepts_explicit_text_format_override(monkeypatch):
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-4.1"
    agent.api_key = "test-key"

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
                    "id": "resp_json_object_test",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"ok":true}'}],
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

    broth_module = importlib.import_module("miso.runtime.providers")
    monkeypatch.setattr(broth_module, "OpenAI", FakeOpenAIClient)

    turn = agent._openai_fetch_once(
        messages=[{"role": "user", "content": "hi"}],
        payload={},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_json_object",
        iteration=0,
        toolkit=Toolkit(),
        emit_stream=False,
        previous_response_id=None,
        openai_text_format={"type": "json_object"},
    )

    assert "response_format" not in captured_kwargs
    assert captured_kwargs["text"]["format"] == {"type": "json_object"}
    assert turn.final_text == '{"ok":true}'


def test_extract_long_term_memory_uses_json_object_for_openai():
    agent = Broth(provider="openai", model="gpt-5")
    captured_kwargs = {}

    def fake_fetch_once(**kwargs):
        captured_kwargs.update(kwargs)
        return ProviderTurnResult(
            assistant_messages=[{
                "role": "assistant",
                "content": '{"profile_patch":{"preferences":{"tone":"concise"}},"facts":[{"subtype":"fact","text":"User prefers concise answers."}]}',
            }],
            tool_calls=[],
            final_text='{"profile_patch":{"preferences":{"tone":"concise"}},"facts":[{"subtype":"fact","text":"User prefers concise answers."}]}',
        )

    agent._fetch_once = fake_fetch_once

    result = agent._extract_long_term_memory(
        previous_profile={"preferences": {"language": "en"}},
        messages=[
            {"role": "user", "content": "Be concise."},
            {"role": "assistant", "content": "Noted."},
        ],
        max_profile_chars=1200,
        max_fact_items=6,
        model="gpt-5",
    )

    assert captured_kwargs["response_format"] is None
    assert captured_kwargs["openai_text_format"] == {"type": "json_object"}
    assert result == {
        "profile_patch": {"preferences": {"tone": "concise"}},
        "facts": [{"subtype": "fact", "text": "User prefers concise answers."}],
        "episodes": [],
        "playbooks": [],
    }


def test_extract_long_term_memory_defaults_missing_keys():
    agent = Broth(provider="openai", model="gpt-5")

    def fake_fetch_once(**kwargs):
        del kwargs
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": '{"facts":[{"text":"Keep weekly digests."}]}' }],
            tool_calls=[],
            final_text='{"facts":[{"text":"Keep weekly digests."}]}',
        )

    agent._fetch_once = fake_fetch_once

    result = agent._extract_long_term_memory(
        previous_profile={},
        messages=[
            {"role": "user", "content": "Remember weekly digests."},
            {"role": "assistant", "content": "Okay."},
        ],
        max_profile_chars=1200,
        max_fact_items=6,
        model="gpt-5",
    )

    assert result == {
        "profile_patch": {},
        "facts": [{"text": "Keep weekly digests."}],
        "episodes": [],
        "playbooks": [],
    }


def test_extract_long_term_memory_rejects_invalid_json():
    agent = Broth(provider="openai", model="gpt-5")

    def fake_fetch_once(**kwargs):
        del kwargs
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "not-json"}],
            tool_calls=[],
            final_text="not-json",
        )

    agent._fetch_once = fake_fetch_once

    with pytest.raises(ValueError, match="long_term_extraction_invalid_json"):
        agent._extract_long_term_memory(
            previous_profile={},
            messages=[
                {"role": "user", "content": "Remember this."},
                {"role": "assistant", "content": "Okay."},
            ],
            max_profile_chars=1200,
            max_fact_items=6,
            model="gpt-5",
        )


def test_extract_long_term_memory_rejects_non_object_json():
    agent = Broth(provider="openai", model="gpt-5")

    def fake_fetch_once(**kwargs):
        del kwargs
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": '["not","an","object"]'}],
            tool_calls=[],
            final_text='["not","an","object"]',
        )

    agent._fetch_once = fake_fetch_once

    with pytest.raises(ValueError, match="long_term_extraction_invalid_top_level"):
        agent._extract_long_term_memory(
            previous_profile={},
            messages=[
                {"role": "user", "content": "Remember this."},
                {"role": "assistant", "content": "Okay."},
            ],
            max_profile_chars=1200,
            max_fact_items=6,
            model="gpt-5",
        )


def test_openai_fetch_once_normalizes_function_call_input_items(monkeypatch):
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-4.1"
    agent.api_key = "test-key"

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
                    "id": "resp_norm_test",
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

    broth_module = importlib.import_module("miso.runtime.providers")
    monkeypatch.setattr(broth_module, "OpenAI", FakeOpenAIClient)

    turn = agent._openai_fetch_once(
        messages=[
            {"role": "user", "content": "hi"},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "demo_tool",
                "arguments": "{}",
                "status": "completed",
            },
        ],
        payload={},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_norm",
        iteration=0,
        toolkit=Toolkit(),
        emit_stream=False,
        previous_response_id=None,
    )

    sent_item = captured_kwargs["input"][1]
    assert sent_item["type"] == "function_call"
    assert sent_item["call_id"] == "call_1"
    assert "status" not in sent_item
    assert turn.final_text == "ok"


def test_openai_fetch_once_handles_missing_completed_with_output_item_done(monkeypatch):
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-4.1"
    agent.api_key = "test-key"

    class FakeOpenAIStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield type("Chunk", (), {
                "type": "response.created",
                "response": type("Resp", (), {"id": "resp_partial"})(),
            })()
            yield type("Chunk", (), {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "demo_tool",
                    "arguments": "{}",
                    "status": "completed",
                },
            })()

    class FakeResponses:
        def create(self, **kwargs):
            return FakeOpenAIStream()

    class FakeOpenAIClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.responses = FakeResponses()

    broth_module = importlib.import_module("miso.runtime.providers")
    monkeypatch.setattr(broth_module, "OpenAI", FakeOpenAIClient)

    turn = agent._openai_fetch_once(
        messages=[{"role": "user", "content": "hi"}],
        payload={},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_partial",
        iteration=0,
        toolkit=Toolkit(),
        emit_stream=False,
        previous_response_id=None,
    )

    assert turn.response_id == "resp_partial"
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "demo_tool"
    assert turn.tool_calls[0].call_id == "call_1"
    assert turn.final_text == ""


def test_build_observation_messages_filters_orphan_anthropic_tool_results():
    agent = Broth()
    agent.provider = "anthropic"

    full_messages = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_old", "content": "{}"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_new", "name": "demo_tool", "input": {}}],
        },
    ]
    tool_messages = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_new", "content": "{\"ok\":1}"}],
        }
    ]

    observe_messages = agent._build_observation_messages(full_messages, tool_messages)
    chat_messages = observe_messages[1:]  # skip system

    assert not any(
        isinstance(msg, dict)
        and msg.get("role") == "user"
        and any(
            isinstance(block, dict)
            and block.get("type") == "tool_result"
            and block.get("tool_use_id") == "toolu_old"
            for block in (msg.get("content") if isinstance(msg.get("content"), list) else [])
        )
        for msg in chat_messages
    )

    idx_tool_use = next(
        idx
        for idx, msg in enumerate(chat_messages)
        if isinstance(msg, dict)
        and msg.get("role") == "assistant"
        and any(
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("id") == "toolu_new"
            for block in (msg.get("content") if isinstance(msg.get("content"), list) else [])
        )
    )
    idx_tool_result = next(
        idx
        for idx, msg in enumerate(chat_messages)
        if isinstance(msg, dict)
        and msg.get("role") == "user"
        and any(
            isinstance(block, dict)
            and block.get("type") == "tool_result"
            and block.get("tool_use_id") == "toolu_new"
            for block in (msg.get("content") if isinstance(msg.get("content"), list) else [])
        )
    )
    assert idx_tool_use < idx_tool_result


def test_observe_tool_batch_skips_anthropic_tool_result_validation_errors():
    agent = Broth()
    agent.provider = "anthropic"

    def fake_fetch_once(**kwargs):
        raise ValueError("tool_result blocks must have a corresponding tool_use block")

    agent._fetch_once = fake_fetch_once
    observation, usage = agent._observe_tool_batch(
        full_messages=[],
        tool_messages=[],
        payload={},
    )

    assert observation == ""
    assert usage.consumed_tokens == 0
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0


def test_extract_openai_message_text_includes_refusal_blocks():
    agent = Broth()
    text = agent._extract_openai_message_text(
        {
            "type": "message",
            "content": [
                {"type": "refusal", "refusal": "I can’t help with that exact request."},
            ],
        }
    )
    assert text == "I can’t help with that exact request."


def test_extract_openai_token_usage_prefers_total_tokens():
    agent = Broth()

    usage = agent._extract_openai_token_usage(
        {"total_tokens": 13, "input_tokens": 8, "output_tokens": 5}
    )

    assert usage.consumed_tokens == 13
    assert usage.input_tokens == 8
    assert usage.output_tokens == 5


def test_extract_openai_token_usage_falls_back_to_input_plus_output():
    agent = Broth()

    usage = agent._extract_openai_token_usage(
        {"input_tokens": 8, "output_tokens": 5}
    )

    assert usage.consumed_tokens == 13
    assert usage.input_tokens == 8
    assert usage.output_tokens == 5


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
                "prompt_eval_count": 4,
                "eval_count": 5,
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

    broth_module = importlib.import_module("miso.runtime.providers")
    monkeypatch.setattr(broth_module.httpx, "stream", fake_httpx_stream)

    turn = agent._ollama_fetch_once(
        messages=[{"role": "user", "content": "hi"}],
        payload={},
        response_format=None,
        callback=None,
        verbose=False,
        run_id="run_stream",
        iteration=0,
        toolkit=Toolkit(),
        emit_stream=False,
    )

    assert captured_request["json"]["stream"] is True
    assert turn.final_text == "ok"
    assert turn.consumed_tokens == 9
    assert turn.input_tokens == 4
    assert turn.output_tokens == 5


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

    from miso.tools import tool, Toolkit
    t1 = tool(name="t1", func=lambda: "ok", parameters=[])
    a.toolkit = Toolkit({"t1": t1})
    a._fetch_once = fake_fetch_once

    _, bundle = a.run([{"role": "user", "content": "hello"}])
    assert bundle["model"] == "deepseek-r1:14b"
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
    assert blocks[2]["file_data"] == "data:application/pdf;base64,JVBERi0xLjQK"
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
