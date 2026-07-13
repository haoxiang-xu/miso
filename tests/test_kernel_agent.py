import json
import tempfile
from pathlib import Path

import pytest

from unchain.input import HumanInputResponse, build_ask_user_question_tool
from unchain.kernel import BaseRuntimeHarness, HarnessDelta, ModelTurnResult, ToolCall
from unchain.agent import (
    Agent,
    CompletionEvaluation,
    CompletionPolicy,
    MemoryModule,
    OptimizersModule,
    PoliciesModule,
    ToolsModule,
)
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


def test_session_memory_rejects_external_previous_response_id_before_model_fetch():
    memory = MemoryManager()

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            del request
            self.calls += 1
            raise AssertionError("model must not be called for conflicting history owners")

    fake_model_io = FakeModelIO()
    agent = Agent(
        name="previous-response-owner-guard",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    with pytest.raises(ValueError, match="previous_response_id"):
        agent.run(
            "hello",
            session_id="previous-response-owner-session",
            previous_response_id="resp_external",
        )

    assert fake_model_io.calls == 0
    assert memory.store.load("previous-response-owner-session") == {}


def test_session_metadata_without_memory_allows_previous_response_id():
    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.requests = []

        def fetch_turn(self, request):
            self.requests.append(request)
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "ok"}],
                tool_calls=[],
                final_text="ok",
                response_id="resp_next",
            )

    fake_model_io = FakeModelIO()
    agent = Agent(
        name="session-metadata-without-memory",
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    result = agent.run(
        "hello",
        session_id="metadata-only-session",
        previous_response_id="resp_external",
    )

    assert result.status == "completed"
    assert fake_model_io.requests[0].previous_response_id == "resp_external"


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


def test_completion_policy_retries_completed_run_until_validator_passes():
    events: list[dict] = []

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.requests = []
            self.results = [
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "draft answer"}],
                    tool_calls=[],
                    final_text="draft answer",
                    response_id="resp_draft",
                ),
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "final answer with checklist"}],
                    tool_calls=[],
                    final_text="final answer with checklist",
                    response_id="resp_final",
                ),
            ]

        def fetch_turn(self, request):
            self.requests.append(request)
            return self.results.pop(0)

    fake_model_io = FakeModelIO()

    def validate(result):
        final_text = str(result.messages[-1].get("content") or "")
        if "checklist" in final_text:
            return CompletionEvaluation(complete=True)
        return CompletionEvaluation(
            complete=False,
            feedback="Revise the answer and include a checklist.",
        )

    agent = Agent(
        name="completion_agent",
        modules=(
            PoliciesModule(
                completion_policy=CompletionPolicy(
                    validator=validate,
                    max_repair_turns=1,
                )
            ),
        ),
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    result = agent.run("produce an answer", max_iterations=1, callback=events.append)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "final answer with checklist"
    assert len(fake_model_io.requests) == 2
    assert fake_model_io.requests[1].messages[-1] == {
        "role": "user",
        "content": "Revise the answer and include a checklist.",
    }
    assert fake_model_io.requests[1].messages == [
        {
            "role": "user",
            "content": "Revise the answer and include a checklist.",
        }
    ]
    assert fake_model_io.requests[1].previous_response_id == "resp_draft"
    assert fake_model_io.requests[1].fallback_messages == [
        {"role": "user", "content": "produce an answer"},
        {"role": "assistant", "content": "draft answer"},
        {
            "role": "user",
            "content": "Revise the answer and include a checklist.",
        },
    ]
    assert result.messages == [
        {"role": "user", "content": "produce an answer"},
        {"role": "assistant", "content": "draft answer"},
        {
            "role": "user",
            "content": "Revise the answer and include a checklist.",
        },
        {"role": "assistant", "content": "final answer with checklist"},
    ]
    assert [event["type"] for event in events if event["type"].startswith("completion_policy_")] == [
        "completion_policy_evaluated",
        "completion_policy_retry",
        "completion_policy_evaluated",
    ]


def test_completion_policy_repairs_external_response_chain_from_delta_only():
    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.requests = []
            self.results = [
                ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": "draft one"}
                    ],
                    tool_calls=[],
                    final_text="draft one",
                    response_id="resp_1",
                ),
                ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": "draft two"}
                    ],
                    tool_calls=[],
                    final_text="draft two",
                    response_id="resp_2",
                ),
                ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": "final"}
                    ],
                    tool_calls=[],
                    final_text="final",
                    response_id="resp_3",
                ),
            ]

        def fetch_turn(self, request):
            self.requests.append(request)
            return self.results.pop(0)

    model_io = FakeModelIO()

    def validate(result):
        final_text = result.messages[-1].get("content")
        if final_text == "final":
            return CompletionEvaluation(complete=True)
        return CompletionEvaluation(
            complete=False,
            feedback=f"repair after {final_text}",
        )

    agent = Agent(
        name="completion-external-response-chain",
        modules=(
            PoliciesModule(
                completion_policy=CompletionPolicy(
                    validator=validate,
                    max_repair_turns=2,
                )
            ),
        ),
        model_io_factory=lambda spec, context: model_io,
    )

    result = agent.run(
        "new delta",
        previous_response_id="external_resp",
        max_iterations=1,
    )

    assert result.status == "completed"
    assert [request.previous_response_id for request in model_io.requests] == [
        "external_resp",
        "resp_1",
        "resp_2",
    ]
    assert model_io.requests[0].messages == [
        {"role": "user", "content": "new delta"}
    ]
    assert model_io.requests[1].messages == [
        {"role": "user", "content": "repair after draft one"}
    ]
    assert model_io.requests[2].messages == [
        {"role": "user", "content": "repair after draft two"}
    ]
    assert model_io.requests[1].fallback_messages is None
    assert model_io.requests[2].fallback_messages is None
    assert result.messages == [
        {"role": "user", "content": "new delta"},
        {"role": "assistant", "content": "draft one"},
        {"role": "user", "content": "repair after draft one"},
        {"role": "assistant", "content": "draft two"},
        {"role": "user", "content": "repair after draft two"},
        {"role": "assistant", "content": "final"},
    ]


