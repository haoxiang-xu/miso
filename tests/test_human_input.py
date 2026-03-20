import json

import pytest

from miso import Agent, MemoryManager, broth as Broth, ask_user_toolkit, tool, toolkit
from miso.broth import ProviderTurnResult, ToolCall
from miso.human_input import ASK_USER_QUESTION_TOOL_NAME


def _selector_args(**overrides):
    payload = {
        "title": "Pick a framework",
        "question": "Which framework should we use?",
        "selection_mode": "single",
        "options": [
            {"label": "React", "value": "react"},
            {"label": "Vue", "value": "vue"},
        ],
        "allow_other": False,
    }
    payload.update(overrides)
    return payload


def test_ask_user_toolkit_is_explicitly_opt_in():
    agent = Broth()
    agent.provider = "openai"

    seen_tool_names = []

    def fake_fetch_once(**kwargs):
        seen_tool_names.append([item["name"] for item in kwargs["toolkit"].to_json()])
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_1",
            consumed_tokens=3,
        )

    agent._fetch_once = fake_fetch_once

    _, bundle = agent.run(
        messages=[{"role": "user", "content": "hello"}],
        max_iterations=1,
    )

    assert ASK_USER_QUESTION_TOOL_NAME not in seen_tool_names[0]
    assert bundle["status"] == "completed"


def test_ask_user_toolkit_exposes_ask_user_question_when_mounted():
    agent = Broth()
    agent.provider = "openai"
    agent.toolkit = ask_user_toolkit()

    seen_tool_names = []

    def fake_fetch_once(**kwargs):
        seen_tool_names.append([item["name"] for item in kwargs["toolkit"].to_json()])
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_1",
            consumed_tokens=3,
        )

    agent._fetch_once = fake_fetch_once

    _, bundle = agent.run(
        messages=[{"role": "user", "content": "hello"}],
        max_iterations=1,
    )

    assert ASK_USER_QUESTION_TOOL_NAME in seen_tool_names[0]
    assert bundle["status"] == "completed"


def test_ask_user_question_description_encourages_asking_when_multiple_paths_exist():
    tk = ask_user_toolkit()

    tool_json = tk.to_json()[0]
    description = tool_json["description"]

    assert "Strongly prefer this" in description
    assert "multiple plausible approaches" in description
    assert "ask the user instead of silently guessing" in description


def test_run_returns_awaiting_human_input_and_emits_request_event():
    agent = Broth()
    agent.provider = "openai"
    agent.toolkit = ask_user_toolkit()

    def fake_fetch_once(**kwargs):
        return ProviderTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": ASK_USER_QUESTION_TOOL_NAME,
                    "arguments": json.dumps(_selector_args()),
                }
            ],
            tool_calls=[
                ToolCall(
                    call_id="call_1",
                    name=ASK_USER_QUESTION_TOOL_NAME,
                    arguments=json.dumps(_selector_args()),
                )
            ],
            final_text="",
            response_id="resp_1",
            consumed_tokens=11,
        )

    agent._fetch_once = fake_fetch_once

    events = []
    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "help me choose"}],
        previous_response_id="prev_0",
        payload={"store": True},
        callback=events.append,
        max_iterations=3,
    )

    assert bundle["status"] == "awaiting_human_input"
    assert bundle["consumed_tokens"] == 11
    assert bundle["human_input_request"]["request_id"] == "call_1"
    assert bundle["human_input_request"]["selection_mode"] == "single"
    assert bundle["continuation"]["previous_response_id"] == "resp_1"
    assert bundle["continuation"]["iteration"] == 1
    assert messages[-1]["type"] == "function_call"
    assert not any(event["type"] == "tool_result" for event in events)
    human_input_event = next(event for event in events if event["type"] == "human_input_requested")
    assert human_input_event["request_id"] == "call_1"
    assert human_input_event["title"] == "Pick a framework"
    assert human_input_event["question"] == "Which framework should we use?"
    assert all(event["type"] != "run_completed" for event in events)


def test_run_accepts_legacy_execute_tool_calls_tuple():
    agent = Broth()
    agent.provider = "openai"

    state = {"turn": 0}
    tool_call = ToolCall(
        call_id="call_1",
        name="get_weather",
        arguments={"city": "SF"},
    )
    tool_message = agent._build_tool_message(
        tool_call=tool_call,
        tool_result={"temp": 72},
    )

    def fake_fetch_once(**kwargs):
        state["turn"] += 1
        if state["turn"] == 1:
            return ProviderTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "get_weather",
                        "arguments": json.dumps({"city": "SF"}),
                    }
                ],
                tool_calls=[tool_call],
                final_text="",
                response_id="resp_1",
                consumed_tokens=5,
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "72F"}],
            tool_calls=[],
            final_text="72F",
            response_id="resp_2",
            consumed_tokens=3,
        )

    agent._fetch_once = fake_fetch_once
    agent._execute_tool_calls = lambda **kwargs: ([tool_message], False)

    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "weather?"}],
        max_iterations=3,
    )

    assert bundle["status"] == "completed"
    assert bundle["consumed_tokens"] == 8
    assert messages[-1]["content"] == "72F"


