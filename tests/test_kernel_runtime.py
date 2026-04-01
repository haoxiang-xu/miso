import json

from unchain.input.human_input import ASK_USER_QUESTION_TOOL_NAME
from unchain.kernel import KernelLoop, ModelTurnResult
from unchain.kernel.types import ToolCall as KernelToolCall
from unchain.tools.observation import inject_observation
from unchain.tools import Toolkit, tool


class _QueueModelIO:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if not self.results:
            raise AssertionError("unexpected fetch_turn call")
        return self.results.pop(0)


def test_kernel_run_executes_openai_tool_and_continues_with_previous_response_chain():
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "demo_tool",
                    "arguments": "{\"x\": 1}",
                }
            ],
            tool_calls=[KernelToolCall(call_id="call_1", name="demo_tool", arguments={"x": 1})],
            response_id="resp_1",
            consumed_tokens=7,
            input_tokens=4,
            output_tokens=3,
        ),
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_2",
            consumed_tokens=6,
            input_tokens=3,
            output_tokens=3,
        ),
    ])
    toolkit = Toolkit()
    toolkit.register(lambda x: {"value": x + 1}, name="demo_tool")
    loop = KernelLoop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "start"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=toolkit,
        max_iterations=3,
    )

    assert result.status == "completed"
    assert result.previous_response_id == "resp_2"
    tool_message = next(message for message in result.messages if message.get("type") == "function_call_output")
    assert tool_message["call_id"] == "call_1"
    assert json.loads(tool_message["output"]) == {"value": 2}
    assert model_io.requests[1].previous_response_id == "resp_1"
    assert model_io.requests[1].messages == [tool_message]


def test_kernel_run_confirmation_denied_and_modified_arguments():
    denied_tool = tool(
        name="dangerous_tool",
        func=lambda path=None: {"path": path},
        requires_confirmation=True,
    )
    modified_tool = tool(
        name="editable_tool",
        func=lambda path=None: {"path": path},
        requires_confirmation=True,
    )

    denied_model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_denied",
                    "name": "dangerous_tool",
                    "arguments": "{\"path\": \"secret.txt\"}",
                }
            ],
            tool_calls=[KernelToolCall(call_id="call_denied", name="dangerous_tool", arguments={"path": "secret.txt"})],
            response_id="resp_denied_1",
        ),
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "denied"}],
            tool_calls=[],
            final_text="denied",
            response_id="resp_denied_2",
        ),
    ])
    modified_model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_modified",
                    "name": "editable_tool",
                    "arguments": "{\"path\": \"draft.txt\"}",
                }
            ],
            tool_calls=[KernelToolCall(call_id="call_modified", name="editable_tool", arguments={"path": "draft.txt"})],
            response_id="resp_modified_1",
        ),
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "modified"}],
            tool_calls=[],
            final_text="modified",
            response_id="resp_modified_2",
        ),
    ])

    denied_loop = KernelLoop(model_io=denied_model_io)
    denied_toolkit = Toolkit()
    denied_toolkit.register(denied_tool)
    denied_result = denied_loop.run(
        [{"role": "user", "content": "run denied"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=denied_toolkit,
        on_tool_confirm=lambda req: {"approved": False, "reason": "nope"},
    )
    denied_output = next(message for message in denied_result.messages if message.get("type") == "function_call_output")
    assert json.loads(denied_output["output"]) == {
        "denied": True,
        "tool": "dangerous_tool",
        "reason": "nope",
    }

    modified_loop = KernelLoop(model_io=modified_model_io)
    modified_toolkit = Toolkit()
    modified_toolkit.register(modified_tool)
    modified_result = modified_loop.run(
        [{"role": "user", "content": "run modified"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=modified_toolkit,
        on_tool_confirm=lambda req: {"approved": True, "modified_arguments": {"path": "final.txt"}},
    )
    modified_output = next(message for message in modified_result.messages if message.get("type") == "function_call_output")
    assert json.loads(modified_output["output"]) == {"path": "final.txt"}


def test_kernel_run_observe_injects_observation_without_interrupting():
    events = []
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_obs",
                    "name": "observe_tool",
                    "arguments": "{\"topic\": \"x\"}",
                }
            ],
            tool_calls=[KernelToolCall(call_id="call_obs", name="observe_tool", arguments={"topic": "x"})],
            response_id="resp_1",
            consumed_tokens=4,
            input_tokens=2,
            output_tokens=2,
        ),
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "looks fine"}],
            tool_calls=[],
            final_text="looks fine",
            response_id="resp_obs",
            consumed_tokens=3,
            input_tokens=1,
            output_tokens=2,
        ),
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_2",
            consumed_tokens=5,
            input_tokens=3,
            output_tokens=2,
        ),
    ])
    toolkit = Toolkit()
    toolkit.register(tool(name="observe_tool", func=lambda topic=None: {"topic": topic, "ok": True}, observe=True))
    loop = KernelLoop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "observe"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=toolkit,
        callback=events.append,
    )

    assert result.status == "completed"
    tool_output = next(message for message in result.messages if message.get("type") == "function_call_output")
    parsed = json.loads(tool_output["output"])
    assert parsed["topic"] == "x"
    assert parsed["observation"] == "looks fine"
    assert any(event["type"] == "observation" for event in events)


