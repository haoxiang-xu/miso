import json

import pytest

from unchain.kernel import KernelLoop, ModelTurnResult
from unchain.optimizers import (
    LastNOptimizer,
    LastNOptimizerConfig,
    LlmSummaryOptimizer,
    LlmSummaryOptimizerConfig,
    SlidingWindowOptimizer,
    SlidingWindowOptimizerConfig,
    ToolHistoryCompactionOptimizer,
    ToolHistoryCompactionOptimizerConfig,
)
from unchain.tools import Toolkit


def _conversation_with_tool_turn() -> list[dict]:
    return [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "demo", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
        {"role": "assistant", "content": "a3"},
    ]


def _build_openai_tool_turn(*, tool_name: str, call_id: str, arguments: dict, result: dict) -> list[dict]:
    return [
        {"role": "user", "content": "u1"},
        {"type": "function_call", "call_id": call_id, "name": tool_name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, ensure_ascii=False)},
        {"role": "assistant", "content": "a1"},
    ]


def _build_ollama_tool_turn(*, tool_name: str, call_id: str, arguments: dict, result: dict) -> list[dict]:
    return [
        {"role": "user", "content": "u1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "function": {"name": tool_name, "arguments": arguments}}],
        },
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)},
        {"role": "assistant", "content": "a1"},
    ]


def _build_anthropic_tool_turn(*, tool_name: str, call_id: str, arguments: dict, result: dict) -> list[dict]:
    return [
        {"role": "user", "content": "u1"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "id": call_id, "name": tool_name, "input": arguments},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": json.dumps(result, ensure_ascii=False)}],
        },
        {"role": "assistant", "content": "a1"},
    ]


def _build_gemini_tool_turn(*, tool_name: str, call_id: str, arguments: dict, result: dict) -> list[dict]:
    return [
        {"role": "user", "parts": [{"text": "u1"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "id": call_id, "name": tool_name, "input": arguments},
            ],
        },
        {
            "role": "user",
            "parts": [{"function_response": {"name": tool_name, "response": result}}],
        },
        {"role": "assistant", "content": "a1"},
    ]


class _FakeModelIO:
    def __init__(self, result: ModelTurnResult) -> None:
        self.result = result
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        return self.result


def test_last_n_optimizer_keeps_system_and_complete_recent_turns():
    loop = KernelLoop(
        harnesses=[
            LastNOptimizer(LastNOptimizerConfig(last_n_turns=2)),
        ]
    )
    original = _conversation_with_tool_turn()
    state = loop.seed_state(original)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    roles = [message.get("role") for message in state.latest_messages()]
    assert roles[0] == "system"
    assert roles.count("user") == 2
    assert any(message.get("role") == "tool" and message.get("tool_call_id") == "call_1" for message in state.latest_messages())
    assert state.transcript == original


def test_llm_summary_optimizer_triggers_on_token_threshold():
    loop = KernelLoop(
        harnesses=[
            LlmSummaryOptimizer(
                LlmSummaryOptimizerConfig(
                    summary_trigger_pct=0.2,
                    summary_target_pct=0.1,
                    max_summary_chars=200,
                    summary_generator=lambda prev, msgs, max_chars, model: f"{prev}\nsummary-{len(msgs)}-{model}"[:max_chars],
                )
            ),
        ]
    )
    history = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "A" * 1200},
        {"role": "assistant", "content": "B" * 1200},
        {"role": "user", "content": "C" * 1200},
        {"role": "assistant", "content": "D" * 1200},
    ]
    state = loop.seed_state(history, model="gpt-5", max_context_window_tokens=1200)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    assert any(
        message.get("role") == "system"
        and isinstance(message.get("content"), str)
        and message["content"].startswith("[CONTEXT SUMMARY]")
        for message in state.latest_messages()
    )
    assert state.optimizer_state["llm_summary"]["summary_triggered"] is True
    assert state.transcript == history


