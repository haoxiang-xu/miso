from __future__ import annotations

import json

import pytest

from unchain.agent import (
    Agent,
    MemoryModule,
    ToolOptimizerModule,
    ToolsModule,
)
from unchain.input import ASK_USER_QUESTION_TOOL_NAME
from unchain.interaction.durable import (
    InteractionAlreadyAppliedError,
    InteractionIntegrityError,
    InteractionNotPendingError,
    InteractionReceiptConflictError,
)
from unchain.interaction.runtime import DurableInteractionRuntime
from unchain.kernel import ModelTurnResult
from unchain.kernel.types import ToolCall
from unchain.memory import (
    ExecutionCheckpointResumeRequiredError,
    InMemorySessionStore,
    KernelMemoryRuntime,
)
from unchain.runtime import build_runtime_loop
from unchain.tools import ToolOptimizerConfig, Toolkit


class _QueueModelIO:
    provider = "openai"
    model = "gpt-5"

    def __init__(self, turns: list[ModelTurnResult]) -> None:
        self.turns = list(turns)
        self.calls = 0

    def fetch_turn(self, _request):
        self.calls += 1
        if not self.turns:
            raise AssertionError("unexpected model call")
        return self.turns.pop(0)


class _SelectorReached(RuntimeError):
    pass


class _PreflightProbeModelIO:
    def __init__(
        self,
        *,
        model: str,
        provider: str = "openai",
        on_fetch=None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.on_fetch = on_fetch
        self.calls = 0

    def fetch_turn(self, request):
        self.calls += 1
        if self.on_fetch is not None:
            self.on_fetch(request)
        raise _SelectorReached("tool selector reached")


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
        assistant_messages=[{"role": "assistant", "content": "done"}],
        tool_calls=[],
        final_text="done",
        response_id="resp-final",
    )