def test_run_accepts_legacy_execute_tool_calls_human_input_tuple():
    agent = Broth()
    agent.provider = "openai"
    agent.toolkit = ask_user_toolkit()

    def fake_fetch_once(**kwargs):
        return ProviderTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": ASK_USER_QUESTION_TOOL_NAME,
                    "arguments": json.dumps(_selector_args()),
                }
            ],
            tool_calls=[
                ToolCall(
                    call_id="call_1",
                    name=ASK_USER_QUESTION_TOOL_NAME,
                    arguments=json.dumps(_selector_args()),
                )
            ],
            final_text="",
            response_id="resp_1",
            consumed_tokens=11,
        )

    legacy_request = {
        "request_id": "call_1",
        "kind": "selector",
        "title": "Pick a framework",
        "question": "Which framework should we use?",
        "selection_mode": "single",
        "options": [
            {"label": "React", "value": "react"},
            {"label": "Vue", "value": "vue"},
        ],
        "allow_other": False,
        "other_label": "Other",
        "other_placeholder": "",
        "min_selected": 1,
        "max_selected": 1,
    }

    agent._fetch_once = fake_fetch_once
    agent._execute_tool_calls = lambda **kwargs: ([], False, True, legacy_request)

    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "help me choose"}],
        previous_response_id="prev_0",
        payload={"store": True},
        max_iterations=3,
    )

    assert bundle["status"] == "awaiting_human_input"
    assert bundle["human_input_request"]["request_id"] == "call_1"
    assert bundle["continuation"]["previous_response_id"] == "resp_1"
    assert messages[-1]["type"] == "function_call"


def test_resume_human_input_openai_uses_previous_response_id_and_function_call_output():
    agent = Broth()
    agent.provider = "openai"
    agent.toolkit = ask_user_toolkit()

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
                        "name": "ask_user_question",
                        "arguments": json.dumps(_selector_args()),
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="call_1",
                        name="ask_user_question",
                        arguments=json.dumps(_selector_args()),
                    )
                ],
                final_text="",
                response_id="resp_1",
                consumed_tokens=11,
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "React selected"}],
            tool_calls=[],
            final_text="React selected",
            response_id="resp_2",
            consumed_tokens=7,
        )

    agent._fetch_once = fake_fetch_once

    suspended_messages, suspended_bundle = agent.run(
        messages=[{"role": "user", "content": "help me choose"}],
        previous_response_id="prev_0",
        payload={"store": True},
        max_iterations=3,
    )
    resumed_messages, resumed_bundle = agent.resume_human_input(
        conversation=suspended_messages,
        continuation=suspended_bundle["continuation"],
        response={
            "request_id": "call_1",
            "selected_values": ["react"],
        },
    )

    assert seen_previous_ids == ["prev_0", "resp_1"]
    assert len(seen_messages[1]) == 1
    assert seen_messages[1][0]["type"] == "function_call_output"
    assert json.loads(seen_messages[1][0]["output"]) == {
        "submitted": True,
        "selected_values": ["react"],
        "other_text": None,
    }
    assert resumed_bundle["status"] == "completed"
    assert resumed_bundle["consumed_tokens"] == 18
    assert resumed_messages[-1]["content"] == "React selected"


def test_resume_human_input_non_openai_uses_provider_native_tool_result():
    agent = Broth()
    agent.provider = "ollama"
    agent.toolkit = ask_user_toolkit()

    seen_messages = []
    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        seen_messages.append(kwargs.get("messages"))
        state["turn"] += 1

        if state["turn"] == 1:
            return ProviderTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "ask_user_question",
                                    "arguments": _selector_args(selection_mode="multiple", allow_other=True),
                                },
                            }
                        ],
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="call_1",
                        name="ask_user_question",
                        arguments=_selector_args(selection_mode="multiple", allow_other=True),
                    )
                ],
                final_text="",
                consumed_tokens=9,
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            consumed_tokens=4,
        )

    agent._fetch_once = fake_fetch_once

    suspended_messages, suspended_bundle = agent.run(
        messages=[{"role": "user", "content": "help me choose"}],
        max_iterations=3,
    )
    resumed_messages, resumed_bundle = agent.resume_human_input(
        conversation=suspended_messages,
        continuation=suspended_bundle["continuation"],
        response={
            "request_id": "call_1",
            "selected_values": ["react", "__other__"],
            "other_text": "SolidJS",
        },
    )

    tool_message = next(msg for msg in seen_messages[1] if msg.get("role") == "tool")
    assert json.loads(tool_message["content"]) == {
        "submitted": True,
        "selected_values": ["react", "__other__"],
        "other_text": "SolidJS",
    }
    assert resumed_bundle["status"] == "completed"
    assert resumed_messages[-1]["content"] == "done"


