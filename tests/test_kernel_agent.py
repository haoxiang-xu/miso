import json
import tempfile
from pathlib import Path

from unchain.input import HumanInputResponse, build_ask_user_question_tool
from unchain.kernel import BaseRuntimeHarness, HarnessDelta, ModelTurnResult, ToolCall
from unchain.agent import Agent, MemoryModule, OptimizersModule, PoliciesModule, ToolsModule
from unchain.memory import MemoryManager
from unchain.tools import Toolkit
from unchain.toolkits import CoreToolkit
from unchain.toolkits.base import BuiltinToolkit


def test_kernel_agent_run_returns_kernel_run_result_and_supports_three_providers():
    class FakeModelIO:
        def __init__(self, provider: str):
            self.provider = provider
            self.model = f"{provider}-model"

        def fetch_turn(self, request):
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": f"{self.provider} ok"}],
                tool_calls=[],
                final_text=f"{self.provider} ok",
                response_id=f"{self.provider}_resp",
                consumed_tokens=3,
                input_tokens=1,
                output_tokens=2,
            )

    for provider in ("openai", "anthropic", "ollama"):
        agent = Agent(
            name=f"{provider}_agent",
            provider=provider,
            model=f"{provider}-model",
            instructions="Be concise.",
            model_io_factory=lambda spec, ctx, provider=provider: FakeModelIO(provider),
        )
        result = agent.run("hello", max_iterations=1)
        assert result.status == "completed"
        assert result.messages[-1]["content"] == f"{provider} ok"
        assert result.previous_response_id == f"{provider}_resp"


def test_kernel_agent_tools_module_executes_tool_calls():
    def echo(text: str) -> dict[str, str]:
        return {"echo": text}

    class FakeModelIO:
        provider = "ollama"
        model = "llama3"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": "{\"text\":\"pong\"}"},
                                }
                            ],
                        }
                    ],
                    tool_calls=[ToolCall(call_id="call_1", name="echo", arguments={"text": "pong"})],
                    final_text="",
                )
            assert request.messages[-1]["role"] == "tool"
            assert json.loads(request.messages[-1]["content"]) == {"echo": "pong"}
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
            )

    fake_model_io = FakeModelIO()
    agent = Agent(
        name="tool_agent",
        provider="ollama",
        model="llama3",
        modules=(ToolsModule(tools=(echo,)),),
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    result = agent.run("use the tool", max_iterations=2)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"


def test_kernel_agent_passes_tool_runtime_config_to_builtin_toolkits():
    class ConfigToolkit(BuiltinToolkit):
        def __init__(self):
            super().__init__()
            self.register(self.show_config)

        def show_config(self) -> dict[str, object]:
            context = self.current_execution_context
            return {
                "config": dict(getattr(context, "tool_runtime_config", {}) or {}) if context is not None else {}
            }

    class FakeModelIO:
        provider = "ollama"
        model = "llama3"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            self.calls += 1
            if self.calls == 1:
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_cfg",
                                    "type": "function",
                                    "function": {"name": "show_config", "arguments": "{}"},
                                }
                            ],
                        }
                    ],
                    tool_calls=[ToolCall(call_id="call_cfg", name="show_config", arguments={})],
                    final_text="",
                )
            assert json.loads(request.messages[-1]["content"]) == {
                "config": {
                    "web_fetch": {
                        "extract_model": {
                            "provider": "openai",
                            "model": "gpt-5-mini",
                        }
                    }
                }
            }
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
            )

    fake_model_io = FakeModelIO()
    agent = Agent(
        name="config_agent",
        provider="ollama",
        model="llama3",
        modules=(ToolsModule(tools=(ConfigToolkit(),)),),
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    result = agent.run(
        "show runtime config",
        max_iterations=2,
        tool_runtime_config={
            "web_fetch": {
                "extract_model": {
                    "provider": "openai",
                    "model": "gpt-5-mini",
                }
            }
        },
    )

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"


def test_kernel_agent_memory_module_attaches_memory_without_exposing_memory_tools():
    memory = MemoryManager()

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
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "name": "recall_profile",
                                    "arguments": "{}",
                                }
                            ],
                        }
                    ],
                    tool_calls=[ToolCall(call_id="call_1", name="recall_profile", arguments={})],
                    final_text="",
                )
            output = json.loads(request.messages[-1]["output"])
            assert output["error"] == "tool not found: recall_profile"
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "memory ok"}],
                tool_calls=[],
                final_text="memory ok",
            )

    fake_model_io = FakeModelIO()
    agent = Agent(
        name="memory_agent",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    result = agent.run("hello", session_id="session-1", memory_namespace="ns-1", max_iterations=2)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "memory ok"
    stored = memory.store.load("session-1")
    assert stored["messages"][-1]["content"] == "memory ok"


def test_kernel_agent_resume_human_input_returns_kernel_run_result():
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
                        {
                            "role": "assistant",
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "ask_user_question",
                            "arguments": json.dumps(
                                {
                                    "title": "Need input",
                                    "question": "Pick one",
                                    "selection_mode": "single",
                                    "options": [
                                        {"label": "A", "value": "a"},
                                        {"label": "B", "value": "b"},
                                    ],
                                }
                            ),
                        }
                    ],
                    tool_calls=[
                        ToolCall(
                            call_id="call_1",
                            name="ask_user_question",
                            arguments={
                                "title": "Need input",
                                "question": "Pick one",
                                "selection_mode": "single",
                                "options": [
                                    {"label": "A", "value": "a"},
                                    {"label": "B", "value": "b"},
                                ],
                            },
                        )
                    ],
                    final_text="",
                )
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "resumed"}],
                tool_calls=[],
                final_text="resumed",
            )

    fake_model_io = FakeModelIO()
    agent = Agent(
        name="asker",
        modules=(ToolsModule(tools=(build_ask_user_question_tool(),)),),
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    suspended = agent.run("ask me", session_id="session-2", max_iterations=2)
    assert suspended.status == "awaiting_human_input"
    assert suspended.continuation is not None
    assert suspended.human_input_request is not None

    resumed = agent.resume_human_input(
        conversation=suspended.messages,
        continuation=suspended.continuation,
        response=HumanInputResponse(
            request_id=suspended.human_input_request["request_id"],
            selected_values=["a"],
        ).to_dict(),
        session_id="session-2",
    )
    assert resumed.status == "completed"
    assert resumed.messages[-1]["content"] == "resumed"


def test_kernel_agent_rejects_duplicate_tool_names_across_toolkits():
    class ConflictingToolkit(Toolkit):
        def __init__(self):
            super().__init__()
            self.register(lambda path: {"path": path}, name="read")

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "demo.txt").write_text("hello\n", encoding="utf-8")
        agent = Agent(
            name="conflict-agent",
            modules=(ToolsModule(tools=(CoreToolkit(workspace_root=tmp), ConflictingToolkit())),),
            model_io_factory=lambda spec, ctx: None,
        )

        try:
            agent.run("hi")
        except ValueError as exc:
            assert "tool name conflict" in str(exc)
            assert "read" in str(exc)
        else:
            raise AssertionError("expected duplicate tool name conflict")


