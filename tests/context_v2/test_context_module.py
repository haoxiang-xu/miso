from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from unchain.agent import AgentBuilder, AgentCallContext, AgentSpec, AgentState
from unchain.agent.model_io import ModelIOFactoryRegistry
from unchain.context import (
    ContextBuildUnavailableError,
    ContextCompileRequest,
    ContextCompileResult,
    ContextCompiler,
    ContextCheckpointBindingForbiddenError,
    ContextCheckpointPersistenceRequiredError,
    ContextRuntime,
    build_checkpoint_request,
    resolve_context_budget,
)
from unchain.agent.modules import ContextModule
from unchain.journal import ContextBuildStatus, ResourceRef
from unchain.kernel import BaseRuntimeHarness, HarnessContext, ModelTurnResult
from unchain.kernel.microcompact import MidRunMicrocompactHarness
from unchain.memory import InMemorySessionStore, KernelMemoryRuntime
from unchain.memory.assembly import build_default_memory_components
from unchain.optimizers import (
    ContextUsageOptimizer,
    LastNOptimizer,
    LlmSummaryOptimizer,
    SlidingWindowOptimizer,
    ToolHistoryCompactionOptimizer,
)
from unchain.optimizers.base import BaseContextOptimizer, OptimizerContext
from unchain.runtime import build_default_runtime_components, build_runtime_loop


class _FinalModelIO:
    provider = "openai"
    model = "gpt-test"

    def __init__(self) -> None:
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        return ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="response-context-v2",
        )


def _request_factory(context):
    run_id = str(context.event.get("run_id") or "context-run")
    execution_id = str(context.state.session_state.session_id or run_id)
    provider = str(context.state.provider_state.provider or "openai")
    model = str(context.state.provider_state.model or "gpt-test")
    return ContextCompileRequest(
        case="runtime-path",
        source_messages=tuple(context.latest_messages()),
        current_generation=f"generation-{execution_id}",
        fixed_overhead_tokens=0,
        budget=resolve_context_budget(context_window_tokens=16_384),
        provider=provider,
        model=model,
        build_id=f"build-{run_id}-{context.state.iteration}",
        execution_id=execution_id,
        generation_id=f"generation-{execution_id}",
        attempt_id=run_id,
    )


def _runtime(
    *,
    owner_id: str = "context-v2",
    durable_events: list | None = None,
    partial_events: list | None = None,
    compiler=None,
):
    durable_events = durable_events if durable_events is not None else []
    partial_events = partial_events if partial_events is not None else []
    return ContextRuntime._for_test(
        owner_id=owner_id,
        compiler=compiler or ContextCompiler(),
        request_factory=_request_factory,
        durable_event_sink=durable_events.append,
        partial_attempt_sink=lambda event, error: partial_events.append(
            (event, error)
        ),
    )


def test_public_context_runtime_requires_the_durable_coordinator() -> None:
    with pytest.raises(TypeError, match="ContextCompileCoordinator"):
        ContextRuntime(
            owner_id="context-v2",
            compiler=ContextCompiler(),
            request_factory=_request_factory,
            durable_event_sink=lambda event: None,
            partial_attempt_sink=lambda event, error: None,
        )


def _builder(
    *,
    runtime: ContextRuntime | None = None,
    callback=None,
    harnesses=(),
    memory_runtime=None,
) -> AgentBuilder:
    model_io = _FinalModelIO()
    builder = AgentBuilder(
        agent=SimpleNamespace(name="context-agent"),
        spec=AgentSpec(
            name="context-agent",
            provider="openai",
            model="gpt-test",
        ),
        state=AgentState(),
        call_context=AgentCallContext(
            mode="run",
            input_messages=[{"role": "user", "content": "hello"}],
            callback=callback,
            session_id="session-context-v2",
            run_id="run-context-v2",
            max_context_window_tokens=16_384,
        ),
        model_io_registry=ModelIOFactoryRegistry(),
        memory_runtime=memory_runtime,
        harnesses=list(harnesses),
    )
    builder.set_model_io(model_io)
    if runtime is not None:
        ContextModule(runtime=runtime).configure(builder)
    return builder