def test_llm_summary_optimizer_falls_back_without_interrupting():
    loop = KernelLoop(
        harnesses=[
            LlmSummaryOptimizer(
                LlmSummaryOptimizerConfig(
                    summary_trigger_pct=0.2,
                    summary_target_pct=0.1,
                    max_summary_chars=200,
                    summary_generator=lambda prev, msgs, max_chars, model: (_ for _ in ()).throw(RuntimeError("boom")),
                )
            ),
        ]
    )
    history = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "u1 " * 600},
        {"role": "assistant", "content": "a1 " * 600},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    state = loop.seed_state(history, model="gpt-5", max_context_window_tokens=800)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    assert not any(
        message.get("role") == "system"
        and isinstance(message.get("content"), str)
        and message["content"].startswith("[CONTEXT SUMMARY]")
        for message in state.latest_messages()
    )
    assert "summary_fallback_reason" in state.optimizer_state["llm_summary"]
    assert state.transcript == history


@pytest.mark.parametrize(
    ("provider", "builder"),
    [
        ("openai", _build_openai_tool_turn),
        ("ollama", _build_ollama_tool_turn),
        ("anthropic", _build_anthropic_tool_turn),
        ("gemini", _build_gemini_tool_turn),
    ],
)
def test_tool_history_compaction_optimizer_handles_all_provider_shapes(provider, builder):
    captured_contexts: list[dict] = []

    def _arguments_optimizer(payload, context):
        captured_contexts.append({"kind": context.kind, "messages": json.dumps(context.latest_messages, ensure_ascii=False)})
        return {"compacted": True, "provider": context.provider, "kind": context.kind, "payload_type": type(payload).__name__}

    def _result_optimizer(payload, context):
        captured_contexts.append({"kind": context.kind, "messages": json.dumps(context.latest_messages, ensure_ascii=False)})
        return {"compacted": True, "provider": context.provider, "kind": context.kind, "payload_type": type(payload).__name__}

    toolkit = Toolkit()
    toolkit.register(
        lambda **kwargs: {},
        name="demo",
        history_arguments_optimizer=_arguments_optimizer,
        history_result_optimizer=_result_optimizer,
    )

    history = (
        builder(
            tool_name="demo",
            call_id="call_old",
            arguments={"path": "old.txt", "content": "A" * 2400},
            result={"content": "B" * 2400},
        )
        + [{"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}, {"role": "user", "content": "u3"}]
    )
    state = KernelLoop().seed_state(history, provider=provider, session_id=f"s_compact_{provider}")
    optimizer = ToolHistoryCompactionOptimizer()
    loop = KernelLoop(harnesses=[optimizer])

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": toolkit})

    info = state.optimizer_state["tool_history_compaction"]
    assert info.get("applied") is True
    assert info.get("deferred_compaction_turns_compacted") == 1
    assert captured_contexts
    for item in captured_contexts:
        assert '"u2"' in item["messages"]
        assert '"u3"' in item["messages"]
        assert '"u1"' not in item["messages"]

    prepared = state.latest_messages()
    if provider == "openai":
        function_call = next(message for message in prepared if message.get("type") == "function_call" and message.get("call_id") == "call_old")
        function_output = next(message for message in prepared if message.get("type") == "function_call_output" and message.get("call_id") == "call_old")
        assert json.loads(function_call["arguments"])["compacted"] is True
        assert json.loads(function_output["output"])["compacted"] is True
    elif provider == "ollama":
        assistant_tool_call = next(message for message in prepared if isinstance(message.get("tool_calls"), list))
        tool_result = next(message for message in prepared if message.get("role") == "tool")
        assert assistant_tool_call["tool_calls"][0]["function"]["arguments"]["compacted"] is True
        assert json.loads(tool_result["content"])["compacted"] is True
    elif provider == "anthropic":
        assistant_tool_use = next(message for message in prepared if isinstance(message.get("content"), list) and any(block.get("type") == "tool_use" for block in message["content"]))
        tool_result = next(message for message in prepared if isinstance(message.get("content"), list) and any(block.get("type") == "tool_result" for block in message["content"]))
        tool_use_block = next(block for block in assistant_tool_use["content"] if block.get("type") == "tool_use")
        tool_result_block = next(block for block in tool_result["content"] if block.get("type") == "tool_result")
        assert tool_use_block["input"]["compacted"] is True
        assert json.loads(tool_result_block["content"])["compacted"] is True
    else:
        assistant_tool_use = next(message for message in prepared if isinstance(message.get("content"), list) and any(block.get("type") == "tool_use" for block in message["content"]))
        tool_result = next(message for message in prepared if isinstance(message.get("parts"), list) and any("function_response" in part for part in message["parts"]))
        tool_use_block = next(block for block in assistant_tool_use["content"] if block.get("type") == "tool_use")
        function_response = next(part["function_response"] for part in tool_result["parts"] if "function_response" in part)
        assert tool_use_block["input"]["compacted"] is True
        assert function_response["response"]["compacted"] is True


def test_tool_history_compaction_optimizer_preserves_latest_completed_turn_raw():
    history = (
        _build_openai_tool_turn(tool_name="demo", call_id="call_old", arguments={"path": "old.txt", "content": "A" * 2000}, result={"ok": True})
        + _build_openai_tool_turn(tool_name="demo", call_id="call_latest", arguments={"path": "latest.txt", "content": "B" * 2000}, result={"ok": True})
        + [{"role": "user", "content": "u3"}]
    )
    loop = KernelLoop(
        harnesses=[
            ToolHistoryCompactionOptimizer(),
        ]
    )
    state = loop.seed_state(history, provider="openai", session_id="s_keep_latest_completed_raw")

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    old_call = next(message for message in state.latest_messages() if message.get("type") == "function_call" and message.get("call_id") == "call_old")
    latest_call = next(message for message in state.latest_messages() if message.get("type") == "function_call" and message.get("call_id") == "call_latest")
    assert json.loads(old_call["arguments"])["compacted"] is True
    assert json.loads(latest_call["arguments"]) == {"path": "latest.txt", "content": "B" * 2000}


def test_context_optimizer_pipeline_order_is_compaction_then_summary_then_last_n():
    history = [
        {"role": "system", "content": "You are helpful."},
        *_build_openai_tool_turn(tool_name="demo", call_id="call_old", arguments={"path": "old.txt", "content": "A" * 2200}, result={"content": "B" * 2200}),
        {"role": "user", "content": "C" * 1200},
        {"role": "assistant", "content": "D" * 1200},
        {"role": "user", "content": "latest question"},
    ]
    model_io = _FakeModelIO(
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="resp_order",
            consumed_tokens=7,
            input_tokens=4,
            output_tokens=3,
        )
    )
    loop = KernelLoop(
        model_io=model_io,
        harnesses=[
            LastNOptimizer(LastNOptimizerConfig(last_n_turns=0)),
            LlmSummaryOptimizer(
                LlmSummaryOptimizerConfig(
                    summary_trigger_pct=0.2,
                    summary_target_pct=0.1,
                    max_summary_chars=200,
                    summary_generator=lambda prev, msgs, max_chars, model: "summary text",
                )
            ),
            ToolHistoryCompactionOptimizer(ToolHistoryCompactionOptimizerConfig(keep_completed_turns=1)),
        ],
    )
    state = loop.seed_state(
        history,
        provider="openai",
        model="gpt-5",
        session_id="pipeline-order",
        max_context_window_tokens=1200,
    )

    loop.step_once(state, toolkit=Toolkit())

    optimizer_lineage = [
        state.versions.get_version(version_id).created_by
        for version_id in state.versions.lineage()
        if state.versions.get_version(version_id).created_by
        and str(state.versions.get_version(version_id).created_by).startswith("optimizer.")
    ]
    assert optimizer_lineage == [
        "optimizer.tool_history_compaction",
        "optimizer.llm_summary",
        "optimizer.last_n",
    ]
    request_messages = model_io.requests[0].messages
    assert any(message.get("role") == "system" and "[CONTEXT SUMMARY]" in str(message.get("content")) for message in request_messages)


def test_context_optimizers_only_mutate_versions_not_transcript():
    original = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "A" * 1200},
        {"role": "assistant", "content": "B" * 1200},
        {"role": "user", "content": "latest"},
    ]
    loop = KernelLoop(
        harnesses=[
            LlmSummaryOptimizer(
                LlmSummaryOptimizerConfig(
                    summary_trigger_pct=0.2,
                    summary_target_pct=0.1,
                    max_summary_chars=200,
                    summary_generator=lambda prev, msgs, max_chars, model: "summary text",
                )
            ),
        ]
    )
    state = loop.seed_state(original, session_id="optimizer-transcript", model="gpt-5", max_context_window_tokens=1000)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    assert state.transcript == original
    assert state.latest_messages() != original


