from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def test_capabilities_surface_exports_core_contracts():
    from unchain.capabilities import (
        ActiveCapability,
        CapabilityOutcome,
        ContextTarget,
        EmitEventOp,
        InsertMessagesOp,
        PassiveCapability,
        RunContext,
        RunDelta,
        RuntimeHook,
        SetRuntimeStateOp,
        normalize_capability_outcome,
        normalize_run_delta,
    )
    from unchain.kernel import HarnessDelta, RuntimeHarness

    assert CapabilityOutcome.__name__ == "CapabilityOutcome"
    assert RunDelta.__name__ == "RunDelta"
    assert RunContext.__name__ == "RunContext"
    assert ContextTarget.MODEL_CONTEXT.value == "model_context"
    assert InsertMessagesOp.__name__ == "InsertMessagesOp"
    assert SetRuntimeStateOp.__name__ == "SetRuntimeStateOp"
    assert EmitEventOp.__name__ == "EmitEventOp"
    assert callable(normalize_capability_outcome)
    assert callable(normalize_run_delta)
    assert RuntimeHook is PassiveCapability
    assert issubclass(HarnessDelta, RunDelta)
    assert RuntimeHarness is RuntimeHook
    assert ActiveCapability.__name__ == "ActiveCapability"


def test_model_adapter_aliases_current_model_io_contract():
    from unchain.kernel import ModelAdapter, ModelIO

    assert ModelAdapter is ModelIO


def test_provider_surface_exports_model_adapter_alias():
    from unchain.providers import ModelAdapter, ModelIO

    assert ModelAdapter is ModelIO


def test_plain_dict_tool_result_normalizes_to_capability_outcome_without_delta():
    from unchain.capabilities import CapabilityOutcome, normalize_capability_outcome

    raw_result = {"ok": True, "value": 3}

    outcome = normalize_capability_outcome(raw_result, created_by="tool.echo")

    assert isinstance(outcome, CapabilityOutcome)
    assert outcome.value == raw_result
    assert outcome.delta is None
    assert outcome.created_by == "tool.echo"


def test_existing_capability_outcome_is_preserved_and_gets_missing_created_by():
    from unchain.capabilities import CapabilityOutcome, RunDelta, normalize_capability_outcome

    delta = RunDelta(created_by="hook.memory")

    outcome = normalize_capability_outcome(
        CapabilityOutcome(value={"ok": True}, delta=delta),
        created_by="hook.memory",
    )

    assert outcome.value == {"ok": True}
    assert outcome.delta is delta
    assert outcome.created_by == "hook.memory"


def test_legacy_harness_delta_normalizes_to_run_delta():
    from unchain.capabilities import RunDelta, normalize_run_delta
    from unchain.kernel import HarnessDelta

    legacy = HarnessDelta.append(
        created_by="harness.legacy",
        messages=[{"role": "assistant", "content": "hello"}],
        state_updates={"run_status": "running"},
        trace={"source": "test"},
    )

    normalized = normalize_run_delta(legacy, created_by="harness.legacy")

    assert isinstance(normalized, RunDelta)
    assert normalized is legacy
    assert normalized.created_by == "harness.legacy"
    assert normalized.state_updates == {"run_status": "running"}
    assert normalized.trace == {"source": "test"}


def test_structured_context_ops_can_describe_message_state_event_and_suspend_effects():
    from unchain.capabilities import (
        ContextTarget,
        EmitEventOp,
        InsertMessagesOp,
        RequestSuspendOp,
        RunDelta,
        SetRuntimeStateOp,
    )
    from unchain.kernel import SuspendSignal

    delta = RunDelta(
        created_by="hook.test",
        context_ops=(
            InsertMessagesOp(
                target=ContextTarget.MODEL_CONTEXT,
                index=0,
                messages=[{"role": "system", "content": "memory"}],
                reason="memory_recall",
            ),
            SetRuntimeStateOp(
                path=("tool_exposure", "active"),
                value=["read_file"],
                reason="tool_discovery",
            ),
            EmitEventOp(
                type="memory_prepare",
                payload={"count": 1},
                reason="memory_hook",
            ),
            RequestSuspendOp(
                kind="human_input",
                payload={"question": "Continue?"},
                reason="ask_user_question",
            ),
        ),
    )

    assert delta.context_ops[0].target is ContextTarget.MODEL_CONTEXT
    assert delta.context_ops[1].path == ("tool_exposure", "active")
    assert delta.context_ops[2].payload == {"count": 1}
    assert delta.context_ops[3].to_suspend_signal() == SuspendSignal(
        kind="human_input",
        payload={"question": "Continue?"},
    )


