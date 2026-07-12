from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from unchain.agent import Agent, InteractionModule, MemoryModule, ToolsModule
from unchain.interaction import FyiChannel
from unchain.kernel import KernelLoop, ModelTurnResult, ToolCall
from unchain.memory import JsonFileSessionStore, MemoryConfig, MemoryManager
from unchain.optimizers import (
    LastNOptimizer,
    LastNOptimizerConfig,
    LlmSummaryOptimizer,
    LlmSummaryOptimizerConfig,
)
from unchain.retry import RetryConfig, RetryContext, fetch_turn_with_retry
from unchain.tools import Toolkit


class _FinalAnswerModelIO:
    provider = "ollama"
    model = "fake"

    def __init__(self) -> None:
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(copy.deepcopy(request))
        index = len(self.requests)
        return ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": f"a{index}"}],
            tool_calls=[],
            final_text=f"a{index}",
        )


def test_multirun_stateful_delta_input_grows_session_linearly():
    model_io = _FinalAnswerModelIO()
    memory = MemoryManager(config=MemoryConfig(last_n_turns=100))
    agent = Agent(
        name="multi-run-delta-baseline",
        provider="ollama",
        model="fake",
        instructions="SYS",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=lambda spec, context: model_io,
    )

    for run_number in range(1, 7):
        agent.run(f"u{run_number}", session_id="multi-run-delta-session")

    stored = memory.store.load("multi-run-delta-session")["messages"]
    assert len(stored) == 13
    assert sum(message.get("content") == "SYS" for message in stored) == 1
    for run_number in range(1, 7):
        assert sum(message.get("content") == f"u{run_number}" for message in stored) == 1
        assert sum(message.get("content") == f"a{run_number}" for message in stored) == 1


def test_memory_config_module_reuses_default_session_store_across_agent_runs():
    model_io = _FinalAnswerModelIO()
    agent = Agent(
        name="memory-config-runtime-cache-regression",
        provider="ollama",
        model="fake",
        instructions="SYS",
        modules=(MemoryModule(memory=MemoryConfig(last_n_turns=100)),),
        model_io_factory=lambda spec, context: model_io,
    )

    for run_number in range(1, 7):
        agent.run(
            f"u{run_number}",
            session_id="memory-config-runtime-cache-session",
        )

    final_request = model_io.requests[-1]
    assert sum(message.get("content") == "SYS" for message in final_request.messages) == 1
    for run_number in range(1, 7):
        assert sum(
            message.get("content") == f"u{run_number}"
            for message in final_request.messages
        ) == 1
    for run_number in range(1, 6):
        assert sum(
            message.get("content") == f"a{run_number}"
            for message in final_request.messages
        ) == 1


def test_empty_session_accepts_seed_history_before_switching_to_delta_input():
    model_io = _FinalAnswerModelIO()
    memory = MemoryManager(config=MemoryConfig(last_n_turns=100))
    agent = Agent(
        name="seed-history-baseline",
        provider="ollama",
        model="fake",
        instructions="SYS",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=lambda spec, context: model_io,
    )

    agent.run(
        [
            {"role": "user", "content": "seed-user"},
            {"role": "assistant", "content": "seed-assistant"},
            {"role": "user", "content": "first-live-turn"},
        ],
        session_id="seed-history-session",
    )
    agent.run("second-live-turn", session_id="seed-history-session")

    stored = memory.store.load("seed-history-session")["messages"]
    assert [message.get("content") for message in stored] == [
        "SYS",
        "seed-user",
        "seed-assistant",
        "first-live-turn",
        "a1",
        "second-live-turn",
        "a2",
    ]


def test_stateful_identical_user_deltas_are_preserved_as_distinct_turns():
    model_io = _FinalAnswerModelIO()
    memory = MemoryManager(config=MemoryConfig(last_n_turns=100))
    agent = Agent(
        name="identical-delta-baseline",
        provider="ollama",
        model="fake",
        instructions="SYS",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=lambda spec, context: model_io,
    )

    agent.run("repeat this", session_id="identical-delta-session")
    agent.run("repeat this", session_id="identical-delta-session")

    stored = memory.store.load("identical-delta-session")["messages"]
    assert sum(message.get("content") == "repeat this" for message in stored) == 2
    assert [message.get("content") for message in stored if message.get("role") == "assistant"] == [
        "a1",
        "a2",
    ]


