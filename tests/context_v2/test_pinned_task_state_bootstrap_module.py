from __future__ import annotations

from types import SimpleNamespace

import pytest

from unchain.agent import AgentBuilder, AgentCallContext, AgentSpec, AgentState
from unchain.agent.model_io import ModelIOFactoryRegistry
from unchain.agent.modules.context import ContextModule
from unchain.agent.modules.task_state_bootstrap import (
    PinnedTaskStateBootstrapModule,
    PinnedTaskStateBootstrapModuleError,
)
from unchain.context.factory import DurableContextRuntimeFactory
from unchain.context.harness import ContextExecutionBindingHarness
from unchain.context.task_state_bootstrap import (
    PinnedTaskStateBootstrapBinding,
    PinnedTaskStateBootstrapHarness,
)
from unchain.context.task_state_runtime import TaskStateContextRuntime


class _ModelIO:
    provider = "openai"
    model = "gpt-test"


def _runtime(owner_id: str) -> TaskStateContextRuntime:
    factory = DurableContextRuntimeFactory(
        bundle_builder=lambda attempt: None,
        generation_resolver=lambda context, execution_id: "generation-test",
        current_input_resolver=lambda context, attempt: None,
    )
    return TaskStateContextRuntime.from_factory(
        owner_id=owner_id,
        execution_factory=factory,
        task_state_reader_resolver=lambda bundle: None,
    )


def _builder() -> AgentBuilder:
    builder = AgentBuilder(
        agent=SimpleNamespace(name="task-state-agent"),
        spec=AgentSpec(
            name="task-state-agent",
            provider="openai",
            model="gpt-test",
        ),
        state=AgentState(),
        call_context=AgentCallContext(
            mode="run",
            input_messages=[{"role": "user", "content": "pin this task"}],
            session_id="chat-task-state",
            run_id="attempt-task-state",
        ),
        model_io_registry=ModelIOFactoryRegistry(),
    )
    builder.set_model_io(_ModelIO())
    return builder


def _binding_resolver(context):
    del context
    raise AssertionError("module configuration must not resolve run bindings")


def test_module_attaches_one_official_harness_after_exact_task_state_runtime() -> None:
    runtime = _runtime("task-state-owner")
    builder = _builder()
    ContextModule(runtime=runtime).configure(builder)
    module = PinnedTaskStateBootstrapModule(
        runtime=runtime,
        binding_resolver=_binding_resolver,
    )

    module.configure(builder)
    [bootstrap] = [
        harness
        for harness in builder.harnesses
        if isinstance(harness, PinnedTaskStateBootstrapHarness)
    ]
    prepared = builder.build()
    ordered_names = [harness.name for harness in prepared.loop.harnesses]

    assert bootstrap.binding_resolver is _binding_resolver
    assert bootstrap.phases == ("bootstrap",)
    assert bootstrap.order == -990
    assert ordered_names.index("context_v2_execution_binding") < ordered_names.index(
        "context_v2_pinned_task_state_bootstrap"
    )
    execution_binding = next(
        harness
        for harness in prepared.loop.harnesses
        if isinstance(harness, ContextExecutionBindingHarness)
    )
    assert execution_binding.runtime is runtime


def test_module_rejects_configuration_before_context_runtime_attachment() -> None:
    runtime = _runtime("task-state-owner-missing")
    builder = _builder()

    with pytest.raises(
        PinnedTaskStateBootstrapModuleError,
        match="requires.*attached first",
    ):
        PinnedTaskStateBootstrapModule(
            runtime=runtime,
            binding_resolver=_binding_resolver,
        ).configure(builder)
    assert not any(
        isinstance(harness, PinnedTaskStateBootstrapHarness)
        for harness in builder.harnesses
    )


def test_module_rejects_a_different_attached_task_state_runtime() -> None:
    attached = _runtime("task-state-owner-attached")
    requested = _runtime("task-state-owner-requested")
    builder = _builder()
    ContextModule(runtime=attached).configure(builder)

    with pytest.raises(
        PinnedTaskStateBootstrapModuleError,
        match="does not match",
    ):
        PinnedTaskStateBootstrapModule(
            runtime=requested,
            binding_resolver=_binding_resolver,
        ).configure(builder)
    assert not any(
        isinstance(harness, PinnedTaskStateBootstrapHarness)
        for harness in builder.harnesses
    )


def test_module_rejects_duplicate_bootstrap_harness_without_mutating_builder() -> None:
    runtime = _runtime("task-state-owner-duplicate")
    builder = _builder()
    ContextModule(runtime=runtime).configure(builder)
    module = PinnedTaskStateBootstrapModule(
        runtime=runtime,
        binding_resolver=_binding_resolver,
    )
    module.configure(builder)
    harnesses_before = tuple(builder.harnesses)

    with pytest.raises(
        PinnedTaskStateBootstrapModuleError,
        match="already attached",
    ):
        module.configure(builder)

    assert tuple(builder.harnesses) == harnesses_before
    assert sum(
        isinstance(harness, PinnedTaskStateBootstrapHarness)
        for harness in builder.harnesses
    ) == 1


@pytest.mark.parametrize(
    ("runtime", "resolver", "message"),
    [
        (object(), _binding_resolver, "TaskStateContextRuntime"),
        (_runtime("task-state-owner-invalid-resolver"), None, "callable"),
    ],
)
def test_module_validates_its_official_runtime_and_binding_resolver(
    runtime,
    resolver,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        PinnedTaskStateBootstrapModule(
            runtime=runtime,
            binding_resolver=resolver,
        )


def test_direct_import_does_not_require_agent_module_export_mutation() -> None:
    assert PinnedTaskStateBootstrapModule.__module__ == (
        "unchain.agent.modules.task_state_bootstrap"
    )
    assert PinnedTaskStateBootstrapBinding.__module__ == (
        "unchain.context.task_state_bootstrap"
    )