def test_invalid_request_schema_returns_clear_tool_error_and_continues():
    agent = Broth()
    agent.provider = "ollama"
    agent.toolkit = ask_user_toolkit()

    seen_messages = []
    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        seen_messages.append(kwargs.get("messages"))
        state["turn"] += 1

        if state["turn"] == 1:
            return ProviderTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "ask_user_question",
                                    "arguments": {
                                        "title": "Broken",
                                        "question": "Broken",
                                        "selection_mode": "single",
                                        "options": [],
                                    },
                                },
                            }
                        ],
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="call_1",
                        name="ask_user_question",
                        arguments={
                            "title": "Broken",
                            "question": "Broken",
                            "selection_mode": "single",
                            "options": [],
                        },
                    )
                ],
                final_text="",
                consumed_tokens=5,
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "recovered"}],
            tool_calls=[],
            final_text="recovered",
            consumed_tokens=4,
        )

    agent._fetch_once = fake_fetch_once

    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "help me choose"}],
        max_iterations=3,
    )

    tool_message = next(msg for msg in seen_messages[1] if msg.get("role") == "tool")
    payload = json.loads(tool_message["content"])
    assert payload["tool"] == "ask_user_question"
    assert "options must be a non-empty array" in payload["error"]
    assert bundle["status"] == "completed"
    assert messages[-1]["content"] == "recovered"


@pytest.mark.parametrize(
    "response_payload,error_text",
    [
        (
            {"request_id": "call_1", "selected_values": ["react", "react"]},
            "duplicate selected value: react",
        ),
        (
            {"request_id": "call_1", "selected_values": ["__other__"]},
            "other_text is required",
        ),
        (
            {"request_id": "call_1", "selected_values": []},
            "at least 1 item",
        ),
    ],
)
def test_resume_human_input_rejects_invalid_user_response(response_payload, error_text):
    agent = Broth()
    agent.provider = "openai"
    agent.toolkit = ask_user_toolkit()

    def fake_fetch_once(**kwargs):
        return ProviderTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "ask_user_question",
                    "arguments": json.dumps(_selector_args(allow_other=True)),
                }
            ],
            tool_calls=[
                ToolCall(
                    call_id="call_1",
                    name="ask_user_question",
                    arguments=json.dumps(_selector_args(allow_other=True)),
                )
            ],
            final_text="",
            response_id="resp_1",
            consumed_tokens=3,
        )

    agent._fetch_once = fake_fetch_once
    suspended_messages, suspended_bundle = agent.run(
        messages=[{"role": "user", "content": "help me choose"}],
        payload={"store": True},
        max_iterations=3,
    )

    with pytest.raises(ValueError, match=error_text):
        agent.resume_human_input(
            conversation=suspended_messages,
            continuation=suspended_bundle["continuation"],
            response=response_payload,
        )


def test_suspended_run_skips_memory_commit_until_resume():
    manager = MemoryManager()
    agent = Broth(memory_manager=manager)
    agent.provider = "openai"
    agent.toolkit = ask_user_toolkit()

    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        state["turn"] += 1

        if state["turn"] == 1:
            return ProviderTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "ask_user_question",
                        "arguments": json.dumps(_selector_args()),
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="call_1",
                        name="ask_user_question",
                        arguments=json.dumps(_selector_args()),
                    )
                ],
                final_text="",
                response_id="resp_1",
                consumed_tokens=5,
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_2",
            consumed_tokens=4,
        )

    agent._fetch_once = fake_fetch_once

    events = []
    suspended_messages, suspended_bundle = agent.run(
        messages=[{"role": "user", "content": "help me choose"}],
        session_id="session-1",
        memory_namespace="shared",
        payload={"store": True},
        callback=events.append,
        max_iterations=3,
    )
    assert suspended_bundle["status"] == "awaiting_human_input"
    assert [event for event in events if event["type"] == "memory_commit"] == []

    resumed_messages, resumed_bundle = agent.resume_human_input(
        conversation=suspended_messages,
        continuation=suspended_bundle["continuation"],
        response={"request_id": "call_1", "selected_values": ["react"]},
        session_id="session-1",
        callback=events.append,
    )

    commit_events = [event for event in events if event["type"] == "memory_commit"]
    assert len(commit_events) == 1
    assert commit_events[0]["applied"] is True
    assert resumed_bundle["status"] == "completed"
    assert resumed_messages[-1]["content"] == "done"


