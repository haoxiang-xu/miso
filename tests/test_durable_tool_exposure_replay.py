from __future__ import annotations

import copy
import json

import pytest

from unchain.agent import Agent, MemoryModule, ToolOptimizerModule, ToolsModule
from unchain.input import ASK_USER_QUESTION_TOOL_NAME
from unchain.interaction.durable import InteractionIntegrityError
from unchain.interaction.runtime import DurableInteractionRuntime
from unchain.kernel import ModelTurnResult
from unchain.kernel.types import ToolCall
from unchain.memory import InMemorySessionStore, KernelMemoryRuntime
from unchain.tools import ToolOptimizerConfig, Toolkit
from unchain.tools.exposure import (
    META_TOOL_NAMES,
    TOOL_EXPOSURE_PLAN_SCHEMA_VERSION,
    DeferredToolExecutionPlugin,
    ToolExposureRuntime,
)


EXPECTED_PLAN_FIELDS = {
    "schema_version",
    "provider",
    "catalog_digest",
    "direct_tool_names",
    "deferred_tool_names",
    "loaded_tool_names",
}


class _CrashAfterReceipt(RuntimeError):
    pass


class _QueueModelIO:
    provider = "openai"
    model = "gpt-5"

    def __init__(
        self,
        turns: list[ModelTurnResult],
        *,
        forbid_selector: bool = False,
    ) -> None:
        self.turns = list(turns)
        self.forbid_selector = forbid_selector
        self.requests = []
        self.selector_calls = 0
        self.visible_tool_names: list[tuple[str, ...]] = []

    def fetch_turn(self, request):
        response_format = getattr(request, "response_format", None)
        is_selector = (
            response_format is not None
            and getattr(response_format, "name", None) == "tool_exposure_selection"
        )
        if is_selector:
            self.selector_calls += 1
            if self.forbid_selector:
                raise AssertionError("durable resume must not rerun the tool selector")
        self.requests.append(request)
        self.visible_tool_names.append(tuple(request.toolkit.tools))
        if not self.turns:
            raise AssertionError("unexpected model turn")
        return self.turns.pop(0)


def _selector_turn(*tool_names: str) -> ModelTurnResult:
    content = json.dumps({"tool_names": list(tool_names)})
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": content}],
        tool_calls=[],
        final_text=content,
        response_id="resp-selector",
    )


def _tool_turn(call_id: str, name: str, arguments: dict) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            }
        ],
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        response_id=f"resp-{call_id}",
    )


def _final_turn() -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "done"}],
        tool_calls=[],
        final_text="done",
        response_id="resp-final",
    )


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
    return _tool_turn("call-user", ASK_USER_QUESTION_TOOL_NAME, arguments)


def _toolkit(
    *,
    safe_calls: list[str] | None = None,
    dangerous_calls: list[str] | None = None,
    include_human_input: bool = False,
    safe_description: str = "Read a stable value safely.",
) -> Toolkit:
    safe_sink = safe_calls if safe_calls is not None else []
    dangerous_sink = dangerous_calls if dangerous_calls is not None else []
    toolkit = Toolkit()

    def safe_tool(value: str = "safe") -> dict[str, str]:
        safe_sink.append(value)
        return {"value": value}

    def dangerous_tool(path: str) -> dict[str, str]:
        dangerous_sink.append(path)
        return {"path": path}

    toolkit.register(
        safe_tool,
        name="safe_tool",
        description=safe_description,
        search_hint="safe stable read",
    )
    toolkit.register(
        dangerous_tool,
        name="dangerous_tool",
        description="Write a path after explicit approval.",
        requires_confirmation=True,
        search_hint="dangerous write path",
    )
    if include_human_input:
        toolkit.register(
            lambda **_: {"error": "reserved"},
            name=ASK_USER_QUESTION_TOOL_NAME,
            description="Ask the user to choose an option.",
            parameters=[],
            search_hint="ask user question choice",
        )
    return toolkit


def _optimizer_config() -> ToolOptimizerConfig:
    # The direct budget includes the four optimizer meta-tools.
    return ToolOptimizerConfig(max_direct_tools=5, trigger_tool_count=1)


def _runtime(
    *,
    toolkit: Toolkit,
    model_io: _QueueModelIO,
) -> ToolExposureRuntime:
    return ToolExposureRuntime(
        config=_optimizer_config(),
        full_toolkit=toolkit,
        model_io=model_io,
        provider="openai",
        model="gpt-5",
        messages=[{"role": "user", "content": "run"}],
    )