def test_multirun_stateless_full_history_does_not_amplify_agent_instruction():
    model_io = _FinalAnswerModelIO()
    agent = Agent(
        name="stateless-full-history-regression",
        provider="ollama",
        model="fake",
        instructions="SYS",
        model_io_factory=lambda spec, context: model_io,
    )

    messages: str | list[dict[str, Any]] = "u1"
    result = None
    for run_number in range(1, 13):
        result = agent.run(messages, session_id="metadata-only-session")
        if run_number < 12:
            messages = [
                *result.messages,
                {"role": "user", "content": f"u{run_number + 1}"},
            ]

    assert result is not None
    assert len(result.messages) == 25
    assert sum(message.get("content") == "SYS" for message in result.messages) == 1
    for run_number in range(1, 13):
        assert sum(message.get("content") == f"u{run_number}" for message in result.messages) == 1
        assert sum(message.get("content") == f"a{run_number}" for message in result.messages) == 1


def test_existing_leading_agent_instruction_is_reused_with_metadata_preserved():
    model_io = _FinalAnswerModelIO()
    agent = Agent(
        name="instruction-metadata-regression",
        provider="ollama",
        model="fake",
        instructions="SYS",
        model_io_factory=lambda spec, context: model_io,
    )
    messages = [
        {
            "role": "system",
            "content": "SYS",
            "metadata": {"trace_id": "prior-run-1"},
        },
        {"role": "user", "content": "u1"},
    ]
    original = copy.deepcopy(messages)

    result = agent.run(messages)

    assert messages == original
    assert result.messages[:2] == original
    assert sum(message.get("content") == "SYS" for message in result.messages) == 1


def test_distinct_caller_system_message_does_not_suppress_agent_instruction():
    model_io = _FinalAnswerModelIO()
    agent = Agent(
        name="distinct-system-regression",
        provider="ollama",
        model="fake",
        instructions="AGENT SYS",
        model_io_factory=lambda spec, context: model_io,
    )

    result = agent.run(
        [
            {"role": "system", "content": "CALLER SYS"},
            {"role": "user", "content": "u1"},
        ]
    )

    assert result.messages[:2] == [
        {"role": "system", "content": "AGENT SYS"},
        {"role": "system", "content": "CALLER SYS"},
    ]


def test_multirun_replay_preserves_distinct_system_layers_without_amplification():
    model_io = _FinalAnswerModelIO()
    agent = Agent(
        name="layered-system-regression",
        provider="ollama",
        model="fake",
        instructions="AGENT SYS",
        model_io_factory=lambda spec, context: model_io,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "CALLER SYS"},
        {"role": "user", "content": "u1"},
    ]
    result = None
    for run_number in range(1, 7):
        result = agent.run(messages)
        if run_number < 6:
            messages = [
                *result.messages,
                {"role": "user", "content": f"u{run_number + 1}"},
            ]

    assert result is not None
    assert sum(message.get("content") == "AGENT SYS" for message in result.messages) == 1
    assert sum(message.get("content") == "CALLER SYS" for message in result.messages) == 1
    assert [message.get("role") for message in result.messages[:2]] == ["system", "system"]


