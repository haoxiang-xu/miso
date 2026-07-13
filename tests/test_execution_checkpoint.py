from __future__ import annotations

import copy

import pytest

from unchain.kernel import BaseRuntimeHarness, HarnessDelta, ModelTurnResult, RunState
from unchain.kernel.types import ToolCall
from unchain.memory import (
    ExecutionCheckpointCompatibilityError,
    ExecutionCheckpointIntegrityError,
    ExecutionCheckpointPersistenceError,
    ExecutionCheckpointReplayUnavailableError,
    InMemorySessionStore,
    KernelMemoryRuntime,
)
from unchain.runtime import attach_memory_runtime_components, build_runtime_loop
from unchain.tools import Toolkit
from unchain.memory.checkpoint_state import (
    build_execution_checkpoint,
    validate_execution_checkpoint,
)


def _checkpoint_state(*, provider: str = "ollama", model: str = "fake") -> RunState:
    state = RunState()
    state.seed_messages([{"role": "user", "content": "start"}])
    state.session_state.session_id = "checkpoint-session"
    state.provider_state.provider = provider
    state.provider_state.model = model
    state.iteration = 1
    state.run_status = "max_iterations"
    return state


def test_execution_checkpoint_is_deterministic_and_detects_tampering():
    state = _checkpoint_state()

    first = build_execution_checkpoint(
        state,
        status="max_iterations",
        run_id="run-1",
    )
    second = build_execution_checkpoint(
        state,
        status="max_iterations",
        run_id="run-1",
    )

    assert first == second
    assert validate_execution_checkpoint(first) == first

    tampered = copy.deepcopy(first)
    tampered["transcript"].append({"role": "assistant", "content": "changed"})
    with pytest.raises(ExecutionCheckpointIntegrityError, match="hash mismatch"):
        validate_execution_checkpoint(tampered)


def test_execution_checkpoint_provider_mismatch_fails_before_restore():
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    checkpoint = build_execution_checkpoint(
        _checkpoint_state(provider="ollama", model="fake"),
        status="max_iterations",
        run_id="run-1",
    )
    runtime.save_execution_checkpoint("checkpoint-session", checkpoint)

    with pytest.raises(ExecutionCheckpointCompatibilityError, match="provider mismatch"):
        runtime.bootstrap_session(
            session_id="checkpoint-session",
            memory_namespace=None,
            incoming_messages=[],
            resume_mode=False,
            provider="openai",
            model="fake",
        )


def test_incomplete_reasoning_replay_frame_fails_closed_on_restore():
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    state = _checkpoint_state(provider="openai", model="gpt-5")
    state.last_model_turn = ModelTurnResult(
        assistant_messages=[
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "demo",
                "arguments": "{}",
            }
        ],
        tool_calls=[],
        reasoning_items=[{"type": "reasoning", "encrypted_content": "opaque"}],
    )
    checkpoint = build_execution_checkpoint(
        state,
        status="max_iterations",
        run_id="run-1",
    )
    assert checkpoint["replay_frame"]["complete"] is False
    runtime.save_execution_checkpoint("checkpoint-session", checkpoint)

    with pytest.raises(ExecutionCheckpointReplayUnavailableError, match="complete provider replay"):
        runtime.bootstrap_session(
            session_id="checkpoint-session",
            memory_namespace=None,
            incoming_messages=[],
            resume_mode=False,
            provider="openai",
            model="gpt-5",
        )


def test_checkpoint_write_must_survive_readback_verification():
    class _DroppingStore:
        def load(self, session_id):
            del session_id
            return {}

        def save(self, session_id, state):
            del session_id, state

    runtime = KernelMemoryRuntime.from_config(store=_DroppingStore())
    checkpoint = build_execution_checkpoint(
        _checkpoint_state(),
        status="max_iterations",
        run_id="run-1",
    )

    with pytest.raises(ExecutionCheckpointPersistenceError, match="verification failed"):
        runtime.save_execution_checkpoint("checkpoint-session", checkpoint)