def test_inject_observation_preserves_anthropic_tool_result_blocks():
    tool_message = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": json.dumps({"topic": "x", "ok": True}),
            }
        ],
    }

    inject_observation(tool_message, "looks fine")

    assert isinstance(tool_message["content"], list)
    assert tool_message["content"][0]["type"] == "tool_result"
    assert tool_message["content"][0]["tool_use_id"] == "toolu_1"
    parsed = json.loads(tool_message["content"][0]["content"])
    assert parsed["topic"] == "x"
    assert parsed["ok"] is True
    assert parsed["observation"] == "looks fine"


def test_kernel_run_ask_user_question_returns_awaiting_human_input():
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_user",
                    "name": ASK_USER_QUESTION_TOOL_NAME,
                    "arguments": json.dumps(
                        {
                            "title": "Choose stack",
                            "question": "Which stack?",
                            "selection_mode": "single",
                            "options": [
                                {"label": "React", "value": "react"},
                                {"label": "Vue", "value": "vue"},
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            tool_calls=[
                KernelToolCall(
                    call_id="call_user",
                    name=ASK_USER_QUESTION_TOOL_NAME,
                    arguments={
                        "title": "Choose stack",
                        "question": "Which stack?",
                        "selection_mode": "single",
                        "options": [
                            {"label": "React", "value": "react"},
                            {"label": "Vue", "value": "vue"},
                        ],
                    },
                )
            ],
            response_id="resp_ask",
            consumed_tokens=11,
            input_tokens=7,
            output_tokens=4,
        ),
    ])
    toolkit = Toolkit()
    toolkit.register(
        lambda **_: {"error": "reserved"},
        name=ASK_USER_QUESTION_TOOL_NAME,
        parameters=[],
    )
    loop = KernelLoop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "need a choice"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=toolkit,
    )

    assert result.status == "awaiting_human_input"
    assert result.continuation is not None
    assert result.continuation["previous_response_id"] == "resp_ask"
    assert result.continuation["iteration"] == 1
    assert result.human_input_request["request_id"] == "call_user"
    assert result.messages[-1]["type"] == "function_call"


def test_kernel_run_mixed_batch_with_ask_user_question_returns_errors_without_executing_other_tools():
    executed = {"count": 0}
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[
                {"type": "function_call", "call_id": "call_user", "name": ASK_USER_QUESTION_TOOL_NAME, "arguments": "{}"},
                {"type": "function_call", "call_id": "call_other", "name": "demo_tool", "arguments": "{\"x\": 1}"},
            ],
            tool_calls=[
                KernelToolCall(call_id="call_user", name=ASK_USER_QUESTION_TOOL_NAME, arguments={}),
                KernelToolCall(call_id="call_other", name="demo_tool", arguments={"x": 1}),
            ],
            response_id="resp_batch_1",
        ),
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_batch_2",
        ),
    ])
    toolkit = Toolkit()
    toolkit.register(lambda x=None: executed.__setitem__("count", executed["count"] + 1) or {"x": x}, name="demo_tool")
    toolkit.register(lambda **_: {"error": "reserved"}, name=ASK_USER_QUESTION_TOOL_NAME, parameters=[])
    loop = KernelLoop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "mixed"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=toolkit,
        max_iterations=3,
    )

    assert result.status == "completed"
    assert executed["count"] == 0
    outputs = [json.loads(message["output"]) for message in result.messages if message.get("type") == "function_call_output"]
    assert len(outputs) == 2
    assert all(output["error"] == "ask_user_question must be the only tool call in a turn" for output in outputs)


