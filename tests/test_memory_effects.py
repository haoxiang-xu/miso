from __future__ import annotations

import inspect


def test_memory_surface_exports_effect_helpers():
    from unchain.capabilities import EmitEventOp
    from unchain.kernel import HarnessDelta
    from unchain.memory import (
        build_memory_commit_event,
        build_memory_delta,
        build_memory_prepare_event,
        memory_prepare_update,
    )

    prepare_info = {"short_term_recall_count": 2}
    commit_info = {"summary_persisted": True}

    prepare_event = build_memory_prepare_event(prepare_info)
    commit_event = build_memory_commit_event(commit_info)
    delta = build_memory_delta(
        created_by="memory.test",
        state_updates=memory_prepare_update(prepare_info),
        trace={"source": "test"},
    )

    prepare_info["short_term_recall_count"] = 99
    commit_info["summary_persisted"] = False

    assert isinstance(prepare_event, EmitEventOp)
    assert prepare_event.type == "memory_prepare"
    assert prepare_event.reason == "memory.prepare"
    assert prepare_event.payload == {"short_term_recall_count": 2}
    assert isinstance(commit_event, EmitEventOp)
    assert commit_event.type == "memory_commit"
    assert commit_event.reason == "memory.commit"
    assert commit_event.payload == {"summary_persisted": True}
    assert isinstance(delta, HarnessDelta)
    assert delta.created_by == "memory.test"
    assert delta.state_updates == {
        "memory_prepare_info": {"short_term_recall_count": 2},
    }
    assert delta.trace == {"source": "test"}


def test_memory_delta_helper_preserves_run_state_memory_merge_behavior():
    from unchain.capabilities import CapabilityOutcome
    from unchain.kernel import BaseRuntimeHarness, KernelLoop
    from unchain.memory import build_memory_delta, memory_prepare_update, memory_state_update

    class MemoryEffectHarness(BaseRuntimeHarness):
        def build_delta(self, context):
            return CapabilityOutcome(
                delta=build_memory_delta(
                    created_by="memory.test",
                    state_updates={
                        **memory_state_update({"loaded": True}),
                        **memory_prepare_update({"short_term_recall_count": 2}),
                    },
                )
            )

    loop = KernelLoop(
        harnesses=[MemoryEffectHarness(name="memory_test", phases=("before_model",))]
    )
    state = loop.seed_state([{"role": "user", "content": "hi"}])
    state.memory_prepare_info = {"bootstrap": True}

    loop.dispatch_phase(state, phase="before_model", event={"run_id": "run-1"})

    assert state.memory_state["loaded"] is True
    assert state.memory_prepare_info == {
        "bootstrap": True,
        "short_term_recall_count": 2,
    }
    assert state.component_state["memory"]["state"]["loaded"] is True
    assert state.component_state["memory"]["prepare_info"] == {
        "bootstrap": True,
        "short_term_recall_count": 2,
    }


def test_memory_surface_exports_event_harnesses_and_default_runtime_registers_them():
    from unchain.memory import (
        KernelMemoryRuntime,
        MemoryCommitEventHarness,
        MemoryPrepareEventHarness,
    )

    runtime = KernelMemoryRuntime.from_config()
    components = runtime.build_default_components()
    names = {component.name for component in components}

    assert MemoryPrepareEventHarness.__name__ == "MemoryPrepareEventHarness"
    assert MemoryCommitEventHarness.__name__ == "MemoryCommitEventHarness"
    assert "memory_prepare_event" in names
    assert "memory_commit_event" in names


def test_kernel_loop_does_not_directly_emit_memory_prepare_or_commit_events():
    from unchain.kernel.loop import KernelLoop

    source = inspect.getsource(KernelLoop.step_once)

    assert "memory_prepare" not in source
    assert "memory_commit" not in source


def test_kernel_loop_does_not_own_memory_component_assembly():
    from unchain.kernel.loop import KernelLoop

    source = inspect.getsource(KernelLoop)

    assert "_memory_runtime" not in source
    assert "_ensure_memory_components" not in source