def _component_signature(components):
    return [
        (component.__class__.__module__, component.__class__.__name__, component.name)
        for component in components
    ]


def test_context_module_claims_one_explicit_owner_and_builds_one_compiler_harness():
    runtime = _runtime()
    prepared = _builder(runtime=runtime).build()

    owners = [
        getattr(harness, "semantic_context_owner", None)
        for harness in prepared.loop.harnesses
        if getattr(harness, "semantic_context_owner", None) is not None
    ]

    assert prepared.semantic_context_owner == "context-v2"
    assert prepared.context_runtime is runtime
    assert owners == ["context-v2"]


def test_second_context_module_fails_during_configuration_even_for_same_owner():
    builder = _builder()
    ContextModule(runtime=_runtime(owner_id="context-v2")).configure(builder)

    with pytest.raises(ValueError, match="semantic context owner"):
        ContextModule(runtime=_runtime(owner_id="context-v2")).configure(builder)


def test_conflicting_context_owner_fails_during_configuration():
    builder = _builder()
    ContextModule(runtime=_runtime(owner_id="first-owner")).configure(builder)

    with pytest.raises(ValueError, match="first-owner.*second-owner"):
        ContextModule(runtime=_runtime(owner_id="second-owner")).configure(builder)


def test_v2_owner_omits_all_legacy_destructive_default_components():
    memory_runtime = KernelMemoryRuntime.from_config(store=InMemorySessionStore())
    prepared = _builder(
        runtime=_runtime(),
        memory_runtime=memory_runtime,
    ).build()
    component_types = {type(component) for component in prepared.loop.harnesses}

    assert MidRunMicrocompactHarness not in component_types
    assert LastNOptimizer not in component_types
    assert SlidingWindowOptimizer not in component_types
    assert LlmSummaryOptimizer not in component_types
    assert ToolHistoryCompactionOptimizer not in component_types
    assert "memory_short_term_recall" not in {
        component.name for component in prepared.loop.harnesses
    }
    assert "memory_long_term_recall" not in {
        component.name for component in prepared.loop.harnesses
    }
    assert {
        "memory_bootstrap",
        "memory_commit",
        "memory_execution_checkpoint",
        "tool_execution",
        "workspace_change_artifacts",
    }.issubset({component.name for component in prepared.loop.harnesses})


@pytest.mark.parametrize(
    "destructive",
    [
        MidRunMicrocompactHarness(),
        LastNOptimizer(),
        SlidingWindowOptimizer(),
        LlmSummaryOptimizer(),
        ToolHistoryCompactionOptimizer(),
    ],
)
def test_v2_owner_rejects_explicit_destructive_component_by_type(destructive):
    with pytest.raises(ValueError, match="incompatible.*semantic context owner"):
        _builder(runtime=_runtime(), harnesses=(destructive,)).build()


def test_v2_owner_rejects_any_custom_context_optimizer_even_after_compiler_order():
    class LateDeletingOptimizer(BaseContextOptimizer):
        def __init__(self):
            super().__init__(
                name="late_deleting_optimizer",
                phases=("before_model",),
                order=1001,
            )

        def build_optimizer_delta(self, context: OptimizerContext):
            return self.replace_messages_delta(context, [])

    with pytest.raises(ValueError, match="incompatible.*semantic context owner"):
        _builder(
            runtime=_runtime(),
            harnesses=(LateDeletingOptimizer(),),
        ).build()


def test_v2_owner_rejects_context_optimizer_registered_after_build():
    prepared = _builder(runtime=_runtime()).build()

    with pytest.raises(ValueError, match="semantic context owner"):
        prepared.loop.register_context_optimizer(ContextUsageOptimizer())


def test_legacy_loop_still_accepts_late_context_optimizer():
    loop = build_runtime_loop(model_io=_FinalModelIO())

    optimizer = ContextUsageOptimizer()
    loop.register_context_optimizer(optimizer)

    assert optimizer in loop.harnesses