def test_kernel_agent_as_tool_wraps_kernel_result():
    class FakeModelIO:
        provider = "anthropic"
        model = "claude"

        def fetch_turn(self, request):
            del request
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "delegated"}],
                tool_calls=[],
                final_text="delegated",
            )

    agent = Agent(
        name="delegate",
        provider="anthropic",
        model="claude",
        model_io_factory=lambda spec, ctx: FakeModelIO(),
    )

    delegated_tool = agent.as_tool(name="delegate_agent")
    result = delegated_tool.execute({"task": "handle this"})

    assert result["agent"] == "delegate"
    assert result["status"] == "completed"
    assert result["output"] == "delegated"


def test_kernel_agent_optimizer_module_registers_custom_harness_and_policy_defaults():
    class PrefixHarness(BaseRuntimeHarness):
        def __init__(self):
            super().__init__(name="prefix", phases=("before_model",), order=1)

        def build_delta(self, context):
            return HarnessDelta.append(
                created_by="optimizer.prefix",
                messages=[{"role": "system", "content": "prefix"}],
            )

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.seen_messages = []
            self.seen_payloads = []

        def fetch_turn(self, request):
            self.seen_messages.append(request.messages)
            self.seen_payloads.append(request.payload)
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "ok"}],
                tool_calls=[],
                final_text="ok",
            )

    fake_model_io = FakeModelIO()
    agent = Agent(
        name="policy_agent",
        modules=(
            OptimizersModule(harnesses=(PrefixHarness(),)),
            PoliciesModule(payload={"store": False}, max_iterations=1, max_context_window_tokens=1234),
        ),
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    result = agent.run("hello")

    assert result.status == "completed"
    assert fake_model_io.seen_messages[0][1] == {"role": "system", "content": "prefix"}
    assert fake_model_io.seen_payloads[0]["store"] is False


def test_unchain_agent_import_works():
    from unchain.agent import Agent as UnchainAgent

    assert UnchainAgent is Agent