def test_active_and_passive_capabilities_can_both_return_delta_outcomes():
    from unchain.capabilities import (
        ActiveCapability,
        CapabilityOutcome,
        PassiveCapability,
        RunContext,
        RunDelta,
        SetRuntimeStateOp,
        normalize_capability_outcome,
    )

    @dataclass
    class DemoTool:
        name: str = "demo_tool"

        def invoke(self, call: dict[str, Any], context: RunContext) -> CapabilityOutcome:
            return CapabilityOutcome(
                value={"received": call["value"], "messages": len(context.messages)},
                delta=RunDelta(
                    created_by="tool.demo_tool",
                    context_ops=(
                        SetRuntimeStateOp(
                            path=("demo", "tool"),
                            value=call["value"],
                            reason="active_capability",
                        ),
                    ),
                ),
            )

    @dataclass
    class DemoHook:
        name: str = "demo_hook"
        phases: tuple[str, ...] = ("before_model",)
        order: int = 100

        def applies(self, context: RunContext) -> bool:
            return context.phase == "before_model"

        def apply(self, context: RunContext) -> CapabilityOutcome:
            return CapabilityOutcome(
                delta=RunDelta(
                    created_by="hook.demo_hook",
                    context_ops=(
                        SetRuntimeStateOp(
                            path=("demo", "hook"),
                            value=context.phase,
                            reason="passive_capability",
                        ),
                    ),
                ),
            )

    run_context = RunContext(
        messages=[{"role": "user", "content": "hi"}],
        phase="before_model",
    )
    active: ActiveCapability = DemoTool()
    passive: PassiveCapability = DemoHook()

    active_outcome = normalize_capability_outcome(
        active.invoke({"value": 7}, run_context),
        created_by="tool.demo_tool",
    )
    passive_outcome = normalize_capability_outcome(
        passive.apply(run_context),
        created_by="hook.demo_hook",
    )

    assert active_outcome.value == {"received": 7, "messages": 1}
    assert active_outcome.delta is not None
    assert active_outcome.delta.context_ops[0].value == 7
    assert passive.applies(run_context)
    assert passive_outcome.delta is not None
    assert passive_outcome.delta.context_ops[0].value == "before_model"


def test_tool_invoke_wraps_existing_dict_result_as_capability_outcome():
    from unchain.capabilities import CapabilityOutcome, RunContext
    from unchain.tools import Tool

    tool = Tool.from_callable(
        lambda value: {"ok": True, "value": value},
        name="echo",
    )

    outcome = tool.invoke(
        {"arguments": {"value": 7}, "call_id": "call-1"},
        RunContext(messages=[{"role": "user", "content": "hi"}]),
    )

    assert isinstance(outcome, CapabilityOutcome)
    assert outcome.value == {"ok": True, "value": 7}
    assert outcome.delta is None
    assert outcome.created_by == "tool.echo"
    assert tool.execute({"value": 7}) == {"ok": True, "value": 7}


def test_tool_invoke_preserves_capability_outcome_returned_by_tool_function():
    from unchain.capabilities import CapabilityOutcome, RunContext, RunDelta, SetRuntimeStateOp
    from unchain.tools import Tool

    def configured_tool(value: int) -> CapabilityOutcome:
        return CapabilityOutcome(
            value={"ok": True},
            delta=RunDelta(
                created_by="tool.configured",
                context_ops=(
                    SetRuntimeStateOp(
                        path=("demo", "value"),
                        value=value,
                        reason="tool_delta",
                    ),
                ),
            ),
        )

    tool = Tool.from_callable(configured_tool, name="configured")

    outcome = tool.invoke(
        {"arguments": {"value": 9}},
        RunContext(messages=[]),
    )

    assert outcome.value == {"ok": True}
    assert outcome.created_by == "tool.configured"
    assert outcome.delta is not None
    assert outcome.delta.context_ops[0].value == 9


def test_base_runtime_harness_apply_wraps_legacy_delta_as_capability_outcome():
    from unchain.capabilities import CapabilityOutcome
    from unchain.kernel import BaseRuntimeHarness, HarnessContext, HarnessDelta, KernelLoop

    class DemoHarness(BaseRuntimeHarness):
        def build_delta(self, context):
            return HarnessDelta.append(
                created_by="harness.demo",
                messages=[{"role": "system", "content": context.phase}],
            )

    loop = KernelLoop()
    state = loop.seed_state([{"role": "user", "content": "start"}])
    context = HarnessContext(state=state, phase="before_model")

    outcome = DemoHarness(name="demo", phases=("before_model",)).apply(context)

    assert isinstance(outcome, CapabilityOutcome)
    assert outcome.value is None
    assert isinstance(outcome.delta, HarnessDelta)
    assert outcome.delta.created_by == "harness.demo"


def test_kernel_loop_dispatch_phase_accepts_capability_outcome_delta():
    from unchain.capabilities import CapabilityOutcome
    from unchain.kernel import BaseRuntimeHarness, HarnessDelta, KernelLoop

    class OutcomeHarness(BaseRuntimeHarness):
        def build_delta(self, context):
            return CapabilityOutcome(
                delta=HarnessDelta.append(
                    created_by="harness.outcome",
                    messages=[{"role": "system", "content": "from outcome"}],
                ),
            )

    loop = KernelLoop(harnesses=[OutcomeHarness(name="outcome", phases=("before_model",))])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    loop.dispatch_phase(state, phase="before_model")

    assert state.latest_messages() == [
        {"role": "user", "content": "start"},
        {"role": "system", "content": "from outcome"},
    ]