def test_v2_owner_does_not_infer_conflicts_from_harness_name():
    harmless = BaseRuntimeHarness(
        name="last_n",
        phases=("after_model",),
    )

    prepared = _builder(runtime=_runtime(), harnesses=(harmless,)).build()

    assert harmless in prepared.loop.harnesses


def test_legacy_component_assembly_is_unchanged_without_an_owner():
    assert _component_signature(build_default_runtime_components()) == [
        ("unchain.tools.prompting", "ToolPromptHarness", "tool_prompt"),
        ("unchain.tools.execution", "ToolExecutionHarness", "tool_execution"),
        (
            "unchain.kernel.microcompact",
            "MidRunMicrocompactHarness",
            "mid_run_microcompact",
        ),
        (
            "unchain.interaction.resume",
            "HumanInputResumeHarness",
            "human_input_resume",
        ),
        (
            "unchain.runtime.workspace_artifacts",
            "WorkspaceChangeArtifactHarness",
            "workspace_change_artifacts",
        ),
    ]

    memory_runtime = KernelMemoryRuntime.from_config(store=InMemorySessionStore())
    assert [
        component.name
        for component in build_default_memory_components(memory_runtime)
    ] == [
        "tool_history_compaction",
        "llm_summary",
        "last_n",
        "sliding_window",
        "memory_prepare_reset",
        "memory_short_term_recall",
        "memory_long_term_recall",
        "memory_prepare_event",
        "memory_bootstrap",
        "memory_commit_reset",
        "memory_commit",
        "memory_commit_event",
        "memory_execution_checkpoint",
    ]


def test_durable_callback_is_wrapped_once_and_runs_before_host():
    order = []
    runtime = ContextRuntime._for_test(
        owner_id="context-v2",
        compiler=ContextCompiler(),
        request_factory=_request_factory,
        durable_event_sink=lambda event: order.append(("durable", event["type"])),
        partial_attempt_sink=lambda event, error: order.append(
            ("partial", event["type"], type(error).__name__)
        ),
    )
    host_callback = lambda event: order.append(("host", event["type"]))
    builder = _builder(runtime=runtime, callback=host_callback)

    prepared = builder.build()
    prepared.call_context.callback({"type": "probe"})

    assert builder.call_context.callback is host_callback
    assert prepared.call_context is not builder.call_context
    assert prepared.call_context.callback is not host_callback
    assert order == [("durable", "probe"), ("host", "probe")]


def test_nested_subagent_callback_forwarding_persists_the_event_only_once():
    order = []
    runtime = ContextRuntime._for_test(
        owner_id="context-v2",
        compiler=ContextCompiler(),
        request_factory=_request_factory,
        durable_event_sink=lambda event: order.append(("durable", event["type"])),
        partial_attempt_sink=lambda event, error: order.append(
            ("partial", event["type"], error)
        ),
    )
    parent_callback = runtime.compose_event_callback(
        lambda event: order.append(("host", event["type"]))
    )
    child_callback = runtime.compose_event_callback(parent_callback)

    child_callback({"type": "child_event"})

    assert order == [
        ("durable", "child_event"),
        ("host", "child_event"),
    ]


def test_distinct_event_emitted_from_a_host_callback_is_also_persisted():
    order = []
    runtime = ContextRuntime._for_test(
        owner_id="context-v2",
        compiler=ContextCompiler(),
        request_factory=_request_factory,
        durable_event_sink=lambda event: order.append(("durable", event["type"])),
        partial_attempt_sink=lambda event, error: order.append(
            ("partial", event["type"], error)
        ),
    )
    callback = None

    def host(event):
        order.append(("host", event["type"]))
        if event["type"] == "outer":
            callback({"type": "inner"})

    callback = runtime.compose_event_callback(host)
    callback({"type": "outer"})

    assert order == [
        ("durable", "outer"),
        ("host", "outer"),
        ("durable", "inner"),
        ("host", "inner"),
    ]