def test_kernel_resume_human_input_openai_uses_function_call_output_and_previous_response_id():
    initial_model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_user",
                    "name": ASK_USER_QUESTION_TOOL_NAME,
                    "arguments": json.dumps(
                        {
                            "title": "Choose stack",
                            "question": "Which stack?",
                            "selection_mode": "single",
                            "options": [
                                {"label": "React", "value": "react"},
                                {"label": "Vue", "value": "vue"},
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            tool_calls=[
                KernelToolCall(
                    call_id="call_user",
                    name=ASK_USER_QUESTION_TOOL_NAME,
                    arguments={
                        "title": "Choose stack",
                        "question": "Which stack?",
                        "selection_mode": "single",
                        "options": [
                            {"label": "React", "value": "react"},
                            {"label": "Vue", "value": "vue"},
                        ],
                    },
                )
            ],
            response_id="resp_ask",
            consumed_tokens=11,
            input_tokens=7,
            output_tokens=4,
        ),
    ])
    ask_toolkit = Toolkit()
    ask_toolkit.register(lambda **_: {"error": "reserved"}, name=ASK_USER_QUESTION_TOOL_NAME, parameters=[])
    initial_loop = KernelLoop(model_io=initial_model_io)
    suspended = initial_loop.run(
        [{"role": "user", "content": "need a choice"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=ask_toolkit,
    )

    resumed_model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "resume done"}],
            tool_calls=[],
            final_text="resume done",
            response_id="resp_resume",
            consumed_tokens=5,
            input_tokens=3,
            output_tokens=2,
        ),
    ])
    resumed_loop = KernelLoop(model_io=resumed_model_io)
    resumed = resumed_loop.resume_human_input(
        conversation=suspended.messages,
        continuation=suspended.continuation,
        response={"request_id": "call_user", "selected_values": ["react"]},
        toolkit=ask_toolkit,
    )

    assert resumed.status == "completed"
    request = resumed_model_io.requests[0]
    assert request.previous_response_id == "resp_ask"
    assert request.messages == [
        {
            "type": "function_call_output",
            "call_id": "call_user",
            "output": json.dumps({"submitted": True, "selected_values": ["react"], "other_text": None}, ensure_ascii=False),
        }
    ]
    assert any(message.get("type") == "function_call_output" and message.get("call_id") == "call_user" for message in resumed.messages)


def test_kernel_run_stops_at_max_iterations():
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[
                {
                    "type": "function_call",
                    "call_id": "call_loop",
                    "name": "demo_tool",
                    "arguments": "{\"x\": 1}",
                }
            ],
            tool_calls=[KernelToolCall(call_id="call_loop", name="demo_tool", arguments={"x": 1})],
            response_id="resp_loop",
        ),
    ])
    toolkit = Toolkit()
    toolkit.register(lambda x=None: {"x": x}, name="demo_tool")
    loop = KernelLoop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "loop"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=toolkit,
        max_iterations=1,
    )

    assert result.status == "max_iterations"
    assert any(message.get("type") == "function_call_output" for message in result.messages)


def test_kernel_run_completes_single_anthropic_turn():
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            consumed_tokens=5,
            input_tokens=3,
            output_tokens=2,
        ),
    ])
    loop = KernelLoop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "hello"}],
        provider="anthropic",
        model="claude-3-7-sonnet",
    )

    assert result.status == "completed"
    assert result.messages[-1] == {"role": "assistant", "content": "done"}
    assert model_io.requests[0].previous_response_id is None