def test_checkpoint_preserves_multiple_system_layers_and_rejects_agent_change():
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    state = _checkpoint_state()
    state.transcript = [
        {"role": "system", "content": "AGENT SYS"},
        {"role": "system", "content": "CALLER SYS"},
        {"role": "user", "content": "start"},
    ]
    checkpoint = build_execution_checkpoint(
        state,
        status="max_iterations",
        run_id="run-1",
    )
    runtime.save_execution_checkpoint("checkpoint-session", checkpoint)

    merged, _, _, _ = runtime.bootstrap_session(
        session_id="checkpoint-session",
        memory_namespace=None,
        incoming_messages=[
            {"role": "system", "content": "AGENT SYS"},
            {"role": "user", "content": "continue"},
        ],
        resume_mode=False,
        provider="ollama",
        model="fake",
    )

    assert merged[:2] == [
        {"role": "system", "content": "AGENT SYS"},
        {"role": "system", "content": "CALLER SYS"},
    ]
    assert merged[-1] == {"role": "user", "content": "continue"}

    with pytest.raises(ExecutionCheckpointCompatibilityError, match="instructions"):
        runtime.bootstrap_session(
            session_id="checkpoint-session",
            memory_namespace=None,
            incoming_messages=[{"role": "system", "content": "OTHER AGENT"}],
            resume_mode=False,
            provider="ollama",
            model="fake",
        )


def test_tool_driven_completed_path_repairs_semantic_commit_before_checkpoint_clear():
    class _CompleteAfterToolHarness(BaseRuntimeHarness):
        def __init__(self):
            super().__init__(
                name="complete_after_tool",
                phases=("after_tool_batch",),
                order=200,
            )

        def build_delta(self, context):
            return HarnessDelta(
                created_by=self.name,
                state_updates={"run_status": "completed"},
            )

    class _ToolModelIO:
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
                                "id": "call-finish",
                                "function": {"name": "finish", "arguments": {}},
                            }
                        ],
                    }
                ],
                tool_calls=[
                    ToolCall(call_id="call-finish", name="finish", arguments={})
                ],
            )

    class _LateFinalizingHarness(BaseRuntimeHarness):
        def __init__(self):
            super().__init__(
                name="late_finalizing_contributor",
                phases=("run_finalizing",),
                order=10_000,
            )

        def build_delta(self, context):
            return HarnessDelta(
                created_by=self.name,
                state_updates={
                    "transcript_append": [
                        {"role": "system", "content": "FINALIZED MARKER"}
                    ]
                },
            )

    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    loop = build_runtime_loop(
        model_io=_ToolModelIO(),
        harnesses=[_CompleteAfterToolHarness(), _LateFinalizingHarness()],
    )
    attach_memory_runtime_components(loop, runtime)
    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="finish")

    events: list[dict] = []

    def assert_durable_before_final_message(event):
        events.append(event)
        if event["type"] != "final_message":
            return
        persisted = store.load("tool-completed-session")
        assert persisted["messages"][-1] == {
            "role": "system",
            "content": "FINALIZED MARKER",
        }
        assert "execution_checkpoint" not in persisted

    result = loop.run(
        [{"role": "user", "content": "finish now"}],
        session_id="tool-completed-session",
        provider="ollama",
        model="fake",
        toolkit=toolkit,
        callback=assert_durable_before_final_message,
    )

    assert result.status == "completed"
    assert result.messages[-1] == {
        "role": "system",
        "content": "FINALIZED MARKER",
    }
    assert store.load("tool-completed-session")["messages"] == result.messages
    assert sum(event["type"] == "final_message" for event in events) == 1


