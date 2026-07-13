from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from unchain.agent import (
    Agent,
    AgentCallContext,
    AgentSpec,
    AgentState,
    MemoryModule,
    PreparedAgent,
    ToolOptimizerModule,
    ToolsModule,
)
from unchain.execution import (
    ActiveExecutionLeaseError,
    ExecutionLeaseConfig,
    ExecutionLeaseConflictError,
    ExecutionLeaseError,
    ExecutionLeaseExpiredError,
    ExecutionLeaseNotOwnedError,
    ExecutionRuntime,
    StaleExecutionLeaseError,
)
from unchain.input import ASK_USER_QUESTION_TOOL_NAME
from unchain.kernel import BaseRuntimeHarness, ModelTurnResult, ToolCall
from unchain.memory import InMemorySessionStore, KernelMemoryRuntime, MemoryManager
from unchain.retry import RetriesExhaustedError, RetryConfig
from unchain.runtime import (
    CompletionEvaluation,
    CompletionPolicy,
    build_runtime_loop,
)
from unchain.tools import Tool, ToolOptimizerConfig, Toolkit


_TTL_MS = 100
_LEASE_REJECTION_ERRORS = (
    ActiveExecutionLeaseError,
    ExecutionLeaseConflictError,
    ExecutionLeaseExpiredError,
    ExecutionLeaseNotOwnedError,
    StaleExecutionLeaseError,
)


class _ManualClock:
    def __init__(self) -> None:
        self._now_ms = 0
        self._lock = threading.Lock()

    def now_ms(self) -> int:
        with self._lock:
            return self._now_ms

    def advance(self, milliseconds: int) -> None:
        with self._lock:
            self._now_ms += int(milliseconds)


class _QueueModelIO:
    provider = "ollama"
    model = "fake"

    def __init__(self, turns: list[ModelTurnResult]) -> None:
        self.turns = list(turns)
        self.requests: list[Any] = []

    def fetch_turn(self, request: Any) -> ModelTurnResult:
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("unexpected model request")
        return self.turns.pop(0)


class _BlockingModelIO:
    provider = "ollama"
    model = "fake"

    def __init__(self, *, text: str) -> None:
        self.text = text
        self.entered = threading.Event()
        self.allow_response = threading.Event()
        self.requests: list[Any] = []

    def fetch_turn(self, request: Any) -> ModelTurnResult:
        self.requests.append(request)
        self.entered.set()
        if not self.allow_response.wait(timeout=3):
            raise AssertionError("test did not release blocked model response")
        return _final_turn(self.text)


class _RecordingLeaseStore(InMemorySessionStore):
    def __init__(self, *, clock_ms: Callable[[], int]) -> None:
        super().__init__(clock_ms=clock_ms)
        self.acquired_leases: list[Any] = []
        self.released_leases: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def acquire_lease(self, *args: Any, **kwargs: Any):
        lease = super().acquire_lease(*args, **kwargs)
        self.acquired_leases.append(lease)
        return lease

    def release_lease(self, *args: Any, **kwargs: Any) -> None:
        super().release_lease(*args, **kwargs)
        self.released_leases.append((args, dict(kwargs)))


class _TakeoverHarness(BaseRuntimeHarness):
    def __init__(
        self,
        *,
        phase: str,
        takeover: Callable[[], None],
    ) -> None:
        super().__init__(
            name=f"takeover_during_{phase}",
            phases=(phase,),
            order=10_000,
        )
        self._takeover = takeover
        self.calls = 0

    def build_delta(self, context):
        del context
        self.calls += 1
        self._takeover()
        return None


def _final_turn(text: str) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": text}],
        tool_calls=[],
        final_text=text,
        response_id=f"response-{text}",
    )


def _tool_turn(name: str, *, call_id: str = "call-tool") -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": "{}"},
                    }
                ],
            }
        ],
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments={})],
        response_id=f"response-{call_id}",
    )