def _agent(
    *,
    store: InMemorySessionStore,
    model_io: _QueueModelIO,
    toolkit: Toolkit,
) -> Agent:
    return Agent(
        name="durable-tool-exposure",
        provider="openai",
        model="gpt-5",
        modules=(
            MemoryModule(
                memory=KernelMemoryRuntime.from_config(store=store)
            ),
            ToolsModule(tools=(toolkit,)),
            ToolOptimizerModule(config=_optimizer_config()),
        ),
        model_io_factory=lambda _spec, _context: model_io,
    )


def _record_receipt(
    store: InMemorySessionStore,
    session_id: str,
    response: dict,
) -> None:
    runtime = DurableInteractionRuntime(
        KernelMemoryRuntime.from_config(store=store),
        clock_ms=lambda: 100,
    )
    pending = runtime.load_active(session_id)
    runtime.record_receipt(
        session_id,
        interaction_id=pending.request.interaction_id,
        response=response,
        submitted_by="ui:test",
        expected_revision=pending.session_snapshot.revision,
    )


def _plan_from_checkpoint(
    store: InMemorySessionStore,
    session_id: str,
) -> dict:
    checkpoint = store.load(session_id)["execution_checkpoint"]
    return checkpoint["continuation"]["tool_exposure_plan"]


def test_exposure_snapshot_replay_is_exact_and_selector_free() -> None:
    first_model = _QueueModelIO([_selector_turn("safe_tool")])
    first = _runtime(toolkit=_toolkit(), model_io=first_model)
    first.prepare()
    snapshot = first.durable_plan_snapshot()
    canonical_bytes = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert set(snapshot) == EXPECTED_PLAN_FIELDS
    assert snapshot["schema_version"] == TOOL_EXPOSURE_PLAN_SCHEMA_VERSION
    assert first_model.selector_calls == 1

    replay_model = _QueueModelIO([], forbid_selector=True)
    replay = _runtime(toolkit=_toolkit(), model_io=replay_model)
    replay.prepare(replay_plan=copy.deepcopy(snapshot))
    replayed_snapshot = replay.durable_plan_snapshot()

    assert replayed_snapshot == snapshot
    assert json.dumps(
        replayed_snapshot,
        sort_keys=True,
        separators=(",", ":"),
    ).encode() == canonical_bytes
    assert replay_model.requests == []
    assert replay_model.selector_calls == 0


def test_loaded_exposure_state_replays_exactly_without_selector() -> None:
    first = _runtime(
        toolkit=_toolkit(),
        model_io=_QueueModelIO([_selector_turn("safe_tool")]),
    )
    first.prepare()
    load_result = first.tool_load(names=["dangerous_tool"])
    snapshot = first.durable_plan_snapshot()

    assert [item["name"] for item in load_result["loaded"]] == [
        "dangerous_tool"
    ]
    assert snapshot["direct_tool_names"] == ["safe_tool", "dangerous_tool"]
    assert snapshot["deferred_tool_names"] == []
    assert snapshot["loaded_tool_names"] == ["dangerous_tool"]

    replay_model = _QueueModelIO([], forbid_selector=True)
    replay = _runtime(toolkit=_toolkit(), model_io=replay_model)
    exposed = replay.prepare(replay_plan=copy.deepcopy(snapshot))

    assert replay.durable_plan_snapshot() == snapshot
    assert "dangerous_tool" in exposed.tools
    assert replay_model.requests == []
    assert replay_model.selector_calls == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda plan: plan.update({"selector_status": "selected"}),
        lambda plan: plan["direct_tool_names"].append("safe_tool"),
        lambda plan: plan["deferred_tool_names"].clear(),
        lambda plan: plan["loaded_tool_names"].append("dangerous_tool"),
    ],
    ids=["extra-field", "duplicate", "invalid-partition", "invalid-loaded-subset"],
)
def test_exposure_replay_rejects_tampered_plan_before_selector(mutate) -> None:
    first = _runtime(
        toolkit=_toolkit(),
        model_io=_QueueModelIO([_selector_turn("safe_tool")]),
    )
    first.prepare()
    tampered = copy.deepcopy(first.durable_plan_snapshot())
    mutate(tampered)
    replay_model = _QueueModelIO([], forbid_selector=True)
    replay = _runtime(toolkit=_toolkit(), model_io=replay_model)

    with pytest.raises(InteractionIntegrityError, match="tool exposure replay"):
        replay.prepare(replay_plan=tampered)

    assert replay_model.requests == []
    assert replay_model.selector_calls == 0