def test_kernel_loop_applies_structured_run_delta_context_ops():
    from unchain.capabilities import (
        CapabilityOutcome,
        RunDelta,
        SetRuntimeStateOp,
    )
    from unchain.kernel import BaseRuntimeHarness, KernelLoop

    class StructuredHarness(BaseRuntimeHarness):
        def build_delta(self, context):
            return CapabilityOutcome(
                delta=RunDelta(
                    created_by="harness.structured",
                    context_ops=(
                        SetRuntimeStateOp(
                            path=("demo", "value"),
                            value=1,
                            reason="phase_two_contract_only",
                        ),
                    ),
                ),
            )

    loop = KernelLoop(harnesses=[StructuredHarness(name="structured", phases=("before_model",))])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    loop.dispatch_phase(state, phase="before_model")

    assert state.component_state["demo"]["value"] == 1


def test_execute_confirmable_tool_call_uses_tool_invoke_capability_value():
    from unchain.capabilities import CapabilityOutcome
    from unchain.kernel import ToolCall
    from unchain.tools import Toolkit
    from unchain.tools.confirmation import execute_confirmable_tool_call

    toolkit = Toolkit()

    @toolkit.tool(name="capability_tool")
    def capability_tool(value: int) -> CapabilityOutcome:
        return CapabilityOutcome(value={"ok": True, "value": value + 1})

    outcome = execute_confirmable_tool_call(
        toolkit=toolkit,
        tool_call=ToolCall(call_id="call-1", name="capability_tool", arguments={"value": 4}),
        on_tool_confirm=None,
        loop=None,
        callback=None,
        run_id="run-1",
        iteration=0,
    )

    assert outcome.tool_result == {"ok": True, "value": 5}
    assert outcome.capability_outcome is not None
    assert outcome.capability_outcome.value == {"ok": True, "value": 5}


def test_kernel_tool_execution_path_uses_tool_invoke_capability_value():
    from unchain.capabilities import CapabilityOutcome
    from unchain.kernel import ModelTurnResult, ToolCall
    from unchain.runtime import build_runtime_loop
    from unchain.tools import Toolkit

    class QueueModelIO:
        provider = "openai"

        def __init__(self):
            self.requests = []
            self.results = [
                ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "capability_tool",
                            "arguments": "{\"value\": 6}",
                        }
                    ],
                    tool_calls=[
                        ToolCall(
                            call_id="call-1",
                            name="capability_tool",
                            arguments={"value": 6},
                        )
                    ],
                    response_id="resp-1",
                ),
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "done"}],
                    tool_calls=[],
                    final_text="done",
                    response_id="resp-2",
                ),
            ]

        def fetch_turn(self, request):
            self.requests.append(request)
            return self.results.pop(0)

    toolkit = Toolkit()

    @toolkit.tool(name="capability_tool")
    def capability_tool(value: int) -> CapabilityOutcome:
        return CapabilityOutcome(value={"ok": True, "value": value + 1})

    result = build_runtime_loop(model_io=QueueModelIO()).run(
        [{"role": "user", "content": "start"}],
        provider="openai",
        model="gpt-5",
        toolkit=toolkit,
        max_iterations=3,
    )

    tool_message = next(
        message
        for message in result.messages
        if message.get("type") == "function_call_output"
    )

    assert result.status == "completed"
    assert json.loads(tool_message["output"]) == {"ok": True, "value": 7}


def test_execute_confirmable_tool_call_preserves_structured_tool_delta_for_harness():
    from unchain.capabilities import CapabilityOutcome, RunDelta, SetRuntimeStateOp
    from unchain.kernel import ToolCall
    from unchain.tools import Toolkit
    from unchain.tools.confirmation import execute_confirmable_tool_call

    toolkit = Toolkit()

    @toolkit.tool(name="delta_tool")
    def delta_tool() -> CapabilityOutcome:
        return CapabilityOutcome(
            value={"ok": True},
            delta=RunDelta(
                created_by="tool.delta_tool",
                context_ops=(
                    SetRuntimeStateOp(
                        path=("demo", "value"),
                        value=1,
                        reason="phase_two_contract_only",
                    ),
                ),
            ),
        )

    outcome = execute_confirmable_tool_call(
        toolkit=toolkit,
        tool_call=ToolCall(call_id="call-1", name="delta_tool", arguments={}),
        on_tool_confirm=None,
        loop=None,
        callback=None,
        run_id="run-1",
        iteration=0,
    )

    assert outcome.tool_result == {"ok": True}
    assert outcome.capability_outcome is not None
    assert outcome.capability_outcome.delta is not None
    assert outcome.capability_outcome.delta.context_ops[0].value == 1