def _ask_turn(call_id: str = "call-user") -> ModelTurnResult:
    arguments = {
        "title": "Choose stack",
        "question": "Which stack?",
        "selection_mode": "single",
        "options": [
            {"label": "React", "value": "react"},
            {"label": "Vue", "value": "vue"},
        ],
    }
    return ModelTurnResult(
        assistant_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": ASK_USER_QUESTION_TOOL_NAME,
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            }
        ],
        tool_calls=[
            ToolCall(
                call_id=call_id,
                name=ASK_USER_QUESTION_TOOL_NAME,
                arguments=arguments,
            )
        ],
        response_id=f"response-{call_id}",
    )


def _human_input_toolkit() -> Toolkit:
    toolkit = Toolkit()
    toolkit.register(
        lambda **_: {"error": "reserved"},
        name=ASK_USER_QUESTION_TOOL_NAME,
        parameters=[],
    )
    return toolkit


def _runtime_loop(
    *,
    store: InMemorySessionStore,
    model_io: Any,
    harnesses: list[Any] | None = None,
    retry_config: RetryConfig | None = None,
):
    memory_runtime = KernelMemoryRuntime.from_config(store=store)
    execution_runtime = ExecutionRuntime(
        store,
        config=ExecutionLeaseConfig(
            ttl_ms=_TTL_MS,
            heartbeat_interval_ms=0,
        ),
    )
    loop = build_runtime_loop(
        model_io=model_io,
        memory_runtime=memory_runtime,
        execution_runtime=execution_runtime,
        harnesses=harnesses,
        retry_config=retry_config,
    )
    return loop, memory_runtime


def _prepared_agent(
    *,
    loop: Any,
    memory_runtime: KernelMemoryRuntime,
    session_id: str,
    completion_policy: CompletionPolicy | None = None,
) -> PreparedAgent:
    return PreparedAgent(
        loop=loop,
        toolkit=Toolkit(),
        spec=AgentSpec(
            name="lease-test-agent",
            provider="ollama",
            model="fake",
        ),
        state=AgentState(),
        call_context=AgentCallContext(
            mode="run",
            input_messages=[{"role": "user", "content": "start"}],
            session_id=session_id,
            run_id=f"run-{session_id}",
            max_iterations=2,
        ),
        memory_runtime=memory_runtime,
        completion_policy=completion_policy,
        session_history_owned_by_memory=True,
    )


def _run_in_thread(call: Callable[[], Any]):
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["result"] = call()
        except BaseException as exc:  # noqa: BLE001 - the assertion inspects it
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def _assert_thread_finished(thread: threading.Thread) -> None:
    thread.join(timeout=3)
    assert not thread.is_alive(), "worker thread did not finish"


def _stored_final_text(store: InMemorySessionStore, session_id: str) -> str:
    messages = store.load(session_id).get("messages") or []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            return str(message.get("content") or "")
    return ""


def _fencing_token(lease: Any) -> int:
    return int(lease.fencing_token)


def test_same_session_prepared_agents_only_winner_enters_model() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock.now_ms)
    winner_io = _BlockingModelIO(text="winner")
    loser_io = _QueueModelIO([_final_turn("loser")])
    winner_loop, winner_memory = _runtime_loop(store=store, model_io=winner_io)
    loser_loop, loser_memory = _runtime_loop(store=store, model_io=loser_io)
    winner = _prepared_agent(
        loop=winner_loop,
        memory_runtime=winner_memory,
        session_id="competitive-session",
    )
    loser = _prepared_agent(
        loop=loser_loop,
        memory_runtime=loser_memory,
        session_id="competitive-session",
    )

    thread, outcome = _run_in_thread(winner.run)
    assert winner_io.entered.wait(timeout=3), "winner did not enter the model"

    with pytest.raises(ExecutionLeaseConflictError):
        loser.run()

    assert loser_io.requests == []
    winner_io.allow_response.set()
    _assert_thread_finished(thread)
    assert "error" not in outcome
    assert outcome["result"].status == "completed"
    assert _stored_final_text(store, "competitive-session") == "winner"


