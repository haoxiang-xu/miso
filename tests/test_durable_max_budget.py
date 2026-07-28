from __future__ import annotations

import pytest

from unchain.interaction.durable import (
    INTERACTION_JOURNAL_KEY,
    InteractionNotPendingError,
)
from unchain.interaction.runtime import DurableInteractionRuntime
from unchain.kernel import ModelTurnResult
from unchain.kernel.types import ToolCall
from unchain.memory import InMemorySessionStore, KernelMemoryRuntime
from unchain.runtime import build_runtime_loop
from unchain.tools import Toolkit


class _CrashAfterReceipt(RuntimeError):
    pass


class _QueueModelIO:
    provider = "openai"
    model = "gpt-5"

    def __init__(self, turns: list[ModelTurnResult]):
        self.turns = list(turns)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("unexpected model turn")
        return self.turns.pop(0)


def _tool_turn() -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[
            {
                "type": "function_call",
                "call_id": "call-tool",
                "name": "demo_tool",
                "arguments": "{}",
            }
        ],
        tool_calls=[ToolCall(call_id="call-tool", name="demo_tool", arguments={})],
        response_id="resp-tool",
    )


def _final_turn() -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "done"}],
        tool_calls=[],
        final_text="done",
        response_id="resp-final",
    )


def _toolkit() -> Toolkit:
    toolkit = Toolkit()
    toolkit.register(lambda: {"ok": True}, name="demo_tool")
    return toolkit


def _suspend_after_recording(
    *,
    session_id: str,
    store: InMemorySessionStore,
    response: dict,
):
    memory = KernelMemoryRuntime.from_config(store=store)
    interaction = DurableInteractionRuntime(memory, clock_ms=lambda: 100)
    loop = build_runtime_loop(
        model_io=_QueueModelIO([_tool_turn()]),
        memory_runtime=memory,
    )

    def record_then_crash(_payload):
        pending = interaction.load_active(session_id)
        interaction.record_receipt(
            session_id,
            interaction_id=pending.request.interaction_id,
            response=response,
            submitted_by="ui:test",
            expected_revision=pending.session_snapshot.revision,
        )
        raise _CrashAfterReceipt("worker stopped before applying max decision")

    with pytest.raises(_CrashAfterReceipt):
        loop.run(
            [{"role": "user", "content": "use a tool"}],
            session_id=session_id,
            provider="openai",
            model="gpt-5",
            toolkit=_toolkit(),
            max_iterations=1,
            on_max_iterations=record_then_crash,
        )
    pending = interaction.load_active(session_id)
    assert pending.receipt is not None
    return pending


def test_approved_budget_receipt_survives_crash_and_resumes_exact_extra() -> None:
    session_id = "max-budget-approved"
    store = InMemorySessionStore()
    pending = _suspend_after_recording(
        session_id=session_id,
        store=store,
        response={"approved": True, "extra_iterations": 1},
    )

    model = _QueueModelIO([_final_turn()])
    loop = build_runtime_loop(
        model_io=model,
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )
    resumed = loop.resume_interaction(
        session_id=session_id,
        response=None,
        toolkit=_toolkit(),
    )

    assert resumed.status == "completed"
    assert len(model.requests) == 1
    final_state = store.load(session_id)
    assert "execution_checkpoint" not in final_state
    entry = final_state[INTERACTION_JOURNAL_KEY]["entries"][
        pending.request.interaction_id
    ]
    assert entry["application"] is not None


def test_denied_budget_receipt_survives_crash_without_model_call() -> None:
    session_id = "max-budget-denied"
    store = InMemorySessionStore()
    pending = _suspend_after_recording(
        session_id=session_id,
        store=store,
        response={"approved": False},
    )

    model = _QueueModelIO([_final_turn()])
    loop = build_runtime_loop(
        model_io=model,
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )
    resumed = loop.resume_interaction(
        session_id=session_id,
        response=None,
        toolkit=_toolkit(),
    )

    assert resumed.status == "max_iterations"
    assert model.requests == []
    final_state = store.load(session_id)
    checkpoint = final_state["execution_checkpoint"]
    assert checkpoint["status"] == "max_iterations"
    assert "interaction_ref" not in checkpoint
    journal = final_state[INTERACTION_JOURNAL_KEY]
    assert journal["active_id"] is None
    assert journal["entries"][pending.request.interaction_id]["application"] is not None


def test_max_budget_resume_without_receipt_does_not_guess() -> None:
    session_id = "max-budget-unanswered"
    store = InMemorySessionStore()
    memory = KernelMemoryRuntime.from_config(store=store)
    loop = build_runtime_loop(
        model_io=_QueueModelIO([_tool_turn()]),
        memory_runtime=memory,
    )

    def stop_before_answer(_payload):
        raise _CrashAfterReceipt("no decision")

    with pytest.raises(_CrashAfterReceipt):
        loop.run(
            [{"role": "user", "content": "use a tool"}],
            session_id=session_id,
            provider="openai",
            model="gpt-5",
            toolkit=_toolkit(),
            max_iterations=1,
            on_max_iterations=stop_before_answer,
        )

    resume_model = _QueueModelIO([_final_turn()])
    resume_loop = build_runtime_loop(
        model_io=resume_model,
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )
    with pytest.raises(InteractionNotPendingError, match="no recorded receipt"):
        resume_loop.resume_interaction(
            session_id=session_id,
            response=None,
            toolkit=_toolkit(),
        )
    assert resume_model.requests == []