def test_sliding_window_optimizer_drops_oldest_turns_exceeding_token_budget():
    loop = KernelLoop(
        harnesses=[
            SlidingWindowOptimizer(SlidingWindowOptimizerConfig(max_window_tokens=200)),
        ]
    )
    original = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 400},
        {"role": "assistant", "content": "B" * 400},
        {"role": "user", "content": "C" * 40},
        {"role": "assistant", "content": "D" * 40},
    ]
    state = loop.seed_state(original)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    msgs = state.latest_messages()
    roles = [m.get("role") for m in msgs]
    assert roles[0] == "system"
    assert "A" * 400 not in str(msgs), "old large turn should be dropped"
    assert "C" * 40 in str(msgs), "recent small turn should be kept"
    assert state.transcript == original


def test_sliding_window_optimizer_preserves_system_messages_and_counts_toward_budget():
    loop = KernelLoop(
        harnesses=[
            SlidingWindowOptimizer(SlidingWindowOptimizerConfig(max_window_tokens=100)),
        ]
    )
    original = [
        {"role": "system", "content": "S" * 500},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    state = loop.seed_state(original)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    msgs = state.latest_messages()
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "S" * 500
    non_system = [m for m in msgs if m.get("role") != "system"]
    assert [m.get("role") for m in non_system] == ["user", "assistant"]
    assert state.optimizer_state["sliding_window"]["forced_keep_latest_turn"] is True


def test_sliding_window_optimizer_keeps_latest_turn_when_budget_would_drop_everything():
    loop = KernelLoop(
        harnesses=[
            SlidingWindowOptimizer(SlidingWindowOptimizerConfig(max_window_tokens=200)),
        ]
    )
    original = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 400},
        {"role": "assistant", "content": "B" * 400},
    ]
    state = loop.seed_state(original)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    msgs = state.latest_messages()
    assert [m.get("role") for m in msgs] == ["system", "user", "assistant"]
    assert state.optimizer_state["sliding_window"]["kept_turn_count"] == 1
    assert state.optimizer_state["sliding_window"]["forced_keep_latest_turn"] is True