def test_completion_policy_store_false_repairs_from_full_local_history():
    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.requests = []

        def fetch_turn(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": "draft answer"}
                    ],
                    tool_calls=[],
                    final_text="draft answer",
                    response_id="resp_draft",
                )
            return ModelTurnResult(
                assistant_messages=[
                    {"role": "assistant", "content": "final answer"}
                ],
                tool_calls=[],
                final_text="final answer",
                response_id="resp_final",
            )

    model_io = FakeModelIO()

    def validate(result):
        if result.messages[-1].get("content") == "final answer":
            return CompletionEvaluation(complete=True)
        return CompletionEvaluation(
            complete=False,
            feedback="repair with the original requirements",
        )

    agent = Agent(
        name="completion-local-repair",
        instructions="SYS",
        modules=(
            PoliciesModule(
                payload={"store": False},
                completion_policy=CompletionPolicy(
                    validator=validate,
                    max_repair_turns=1,
                ),
            ),
        ),
        model_io_factory=lambda spec, context: model_io,
    )

    result = agent.run("ORIGINAL", max_iterations=1)

    assert result.status == "completed"
    assert len(model_io.requests) == 2
    assert model_io.requests[1].previous_response_id is None
    assert model_io.requests[1].messages == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "ORIGINAL"},
        {"role": "assistant", "content": "draft answer"},
        {"role": "user", "content": "repair with the original requirements"},
    ]
    assert result.messages[:4] == model_io.requests[1].messages