def test_kernel_run_executes_anthropic_tool_and_continues_with_full_transcript():
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call_1", "name": "demo_tool", "input": {"x": 1}}],
            }],
            tool_calls=[KernelToolCall(call_id="call_1", name="demo_tool", arguments={"x": 1})],
            consumed_tokens=6,
            input_tokens=4,
            output_tokens=2,
        ),
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
        ),
    ])
    toolkit = Toolkit()
    toolkit.register(lambda x=None: {"value": x + 1}, name="demo_tool")
    loop = KernelLoop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "start"}],
        provider="anthropic",
        model="claude-3-7-sonnet",
        toolkit=toolkit,
        max_iterations=3,
    )

    assert result.status == "completed"
    tool_message = next(
        message
        for message in result.messages
        if message.get("role") == "user"
        and isinstance(message.get("content"), list)
        and message["content"][0].get("type") == "tool_result"
    )
    assert json.loads(tool_message["content"][0]["content"]) == {"value": 2}
    second_request_messages = model_io.requests[1].messages
    assert second_request_messages[0] == {"role": "user", "content": "start"}
    assert any(
        message.get("role") == "assistant"
        and isinstance(message.get("content"), list)
        and message["content"][0].get("type") == "tool_use"
        for message in second_request_messages
    )
    assert any(
        message.get("role") == "user"
        and isinstance(message.get("content"), list)
        and message["content"][0].get("type") == "tool_result"
        for message in second_request_messages
    )


def test_kernel_resume_human_input_anthropic_appends_tool_result_and_uses_full_transcript():
    initial_model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "call_user",
                    "name": ASK_USER_QUESTION_TOOL_NAME,
                    "input": {
                        "title": "Choose stack",
                        "question": "Which stack?",
                        "selection_mode": "single",
                        "options": [
                            {"label": "React", "value": "react"},
                            {"label": "Vue", "value": "vue"},
                        ],
                    },
                }],
            }],
            tool_calls=[
                KernelToolCall(
                    call_id="call_user",
                    name=ASK_USER_QUESTION_TOOL_NAME,
                    arguments={
                        "title": "Choose stack",
                        "question": "Which stack?",
                        "selection_mode": "single",
                        "options": [
                            {"label": "React", "value": "react"},
                            {"label": "Vue", "value": "vue"},
                        ],
                    },
                )
            ],
        ),
    ])
    ask_toolkit = Toolkit()
    ask_toolkit.register(lambda **_: {"error": "reserved"}, name=ASK_USER_QUESTION_TOOL_NAME, parameters=[])
    initial_loop = KernelLoop(model_io=initial_model_io)
    suspended = initial_loop.run(
        [{"role": "user", "content": "need a choice"}],
        provider="anthropic",
        model="claude-3-7-sonnet",
        toolkit=ask_toolkit,
    )

    resumed_model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "resume done"}],
            tool_calls=[],
            final_text="resume done",
        ),
    ])
    resumed_loop = KernelLoop(model_io=resumed_model_io)
    resumed = resumed_loop.resume_human_input(
        conversation=suspended.messages,
        continuation=suspended.continuation,
        response={"request_id": "call_user", "selected_values": ["react"]},
        toolkit=ask_toolkit,
    )

    assert resumed.status == "completed"
    request = resumed_model_io.requests[0]
    assert request.previous_response_id is None
    assert any(
        message.get("role") == "user"
        and isinstance(message.get("content"), list)
        and message["content"][0].get("type") == "tool_result"
        and json.loads(message["content"][0]["content"])["selected_values"] == ["react"]
        for message in request.messages
    )
    assert len(request.messages) > 1


def test_kernel_run_completes_single_ollama_turn():
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
        ),
    ])
    loop = KernelLoop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "hello"}],
        provider="ollama",
        model="qwen3",
    )

    assert result.status == "completed"
    assert result.messages[-1] == {"role": "assistant", "content": "done"}
    assert model_io.requests[0].previous_response_id is None