def _pending_human_interaction(
    *,
    store: InMemorySessionStore,
    session_id: str,
):
    toolkit = Toolkit()
    toolkit.register(
        lambda **_: {"error": "reserved"},
        name=ASK_USER_QUESTION_TOOL_NAME,
        parameters=[],
    )
    loop = build_runtime_loop(
        model_io=_QueueModelIO([_ask_turn()]),
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )
    suspended = loop.run(
        [{"role": "user", "content": "pick one"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=toolkit,
        max_iterations=3,
    )
    assert suspended.status == "awaiting_human_input"
    return suspended


def _optimizer_agent(
    *,
    store: InMemorySessionStore,
    model_io: _PreflightProbeModelIO,
    tool_calls: list[int] | None = None,
) -> Agent:
    toolkit = Toolkit()
    recorded_tool_calls = tool_calls if tool_calls is not None else []
    for index in range(3):
        def run(value="", *, _index=index):
            recorded_tool_calls.append(_index)
            return {"tool": _index, "value": value}

        toolkit.register(
            run,
            name=f"tool_{index}",
            parameters=[],
        )
    return Agent(
        name="durable-preflight",
        provider=model_io.provider,
        model=model_io.model,
        modules=(
            MemoryModule(
                memory=KernelMemoryRuntime.from_config(store=store)
            ),
            ToolsModule(tools=(toolkit,)),
            ToolOptimizerModule(
                config=ToolOptimizerConfig(
                    max_direct_tools=1,
                    trigger_tool_count=1,
                )
            ),
        ),
        model_io_factory=lambda _spec, _context: model_io,
    )


def _human_response() -> dict[str, object]:
    return {
        "request_id": "call-user",
        "selected_values": ["react"],
    }


@pytest.mark.parametrize("api", ["resume_interaction", "resume_human_input"])
def test_missing_receipt_fails_before_tool_selector(api: str) -> None:
    store = InMemorySessionStore()
    session_id = f"agent-preflight-no-receipt-{api}"
    _pending_human_interaction(store=store, session_id=session_id)
    model_io = _PreflightProbeModelIO(model="gpt-5")
    tool_calls: list[int] = []
    agent = _optimizer_agent(
        store=store,
        model_io=model_io,
        tool_calls=tool_calls,
    )

    with pytest.raises(InteractionNotPendingError, match="no recorded receipt"):
        getattr(agent, api)(session_id=session_id)

    assert model_io.calls == 0
    assert tool_calls == []


@pytest.mark.parametrize("api", ["resume_interaction", "resume_human_input"])
def test_response_is_persisted_before_missing_exposure_plan_fails_closed(
    api: str,
) -> None:
    store = InMemorySessionStore()
    session_id = f"agent-preflight-write-first-{api}"
    _pending_human_interaction(store=store, session_id=session_id)
    memory = KernelMemoryRuntime.from_config(store=store)

    model_io = _PreflightProbeModelIO(model="gpt-5")
    agent = _optimizer_agent(store=store, model_io=model_io)

    with pytest.raises(
        InteractionIntegrityError,
        match="missing its exposure plan",
    ):
        getattr(agent, api)(
            session_id=session_id,
            response=_human_response(),
        )

    assert model_io.calls == 0
    pending = DurableInteractionRuntime(memory).load_active(session_id)
    assert pending.response == {
        "request_id": "call-user",
        "selected_values": ["react"],
        "other_text": None,
    }


@pytest.mark.parametrize("api", ["resume_interaction", "resume_human_input"])
@pytest.mark.parametrize(
    ("provider", "model"),
    [("openai", "gpt-6"), ("anthropic", "gpt-5")],
)
def test_changed_runtime_binding_fails_before_receipt_and_tool_selector(
    api: str,
    provider: str,
    model: str,
) -> None:
    store = InMemorySessionStore()
    session_id = f"agent-preflight-binding-mismatch-{api}-{provider}-{model}"
    _pending_human_interaction(store=store, session_id=session_id)
    memory = KernelMemoryRuntime.from_config(store=store)
    model_io = _PreflightProbeModelIO(provider=provider, model=model)
    tool_calls: list[int] = []
    agent = _optimizer_agent(
        store=store,
        model_io=model_io,
        tool_calls=tool_calls,
    )

    with pytest.raises(
        InteractionIntegrityError,
        match="does not match the current model",
    ):
        getattr(agent, api)(
            session_id=session_id,
            response=_human_response(),
        )

    assert model_io.calls == 0
    assert tool_calls == []
    assert DurableInteractionRuntime(memory).load_active(session_id).receipt is None


def test_fresh_run_with_pending_interaction_fails_before_tool_selector() -> None:
    store = InMemorySessionStore()
    session_id = "agent-preflight-fresh-run"
    _pending_human_interaction(store=store, session_id=session_id)
    model_io = _PreflightProbeModelIO(model="gpt-5")
    tool_calls: list[int] = []
    agent = _optimizer_agent(
        store=store,
        model_io=model_io,
        tool_calls=tool_calls,
    )

    with pytest.raises(
        ExecutionCheckpointResumeRequiredError,
        match="awaiting a durable interaction",
    ):
        agent.run("start something new", session_id=session_id)

    assert model_io.calls == 0
    assert tool_calls == []


def test_submit_interaction_same_answer_is_idempotent_after_application() -> None:
    store = InMemorySessionStore()
    session_id = "agent-submit-retry-after-application"
    suspended = _pending_human_interaction(store=store, session_id=session_id)
    final_model = _QueueModelIO([_final_turn()])
    agent = Agent(
        name="durable-submit-retry",
        provider="openai",
        model="gpt-5",
        modules=(
            MemoryModule(
                memory=KernelMemoryRuntime.from_config(store=store)
            ),
        ),
        model_io_factory=lambda _spec, _context: final_model,
    )
    interaction_id = suspended.interaction_request["interaction_id"]

    first = agent.submit_interaction(
        session_id=session_id,
        interaction_id=interaction_id,
        response=_human_response(),
        submitted_by="ui:first",
    )
    resumed = agent.resume_interaction(session_id=session_id)
    assert resumed.status == "completed"

    retry = agent.submit_interaction(
        session_id=session_id,
        interaction_id=interaction_id,
        response=_human_response(),
        submitted_by="ui:retry",
    )
    assert retry.receipt_id == first.receipt_id
    assert retry.submitted_by == "ui:first"

    with pytest.raises(
        (InteractionReceiptConflictError, InteractionAlreadyAppliedError),
    ):
        agent.submit_interaction(
            session_id=session_id,
            interaction_id=interaction_id,
            response={
                "request_id": "call-user",
                "selected_values": ["vue"],
            },
            submitted_by="ui:conflict",
        )
