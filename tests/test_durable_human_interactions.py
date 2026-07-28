from __future__ import annotations

import json

import pytest

from unchain.agent import Agent, MemoryModule
from unchain.input import ASK_USER_QUESTION_TOOL_NAME
from unchain.interaction.durable import (
    INTERACTION_JOURNAL_KEY,
    InteractionIntegrityError,
    InteractionNotPendingError,
)
from unchain.interaction.runtime import DurableInteractionRuntime
from unchain.kernel import ModelTurnResult
from unchain.kernel.types import ToolCall
from unchain.memory import InMemorySessionStore, JsonFileSessionStore, KernelMemoryRuntime
from unchain.runtime import build_runtime_loop
from unchain.tools import Toolkit


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


def _ask_turn() -> ModelTurnResult:
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
                "type": "function_call",
                "call_id": "call-user",
                "name": ASK_USER_QUESTION_TOOL_NAME,
                "arguments": json.dumps(arguments),
            }
        ],
        tool_calls=[
            ToolCall(
                call_id="call-user",
                name=ASK_USER_QUESTION_TOOL_NAME,
                arguments=arguments,
            )
        ],
        response_id="resp-ask",
    )


def _final_turn() -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "React selected"}],
        tool_calls=[],
        final_text="React selected",
        response_id="resp-final",
    )


def _toolkit() -> Toolkit:
    toolkit = Toolkit()
    toolkit.register(
        lambda **_: {"error": "reserved"},
        name=ASK_USER_QUESTION_TOOL_NAME,
        parameters=[],
    )
    return toolkit