def test_model_response_is_rejected_after_in_flight_lease_takeover() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock.now_ms)
    stale_io = _BlockingModelIO(text="stale response")
    winner_io = _QueueModelIO([_final_turn("new owner response")])
    stale_loop, _ = _runtime_loop(store=store, model_io=stale_io)
    winner_loop, _ = _runtime_loop(store=store, model_io=winner_io)
    stale_events: list[dict[str, Any]] = []

    thread, outcome = _run_in_thread(
        lambda: stale_loop.run(
            [{"role": "user", "content": "old request"}],
            session_id="in-flight-session",
            provider="ollama",
            model="fake",
            callback=stale_events.append,
        )
    )
    assert stale_io.entered.wait(timeout=3), "stale owner did not enter the model"

    clock.advance(_TTL_MS + 1)
    winner = winner_loop.run(
        [{"role": "user", "content": "new request"}],
        session_id="in-flight-session",
        provider="ollama",
        model="fake",
    )
    assert winner.status == "completed"

    stale_io.allow_response.set()
    _assert_thread_finished(thread)
    assert isinstance(outcome.get("error"), _LEASE_REJECTION_ERRORS)
    assert _stored_final_text(store, "in-flight-session") == "new owner response"
    assert not any(
        event.get("type") in {"response_received", "final_message", "run_completed"}
        for event in stale_events
    )


def test_lease_takeover_between_retries_prevents_second_model_request() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock.now_ms)
    winner_io = _QueueModelIO([_final_turn("new owner response")])
    winner_loop, _ = _runtime_loop(store=store, model_io=winner_io)

    class _TakeoverThenTransientFailureModelIO:
        provider = "ollama"
        model = "fake"

        def __init__(self) -> None:
            self.call_count = 0

        def fetch_turn(self, request: Any) -> ModelTurnResult:
            del request
            self.call_count += 1
            if self.call_count == 1:
                clock.advance(_TTL_MS + 1)
                winner_loop.run(
                    [{"role": "user", "content": "take over"}],
                    session_id="retry-session",
                    provider="ollama",
                    model="fake",
                )
                raise httpx.ConnectError("transient after takeover")
            return _final_turn("must not be requested")

    stale_io = _TakeoverThenTransientFailureModelIO()
    stale_loop, _ = _runtime_loop(
        store=store,
        model_io=stale_io,
        retry_config=RetryConfig(
            max_retries=1,
            base_delay_ms=0,
            max_delay_ms=0,
            jitter_ratio=0,
        ),
    )

    with pytest.raises((*_LEASE_REJECTION_ERRORS, httpx.ConnectError, RetriesExhaustedError)):
        stale_loop.run(
            [{"role": "user", "content": "old request"}],
            session_id="retry-session",
            provider="ollama",
            model="fake",
        )

    assert stale_io.call_count == 1
    assert _stored_final_text(store, "retry-session") == "new owner response"


def test_takeover_inside_confirmation_callback_prevents_real_tool_execution() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock.now_ms)
    winner_loop, _ = _runtime_loop(
        store=store,
        model_io=_QueueModelIO([_final_turn("new owner response")]),
    )
    stale_loop, _ = _runtime_loop(
        store=store,
        model_io=_QueueModelIO([_tool_turn("dangerous")]),
    )
    real_tool_calls: list[str] = []
    toolkit = Toolkit()
    toolkit.register(
        lambda: real_tool_calls.append("executed") or {"ok": True},
        name="dangerous",
        parameters=[],
        requires_confirmation=True,
    )

    def confirm_after_takeover(_request: Any) -> dict[str, bool]:
        clock.advance(_TTL_MS + 1)
        winner_loop.run(
            [{"role": "user", "content": "take over"}],
            session_id="confirmation-session",
            provider="ollama",
            model="fake",
        )
        return {"approved": True}

    with pytest.raises(_LEASE_REJECTION_ERRORS):
        stale_loop.run(
            [{"role": "user", "content": "run dangerous"}],
            session_id="confirmation-session",
            provider="ollama",
            model="fake",
            toolkit=toolkit,
            on_tool_confirm=confirm_after_takeover,
        )

    assert real_tool_calls == []
    assert _stored_final_text(store, "confirmation-session") == "new owner response"


