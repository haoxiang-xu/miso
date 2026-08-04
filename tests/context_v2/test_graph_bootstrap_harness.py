from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from unchain.agent import AgentBuilder, AgentCallContext, AgentSpec, AgentState
from unchain.agent.model_io import ModelIOFactoryRegistry
from unchain.agent.modules.context import ContextModule, ContextShadowModule
from unchain.agent.modules.graph_checkpoint import (
    GraphStepBootstrapModule,
    GraphStepBootstrapModuleError,
    GraphStepResumeModule,
    GraphStepResumeModuleError,
)
from unchain.agent.modules.task_state_bootstrap import (
    PinnedTaskStateBootstrapModule,
)
from unchain.context.artifacts import ArtifactService
from unchain.context.derived_handoff import DerivedHandoffInputIngress
from unchain.context.factory import (
    ContextExecutionBundleError,
    DurableContextRuntimeFactory,
)
from unchain.context.graph_checkpoint import (
    GraphCheckpointService,
    GraphExecutionPlan,
    GraphStepBinding,
    JournalGraphCheckpointRepository,
)
from unchain.context.graph_harness import (
    GraphStepBootstrapBinding,
    GraphStepBootstrapError,
    GraphStepBootstrapHarness,
    GraphStepResumeBinding,
    GraphStepResumeHarness,
)
from unchain.context.handoff import DurableHandoffRecorder, HandoffService
from unchain.context.ingress import (
    ContextInputIngress,
    HostResolvedCurrentInput,
    HostResolvedInteractionInput,
)
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.context.runtime import ContextRuntime
from unchain.context.task_state_runtime import TaskStateContextRuntime
from unchain.journal import (
    AttemptRef,
    DurableEventSink,
    GenerationRef,
    SemanticEventDraft,
)
from unchain.kernel import HarnessContext, KernelLoop, ModelTurnResult
from unchain.kernel.state import RunState
from unchain.persistence import SQLiteContextV2Store


GENERATION = GenerationRef("graph-harness-execution", "graph-harness-generation")
ORCHESTRATION = AttemptRef(GENERATION, "graph-harness-orchestration")
STEP = AttemptRef(GENERATION, "graph-harness-step")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _runtime(*, task_state: bool = False):
    factory = DurableContextRuntimeFactory(
        bundle_builder=lambda attempt: None,
        generation_resolver=lambda context, execution_id: GENERATION.generation_id,
        current_input_resolver=lambda context, attempt: None,
    )
    if task_state:
        return TaskStateContextRuntime.from_factory(
            owner_id="graph-harness-context",
            execution_factory=factory,
            task_state_reader_resolver=lambda bundle: None,
        )
    return ContextRuntime.from_factory(
        owner_id="graph-harness-context",
        execution_factory=factory,
    )


def _open(tmp_path):
    store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )
    journal = store.bind_execution(GENERATION.execution_id)
    artifacts = ArtifactService(
        journal,
        sanitizer=lambda content, _media_type: content,
    )
    projectors = {
        attempt: CanonicalSemanticEventProjector(
            attempt=attempt,
            artifacts=artifacts,
            payload_sanitizer=lambda _event_type, payload: payload,
        )
        for attempt in (ORCHESTRATION, STEP)
    }
    sinks = {
        attempt: DurableEventSink(journal, attempt, projectors[attempt])
        for attempt in projectors
    }

    def resolve_ingress(consumer_attempt, source_attempt):
        projector = projectors[consumer_attempt]
        sink = sinks[consumer_attempt]
        ingress = ContextInputIngress(
            attempt=consumer_attempt,
            projector=projector,
            sink=sink,
        )
        return DerivedHandoffInputIngress(
            consumer_attempt=consumer_attempt,
            source_attempt=source_attempt,
            handoff_recorder=DurableHandoffRecorder(
                attempt=consumer_attempt,
                handoffs=HandoffService(artifacts),
                projector=projector,
                sink=sink,
            ),
            input_ingress=ingress,
        )

    service = GraphCheckpointService(
        repository=JournalGraphCheckpointRepository(journal),
        artifacts=artifacts,
        derived_ingress_resolver=resolve_ingress,
    )
    seed = ContextInputIngress(
        attempt=ORCHESTRATION,
        projector=projectors[ORCHESTRATION],
        sink=sinks[ORCHESTRATION],
    ).persist(
        HostResolvedCurrentInput(
            attempt=ORCHESTRATION,
            content="bootstrap graph step",
            message_index=0,
        )
    )
    plan = GraphExecutionPlan(
        orchestration_attempt=ORCHESTRATION,
        topology_sha256=_digest("graph-harness-topology"),
        initial_input_cursor=seed.cursor,
        steps=(
            GraphStepBinding(
                index=0,
                node_id="graph-harness-node",
                attempt=STEP,
                source_attempt=ORCHESTRATION,
                provider="openai",
                model="gpt-test",
                configuration_sha256=_digest("graph-harness-step-config"),
            ),
        ),
    )
    service.admit(plan)
    runtime = _runtime()
    binding_key = (GENERATION.execution_id, STEP.attempt_id)
    runtime._attempt_bundles[binding_key] = SimpleNamespace(
        attempt=STEP,
        journal=journal,
        durable_event_sink=sinks[STEP],
        partial_attempt_sink=lambda event, error: None,
    )
    runtime._run_bindings[STEP.attempt_id] = binding_key
    binding = GraphStepBootstrapBinding(
        service=service,
        plan=plan,
        step_index=0,
    )
    harness = GraphStepBootstrapHarness(
        runtime=runtime,
        binding_resolver=lambda context: binding,
    )
    return journal, sinks, service, plan, runtime, binding, harness


