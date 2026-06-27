from __future__ import annotations

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