def test_agent_caller_and_harness_system_layers_compose_without_persisting_harness_view():
    model_io = _FinalAnswerModelIO()
    toolkit = Toolkit(prompt_sections=("HARNESS SYS",))
    agent = Agent(
        name="harness-system-layer-regression",
        provider="ollama",
        model="fake",
        instructions="AGENT SYS",
        modules=(ToolsModule(tools=(toolkit,)),),
        model_io_factory=lambda spec, context: model_io,
    )

    result = agent.run(
        [
            {"role": "system", "content": "CALLER SYS"},
            {"role": "user", "content": "u1"},
        ]
    )

    request_systems = [
        message.get("content")
        for message in model_io.requests[0].messages
        if message.get("role") == "system"
    ]
    assert request_systems[:2] == ["AGENT SYS", "CALLER SYS"]
    assert sum("HARNESS SYS" in str(content) for content in request_systems) == 1
    assert [message.get("content") for message in result.messages[:2]] == [
        "AGENT SYS",
        "CALLER SYS",
    ]
    assert all("HARNESS SYS" not in str(message.get("content")) for message in result.messages)


def test_multirun_full_history_with_memory_never_silently_amplifies_session_transcript():
    model_io = _FinalAnswerModelIO()
    memory = MemoryManager(config=MemoryConfig(last_n_turns=100))
    agent = Agent(
        name="multi-run-regression",
        provider="ollama",
        model="fake",
        instructions="SYS",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=lambda spec, context: model_io,
    )

    messages: str | list[dict[str, Any]] = "u1"
    result = None
    for run_number in range(1, 7):
        try:
            result = agent.run(messages, session_id="multi-run-session")
        except ValueError as exc:
            assert "session" in str(exc).lower() or "history" in str(exc).lower()
            return
        messages = [
            *result.messages,
            {"role": "user", "content": f"u{run_number + 1}"},
        ]

    assert result is not None
    stored = memory.store.load("multi-run-session")["messages"]
    assert len(stored) == 13
    assert sum(message.get("content") == "SYS" for message in stored) == 1
    for run_number in range(1, 7):
        assert sum(message.get("content") == f"u{run_number}" for message in stored) == 1
        assert sum(message.get("content") == f"a{run_number}" for message in stored) == 1


def test_session_owned_full_history_replay_is_rejected_before_model_fetch():
    model_io = _FinalAnswerModelIO()
    memory = MemoryManager(config=MemoryConfig(last_n_turns=100))
    agent = Agent(
        name="session-owner-guard-regression",
        provider="ollama",
        model="fake",
        instructions="SYS",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=lambda spec, context: model_io,
    )

    first = agent.run("u1", session_id="session-owner-guard-session")
    stored_before = copy.deepcopy(memory.store.load("session-owner-guard-session"))

    error = None
    try:
        agent.run(
            [
                *first.messages,
                {"role": "user", "content": "u2"},
            ],
            session_id="session-owner-guard-session",
        )
    except ValueError as exc:
        error = exc

    assert error is not None, "session-backed full-history replay must fail fast"
    assert "session" in str(error).lower() or "history" in str(error).lower()
    assert len(model_io.requests) == 1
    assert memory.store.load("session-owner-guard-session") == stored_before