def test_kernel_run_executes_ollama_tool_and_continues_with_full_transcript():
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "demo_tool", "arguments": {"x": 1}}}],
            }],
            tool_calls=[KernelToolCall(call_id="call_1", name="demo_tool", arguments={"x": 1})],
        ),
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
        ),
    ])
    toolkit = Toolkit()
    toolkit.register(lambda x=None: {"value": x + 1}, name="demo_tool")
    loop = KernelLoop(model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "start"}],
        provider="ollama",
        model="qwen3",
        toolkit=toolkit,
        max_iterations=3,
    )

    assert result.status == "completed"
    tool_message = next(message for message in result.messages if message.get("role") == "tool")
    assert json.loads(tool_message["content"]) == {"value": 2}
    second_request_messages = model_io.requests[1].messages
    assert second_request_messages[0] == {"role": "user", "content": "start"}
    assert any(message.get("role") == "assistant" and "tool_calls" in message for message in second_request_messages)
    assert any(message.get("role") == "tool" for message in second_request_messages)


def test_kernel_resume_human_input_ollama_appends_tool_result_and_uses_full_transcript():
    initial_model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_user",
                    "function": {
                        "name": ASK_USER_QUESTION_TOOL_NAME,
                        "arguments": {
                            "title": "Choose stack",
                            "question": "Which stack?",
                            "selection_mode": "single",
                            "options": [
                                {"label": "React", "value": "react"},
                                {"label": "Vue", "value": "vue"},
                            ],
                        },
                    },
                }],
            }],
            tool_calls=[
                KernelToolCall(
                    call_id="call_user",
                    name=ASK_USER_QUESTION_TOOL_NAME,
                    arguments={
                        "title": "Choose stack",
                        "question": "Which stack?",
                        "selection_mode": "single",
                        "options": [
                            {"label": "React", "value": "react"},
                            {"label": "Vue", "value": "vue"},
                        ],
                    },
                )
            ],
        ),
    ])
    ask_toolkit = Toolkit()
    ask_toolkit.register(lambda **_: {"error": "reserved"}, name=ASK_USER_QUESTION_TOOL_NAME, parameters=[])
    initial_loop = KernelLoop(model_io=initial_model_io)
    suspended = initial_loop.run(
        [{"role": "user", "content": "need a choice"}],
        provider="ollama",
        model="qwen3",
        toolkit=ask_toolkit,
    )

    resumed_model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "resume done"}],
            tool_calls=[],
            final_text="resume done",
        ),
    ])
    resumed_loop = KernelLoop(model_io=resumed_model_io)
    resumed = resumed_loop.resume_human_input(
        conversation=suspended.messages,
        continuation=suspended.continuation,
        response={"request_id": "call_user", "selected_values": ["react"]},
        toolkit=ask_toolkit,
    )

    assert resumed.status == "completed"
    request = resumed_model_io.requests[0]
    assert request.previous_response_id is None
    assert any(
        message.get("role") == "tool"
        and json.loads(message.get("content", "{}"))["selected_values"] == ["react"]
        for message in request.messages
    )
    assert len(request.messages) > 1


def test_kernel_observe_tool_batch_uses_anthropic_payload_keys():
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "looks fine"}],
            tool_calls=[],
            final_text="looks fine",
        ),
    ])
    loop = KernelLoop(model_io=model_io)

    observation, usage = loop.observe_tool_batch(
        full_messages=[{"role": "user", "content": "observe"}],
        tool_messages=[{"role": "user", "content": '{"topic":"x","ok":true}'}],
        payload={},
        provider="anthropic",
        iteration=1,
    )

    assert observation == "looks fine"
    assert usage.output_tokens == 0
    observe_request = model_io.requests[0]
    assert observe_request.payload["max_tokens"] > 0
    assert "max_output_tokens" not in observe_request.payload
    assert "num_predict" not in observe_request.payload


def test_kernel_observe_tool_batch_uses_ollama_payload_keys():
    model_io = _QueueModelIO([
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "looks fine"}],
            tool_calls=[],
            final_text="looks fine",
        ),
    ])
    loop = KernelLoop(model_io=model_io)

    observation, usage = loop.observe_tool_batch(
        full_messages=[{"role": "user", "content": "observe"}],
        tool_messages=[{"role": "tool", "content": '{"topic":"x","ok":true}'}],
        payload={},
        provider="ollama",
        iteration=1,
    )

    assert observation == "looks fine"
    assert usage.output_tokens == 0
    observe_request = model_io.requests[0]
    assert observe_request.payload["num_predict"] > 0
    assert "max_output_tokens" not in observe_request.payload
    assert "max_tokens" not in observe_request.payload
