from __future__ import annotations

from typing import Any

import pytest

from unchain.execution import (
    ExecutionLeaseConfig,
    ExecutionRuntime,
    StaleExecutionLeaseError,
)
from unchain.kernel import ModelTurnResult
from unchain.memory import InMemorySessionStore, KernelMemoryRuntime
from unchain.runtime import build_runtime_loop


class _ManualClock:
    def __init__(self) -> None:
        self.now_ms = 0

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, milliseconds: int) -> None:
        self.now_ms += milliseconds


class _FinalModelIO:
    provider = "ollama"
    model = "fake"

    def __init__(self, text: str) -> None:
        self.text = text

    def fetch_turn(self, request: Any) -> ModelTurnResult:
        del request
        return ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": self.text}],
            tool_calls=[],
            final_text=self.text,
            response_id=f"response-{self.text}",
        )


def _loop(store: InMemorySessionStore, text: str):
    memory_runtime = KernelMemoryRuntime.from_config(store=store)
    execution_runtime = ExecutionRuntime(
        store,
        ExecutionLeaseConfig(ttl_ms=100, heartbeat_interval_ms=0),
    )
    return build_runtime_loop(
        model_io=_FinalModelIO(text),
        memory_runtime=memory_runtime,
        execution_runtime=execution_runtime,
    )


def test_takeover_inside_final_message_callback_blocks_run_completed() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock)
    stale_loop = _loop(store, "stale response")
    winner_loop = _loop(store, "winner response")
    stale_events: list[dict[str, Any]] = []
    winner_statuses: list[str] = []

    def take_over_on_final_message(event: dict[str, Any]) -> None:
        stale_events.append(event)
        if event.get("type") != "final_message":
            return
        clock.advance(101)
        winner = winner_loop.run(
            [{"role": "user", "content": "take over"}],
            session_id="terminal-callback-session",
            provider="ollama",
            model="fake",
        )
        winner_statuses.append(winner.status)

    with pytest.raises(StaleExecutionLeaseError):
        stale_loop.run(
            [{"role": "user", "content": "old request"}],
            session_id="terminal-callback-session",
            provider="ollama",
            model="fake",
            callback=take_over_on_final_message,
        )

    assert winner_statuses == ["completed"]
    assert any(event.get("type") == "final_message" for event in stale_events)
    assert not any(event.get("type") == "run_completed" for event in stale_events)
    messages = store.load("terminal-callback-session").get("messages") or []
    assert messages[-1]["content"] == "winner response"