def _context(*, run_id=STEP.attempt_id, session_id=GENERATION.execution_id):
    state = RunState()
    state.seed_messages([{"role": "user", "content": "host transcript"}])
    state.session_state.session_id = session_id
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-test"
    return HarnessContext(
        state=state,
        phase="bootstrap",
        event={"run_id": run_id},
    )


def _append(sink, event_type: str, sequence: int, **payload):
    return sink.append_projected(
        SemanticEventDraft(
            event_id=f"event-{sequence}-{event_type}",
            event_type=event_type,
            attempt=STEP,
            operation_id=f"operation-{sequence}-{event_type}",
            payload={"run_id": STEP.attempt_id, **payload},
        )
    )


class _ModelIO:
    provider = "openai"
    model = "gpt-test"

    def __init__(self, journal=None):
        self.journal = journal
        self.calls = 0

    def fetch_turn(self, request):
        del request
        self.calls += 1
        if self.journal is not None:
            assert "graph.step.started" in {
                event.event_type
                for event in self.journal.capture_snapshot().events
            }
        return ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="graph-harness-response",
        )


class _ResumeModelIO(_ModelIO):
    def fetch_turn(self, request):
        assert "graph.step.resume.admitted" in {
            event.event_type
            for event in self.journal.capture_snapshot().events
        }
        return super().fetch_turn(request)


def _builder(runtime):
    builder = AgentBuilder(
        agent=SimpleNamespace(name="graph-harness-agent"),
        spec=AgentSpec(
            name="graph-harness-agent",
            provider="openai",
            model="gpt-test",
        ),
        state=AgentState(),
        call_context=AgentCallContext(
            mode="run",
            input_messages=[{"role": "user", "content": "run graph"}],
            session_id=GENERATION.execution_id,
            run_id=STEP.attempt_id,
        ),
        model_io_registry=ModelIOFactoryRegistry(),
    )
    builder.set_model_io(_ModelIO())
    ContextModule(runtime=runtime).configure(builder)
    return builder


def _unowned_builder():
    builder = AgentBuilder(
        agent=SimpleNamespace(name="graph-shadow-agent"),
        spec=AgentSpec(
            name="graph-shadow-agent",
            provider="openai",
            model="gpt-test",
        ),
        state=AgentState(),
        call_context=AgentCallContext(
            mode="run",
            input_messages=[{"role": "user", "content": "shadow graph"}],
            session_id=GENERATION.execution_id,
            run_id=STEP.attempt_id,
        ),
        model_io_registry=ModelIOFactoryRegistry(),
    )
    builder.set_model_io(_ModelIO())
    return builder


def test_module_orders_graph_after_context_binding_and_pinned_task_bootstrap():
    runtime = _runtime(task_state=True)
    builder = _builder(runtime)
    PinnedTaskStateBootstrapModule(
        runtime=runtime,
        binding_resolver=lambda context: None,
    ).configure(builder)
    GraphStepBootstrapModule(
        runtime=runtime,
        binding_resolver=lambda context: None,
    ).configure(builder)

    prepared = builder.build()
    harnesses = {harness.name: harness for harness in prepared.loop.harnesses}
    names = [harness.name for harness in prepared.loop.harnesses]

    assert harnesses["context_v2_execution_binding"].order == -1000
    assert harnesses["context_v2_pinned_task_state_bootstrap"].order == -990
    assert harnesses["context_v2_graph_step_bootstrap"].order == -980
    assert names.index("context_v2_execution_binding") < names.index(
        "context_v2_pinned_task_state_bootstrap"
    ) < names.index("context_v2_graph_step_bootstrap")


