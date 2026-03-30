import json
from pathlib import Path

import pytest

from unchain.kernel import KernelLoop, ModelTurnResult
from unchain.optimizers import (
    LastNOptimizer,
    LastNOptimizerConfig,
    LlmSummaryOptimizer,
    LlmSummaryOptimizerConfig,
    ToolHistoryCompactionOptimizer,
    ToolHistoryCompactionOptimizerConfig,
    WorkspacePinsOptimizer,
    WorkspacePinsOptimizerConfig,
)
from unchain.kernel.types import ToolCall as KernelToolCall
from unchain.memory import InMemorySessionStore
from unchain.tools import Toolkit
from unchain.workspace import build_pin_record, load_workspace_pins, save_workspace_pins


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


def test_workspace_pins_optimizer_live_resolves_updated_content_and_shifted_range(tmp_path):
    store = InMemorySessionStore()
    session_id = "pin-live-reload"
    file_path = tmp_path / "demo.py"
    file_path.write_text(
        "before\n"
        "def run_task():\n"
        "    value = 1\n"
        "    return value\n"
        "after\n",
        encoding="utf-8",
    )

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    pin = build_pin_record(
        path=file_path.resolve(),
        lines=lines,
        start=2,
        end=4,
        start_with="def run_task():",
        reason="keep function in view",
    )
    save_workspace_pins(store, session_id, {}, [pin])

    file_path.write_text(
        "intro\n"
        "before\n"
        "def run_task():\n"
        "    value = 2\n"
        "    return value\n"
        "after\n",
        encoding="utf-8",
    )

    loop = KernelLoop(
        harnesses=[
            WorkspacePinsOptimizer(WorkspacePinsOptimizerConfig(store=store)),
        ]
    )
    state = loop.seed_state([{"role": "user", "content": "check pins"}], session_id=session_id)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    latest_messages = state.latest_messages()
    assert len(latest_messages) == 3
    assert "status=resolved" in latest_messages[0]["content"]
    assert "current=lines=3-5" in latest_messages[0]["content"]
    assert "value = 2" in latest_messages[1]["content"]
    assert "value = 1" not in latest_messages[1]["content"]

    _, saved_pins = load_workspace_pins(store, session_id)
    assert saved_pins[0]["last_resolved"] == {"start": 3, "end": 5}


def test_workspace_pins_optimizer_marks_unresolved_without_stale_reinjection(tmp_path):
    store = InMemorySessionStore()
    session_id = "pin-unresolved"
    file_path = tmp_path / "demo.py"
    file_path.write_text(
        "before\n"
        "def run_task():\n"
        "    value = 1\n"
        "    return value\n"
        "after\n",
        encoding="utf-8",
    )

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    pin = build_pin_record(
        path=file_path.resolve(),
        lines=lines,
        start=2,
        end=4,
        start_with="def run_task():",
    )
    save_workspace_pins(store, session_id, {}, [pin])

    file_path.write_text(
        "before\n"
        "def other_task():\n"
        "    pass\n"
        "after\n",
        encoding="utf-8",
    )

    loop = KernelLoop(
        harnesses=[
            WorkspacePinsOptimizer(WorkspacePinsOptimizerConfig(store=store)),
        ]
    )
    state = loop.seed_state([{"role": "user", "content": "check pins"}], session_id=session_id)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    latest_messages = state.latest_messages()
    assert "status=unresolved" in latest_messages[0]["content"]
    assert "re-pin or unpin" in latest_messages[0]["content"]
    assert latest_messages[1]["content"].endswith("No live pinned content injected for this request.")
    assert "return value" not in latest_messages[1]["content"]


def test_context_optimizer_pipeline_order_is_compaction_then_summary_then_last_n_then_pins(tmp_path):
    store = InMemorySessionStore()
    session_id = "pipeline-order"
    pin_path = tmp_path / "notes.py"
    pin_path.write_text("def pinned():\n    return 'ok'\n", encoding="utf-8")
    pin = build_pin_record(
        path=pin_path.resolve(),
        lines=pin_path.read_text(encoding="utf-8").splitlines(keepends=True),
        start=1,
        end=2,
        start_with="def pinned():",
    )
    save_workspace_pins(store, session_id, {}, [pin])

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
                WorkspacePinsOptimizer(WorkspacePinsOptimizerConfig(store=store)),
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
        session_id=session_id,
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
        "optimizer.workspace_pins",
    ]
    request_messages = model_io.requests[0].messages
    assert any(message.get("role") == "system" and "[CONTEXT SUMMARY]" in str(message.get("content")) for message in request_messages)
    assert any(message.get("role") == "system" and "[PINNED SUMMARY]" in str(message.get("content")) for message in request_messages)


def test_context_optimizers_only_mutate_versions_not_transcript(tmp_path):
    store = InMemorySessionStore()
    session_id = "optimizer-transcript"
    file_path = tmp_path / "demo.py"
    file_path.write_text("def demo():\n    return 1\n", encoding="utf-8")
    pin = build_pin_record(
        path=Path(file_path).resolve(),
        lines=file_path.read_text(encoding="utf-8").splitlines(keepends=True),
        start=1,
        end=2,
        start_with="def demo():",
    )
    save_workspace_pins(store, session_id, {}, [pin])
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
            WorkspacePinsOptimizer(WorkspacePinsOptimizerConfig(store=store)),
        ]
    )
    state = loop.seed_state(original, session_id=session_id, model="gpt-5", max_context_window_tokens=1000)

    loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})

    assert state.transcript == original
    assert state.latest_messages() != original
    assert not any("[PINNED SUMMARY]" in str(message.get("content")) for message in state.transcript)