def test_mixed_batch_with_ask_user_question_returns_errors_without_executing_other_tools():
    agent = Broth()
    agent.provider = "ollama"

    call_log = []
    safe_tool = tool(name="safe_action", func=lambda: call_log.append("called") or {"ok": True}, parameters=[])
    agent.toolkit = ask_user_toolkit()
    agent.add_toolkit(toolkit({safe_tool.name: safe_tool}))

    seen_messages = []
    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        seen_messages.append(kwargs.get("messages"))
        state["turn"] += 1

        if state["turn"] == 1:
            return ProviderTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "ask_user_question",
                                    "arguments": _selector_args(),
                                },
                            },
                            {
                                "id": "call_2",
                                "function": {
                                    "name": "safe_action",
                                    "arguments": {},
                                },
                            },
                        ],
                    }
                ],
                tool_calls=[
                    ToolCall(call_id="call_1", name="ask_user_question", arguments=_selector_args()),
                    ToolCall(call_id="call_2", name="safe_action", arguments={}),
                ],
                final_text="",
                consumed_tokens=5,
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            consumed_tokens=4,
        )

    agent._fetch_once = fake_fetch_once

    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "help me choose"}],
        max_iterations=3,
    )

    tool_messages = [msg for msg in seen_messages[1] if msg.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert all(
        json.loads(msg["content"])["error"] == "ask_user_question must be the only tool call in a turn"
        for msg in tool_messages
    )
    assert call_log == []
    assert bundle["status"] == "completed"
    assert messages[-1]["content"] == "done"


def test_ask_user_toolkit_fails_fast_when_model_does_not_support_tools():
    agent = Broth()
    agent.provider = "openai"
    agent.model = "no-tools-model"
    agent.model_capabilities["no-tools-model"] = {
        "supports_tools": False,
        "max_context_window_tokens": 0,
    }
    agent.toolkit = ask_user_toolkit()

    def fake_fetch_once(**kwargs):
        raise AssertionError("_fetch_once should not be called when tools are unsupported")

    agent._fetch_once = fake_fetch_once

    with pytest.raises(ValueError, match="ask_user_toolkit requires a tool-calling model"):
        agent.run(
            messages=[{"role": "user", "content": "help me choose"}],
            max_iterations=1,
        )


def test_models_without_tool_support_are_unaffected_when_ask_user_toolkit_is_not_mounted():
    agent = Broth()
    agent.provider = "openai"
    agent.model = "no-tools-model"
    agent.model_capabilities["no-tools-model"] = {
        "supports_tools": False,
        "max_context_window_tokens": 0,
    }

    seen_tool_names = []

    def fake_fetch_once(**kwargs):
        seen_tool_names.append([item["name"] for item in kwargs["toolkit"].to_json()])
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_1",
            consumed_tokens=2,
        )

    agent._fetch_once = fake_fetch_once

    messages, bundle = agent.run(
        messages=[{"role": "user", "content": "hello"}],
        max_iterations=1,
    )

    assert seen_tool_names == [[]]
    assert messages[-1]["content"] == "done"
    assert bundle["status"] == "completed"


def test_agent_resume_human_input_forwards_to_broth(monkeypatch):
    instances = []

    class FakeBroth:
        def __init__(self, provider=None, model=None, api_key=None, memory_manager=None):
            self.provider = provider
            self.model = model
            self.api_key = api_key
            self.memory_manager = memory_manager
            self.toolkit = None
            self.max_iterations = 6
            self.on_tool_confirm = None
            self.resume_calls = []
            instances.append(self)

        def resume_human_input(
            self,
            *,
            conversation,
            continuation,
            response,
            payload,
            response_format,
            callback,
            verbose,
            on_tool_confirm,
            on_continuation_request,
            session_id,
            memory_namespace,
        ):
            self.resume_calls.append(
                {
                    "conversation": conversation,
                    "continuation": continuation,
                    "response": response,
                    "payload": payload,
                    "response_format": response_format,
                    "session_id": session_id,
                    "memory_namespace": memory_namespace,
                }
            )
            del callback, verbose, on_tool_confirm, on_continuation_request
            return conversation + [{"role": "assistant", "content": "done"}], {"status": "completed"}

    monkeypatch.setattr("miso.agent.Broth", FakeBroth)

    agent = Agent(name="planner", defaults={"payload": {"temperature": 0.1}})
    conversation, bundle = agent.resume_human_input(
        conversation=[{"role": "user", "content": "hello"}],
        continuation={"type": "human_input_continuation"},
        response={"request_id": "call_1", "selected_values": ["react"]},
        session_id="session-1",
        memory_namespace="shared",
    )

    assert len(instances) == 1
    assert instances[0].resume_calls[0]["payload"] is None
    assert instances[0].resume_calls[0]["session_id"] == "session-1"
    assert instances[0].resume_calls[0]["memory_namespace"] == "shared"
    assert conversation[-1]["content"] == "done"
    assert bundle["status"] == "completed"