def test_sliding_window_optimizer_keeps_all_when_under_budget():
    loop = KernelLoop(
        harnesses=[
            SlidingWindowOptimizer(SlidingWindowOptimizerConfig(max_window_tokens=100000)),
        ]
    )
    original = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    state = loop.seed_state(original)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    assert [m.get("role") for m in state.latest_messages()] == ["system", "user", "assistant"]


def test_sliding_window_optimizer_uses_percentage_of_max_context():
    loop = KernelLoop(
        harnesses=[
            SlidingWindowOptimizer(SlidingWindowOptimizerConfig(max_window_pct=0.5)),
        ]
    )
    original = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "A" * 800},
        {"role": "assistant", "content": "B" * 800},
        {"role": "user", "content": "short"},
        {"role": "assistant", "content": "short"},
    ]
    state = loop.seed_state(original, max_context_window_tokens=600)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    msgs = state.latest_messages()
    assert "A" * 800 not in str(msgs), "old large turn dropped"
    assert state.optimizer_state["sliding_window"]["effective_budget_tokens"] == 300


def test_sliding_window_optimizer_takes_min_of_pct_and_absolute():
    loop = KernelLoop(
        harnesses=[
            SlidingWindowOptimizer(SlidingWindowOptimizerConfig(max_window_pct=0.9, max_window_tokens=100)),
        ]
    )
    original = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    state = loop.seed_state(original, max_context_window_tokens=10000)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    assert state.optimizer_state["sliding_window"]["effective_budget_tokens"] == 100


def test_sliding_window_optimizer_is_noop_when_no_budget_available():
    loop = KernelLoop(
        harnesses=[
            SlidingWindowOptimizer(SlidingWindowOptimizerConfig(max_window_pct=0.7, max_window_tokens=None)),
        ]
    )
    original = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    state = loop.seed_state(original, max_context_window_tokens=0)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    assert [m.get("role") for m in state.latest_messages()] == ["system", "user", "assistant"]


def test_sliding_window_optimizer_runs_at_order_25():
    sw = SlidingWindowOptimizer()
    last_n = LastNOptimizer()
    assert sw.order == 25
    assert last_n.order == 30
    assert sw.order < last_n.order