@pytest.mark.parametrize(
    ("run_id", "session_id", "message"),
    [
        ("wrong-step", GENERATION.execution_id, "run_id"),
        (STEP.attempt_id, "wrong-execution", "session"),
    ],
)
def test_harness_rejects_wrong_run_or_execution_identity(
    tmp_path,
    run_id,
    session_id,
    message,
):
    journal, _sinks, _service, _plan, _runtime, _binding, harness = _open(
        tmp_path
    )

    with pytest.raises(GraphStepBootstrapError, match=message):
        harness.build_delta(_context(run_id=run_id, session_id=session_id))

    assert "graph.step.started" not in {
        event.event_type for event in journal.capture_snapshot().events
    }


def test_graph_start_is_durable_before_the_first_provider_request(tmp_path):
    journal, _sinks, _service, _plan, _runtime, _binding, harness = _open(
        tmp_path
    )
    model_io = _ModelIO(journal)
    loop = KernelLoop(harnesses=[harness], model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "provider must wait"}],
        session_id=GENERATION.execution_id,
        run_id=STEP.attempt_id,
        provider="openai",
        model="gpt-test",
        max_iterations=1,
    )

    assert result.status == "completed"
    assert model_io.calls == 1
    events = journal.capture_snapshot().events
    handoff_index = next(
        index for index, event in enumerate(events) if event.event_type == "handoff.recorded"
    )
    input_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type == "message.user" and event.attempt == STEP
    )
    start_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type == "graph.step.started"
    )
    assert handoff_index < input_index < start_index


def test_graph_bootstrap_scopes_runtime_events_before_durable_projection(
    tmp_path,
):
    journal, _sinks, _service, plan, runtime, _binding, harness = _open(
        tmp_path
    )
    context = _context()
    harness.build_delta(context)

    runtime.persist_event(
        {
            "type": "final_message",
            "run_id": STEP.attempt_id,
            "iteration": 0,
            "content": "scoped graph output",
        }
    )
    runtime.persist_event(
        {
            "type": "run_completed",
            "run_id": STEP.attempt_id,
            "iteration": 0,
            "status": "completed",
        }
    )

    lifecycle = tuple(
        event
        for event in journal.capture_snapshot().events
        if event.event_type in {"final_message", "run_completed"}
    )
    assert len(lifecycle) == 2
    assert all(
        event.payload["workflow_node_id"] == plan.steps[0].node_id
        and event.payload["workflow_step_index"] == 0
        and event.payload["workflow_step_count"] == 1
        for event in lifecycle
    )


def test_graph_runtime_event_rejects_conflicting_host_scope(tmp_path):
    _journal, _sinks, _service, _plan, runtime, _binding, harness = _open(
        tmp_path
    )
    harness.build_delta(_context())

    with pytest.raises(
        ContextExecutionBundleError,
        match="changed its workflow scope",
    ):
        runtime.persist_event(
            {
                "type": "final_message",
                "run_id": STEP.attempt_id,
                "iteration": 0,
                "content": "wrong scope",
                "workflow_node_id": "other-node",
                "workflow_step_index": 0,
                "workflow_step_count": 1,
            }
        )


def test_harness_accepts_distinct_journal_wrappers_for_the_same_store(tmp_path):
    journal, _sinks, _service, _plan, runtime, _binding, harness = _open(
        tmp_path
    )
    binding_key = (GENERATION.execution_id, STEP.attempt_id)
    distinct_wrapper = journal._store.bind_execution(GENERATION.execution_id)
    assert distinct_wrapper is not journal
    runtime._attempt_bundles[binding_key] = SimpleNamespace(
        attempt=STEP,
        journal=distinct_wrapper,
    )

    harness.build_delta(_context())

    assert "graph.step.started" in {
        event.event_type for event in journal.capture_snapshot().events
    }


def test_completed_step_fails_closed_in_harness_until_host_skips_it(tmp_path):
    _journal, sinks, service, plan, _runtime, _binding, harness = _open(tmp_path)
    context = _context()
    harness.build_delta(context)
    _append(sinks[STEP], "run_started", 1, status="running")
    _append(sinks[STEP], "final_message", 2, content="done")
    _append(sinks[STEP], "run_completed", 3, status="completed")
    service.complete_step(plan, 0, full_output={"output": "done"})

    with pytest.raises(GraphStepBootstrapError, match="host must recover and skip"):
        harness.build_delta(context)