def test_completion_policy_remote_repairs_keep_linear_transcript_and_fallback():
    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.requests = []
            self.results = [
                ("draft one", "resp_1"),
                ("draft two", "resp_2"),
                ("final", "resp_3"),
            ]

        def fetch_turn(self, request):
            self.requests.append(request)
            text, response_id = self.results.pop(0)
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": text}],
                tool_calls=[],
                final_text=text,
                response_id=response_id,
            )

    model_io = FakeModelIO()

    def validate(result):
        final_text = result.messages[-1].get("content")
        if final_text == "final":
            return CompletionEvaluation(complete=True)
        return CompletionEvaluation(
            complete=False,
            feedback=f"repair after {final_text}",
        )

    agent = Agent(
        name="completion-remote-multiple-repairs",
        modules=(
            PoliciesModule(
                completion_policy=CompletionPolicy(
                    validator=validate,
                    max_repair_turns=2,
                )
            ),
        ),
        model_io_factory=lambda spec, context: model_io,
    )

    result = agent.run("start", max_iterations=1)

    assert result.status == "completed"
    assert [request.previous_response_id for request in model_io.requests] == [
        None,
        "resp_1",
        "resp_2",
    ]
    assert model_io.requests[1].messages == [
        {"role": "user", "content": "repair after draft one"}
    ]
    assert model_io.requests[2].messages == [
        {"role": "user", "content": "repair after draft two"}
    ]
    assert model_io.requests[2].fallback_messages == [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "draft one"},
        {"role": "user", "content": "repair after draft one"},
        {"role": "assistant", "content": "draft two"},
        {"role": "user", "content": "repair after draft two"},
    ]
    assert result.messages == [
        *model_io.requests[2].fallback_messages,
        {"role": "assistant", "content": "final"},
    ]


def test_completion_policy_native_remote_repairs_keep_each_request_delta():
    class NativeFrameModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.requests = []
            self.results = [
                ("draft one", "resp_1"),
                ("draft two", "resp_2"),
                ("final", "resp_3"),
            ]

        def fetch_turn(self, request):
            self.requests.append(request)
            text, response_id = self.results.pop(0)
            output_item = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
            remote = bool(request.previous_response_id)
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": text}],
                tool_calls=[],
                final_text=text,
                response_id=response_id,
                provider_replay_frame={
                    "format": "openai.responses.v1",
                    "complete": not remote,
                    "items": [*request.messages, output_item],
                    "response_items": [output_item],
                    "mode": "append_response" if remote else "replace",
                    "tool_schema_manifest": {},
                },
            )

    model_io = NativeFrameModelIO()

    def validate(result):
        final_text = result.messages[-1].get("content")
        if final_text == "final":
            return CompletionEvaluation(complete=True)
        return CompletionEvaluation(
            complete=False,
            feedback=f"repair after {final_text}",
        )

    agent = Agent(
        name="completion-native-remote-multiple-repairs",
        modules=(
            PoliciesModule(
                completion_policy=CompletionPolicy(
                    validator=validate,
                    max_repair_turns=2,
                )
            ),
        ),
        model_io_factory=lambda spec, context: model_io,
    )

    result = agent.run("start", max_iterations=1)

    assert result.status == "completed"
    assert [request.previous_response_id for request in model_io.requests] == [
        None,
        "resp_1",
        "resp_2",
    ]
    assert [request.messages for request in model_io.requests[1:]] == [
        [{"role": "user", "content": "repair after draft one"}],
        [{"role": "user", "content": "repair after draft two"}],
    ]
    assert result.messages == [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "draft one"},
        {"role": "user", "content": "repair after draft one"},
        {"role": "assistant", "content": "draft two"},
        {"role": "user", "content": "repair after draft two"},
        {"role": "assistant", "content": "final"},
    ]