def test_durable_sink_failure_marks_partial_blocks_host_and_preserves_error():
    host_events = []
    partial_events = []
    failure = RuntimeError("journal unavailable")

    def fail_sink(event):
        raise failure

    runtime = ContextRuntime._for_test(
        owner_id="context-v2",
        compiler=ContextCompiler(),
        request_factory=_request_factory,
        durable_event_sink=fail_sink,
        partial_attempt_sink=lambda event, error: partial_events.append(
            (event["type"], error)
        ),
    )
    prepared = _builder(runtime=runtime, callback=host_events.append).build()

    with pytest.raises(RuntimeError) as raised:
        prepared.run()

    assert raised.value is failure
    assert host_events == []
    assert partial_events == [("run_started", failure)]
    assert prepared.loop.model_io.requests == []


def test_durable_failure_latches_per_attempt_and_marks_partial_only_once():
    failure = RuntimeError("journal unavailable")
    sink_calls = []
    partial_events = []
    host_events = []

    def fail_once(event):
        sink_calls.append(event["type"])
        if len(sink_calls) == 1:
            raise failure

    runtime = ContextRuntime._for_test(
        owner_id="context-v2",
        compiler=ContextCompiler(),
        request_factory=_request_factory,
        durable_event_sink=fail_once,
        partial_attempt_sink=lambda event, error: partial_events.append(
            (event["type"], error)
        ),
    )
    callback = runtime.compose_event_callback(host_events.append)

    with pytest.raises(RuntimeError) as first:
        callback({"type": "subagent_spawned", "run_id": "attempt-a"})
    with pytest.raises(RuntimeError) as replay:
        callback({"type": "tool_result", "run_id": "attempt-a"})

    assert first.value is failure
    assert replay.value is failure
    assert sink_calls == ["subagent_spawned"]
    assert partial_events == [("subagent_spawned", failure)]
    assert host_events == []

    callback({"type": "other_attempt", "run_id": "attempt-b"})
    assert sink_calls == ["subagent_spawned", "other_attempt"]
    assert host_events == [{"type": "other_attempt", "run_id": "attempt-b"}]


def test_latched_failure_stops_before_request_factory_or_compiler():
    failure = RuntimeError("journal unavailable")
    request_factory_calls = []

    def fail_sink(event):
        raise failure

    def request_factory(context):
        request_factory_calls.append(context.event["run_id"])
        return _request_factory(context)

    runtime = ContextRuntime._for_test(
        owner_id="context-v2",
        compiler=ContextCompiler(),
        request_factory=request_factory,
        durable_event_sink=fail_sink,
        partial_attempt_sink=lambda event, error: None,
    )
    callback = runtime.compose_event_callback(None)
    with pytest.raises(RuntimeError):
        callback({"type": "probe", "run_id": "attempt-latched"})

    prepared = _builder(runtime=runtime).build()
    state = prepared.loop.seed_state(
        [{"role": "user", "content": "hello"}],
        provider="openai",
        model="gpt-test",
        session_id="session-context-v2",
        max_context_window_tokens=16_384,
    )
    context = HarnessContext(
        state=state,
        phase="before_model",
        event={"run_id": "attempt-latched"},
    )

    with pytest.raises(RuntimeError) as raised:
        runtime.compile_context(context)

    assert raised.value is failure
    assert request_factory_calls == []


def test_non_durable_event_payload_marks_partial_and_never_reaches_host():
    class NonDurablePayload:
        def __deepcopy__(self, memo):
            del memo
            raise TypeError("payload cannot be copied for durable storage")

    durable_events = []
    partial_events = []
    host_events = []
    runtime = ContextRuntime._for_test(
        owner_id="context-v2",
        compiler=ContextCompiler(),
        request_factory=_request_factory,
        durable_event_sink=durable_events.append,
        partial_attempt_sink=lambda event, error: partial_events.append(
            (event["type"], error)
        ),
    )
    callback = runtime.compose_event_callback(host_events.append)

    with pytest.raises(TypeError, match="cannot be copied") as raised:
        callback({"type": "bad_event", "payload": NonDurablePayload()})

    assert durable_events == []
    assert host_events == []
    assert partial_events == [("bad_event", raised.value)]