def test_tool_unfenced_store_write_raises_lease_error_instead_of_model_recovery() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock.now_ms)

    class _RecoveringModelIO:
        provider = "ollama"
        model = "fake"

        def __init__(self) -> None:
            self.call_count = 0

        def fetch_turn(self, request: Any) -> ModelTurnResult:
            del request
            self.call_count += 1
            if self.call_count == 1:
                return _tool_turn("unsafe_save", call_id="call-unsafe-save")
            return _final_turn("incorrectly recovered from safety failure")

    model_io = _RecoveringModelIO()
    loop, _ = _runtime_loop(store=store, model_io=model_io)
    toolkit = Toolkit()

    def unsafe_save() -> dict[str, bool]:
        store.save("unsafe-tool-session", {"unsafe": True})
        return {"saved": True}

    toolkit.register(unsafe_save, name="unsafe_save", parameters=[])

    with pytest.raises(ExecutionLeaseError):
        loop.run(
            [{"role": "user", "content": "try unsafe save"}],
            session_id="unsafe-tool-session",
            provider="ollama",
            model="fake",
            toolkit=toolkit,
            max_iterations=2,
        )

    assert model_io.call_count == 1
    assert "unsafe" not in store.load("unsafe-tool-session")

    # The legacy direct Tool.execute surface must preserve the same fatal
    # safety boundary rather than serializing it as a normal tool result.
    guard = ExecutionRuntime(
        store,
        config=ExecutionLeaseConfig(ttl_ms=_TTL_MS, heartbeat_interval_ms=0),
    ).acquire("unsafe-direct-session", owner_id="direct-owner")
    direct_tool = Tool.from_callable(
        lambda: store.save("unsafe-direct-session", {"unsafe": True}),
        name="unsafe_direct_save",
    )
    try:
        with pytest.raises(ExecutionLeaseError):
            direct_tool.execute({})
    finally:
        guard.release()
    assert "unsafe" not in store.load("unsafe-direct-session")


def test_confirmation_resolver_unfenced_write_cannot_become_tool_error() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock.now_ms)

    class _RecoveringModelIO:
        provider = "ollama"
        model = "fake"

        def __init__(self) -> None:
            self.call_count = 0

        def fetch_turn(self, request: Any) -> ModelTurnResult:
            del request
            self.call_count += 1
            if self.call_count == 1:
                return _tool_turn("resolved_tool", call_id="call-resolver")
            return _final_turn("incorrectly recovered from resolver safety failure")

    model_io = _RecoveringModelIO()
    loop, _ = _runtime_loop(store=store, model_io=model_io)
    real_tool_calls: list[str] = []
    toolkit = Toolkit()

    def unsafe_resolver(arguments: dict[str, Any], context: Any) -> bool:
        del arguments, context
        store.save("unsafe-resolver-session", {"unsafe": True})
        return False

    toolkit.register(
        lambda: real_tool_calls.append("executed") or {"ok": True},
        name="resolved_tool",
        parameters=[],
        requires_confirmation=True,
        confirmation_resolver=unsafe_resolver,
    )

    with pytest.raises(ExecutionLeaseError):
        loop.run(
            [{"role": "user", "content": "resolve unsafe policy"}],
            session_id="unsafe-resolver-session",
            provider="ollama",
            model="fake",
            toolkit=toolkit,
            max_iterations=2,
        )

    assert model_io.call_count == 1
    assert real_tool_calls == []
    assert "unsafe" not in store.load("unsafe-resolver-session")


