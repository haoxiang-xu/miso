from __future__ import annotations

import json

from unchain.agent import Agent, ToolOptimizerModule, ToolsModule
from unchain.kernel import ModelTurnResult, ToolCall
from unchain.tools import Tool, ToolOptimizerConfig, ToolPromptSpec, Toolkit


META_TOOL_NAMES = {
    "tool_search",
    "tool_describe",
    "tool_load",
    "tool_execute_deferred",
}


def _demo_tool(
    name: str,
    *,
    description: str | None = None,
    always_load: bool = False,
    defer_by_default: bool = False,
    search_hint: str = "",
    prompt_token: str = "",
) -> Tool:
    def run(value: str = "") -> dict[str, str]:
        return {"tool": name, "value": value}

    return Tool.from_callable(
        run,
        name=name,
        description=description or f"{name} short description.",
        parameters=[
            {
                "name": "value",
                "description": f"{name} value parameter.",
                "type_": "string",
                "required": False,
            }
        ],
        always_load=always_load,
        defer_by_default=defer_by_default,
        search_hint=search_hint,
        prompt_spec=ToolPromptSpec(
            purpose=prompt_token or f"Use {name} for demo work.",
        ),
    )


def _large_toolkit(count: int = 20) -> Toolkit:
    toolkit = Toolkit()
    for index in range(count):
        toolkit.register(_demo_tool(f"tool_{index}", search_hint=f"topic-{index}"))
    return toolkit


def test_tool_optimizer_selects_subset_and_exposes_deferred_summary_only():
    toolkit = _large_toolkit(12)
    toolkit.register(
        _demo_tool(
            "schema_heavy",
            description="schema_heavy compact description.",
            prompt_token="FULL_DEFERRED_PROMPT_SPEC_TOKEN",
            search_hint="rare schema heavy",
        )
    )

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            self.calls += 1
            if self.calls == 1:
                assert request.toolkit.tools == {}
                assert request.response_format is not None
                return ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": json.dumps({"tool_names": ["tool_3", "tool_5"]})}
                    ],
                    tool_calls=[],
                    final_text=json.dumps({"tool_names": ["tool_3", "tool_5"]}),
                )

            assert set(request.toolkit.tools) == {"tool_3", "tool_5", *META_TOOL_NAMES}
            tools_prompt = "\n".join(
                str(message.get("content"))
                for message in request.messages
                if message.get("role") == "system"
            )
            assert "Deferred tools available through tool_search" in tools_prompt
            assert "schema_heavy" in tools_prompt
            assert "schema_heavy compact description." in tools_prompt
            assert "FULL_DEFERRED_PROMPT_SPEC_TOKEN" not in tools_prompt
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
            )

    model_io = FakeModelIO()
    agent = Agent(
        name="optimizer-subset",
        modules=(
            ToolsModule(tools=(toolkit,)),
            ToolOptimizerModule(config=ToolOptimizerConfig(max_direct_tools=6, trigger_tool_count=5)),
        ),
        model_io_factory=lambda spec, ctx: model_io,
    )

    result = agent.run("use tool 3 and tool 5", max_iterations=1)

    assert result.status == "completed"
    assert model_io.calls == 2
    assert "tool_search" not in toolkit.tools
    assert set(toolkit.tools) == {f"tool_{index}" for index in range(12)} | {"schema_heavy"}


def test_tool_optimizer_selector_failure_falls_back_without_full_exposure():
    toolkit = _large_toolkit(30)

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "not json"}],
                    tool_calls=[],
                    final_text="not json",
                )

            assert len(request.toolkit.tools) <= 8
            assert META_TOOL_NAMES.issubset(request.toolkit.tools)
            assert len(set(request.toolkit.tools) - META_TOOL_NAMES) < 30
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
            )

    model_io = FakeModelIO()
    agent = Agent(
        name="optimizer-fallback",
        modules=(
            ToolsModule(tools=(toolkit,)),
            ToolOptimizerModule(config=ToolOptimizerConfig(max_direct_tools=8, trigger_tool_count=5)),
        ),
        model_io_factory=lambda spec, ctx: model_io,
    )

    result = agent.run("need topic-29", max_iterations=1)

    assert result.status == "completed"


def test_tool_optimizer_respects_allowed_tools_before_direct_or_deferred_exposure():
    toolkit = _large_toolkit(12)

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": json.dumps({"tool_names": ["tool_3", "tool_1"]})}
                    ],
                    tool_calls=[],
                    final_text=json.dumps({"tool_names": ["tool_3", "tool_1"]}),
                )

            assert "tool_3" not in request.toolkit.tools
            assert set(request.toolkit.tools).issubset({"tool_1", "tool_2", "tool_4", *META_TOOL_NAMES})
            return ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call_search",
                        "name": "tool_search",
                        "arguments": json.dumps({"query": "tool_3"}),
                    }
                ],
                tool_calls=[ToolCall(call_id="call_search", name="tool_search", arguments={"query": "tool_3"})],
            )

    model_io = FakeModelIO()
    agent = Agent(
        name="optimizer-allowed",
        allowed_tools=("tool_1", "tool_2", "tool_4"),
        modules=(
            ToolsModule(tools=(toolkit,)),
            ToolOptimizerModule(config=ToolOptimizerConfig(max_direct_tools=6, trigger_tool_count=2)),
        ),
        model_io_factory=lambda spec, ctx: model_io,
    )

    result = agent.run("try disallowed tool_3", max_iterations=1)

    assert result.status == "max_iterations"
    tool_outputs = [message for message in result.messages if message.get("type") == "function_call_output"]
    assert tool_outputs
    assert json.loads(tool_outputs[-1]["output"])["matches"] == []