def test_completion_policy_with_session_memory_repairs_from_delta_only():
    memory = MemoryManager()

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.requests = []
            self.results = [
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "draft answer"}],
                    tool_calls=[],
                    final_text="draft answer",
                    response_id="resp_draft",
                ),
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "final answer"}],
                    tool_calls=[],
                    final_text="final answer",
                    response_id="resp_final",
                ),
            ]

        def fetch_turn(self, request):
            self.requests.append(request)
            return self.results.pop(0)

    fake_model_io = FakeModelIO()

    def validate(result):
        if result.messages[-1].get("content") == "final answer":
            return CompletionEvaluation(complete=True)
        return CompletionEvaluation(complete=False, feedback="repair feedback")

    agent = Agent(
        name="completion-memory-agent",
        instructions="SYS",
        modules=(
            MemoryModule(memory=memory),
            PoliciesModule(
                completion_policy=CompletionPolicy(
                    validator=validate,
                    max_repair_turns=1,
                )
            ),
        ),
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    result = agent.run("produce an answer", session_id="completion-memory-session")

    assert result.status == "completed"
    assert len(fake_model_io.requests) == 2
    assert fake_model_io.requests[1].previous_response_id is None
    assert [message.get("content") for message in fake_model_io.requests[1].messages] == [
        "SYS",
        "produce an answer",
        "draft answer",
        "repair feedback",
    ]
    stored = memory.store.load("completion-memory-session")["messages"]
    assert stored == result.messages
    assert [message.get("content") for message in stored] == [
        "SYS",
        "produce an answer",
        "draft answer",
        "repair feedback",
        "final answer",
    ]


def test_completion_policy_with_session_memory_keeps_multiple_repairs_linear():
    memory = MemoryManager()

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.requests = []
            self.results = [
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "draft one"}],
                    tool_calls=[],
                    final_text="draft one",
                    response_id="resp_1",
                ),
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "draft two"}],
                    tool_calls=[],
                    final_text="draft two",
                    response_id="resp_2",
                ),
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "final"}],
                    tool_calls=[],
                    final_text="final",
                    response_id="resp_3",
                ),
            ]

        def fetch_turn(self, request):
            self.requests.append(request)
            return self.results.pop(0)

    fake_model_io = FakeModelIO()

    def validate(result):
        final_text = result.messages[-1].get("content")
        if final_text == "final":
            return CompletionEvaluation(complete=True)
        return CompletionEvaluation(complete=False, feedback=f"repair after {final_text}")

    agent = Agent(
        name="completion-memory-multiple-repairs",
        instructions="SYS",
        modules=(
            MemoryModule(memory=memory),
            PoliciesModule(
                completion_policy=CompletionPolicy(
                    validator=validate,
                    max_repair_turns=2,
                )
            ),
        ),
        model_io_factory=lambda spec, ctx: fake_model_io,
    )

    result = agent.run("start", session_id="completion-memory-multiple-repairs-session")

    assert result.status == "completed"
    assert len(fake_model_io.requests) == 3
    assert [request.previous_response_id for request in fake_model_io.requests] == [
        None,
        None,
        None,
    ]
    assert [len(request.messages) for request in fake_model_io.requests] == [2, 4, 6]
    assert [message.get("content") for message in result.messages] == [
        "SYS",
        "start",
        "draft one",
        "repair after draft one",
        "draft two",
        "repair after draft two",
        "final",
    ]
    assert memory.store.load("completion-memory-multiple-repairs-session")["messages"] == (
        result.messages
    )


def test_completion_policy_marks_incomplete_when_repair_budget_is_exhausted():
    events: list[dict] = []

    class FakeModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self):
            self.results = [
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "still draft"}],
                    tool_calls=[],
                    final_text="still draft",
                    response_id="resp_1",
                ),
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "still draft"}],
                    tool_calls=[],
                    final_text="still draft",
                    response_id="resp_2",
                ),
            ]

        def fetch_turn(self, request):
            return self.results.pop(0)

    agent = Agent(
        name="completion_budget_agent",
        modules=(
            PoliciesModule(
                completion_policy=CompletionPolicy(
                    validator=lambda result: CompletionEvaluation(
                        complete=False,
                        feedback="Try again with the missing acceptance criteria.",
                    ),
                    max_repair_turns=1,
                )
            ),
        ),
        model_io_factory=lambda spec, ctx: FakeModelIO(),
    )

    result = agent.run("produce an answer", max_iterations=1, callback=events.append)

    assert result.status == "completion_incomplete"
    assert events[-1]["type"] == "completion_policy_exhausted"
    assert events[-1]["reason"] == "repair_budget_exhausted"


def test_unchain_agent_import_works():
    from unchain.agent import Agent as UnchainAgent

    assert UnchainAgent is Agent