def test_takeover_inside_deferred_confirmation_prevents_real_tool_execution() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock.now_ms)
    winner_loop, _ = _runtime_loop(
        store=store,
        model_io=_QueueModelIO([_final_turn("new owner response")]),
    )
    real_tool_calls: list[str] = []
    toolkit = Toolkit()
    for index in range(8):
        toolkit.register(
            Tool.from_callable(
                lambda value="", index=index: {
                    "tool": f"tool_{index}",
                    "value": value,
                },
                name=f"tool_{index}",
                description=f"Tool {index}.",
            )
        )

    def dangerous(path: str) -> dict[str, str]:
        real_tool_calls.append(path)
        return {"wrote": path}

    toolkit.register(
        Tool.from_callable(
            dangerous,
            name="dangerous",
            description="Dangerous deferred tool.",
            requires_confirmation=True,
        )
    )

    class _DeferredModelIO:
        provider = "openai"
        model = "gpt-5"

        def __init__(self) -> None:
            self.call_count = 0

        def fetch_turn(self, request: Any) -> ModelTurnResult:
            self.call_count += 1
            if self.call_count == 1:
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "role": "assistant",
                            "content": json.dumps({"tool_names": ["tool_0"]}),
                        }
                    ],
                    tool_calls=[],
                    final_text=json.dumps({"tool_names": ["tool_0"]}),
                )
            if self.call_count == 2:
                assert "dangerous" not in request.toolkit.tools
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call-deferred",
                            "name": "tool_execute_deferred",
                            "arguments": json.dumps(
                                {
                                    "tool_name": "dangerous",
                                    "arguments": {"path": "secret.txt"},
                                }
                            ),
                        }
                    ],
                    tool_calls=[
                        ToolCall(
                            call_id="call-deferred",
                            name="tool_execute_deferred",
                            arguments={
                                "tool_name": "dangerous",
                                "arguments": {"path": "secret.txt"},
                            },
                        )
                    ],
                )
            raise AssertionError("stale deferred execution requested another model turn")

    model_io = _DeferredModelIO()
    agent = Agent(
        name="deferred-lease-test",
        provider="openai",
        model="gpt-5",
        modules=(
            ToolsModule(tools=(toolkit,)),
            ToolOptimizerModule(
                config=ToolOptimizerConfig(
                    max_direct_tools=6,
                    trigger_tool_count=5,
                )
            ),
            MemoryModule(memory=MemoryManager(store=store)),
        ),
        model_io_factory=lambda spec, context: model_io,
    )

    def confirm_after_takeover(_request: Any) -> dict[str, bool]:
        # PreparedAgent's automatically assembled runtime uses the default
        # 60-second TTL.  Advancing the store-owned clock is deterministic and
        # does not wait for wall time or a heartbeat thread.
        clock.advance(60_001)
        winner_loop.run(
            [{"role": "user", "content": "take over"}],
            session_id="deferred-confirmation-session",
            provider="ollama",
            model="fake",
        )
        return {"approved": True}

    with pytest.raises(_LEASE_REJECTION_ERRORS):
        agent.run(
            "write secret",
            session_id="deferred-confirmation-session",
            max_iterations=2,
            on_tool_confirm=confirm_after_takeover,
        )

    assert model_io.call_count == 2
    assert real_tool_calls == []
    assert (
        _stored_final_text(store, "deferred-confirmation-session")
        == "new owner response"
    )


def test_takeover_between_on_suspend_and_persist_cannot_write_checkpoint() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock.now_ms)
    winner_loop, _ = _runtime_loop(
        store=store,
        model_io=_QueueModelIO([_final_turn("new owner response")]),
    )

    def take_over() -> None:
        clock.advance(_TTL_MS + 1)
        winner_loop.run(
            [{"role": "user", "content": "take over"}],
            session_id="suspend-session",
            provider="ollama",
            model="fake",
        )

    takeover = _TakeoverHarness(phase="on_suspend", takeover=take_over)
    stale_loop, _ = _runtime_loop(
        store=store,
        model_io=_QueueModelIO([_ask_turn()]),
        harnesses=[takeover],
    )

    with pytest.raises(_LEASE_REJECTION_ERRORS):
        stale_loop.run(
            [{"role": "user", "content": "ask me"}],
            session_id="suspend-session",
            provider="ollama",
            model="fake",
            toolkit=_human_input_toolkit(),
        )

    assert takeover.calls == 1
    persisted = store.load("suspend-session")
    assert "execution_checkpoint" not in persisted
    assert _stored_final_text(store, "suspend-session") == "new owner response"