def test_module_requires_the_exact_attached_context_runtime_and_is_unique():
    runtime = _runtime()
    builder = _builder(runtime)
    module = GraphStepBootstrapModule(
        runtime=runtime,
        binding_resolver=lambda context: None,
    )
    module.configure(builder)
    harnesses_before = tuple(builder.harnesses)

    with pytest.raises(GraphStepBootstrapModuleError, match="already attached"):
        module.configure(builder)
    assert tuple(builder.harnesses) == harnesses_before

    unattached = AgentBuilder(
        agent=SimpleNamespace(name="unattached"),
        spec=AgentSpec(name="unattached", provider="openai", model="gpt-test"),
        state=AgentState(),
        call_context=AgentCallContext(mode="run"),
        model_io_registry=ModelIOFactoryRegistry(),
    )
    with pytest.raises(GraphStepBootstrapModuleError, match="attached first"):
        GraphStepBootstrapModule(
            runtime=runtime,
            binding_resolver=lambda context: None,
        ).configure(unattached)


def test_resume_harness_admits_exact_resolution_before_provider(tmp_path):
    journal, sinks, _service, plan, runtime, _binding, bootstrap = _open(
        tmp_path
    )
    context = _context()
    bootstrap.build_delta(context)
    request = sinks[STEP](
        {
            "type": "interaction_requested",
            "run_id": STEP.attempt_id,
            "iteration": 1,
            "interaction_id": "graph-harness-interaction",
            "interaction_request": {
                "interaction_id": "graph-harness-interaction",
                "kind": "human_input",
                "question": "Continue?",
            },
        }
    )
    resolution = ContextInputIngress(
        attempt=STEP,
        projector=sinks[STEP].projector,
        sink=sinks[STEP],
    ).persist(
        HostResolvedInteractionInput(
            attempt=STEP,
            interaction_id="graph-harness-interaction",
            response={"answer": "yes"},
            submitted_by="ui:test",
        )
    )
    binding = GraphStepResumeBinding(
        service=_service,
        plan=plan,
        step_index=0,
        interaction_id="graph-harness-interaction",
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    )
    harness = GraphStepResumeHarness(
        runtime=runtime,
        binding_resolver=lambda _context: binding,
    )
    model_io = _ResumeModelIO(journal)
    loop = KernelLoop(harnesses=[harness], model_io=model_io)

    result = loop.run(
        [{"role": "user", "content": "resume graph"}],
        session_id=GENERATION.execution_id,
        run_id=STEP.attempt_id,
        provider="openai",
        model="gpt-test",
        max_iterations=1,
    )

    assert result.status == "completed"
    assert model_io.calls == 1
    events = journal.capture_snapshot().events
    assert sum(event.event_type == "graph.step.started" for event in events) == 1
    assert sum(event.event_type == "handoff.recorded" for event in events) == 1
    assert sum(
        event.event_type == "graph.step.resume.admitted" for event in events
    ) == 1
    admitted = next(
        event
        for event in events
        if event.event_type == "graph.step.resume.admitted"
    )
    assert resolution.cursor.store_seq < admitted.store_seq