def test_exposure_replay_rejects_catalog_drift_before_selector() -> None:
    first = _runtime(
        toolkit=_toolkit(),
        model_io=_QueueModelIO([_selector_turn("safe_tool")]),
    )
    first.prepare()
    snapshot = first.durable_plan_snapshot()
    replay_model = _QueueModelIO([], forbid_selector=True)
    replay = _runtime(
        toolkit=_toolkit(safe_description="Changed schema-visible description."),
        model_io=replay_model,
    )

    with pytest.raises(InteractionIntegrityError, match="catalog digest"):
        replay.prepare(replay_plan=snapshot)

    assert replay_model.requests == []
    assert replay_model.selector_calls == 0


def test_direct_approval_cold_resume_replays_plan_and_executes_once() -> None:
    store = InMemorySessionStore()
    session_id = "exposure-direct-approval"
    dangerous_calls: list[str] = []
    first_model = _QueueModelIO(
        [
            _selector_turn("dangerous_tool"),
            _tool_turn(
                "call-danger",
                "dangerous_tool",
                {"path": "direct.txt"},
            ),
        ]
    )
    first_agent = _agent(
        store=store,
        model_io=first_model,
        toolkit=_toolkit(dangerous_calls=dangerous_calls),
    )

    suspended = first_agent.run(
        "write directly",
        session_id=session_id,
        max_iterations=3,
    )

    assert suspended.status == "awaiting_interaction"
    assert dangerous_calls == []
    plan = suspended.continuation["tool_exposure_plan"]
    assert plan == _plan_from_checkpoint(store, session_id)
    assert set(plan) == EXPECTED_PLAN_FIELDS
    assert first_model.selector_calls == 1
    first_visible = first_model.visible_tool_names[1]

    _record_receipt(store, session_id, {"approved": True})
    resume_model = _QueueModelIO([_final_turn()], forbid_selector=True)
    resume_agent = _agent(
        store=store,
        model_io=resume_model,
        toolkit=_toolkit(dangerous_calls=dangerous_calls),
    )
    resumed = resume_agent.resume_interaction(session_id=session_id)

    assert resumed.status == "completed"
    assert dangerous_calls == ["direct.txt"]
    assert resume_model.selector_calls == 0
    assert len(resume_model.requests) == 1
    assert resume_model.visible_tool_names[0] == first_visible


def test_deferred_approval_cold_resume_replays_plan_and_executes_once() -> None:
    store = InMemorySessionStore()
    session_id = "exposure-deferred-approval"
    dangerous_calls: list[str] = []
    first_model = _QueueModelIO(
        [
            _selector_turn("safe_tool"),
            _tool_turn(
                "call-deferred",
                "tool_execute_deferred",
                {
                    "tool_name": "dangerous_tool",
                    "arguments": {"path": "deferred.txt"},
                },
            ),
        ]
    )
    first_agent = _agent(
        store=store,
        model_io=first_model,
        toolkit=_toolkit(dangerous_calls=dangerous_calls),
    )

    suspended = first_agent.run(
        "write through deferred execution",
        session_id=session_id,
        max_iterations=3,
    )

    assert suspended.status == "awaiting_interaction"
    assert dangerous_calls == []
    plan = suspended.continuation["tool_exposure_plan"]
    assert plan["direct_tool_names"] == ["safe_tool"]
    assert plan["deferred_tool_names"] == ["dangerous_tool"]
    first_visible = first_model.visible_tool_names[1]

    _record_receipt(store, session_id, {"approved": True})
    resume_model = _QueueModelIO([_final_turn()], forbid_selector=True)
    resume_agent = _agent(
        store=store,
        model_io=resume_model,
        toolkit=_toolkit(dangerous_calls=dangerous_calls),
    )
    resumed = resume_agent.resume_interaction(session_id=session_id)

    assert resumed.status == "completed"
    assert dangerous_calls == ["deferred.txt"]
    assert resume_model.selector_calls == 0
    assert len(resume_model.requests) == 1
    assert resume_model.visible_tool_names[0] == first_visible


