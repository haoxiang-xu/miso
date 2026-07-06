import json

from unchain.agent import Agent, SubagentModule
from unchain.kernel import ModelTurnResult, ToolCall
from unchain.subagents import (
    build_close_agent_thread_tool,
    build_read_agent_board_tool,
    build_return_handoff_to_subagent_tool,
    build_return_to_parent_tool,
    build_send_agent_message_tool,
    build_spawn_agent_thread_tool,
    build_wait_agent_messages_tool,
    build_write_agent_board_tool,
    SubagentPolicy,
    SubagentTemplate,
)


def _openai_tool_turn(*, call_id: str, name: str, arguments: dict) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[
            {
                "role": "assistant",
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            }
        ],
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        final_text="",
    )


def _text_turn(text: str) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": text}],
        tool_calls=[],
        final_text=text,
    )


class SequenceModelIO:
    def __init__(self, provider: str, steps):
        self.provider = provider
        self.model = f"{provider}-model"
        self._steps = list(steps)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if not self._steps:
            raise AssertionError("unexpected model turn")
        step = self._steps.pop(0)
        if callable(step):
            return step(request)
        return step


def test_communication_runtime_tool_builders_have_expected_names():
    tools = [
        build_spawn_agent_thread_tool(),
        build_send_agent_message_tool(),
        build_wait_agent_messages_tool(),
        build_close_agent_thread_tool(),
        build_write_agent_board_tool(),
        build_read_agent_board_tool(),
        build_return_handoff_to_subagent_tool(),
        build_return_to_parent_tool(),
    ]

    assert [tool.name for tool in tools] == [
        "spawn_agent_thread",
        "send_agent_message",
        "wait_agent_messages",
        "close_agent_thread",
        "write_agent_board",
        "read_agent_board",
        "return_handoff_to_subagent",
        "return_to_parent",
    ]


def test_spawn_wait_and_close_agent_thread_records_state_and_result():
    child = Agent(
        name="researcher",
        provider="openai",
        model_io_factory=lambda spec, ctx: SequenceModelIO("openai", [_text_turn("thread result")]),
    )
    observed_thread_id = ""

    def _after_spawn(request):
        nonlocal observed_thread_id
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_thread"
        assert payload["status"] == "completed"
        assert payload["summary"] == "thread result"
        assert payload["thread_id"]
        observed_thread_id = payload["thread_id"]
        return _openai_tool_turn(
            call_id="call_wait",
            name="wait_agent_messages",
            arguments={"thread_ids": [observed_thread_id], "condition": "all_done"},
        )

    def _after_wait(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "completed"
        assert payload["threads"][0]["status"] == "completed"
        return _openai_tool_turn(
            call_id="call_close",
            name="close_agent_thread",
            arguments={"thread_id": observed_thread_id, "reason": "inspected"},
        )

    def _after_close(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "closed"
        assert payload["thread"]["close_reason"] == "inspected"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        agent=child,
                        allowed_modes=("delegate", "worker"),
                    ),
                ),
                policy=SubagentPolicy(max_open_threads=2),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_spawn",
                    name="spawn_agent_thread",
                    arguments={"target": "researcher", "task": "Investigate"},
                ),
                _after_spawn,
                _after_wait,
                _after_close,
            ],
        ),
    )
    events = []

    result = parent.run("coordinate", max_iterations=4, callback=events.append)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"
    event_types = [event["type"] for event in events]
    assert "agent_thread_spawned" in event_types
    assert "agent_thread_completed" in event_types
    assert "agent_thread_closed" in event_types