def test_max_checkpoint_cold_restore_preserves_runtime_counters_and_adds_run_budget():
    restored: list[dict[str, object]] = []

    class _CaptureRestoredState(BaseRuntimeHarness):
        def __init__(self):
            super().__init__(
                name="capture_restored_checkpoint_state",
                phases=("before_model",),
                order=1,
            )

        def build_delta(self, context):
            restored.append(
                {
                    "iteration": context.state.iteration,
                    "token_state": copy.deepcopy(
                        vars(context.state.token_state)
                    ),
                    "workspace_change_state": copy.deepcopy(
                        context.state.workspace_change_state
                    ),
                    "workspace_component_state": copy.deepcopy(
                        context.state.component_bucket("workspace_changes").get(
                            "state"
                        )
                    ),
                    "max_context_window_tokens": (
                        context.state.provider_state.max_context_window_tokens
                    ),
                }
            )
            return None

    class _FinalModelIO:
        provider = "ollama"
        model = "fake"

        def __init__(self):
            self.request_count = 0

        def fetch_turn(self, request):
            del request
            self.request_count += 1
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
                consumed_tokens=7,
                input_tokens=5,
                output_tokens=2,
                cache_read_input_tokens=3,
                cache_creation_input_tokens=1,
            )

    checkpoint_state = _checkpoint_state()
    checkpoint_state.iteration = 4
    checkpoint_state.provider_state.max_context_window_tokens = 32_768
    checkpoint_state.token_state.consumed_tokens = 101
    checkpoint_state.token_state.input_tokens = 70
    checkpoint_state.token_state.output_tokens = 31
    checkpoint_state.token_state.cache_read_input_tokens = 17
    checkpoint_state.token_state.cache_creation_input_tokens = 13
    checkpoint_state.token_state.last_turn_tokens = 19
    checkpoint_state.token_state.last_turn_input_tokens = 12
    checkpoint_state.token_state.last_turn_output_tokens = 7
    checkpoint_state.token_state.last_turn_cache_read_input_tokens = 5
    checkpoint_state.token_state.last_turn_cache_creation_input_tokens = 3
    checkpoint_state.workspace_change_state = {
        "changed_paths": ["src/demo.py"],
        "generation": 2,
    }

    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    runtime.save_execution_checkpoint(
        "checkpoint-session",
        build_execution_checkpoint(
            checkpoint_state,
            status="max_iterations",
            run_id="run-before-restart",
        ),
    )
    model_io = _FinalModelIO()
    loop = build_runtime_loop(
        model_io=model_io,
        memory_runtime=runtime,
        harnesses=[_CaptureRestoredState()],
    )

    result = loop.run(
        [],
        session_id="checkpoint-session",
        provider="ollama",
        model="fake",
        max_iterations=1,
    )

    assert model_io.request_count == 1
    assert result.status == "completed"
    assert result.iteration == 5
    assert restored == [
        {
            "iteration": 4,
            "token_state": {
                "consumed_tokens": 101,
                "input_tokens": 70,
                "output_tokens": 31,
                "cache_read_input_tokens": 17,
                "cache_creation_input_tokens": 13,
                "last_turn_tokens": 19,
                "last_turn_input_tokens": 12,
                "last_turn_output_tokens": 7,
                "last_turn_cache_read_input_tokens": 5,
                "last_turn_cache_creation_input_tokens": 3,
            },
            "workspace_change_state": {
                "changed_paths": ["src/demo.py"],
                "generation": 2,
            },
            "workspace_component_state": {
                "changed_paths": ["src/demo.py"],
                "generation": 2,
            },
            "max_context_window_tokens": 32_768,
        }
    ]
    assert result.consumed_tokens == 108
    assert result.cache_read_input_tokens == 20
    assert result.cache_creation_input_tokens == 14
    assert "execution_checkpoint" not in store.load("checkpoint-session")


def test_max_checkpoint_cold_restore_preserves_pending_model_context_and_new_input():
    class _CaptureModelIO:
        provider = "ollama"
        model = "fake"

        def __init__(self):
            self.requests = []

        def fetch_turn(self, request):
            self.requests.append(request)
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
            )

    checkpoint_state = _checkpoint_state()
    checkpoint_state.next_model_input = [
        {"role": "system", "content": "PERSIST-ME"},
        {"role": "user", "content": "start"},
    ]
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    runtime.save_execution_checkpoint(
        "checkpoint-session",
        build_execution_checkpoint(
            checkpoint_state,
            status="max_iterations",
            run_id="run-before-restart",
        ),
    )
    model_io = _CaptureModelIO()
    loop = build_runtime_loop(model_io=model_io, memory_runtime=runtime)

    result = loop.run(
        [{"role": "user", "content": "continue"}],
        session_id="checkpoint-session",
        provider="ollama",
        model="fake",
        max_iterations=1,
    )

    assert result.status == "completed"
    request_messages = model_io.requests[0].messages
    assert {"role": "system", "content": "PERSIST-ME"} in request_messages
    assert {"role": "user", "content": "start"} in request_messages
    assert {"role": "user", "content": "continue"} in request_messages