def test_human_input_cold_resume_replays_plan_without_selector() -> None:
    store = InMemorySessionStore()
    session_id = "exposure-human-input"
    first_model = _QueueModelIO(
        [
            _selector_turn(ASK_USER_QUESTION_TOOL_NAME),
            _ask_turn(),
        ]
    )
    first_agent = _agent(
        store=store,
        model_io=first_model,
        toolkit=_toolkit(include_human_input=True),
    )

    suspended = first_agent.run(
        "ask me",
        session_id=session_id,
        max_iterations=3,
    )

    assert suspended.status == "awaiting_human_input"
    assert set(suspended.continuation["tool_exposure_plan"]) == EXPECTED_PLAN_FIELDS
    first_visible = first_model.visible_tool_names[1]
    _record_receipt(
        store,
        session_id,
        {
            "request_id": "call-user",
            "selected_values": ["react"],
        },
    )

    resume_model = _QueueModelIO([_final_turn()], forbid_selector=True)
    resume_agent = _agent(
        store=store,
        model_io=resume_model,
        toolkit=_toolkit(include_human_input=True),
    )
    resumed = resume_agent.resume_human_input(session_id=session_id)

    assert resumed.status == "completed"
    assert resume_model.selector_calls == 0
    assert len(resume_model.requests) == 1
    assert resume_model.visible_tool_names[0] == first_visible


def test_max_budget_cold_resume_replays_plan_without_selector() -> None:
    store = InMemorySessionStore()
    session_id = "exposure-max-budget"
    safe_calls: list[str] = []
    first_model = _QueueModelIO(
        [
            _selector_turn("safe_tool"),
            _tool_turn("call-safe", "safe_tool", {"value": "once"}),
        ]
    )
    first_agent = _agent(
        store=store,
        model_io=first_model,
        toolkit=_toolkit(safe_calls=safe_calls),
    )

    def record_then_crash(_payload) -> None:
        _record_receipt(
            store,
            session_id,
            {"approved": True, "extra_iterations": 1},
        )
        raise _CrashAfterReceipt("worker stopped before applying max receipt")

    with pytest.raises(_CrashAfterReceipt):
        first_agent.run(
            "use one tool",
            session_id=session_id,
            max_iterations=1,
            on_max_iterations=record_then_crash,
        )

    assert safe_calls == ["once"]
    assert set(_plan_from_checkpoint(store, session_id)) == EXPECTED_PLAN_FIELDS
    first_visible = first_model.visible_tool_names[1]
    resume_model = _QueueModelIO([_final_turn()], forbid_selector=True)
    resume_agent = _agent(
        store=store,
        model_io=resume_model,
        toolkit=_toolkit(safe_calls=safe_calls),
    )
    resumed = resume_agent.resume_interaction(session_id=session_id)

    assert resumed.status == "completed"
    assert safe_calls == ["once"]
    assert resume_model.selector_calls == 0
    assert len(resume_model.requests) == 1
    assert resume_model.visible_tool_names[0] == first_visible


def test_max_exposure_snapshot_is_lazy_and_only_runs_at_boundary(
    monkeypatch,
) -> None:
    snapshot_calls = 0
    original = DeferredToolExecutionPlugin.durable_exposure_plan

    def counted_snapshot(self):
        nonlocal snapshot_calls
        snapshot_calls += 1
        return original(self)

    monkeypatch.setattr(
        DeferredToolExecutionPlugin,
        "durable_exposure_plan",
        counted_snapshot,
    )

    completed_store = InMemorySessionStore()
    completed_agent = _agent(
        store=completed_store,
        model_io=_QueueModelIO(
            [_selector_turn("safe_tool"), _final_turn()]
        ),
        toolkit=_toolkit(),
    )
    completed = completed_agent.run(
        "finish before max",
        session_id="exposure-before-max",
        max_iterations=3,
        on_max_iterations=lambda _payload: None,
    )

    assert completed.status == "completed"
    assert snapshot_calls == 0

    boundary_store = InMemorySessionStore()
    boundary_agent = _agent(
        store=boundary_store,
        model_io=_QueueModelIO(
            [
                _selector_turn("safe_tool"),
                _tool_turn("call-safe", "safe_tool", {"value": "boundary"}),
            ]
        ),
        toolkit=_toolkit(),
    )

    def stop_at_boundary(_payload) -> None:
        raise _CrashAfterReceipt("stop at max boundary")

    with pytest.raises(_CrashAfterReceipt):
        boundary_agent.run(
            "reach max",
            session_id="exposure-at-max",
            max_iterations=1,
            on_max_iterations=stop_at_boundary,
        )

    assert snapshot_calls == 1
