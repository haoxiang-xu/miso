import json

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
            )

        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
        )

    agent._fetch_once = fake_fetch_once

    events = []
    result = agent.run(
        messages=[{"role": "user", "content": "start"}],
        callback=events.append,
        max_iterations=3,
    )

    tool_messages = [msg for msg in result if isinstance(msg, dict) and msg.get("role") == "tool"]
    assert len(tool_messages) == 2

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

    result = agent.run(
        messages=[{"role": "user", "content": "give me json"}],
        response_format=fmt,
        max_iterations=1,
    )

    last_assistant = [msg for msg in result if msg.get("role") == "assistant"][-1]
    assert json.loads(last_assistant["content"]) == {"answer": "ok"}