def test_max_iteration_checkpoint_restores_tool_result_without_reexecution(tmp_path):
    tool_calls = {"count": 0}

    def echo(text: str) -> dict[str, str]:
        tool_calls["count"] += 1
        return {"echo": text}

    class _ToolCallingModelIO:
        provider = "ollama"
        model = "fake"

        def fetch_turn(self, request):
            del request
            return ModelTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_echo",
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": {"text": "value"},
                                },
                            }
                        ],
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="call_echo",
                        name="echo",
                        arguments={"text": "value"},
                    )
                ],
            )

    first_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
    first_agent = Agent(
        name="max-iteration-commit-regression",
        provider="ollama",
        model="fake",
        modules=(
            ToolsModule(tools=(echo,)),
            MemoryModule(memory=first_memory),
        ),
        model_io_factory=lambda spec, context: _ToolCallingModelIO(),
    )

    stopped = first_agent.run(
        "run the tool",
        session_id="max-iteration-session",
        max_iterations=1,
    )

    assert stopped.status == "max_iterations"
    assert tool_calls["count"] == 1
    stored_after_stop = first_memory.store.load("max-iteration-session")
    checkpoint = stored_after_stop.get("execution_checkpoint")
    assert checkpoint.get("status") == "max_iterations"
    assert checkpoint.get("transcript") == stopped.messages
    assert checkpoint.get("replay_frame", {}).get("complete") is True
    assert stored_after_stop.get("messages", []) == []

    class _ResumeFromCheckpointModelIO:
        provider = "ollama"
        model = "fake"

        def __init__(self):
            self.requests = []

        def fetch_turn(self, request):
            self.requests.append(copy.deepcopy(request))
            tool_messages = [
                message
                for message in request.messages
                if isinstance(message, dict) and message.get("role") == "tool"
            ]
            assert tool_messages
            assert json.loads(tool_messages[-1]["content"]) == {"echo": "value"}
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
            )

    resumed_model_io = _ResumeFromCheckpointModelIO()
    resumed_memory = MemoryManager(store=JsonFileSessionStore(tmp_path))
    resumed_agent = Agent(
        name="max-iteration-commit-regression",
        provider="ollama",
        model="fake",
        modules=(
            ToolsModule(tools=(echo,)),
            MemoryModule(memory=resumed_memory),
        ),
        model_io_factory=lambda spec, context: resumed_model_io,
    )

    completed = resumed_agent.run(
        [],
        session_id="max-iteration-session",
        max_iterations=1,
    )

    assert completed.status == "completed"
    assert tool_calls["count"] == 1
    stored_after_resume = resumed_memory.store.load("max-iteration-session")
    assert stored_after_resume.get("messages") == completed.messages
    assert "execution_checkpoint" not in stored_after_resume


def test_current_large_tool_result_preserves_or_retrieves_middle_evidence():
    marker = "MIDDLE_ACCEPTANCE_EVIDENCE_42"
    cursor = "large-result-cursor-1"

    def large_tool() -> dict[str, str]:
        return {
            "a_head": "A" * 3_500,
            "m_cursor": cursor,
            "n_retrieve_with": "read_range",
            "o_middle": marker,
            "z_tail": "Z" * 3_500,
        }

    def read_range(cursor: str) -> dict[str, str]:
        if cursor != "large-result-cursor-1":
            raise ValueError("unknown cursor")
        return {"content": marker}

    class _LargeResultModelIO:
        provider = "ollama"
        model = "fake"

        def __init__(self) -> None:
            self.requests = []
            self.marker_seen = False

        def fetch_turn(self, request):
            self.requests.append(copy.deepcopy(request))
            serialized = json.dumps(request.messages, ensure_ascii=False)
            if len(self.requests) == 1:
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_large",
                                    "type": "function",
                                    "function": {"name": "large_tool", "arguments": {}},
                                }
                            ],
                        }
                    ],
                    tool_calls=[ToolCall(call_id="call_large", name="large_tool", arguments={})],
                )
            if marker in serialized:
                self.marker_seen = True
                return ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "evidence recovered"}],
                    tool_calls=[],
                    final_text="evidence recovered",
                )
            if cursor in serialized:
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_read_range",
                                    "type": "function",
                                    "function": {
                                        "name": "read_range",
                                        "arguments": {"cursor": cursor},
                                    },
                                }
                            ],
                        }
                    ],
                    tool_calls=[
                        ToolCall(
                            call_id="call_read_range",
                            name="read_range",
                            arguments={"cursor": cursor},
                        )
                    ],
                )
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "evidence unavailable"}],
                tool_calls=[],
                final_text="evidence unavailable",
            )

    model_io = _LargeResultModelIO()
    agent = Agent(
        name="large-result-regression",
        provider="ollama",
        model="fake",
        modules=(ToolsModule(tools=(large_tool, read_range)),),
        model_io_factory=lambda spec, context: model_io,
    )

    result = agent.run(
        "find the middle evidence",
        max_iterations=4,
        tool_runtime_config={
            "tool_result_budget": {
                "max_result_chars": 4_000,
                "max_batch_chars": 4_000,
                "preview_chars": 600,
            }
        },
    )

    assert result.status == "completed"
    assert model_io.marker_seen


@dataclass
class _RetryRequest:
    messages: list[dict[str, Any]]
    callback: Callable[[dict[str, Any]], None] | None = None