def test_tool_optimizer_search_describe_and_load_make_deferred_tool_native_next_turn():
    toolkit = _large_toolkit(15)

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": json.dumps({"tool_names": ["tool_0"]})}
                    ],
                    tool_calls=[],
                    final_text=json.dumps({"tool_names": ["tool_0"]}),
                )
            if self.calls == 2:
                assert "tool_9" not in request.toolkit.tools
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call_search",
                            "name": "tool_search",
                            "arguments": json.dumps({"query": "topic-9", "max_results": 3}),
                        }
                    ],
                    tool_calls=[
                        ToolCall(
                            call_id="call_search",
                            name="tool_search",
                            arguments={"query": "topic-9", "max_results": 3},
                        )
                    ],
                )
            if self.calls == 3:
                output = json.loads(request.messages[-1]["output"])
                assert output["matches"][0]["handle"] == "tool_9"
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call_describe",
                            "name": "tool_describe",
                            "arguments": json.dumps({"names": ["tool_9"]}),
                        }
                    ],
                    tool_calls=[
                        ToolCall(call_id="call_describe", name="tool_describe", arguments={"names": ["tool_9"]})
                    ],
                )
            if self.calls == 4:
                output = json.loads(request.messages[-1]["output"])
                assert output["tools"][0]["name"] == "tool_9"
                assert "parameters" in output["tools"][0]
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call_load",
                            "name": "tool_load",
                            "arguments": json.dumps({"handles": ["tool_9"]}),
                        }
                    ],
                    tool_calls=[ToolCall(call_id="call_load", name="tool_load", arguments={"handles": ["tool_9"]})],
                )

            assert "tool_9" in request.toolkit.tools
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "loaded"}],
                tool_calls=[],
                final_text="loaded",
            )

    model_io = FakeModelIO()
    agent = Agent(
        name="optimizer-load",
        modules=(
            ToolsModule(tools=(toolkit,)),
            ToolOptimizerModule(config=ToolOptimizerConfig(max_direct_tools=6, trigger_tool_count=5)),
        ),
        model_io_factory=lambda spec, ctx: model_io,
    )

    result = agent.run("find topic 9", max_iterations=5)

    assert result.status == "completed"
    assert model_io.calls == 5


def test_tool_execute_deferred_uses_confirmation_before_running_target_tool():
    calls: list[dict[str, str]] = []
    toolkit = _large_toolkit(8)

    def dangerous(path: str) -> dict[str, str]:
        calls.append({"path": path})
        return {"wrote": path}

    toolkit.register(
        Tool.from_callable(
            dangerous,
            name="dangerous",
            description="Dangerous deferred tool.",
            requires_confirmation=True,
            search_hint="dangerous write",
        )
    )

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": json.dumps({"tool_names": ["tool_0"]})}
                    ],
                    tool_calls=[],
                    final_text=json.dumps({"tool_names": ["tool_0"]}),
                )
            if self.calls == 2:
                assert "dangerous" not in request.toolkit.tools
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call_exec",
                            "name": "tool_execute_deferred",
                            "arguments": json.dumps({"tool_name": "dangerous", "arguments": {"path": "secret.txt"}}),
                        }
                    ],
                    tool_calls=[
                        ToolCall(
                            call_id="call_exec",
                            name="tool_execute_deferred",
                            arguments={"tool_name": "dangerous", "arguments": {"path": "secret.txt"}},
                        )
                    ],
                )

            output = json.loads(request.messages[-1]["output"])
            assert output["denied"] is True
            assert output["tool"] == "dangerous"
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "denied"}],
                tool_calls=[],
                final_text="denied",
            )

    model_io = FakeModelIO()
    agent = Agent(
        name="optimizer-execute-deferred",
        modules=(
            ToolsModule(tools=(toolkit,)),
            ToolOptimizerModule(config=ToolOptimizerConfig(max_direct_tools=6, trigger_tool_count=5)),
        ),
        model_io_factory=lambda spec, ctx: model_io,
    )

    result = agent.run(
        "write secret",
        max_iterations=3,
        on_tool_confirm=lambda request: {"approved": False, "reason": "no"},
    )

    assert result.status == "completed"
    assert calls == []


def test_small_tool_pool_below_threshold_skips_optimizer_selector_and_exposes_all_tools():
    toolkit = _large_toolkit(3)

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            self.calls += 1
            assert set(request.toolkit.tools) == {"tool_0", "tool_1", "tool_2"}
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
            )

    model_io = FakeModelIO()
    agent = Agent(
        name="optimizer-small",
        modules=(
            ToolsModule(tools=(toolkit,)),
            ToolOptimizerModule(config=ToolOptimizerConfig(max_direct_tools=6, trigger_tool_count=5)),
        ),
        model_io_factory=lambda spec, ctx: model_io,
    )

    result = agent.run("hello", max_iterations=1)

    assert result.status == "completed"
    assert model_io.calls == 1