def test_takeover_during_run_finalizing_blocks_old_final_events_and_writes() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock.now_ms)
    winner_loop, _ = _runtime_loop(
        store=store,
        model_io=_QueueModelIO([_final_turn("new owner response")]),
    )

    def take_over() -> None:
        clock.advance(_TTL_MS + 1)
        winner_loop.run(
            [{"role": "user", "content": "take over"}],
            session_id="finalizing-session",
            provider="ollama",
            model="fake",
        )

    takeover = _TakeoverHarness(phase="run_finalizing", takeover=take_over)
    stale_loop, _ = _runtime_loop(
        store=store,
        model_io=_QueueModelIO([_final_turn("stale response")]),
        harnesses=[takeover],
    )
    stale_events: list[dict[str, Any]] = []

    with pytest.raises(_LEASE_REJECTION_ERRORS):
        stale_loop.run(
            [{"role": "user", "content": "old request"}],
            session_id="finalizing-session",
            provider="ollama",
            model="fake",
            callback=stale_events.append,
        )

    assert takeover.calls == 1
    assert not any(
        event.get("type") in {"final_message", "run_completed"}
        for event in stale_events
    )
    persisted = store.load("finalizing-session")
    assert "execution_checkpoint" not in persisted
    assert _stored_final_text(store, "finalizing-session") == "new owner response"


def test_human_input_wait_persists_then_releases_and_reacquires_new_token() -> None:
    clock = _ManualClock()
    store = _RecordingLeaseStore(clock_ms=clock.now_ms)
    model_io = _QueueModelIO([_ask_turn(), _final_turn("React selected")])
    loop, _ = _runtime_loop(store=store, model_io=model_io)

    def provide_input(request: Any) -> dict[str, Any]:
        checkpoint = store.load("human-wait-session")["execution_checkpoint"]
        assert checkpoint["status"] == "awaiting_human_input"
        assert checkpoint["continuation"]["call_id"] == request.request_id
        assert len(store.released_leases) == 1
        return {
            "request_id": request.request_id,
            "selected_values": ["react"],
        }

    result = loop.run(
        [{"role": "user", "content": "pick one"}],
        session_id="human-wait-session",
        provider="ollama",
        model="fake",
        toolkit=_human_input_toolkit(),
        on_human_input=provide_input,
        max_iterations=3,
    )

    assert result.status == "completed"
    assert len(store.acquired_leases) == 2
    first_token, second_token = map(_fencing_token, store.acquired_leases)
    assert second_token > first_token
    assert len(store.released_leases) == 2
    assert "execution_checkpoint" not in store.load("human-wait-session")


def test_completion_repair_holds_one_active_token_for_entire_prepared_run() -> None:
    clock = _ManualClock()
    store = _RecordingLeaseStore(clock_ms=clock.now_ms)
    tokens_seen_by_model: list[int] = []

    class _RepairModelIO(_QueueModelIO):
        def fetch_turn(self, request: Any) -> ModelTurnResult:
            assert store.acquired_leases
            tokens_seen_by_model.append(_fencing_token(store.acquired_leases[-1]))
            return super().fetch_turn(request)

    model_io = _RepairModelIO(
        [_final_turn("draft answer"), _final_turn("final answer")]
    )
    loop, memory_runtime = _runtime_loop(store=store, model_io=model_io)

    def validate(result: Any) -> CompletionEvaluation:
        final_text = str(result.messages[-1].get("content") or "")
        if final_text == "final answer":
            return CompletionEvaluation(complete=True)
        return CompletionEvaluation(
            complete=False,
            feedback="Return the final answer.",
        )

    prepared = _prepared_agent(
        loop=loop,
        memory_runtime=memory_runtime,
        session_id="completion-session",
        completion_policy=CompletionPolicy(
            validator=validate,
            max_repair_turns=1,
        ),
    )

    result = prepared.run()

    assert result.status == "completed"
    assert len(model_io.requests) == 2
    assert len(store.acquired_leases) == 1
    assert tokens_seen_by_model == [
        _fencing_token(store.acquired_leases[0]),
        _fencing_token(store.acquired_leases[0]),
    ]
    assert len(store.released_leases) == 1