def test_build_runtime_loop_requires_owner_harness_pairing():
    with pytest.raises(ValueError, match="exactly one.*compiler harness"):
        build_runtime_loop(
            model_io=_FinalModelIO(),
            semantic_context_owner="context-v2",
        )


def test_owner_attribute_on_an_unrelated_harness_cannot_impersonate_the_compiler():
    class FakeOwnerHarness(BaseRuntimeHarness):
        semantic_context_owner = "context-v2"

    fake_owner = FakeOwnerHarness(name="not_a_compiler", phases=("before_model",))

    with pytest.raises(ValueError, match="exactly one.*compiler harness"):
        build_runtime_loop(
            harnesses=[fake_owner],
            model_io=_FinalModelIO(),
            semantic_context_owner="context-v2",
        )


def test_checkpoint_required_fails_closed_before_calling_the_model():
    checkpoint_request = build_checkpoint_request(
        source_event_ids=("event-1",),
        source_event_store_seqs=(1,),
        source_messages=({"role": "user", "content": "older history"},),
    )

    class CheckpointRequiredCompiler:
        def compile(self, request):
            del request
            return ContextCompileResult(
                messages=(),
                diagnostics={"status": "checkpoint_required"},
                checkpoint_requests=(checkpoint_request,),
            )

    prepared = _builder(
        runtime=_runtime(compiler=CheckpointRequiredCompiler())
    ).build()

    with pytest.raises(ContextCheckpointPersistenceRequiredError) as raised:
        prepared.run()

    assert raised.value.code == "context_v2_checkpoint_persistence_required"
    assert raised.value.checkpoint_requests == (checkpoint_request,)
    assert prepared.loop.model_io.requests == []


def test_checkpoint_required_status_alone_also_fails_with_the_stable_error():
    class StatusOnlyCheckpointCompiler:
        def compile(self, request):
            del request
            return ContextCompileResult(
                messages=(),
                diagnostics={"status": "checkpoint_required"},
            )

    prepared = _builder(
        runtime=_runtime(compiler=StatusOnlyCheckpointCompiler())
    ).build()

    with pytest.raises(ContextCheckpointPersistenceRequiredError) as raised:
        prepared.run()

    assert raised.value.code == "context_v2_checkpoint_persistence_required"
    assert raised.value.checkpoint_requests == ()
    assert prepared.loop.model_io.requests == []


def test_runtime_rejects_external_checkpoint_binding_before_custom_compiler() -> None:
    compiler_calls = []

    class AcceptingCompiler:
        def compile(self, request):
            compiler_calls.append(request)
            return ContextCompileResult(messages=(), diagnostics={})

    request = ContextCompileRequest(
        case="external-checkpoint-binding",
        source_messages=({"role": "user", "content": "current"},),
        checkpoint_ref=ResourceRef("checkpoint", "external", 1),
        checkpoint_request_id="checkpoint-" + ("a" * 64),
    )
    runtime = ContextRuntime._for_test(
        owner_id="context-v2",
        request_factory=lambda context: request,
        durable_event_sink=lambda event: None,
        partial_attempt_sink=lambda event, error: None,
        compiler=AcceptingCompiler(),
    )

    with pytest.raises(ContextCheckpointBindingForbiddenError) as raised:
        runtime.compile_context(SimpleNamespace(event={}))

    assert raised.value.code == "context_v2_checkpoint_binding_forbidden"
    assert compiler_calls == []


def test_unavailable_context_envelope_fails_closed_before_model():
    class UnavailableCompiler:
        def compile(self, request):
            result = ContextCompiler().compile(request)
            return replace(
                result,
                envelope=replace(
                    result.envelope,
                    status=ContextBuildStatus.UNAVAILABLE,
                ),
            )

    prepared = _builder(runtime=_runtime(compiler=UnavailableCompiler())).build()

    with pytest.raises(ContextBuildUnavailableError) as raised:
        prepared.run()

    assert raised.value.code == "context_v2_build_unavailable"
    assert prepared.loop.model_io.requests == []