def test_completed_resumed_step_is_skipped_before_provider_reexecution(tmp_path):
    journal, sinks, service, plan, runtime, _binding, bootstrap = _open(tmp_path)
    bootstrap.build_delta(_context())
    request = sinks[STEP](
        {
            "type": "interaction_requested",
            "run_id": STEP.attempt_id,
            "iteration": 1,
            "interaction_id": "graph-harness-completed-resume",
            "interaction_request": {
                "interaction_id": "graph-harness-completed-resume",
                "kind": "human_input",
                "question": "Continue?",
            },
        }
    )
    resolution = ContextInputIngress(
        attempt=STEP,
        projector=sinks[STEP].projector,
        sink=sinks[STEP],
    ).persist(
        HostResolvedInteractionInput(
            attempt=STEP,
            interaction_id="graph-harness-completed-resume",
            response={"answer": "yes"},
            submitted_by="ui:test",
        )
    )
    service.resume_step(
        plan,
        0,
        interaction_id="graph-harness-completed-resume",
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    )
    _append(sinks[STEP], "run_started", 50, status="running")
    _append(sinks[STEP], "final_message", 51, content="already complete")
    _append(sinks[STEP], "run_completed", 52, status="completed")
    service.complete_step(plan, 0, full_output="already complete")
    binding = GraphStepResumeBinding(
        service=service,
        plan=plan,
        step_index=0,
        interaction_id="graph-harness-completed-resume",
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    )
    harness = GraphStepResumeHarness(
        runtime=runtime,
        binding_resolver=lambda _context: binding,
    )
    model_io = _ResumeModelIO(journal)
    loop = KernelLoop(harnesses=[harness], model_io=model_io)

    with pytest.raises(GraphStepBootstrapError, match="host must recover and skip"):
        loop.run(
            [{"role": "user", "content": "must not replay"}],
            session_id=GENERATION.execution_id,
            run_id=STEP.attempt_id,
            provider="openai",
            model="gpt-test",
            max_iterations=1,
        )
    assert model_io.calls == 0
    assert sum(
        event.event_type == "graph.step.resume.admitted"
        for event in journal.capture_snapshot().events
    ) == 1


def test_resume_module_follows_context_binding_and_excludes_start_bootstrap():
    runtime = _runtime()
    builder = _builder(runtime)
    GraphStepResumeModule(
        runtime=runtime,
        binding_resolver=lambda context: None,
    ).configure(builder)

    prepared = builder.build()
    harnesses = {harness.name: harness for harness in prepared.loop.harnesses}
    names = [harness.name for harness in prepared.loop.harnesses]
    assert harnesses["context_v2_execution_binding"].order == -1000
    assert harnesses["context_v2_graph_step_resume"].order == -980
    assert names.index("context_v2_execution_binding") < names.index(
        "context_v2_graph_step_resume"
    )

    with pytest.raises(GraphStepBootstrapModuleError, match="already attached"):
        GraphStepBootstrapModule(
            runtime=runtime,
            binding_resolver=lambda context: None,
        ).configure(builder)

    other_runtime = _runtime()
    other_builder = _builder(other_runtime)
    GraphStepBootstrapModule(
        runtime=other_runtime,
        binding_resolver=lambda context: None,
    ).configure(other_builder)
    with pytest.raises(GraphStepResumeModuleError, match="already attached"):
        GraphStepResumeModule(
            runtime=other_runtime,
            binding_resolver=lambda context: None,
        ).configure(other_builder)


@pytest.mark.parametrize(
    "module_type",
    [GraphStepBootstrapModule, GraphStepResumeModule],
)
def test_graph_module_accepts_one_enabled_official_shadow_owner(module_type):
    runtime = _runtime()
    builder = _unowned_builder()
    ContextShadowModule(runtime=runtime, enabled=True).configure(builder)

    module_type(
        runtime=runtime,
        binding_resolver=lambda context: None,
    ).configure(builder)
    prepared = builder.build()

    assert prepared.context_runtime is None
    assert prepared.semantic_context_owner is None
    names = [harness.name for harness in prepared.loop.harnesses]
    graph_name = (
        "context_v2_graph_step_bootstrap"
        if module_type is GraphStepBootstrapModule
        else "context_v2_graph_step_resume"
    )
    assert names.count("context_v2_execution_binding") == 1
    assert names.count("context_v2_shadow_compiler") == 1
    assert names.count(graph_name) == 1
    assert names.index("context_v2_execution_binding") < names.index(graph_name)
    assert names.index(graph_name) < names.index("context_v2_shadow_compiler")


@pytest.mark.parametrize(
    ("module_type", "error_type"),
    [
        (GraphStepBootstrapModule, GraphStepBootstrapModuleError),
        (GraphStepResumeModule, GraphStepResumeModuleError),
    ],
)
def test_graph_module_rejects_disabled_or_foreign_shadow_owner(
    module_type,
    error_type,
):
    runtime = _runtime()
    disabled = _unowned_builder()
    ContextShadowModule(runtime=runtime, enabled=False).configure(disabled)
    with pytest.raises(error_type, match="attached first|enabled"):
        module_type(
            runtime=runtime,
            binding_resolver=lambda context: None,
        ).configure(disabled)

    foreign_runtime = _runtime()
    foreign = _unowned_builder()
    ContextShadowModule(runtime=foreign_runtime, enabled=True).configure(foreign)
    with pytest.raises(error_type, match="enabled"):
        module_type(
            runtime=runtime,
            binding_resolver=lambda context: None,
        ).configure(foreign)