def test_cold_worker_consumes_previously_recorded_human_receipt(tmp_path) -> None:
    session_id = "durable-human-cold"
    store = JsonFileSessionStore(tmp_path)
    first_model = _QueueModelIO([_ask_turn()])
    first_loop = build_runtime_loop(
        model_io=first_model,
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )

    suspended = first_loop.run(
        [{"role": "user", "content": "pick one"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=_toolkit(),
        max_iterations=3,
    )
    assert suspended.status == "awaiting_human_input"
    assert suspended.interaction_request is not None

    cold_memory = KernelMemoryRuntime.from_config(
        store=JsonFileSessionStore(tmp_path)
    )
    interaction = DurableInteractionRuntime(cold_memory, clock_ms=lambda: 100)
    pending = interaction.load_active(session_id)
    answered = interaction.record_receipt(
        session_id,
        interaction_id=pending.request.interaction_id,
        response={
            "request_id": "call-user",
            "selected_values": ["react"],
        },
        submitted_by="ui:test",
        expected_revision=pending.session_snapshot.revision,
    )
    assert answered.response == {
        "request_id": "call-user",
        "selected_values": ["react"],
        "other_text": None,
    }

    resume_model = _QueueModelIO([_final_turn()])
    resume_loop = build_runtime_loop(
        model_io=resume_model,
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(tmp_path)
        ),
    )
    resumed = resume_loop.resume_human_input(
        conversation=suspended.messages,
        continuation=suspended.continuation or {},
        response=None,
        session_id=session_id,
        toolkit=_toolkit(),
    )

    assert resumed.status == "completed"
    assert len(resume_model.requests) == 1
    final_state = JsonFileSessionStore(tmp_path).load(session_id)
    assert "execution_checkpoint" not in final_state
    journal = final_state[INTERACTION_JOURNAL_KEY]
    assert journal["active_id"] is None
    entry = journal["entries"][pending.request.interaction_id]
    assert entry["receipt"]["submitted_by"] == "ui:test"
    assert entry["application"] is not None


def test_cold_resume_without_receipt_does_not_call_model(tmp_path) -> None:
    session_id = "durable-human-no-receipt"
    first_loop = build_runtime_loop(
        model_io=_QueueModelIO([_ask_turn()]),
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(tmp_path)
        ),
    )
    suspended = first_loop.run(
        [{"role": "user", "content": "pick one"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=_toolkit(),
    )

    resume_model = _QueueModelIO([_final_turn()])
    resume_loop = build_runtime_loop(
        model_io=resume_model,
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(tmp_path)
        ),
    )
    with pytest.raises(InteractionNotPendingError, match="no recorded receipt"):
        resume_loop.resume_human_input(
            conversation=suspended.messages,
            continuation=suspended.continuation or {},
            response=None,
            session_id=session_id,
            toolkit=_toolkit(),
        )
    assert resume_model.requests == []


def test_human_input_event_carries_the_full_durable_request() -> None:
    session_id = "durable-human-event-request"
    events: list[dict] = []
    loop = build_runtime_loop(
        model_io=_QueueModelIO([_ask_turn()]),
        memory_runtime=KernelMemoryRuntime.from_config(
            store=InMemorySessionStore()
        ),
    )

    suspended = loop.run(
        [{"role": "user", "content": "pick one"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=_toolkit(),
        callback=events.append,
    )

    [requested] = [
        event
        for event in events
        if event.get("type") == "human_input_requested"
    ]
    assert requested["interaction_id"] == suspended.interaction_request["interaction_id"]
    assert requested["interaction_request"] == suspended.interaction_request


def test_low_level_human_resume_rejects_changed_model_io() -> None:
    session_id = "durable-human-model-change"
    store = InMemorySessionStore()
    suspended = build_runtime_loop(
        model_io=_QueueModelIO([_ask_turn()]),
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    ).run(
        [{"role": "user", "content": "pick one"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=_toolkit(),
    )
    interaction = DurableInteractionRuntime(
        KernelMemoryRuntime.from_config(store=store)
    )
    pending = interaction.load_active(session_id)
    interaction.record_receipt(
        session_id,
        interaction_id=pending.request.interaction_id,
        response={
            "request_id": "call-user",
            "selected_values": ["react"],
        },
        expected_revision=pending.session_snapshot.revision,
    )

    changed_model = _QueueModelIO([_final_turn()])
    changed_model.model = "gpt-6"
    changed_loop = build_runtime_loop(
        model_io=changed_model,
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )
    with pytest.raises(InteractionIntegrityError, match="provider/model"):
        changed_loop.resume_human_input(
            conversation=suspended.messages,
            continuation=suspended.continuation or {},
            response=None,
            session_id=session_id,
            toolkit=_toolkit(),
        )
    assert changed_model.requests == []


def test_low_level_human_resume_rejects_changed_continuation_model() -> None:
    session_id = "durable-human-continuation-model-change"
    store = InMemorySessionStore()
    suspended = build_runtime_loop(
        model_io=_QueueModelIO([_ask_turn()]),
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    ).run(
        [{"role": "user", "content": "pick one"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=_toolkit(),
    )
    interaction = DurableInteractionRuntime(
        KernelMemoryRuntime.from_config(store=store)
    )
    pending = interaction.load_active(session_id)
    interaction.record_receipt(
        session_id,
        interaction_id=pending.request.interaction_id,
        response={
            "request_id": "call-user",
            "selected_values": ["react"],
        },
        expected_revision=pending.session_snapshot.revision,
    )

    model = _QueueModelIO([_final_turn()])
    loop = build_runtime_loop(
        model_io=model,
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )
    changed_continuation = dict(suspended.continuation or {})
    changed_continuation["model"] = "gpt-6"
    with pytest.raises(InteractionIntegrityError, match="provider/model"):
        loop.resume_human_input(
            conversation=suspended.messages,
            continuation=changed_continuation,
            response=None,
            session_id=session_id,
            toolkit=_toolkit(),
        )
    assert model.requests == []


def test_agent_submit_and_generic_resume_are_public_durable_api() -> None:
    session_id = "agent-durable-interaction-api"
    store = InMemorySessionStore()
    first_loop = build_runtime_loop(
        model_io=_QueueModelIO([_ask_turn()]),
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )
    suspended = first_loop.run(
        [{"role": "user", "content": "pick one"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=_toolkit(),
    )

    resume_model = _QueueModelIO([_final_turn()])
    agent = Agent(
        name="durable-api",
        modules=(
            MemoryModule(
                memory=KernelMemoryRuntime.from_config(store=store)
            ),
        ),
        model_io_factory=lambda _spec, _context: resume_model,
    )
    receipt = agent.submit_interaction(
        session_id=session_id,
        interaction_id=suspended.interaction_request["interaction_id"],
        response={
            "request_id": "call-user",
            "selected_values": ["react"],
        },
        submitted_by="ui:agent-test",
    )
    assert receipt.submitted_by == "ui:agent-test"
    assert resume_model.requests == []

    resumed = agent.resume_interaction(session_id=session_id)
    assert resumed.status == "completed"
    assert len(resume_model.requests) == 1