def test_retry_continues_after_non_user_visible_request_messages_event():
    class _RequestTracingModelIO:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_turn(self, request):
            self.calls += 1
            if request.callback is not None:
                request.callback({"type": "request_messages", "messages": request.messages})
            if self.calls == 1:
                raise httpx.ConnectError("transient before model output")
            return "ok"

    callback_events: list[dict[str, Any]] = []
    model_io = _RequestTracingModelIO()

    result = fetch_turn_with_retry(
        model_io=model_io,
        request=_RetryRequest(messages=[], callback=callback_events.append),
        config=RetryConfig(
            max_retries=1,
            base_delay_ms=1,
            max_delay_ms=1,
            jitter_ratio=0.0,
        ),
        context=RetryContext(run_id="retry-regression", iteration=0, is_background=False),
        sleep=lambda seconds: None,
    )

    assert result == "ok"
    assert model_io.calls == 2
    assert callback_events


def test_openai_previous_response_chain_includes_mid_run_fyi_in_next_request():
    channel = FyiChannel()
    seen_requests = []

    def echo(text: str) -> dict[str, str]:
        return {"echo": text}

    class _OpenAIModelIO:
        provider = "openai"
        model = "gpt-5"

        def fetch_turn(self, request):
            seen_requests.append(copy.deepcopy(request))
            if len(seen_requests) == 1:
                channel.post("please also handle Chinese input")
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_echo",
                                    "type": "function",
                                    "name": "echo",
                                    "arguments": json.dumps({"text": "hi"}),
                                }
                            ],
                        }
                    ],
                    tool_calls=[
                        ToolCall(
                            call_id="call_echo",
                            name="echo",
                            arguments={"text": "hi"},
                        )
                    ],
                    response_id="resp_echo",
                )
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
                response_id="resp_done",
            )

    model_io = _OpenAIModelIO()
    agent = Agent(
        name="openai-fyi-regression",
        provider="openai",
        model="gpt-5",
        modules=(
            ToolsModule(tools=(echo,)),
            InteractionModule(fyi_channel=channel),
        ),
        model_io_factory=lambda spec, context: model_io,
    )

    result = agent.run(
        "do the task",
        payload={"store": True},
        max_iterations=3,
    )

    assert result.status == "completed"
    assert len(seen_requests) == 2
    assert seen_requests[1].previous_response_id == "resp_echo"
    assert any(
        "<fyi_message>" in str(message.get("content", ""))
        for message in seen_requests[1].messages
    )


def test_last_n_preserves_history_when_summary_has_no_replacement():
    history: list[dict[str, Any]] = [
        {"role": "system", "content": "system"},
    ]
    for index in range(5):
        user_content = (
            "EARLY_OBJECTIVE_MUST_SURVIVE " + ("U" * 1_000)
            if index == 0
            else f"u{index} " + ("U" * 1_000)
        )
        history.extend(
            [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": f"a{index} " + ("A" * 1_000)},
            ]
        )

    loop = KernelLoop(
        harnesses=[
            LlmSummaryOptimizer(
                LlmSummaryOptimizerConfig(
                    summary_trigger_pct=0.2,
                    summary_target_pct=0.1,
                    max_summary_chars=200,
                    summary_generator=None,
                )
            ),
            LastNOptimizer(LastNOptimizerConfig(last_n_turns=2)),
        ]
    )
    state = loop.seed_state(
        history,
        model="gpt-5",
        max_context_window_tokens=500,
    )

    loop.dispatch_phase(
        state,
        phase="before_model",
        event={"toolkit": Toolkit()},
    )

    assert state.optimizer_state["llm_summary"]["summary_fallback_reason"] == (
        "summary_generator_missing"
    )
    assert state.optimizer_state["last_n"]["skip_reason"] == (
        "upstream_summary_replacement_unavailable"
    )
    assert any(
        "EARLY_OBJECTIVE_MUST_SURVIVE" in str(message.get("content", ""))
        for message in state.latest_messages()
    )
