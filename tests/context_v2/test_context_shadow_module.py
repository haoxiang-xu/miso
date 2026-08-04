from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from unchain.agent import (
    AgentBuilder,
    AgentCallContext,
    AgentSpec,
    AgentState,
    ContextShadowModule,
)
from unchain.agent.model_io import ModelIOFactoryRegistry
from unchain.context import (
    ArtifactService,
    ContextCompileCoordinator,
    ContextExecutionBundle,
    ContextRuntime,
    ContextShadowCompilerHarness,
    DurableContextRuntimeFactory,
    DurableHandoffRecorder,
    DurableToolBoundary,
    HandoffService,
    HostResolvedCurrentInput,
    JournalContextRequestFactory,
    find_durable_persistence_failure,
)
from unchain.context.ingress import ContextInputIngress
from unchain.context.compiler import project_canonical_journal_messages
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.journal import DurableEventSink
from unchain.kernel import HarnessContext, ModelTurnResult, RunState
from unchain.optimizers import LastNOptimizer, LastNOptimizerConfig
from unchain.persistence.sqlite_context_compiler_v2 import (
    SQLiteContextCompilerV2Store,
)
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store
from unchain.subagents import SubagentResult


class _CaptureModelIO:
    provider = "openai"
    model = "gpt-shadow-test"

    def __init__(self) -> None:
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        return ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            response_id="response-shadow",
        )


class _FailingContextBuildRepository:
    def __init__(self, repository) -> None:
        self.execution_id = repository.execution_id
        self._repository = repository

    def get_by_operation(self, **kwargs):
        return self._repository.get_by_operation(**kwargs)

    def get_by_trigger(self, **kwargs):
        return self._repository.get_by_trigger(**kwargs)

    def record(self, **kwargs):
        del kwargs
        raise OSError("injected shadow context-build persistence failure")


def _lookup_weather(city: str) -> str:
    return f"sunny in {city}"


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _current_input(context, attempt):
    for index in range(len(context.latest_messages()) - 1, -1, -1):
        message = context.latest_messages()[index]
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return HostResolvedCurrentInput(
                attempt=attempt,
                content=content,
                message_index=index,
            )
    return None


def _shadow_runtime(
    root: Path,
    *,
    fail_context_build: bool = False,
):
    context_store = SQLiteContextV2Store(
        database_path=root / "context_v2.sqlite3",
        object_directory=root / "objects",
    )
    compiler_store = SQLiteContextCompilerV2Store(context_store=context_store)
    bundles = {}
    partials = []

    def build(attempt):
        execution_id = attempt.generation.execution_id
        journal = context_store.bind_execution(execution_id)
        artifacts = ArtifactService(
            journal,
            sanitizer=lambda content, media_type: content,
        )
        compiler_capabilities = compiler_store.bind_execution(
            execution_id,
            artifacts=artifacts,
        )
        projector = CanonicalSemanticEventProjector(
            attempt=attempt,
            artifacts=artifacts,
            payload_sanitizer=lambda event_type, payload: payload,
        )
        sink = DurableEventSink(journal, attempt, projector)
        build_repository = compiler_capabilities.context_builds
        if fail_context_build:
            build_repository = _FailingContextBuildRepository(build_repository)
        coordinator = ContextCompileCoordinator(
            journal=journal,
            checkpoint_repository=compiler_capabilities.checkpoints,
            build_repository=build_repository,
            partial_attempt_sink=lambda request, error: partials.append(
                (request, error)
            ),
        )
        handoffs = HandoffService(artifacts)
        bundle = ContextExecutionBundle(
            attempt=attempt,
            journal=journal,
            projector=projector,
            durable_event_sink=sink,
            coordinator=coordinator,
            artifacts=artifacts,
            handoffs=handoffs,
            ingress=ContextInputIngress(
                attempt=attempt,
                projector=projector,
                sink=sink,
            ),
            request_factory=JournalContextRequestFactory(
                attempt=attempt,
                journal=journal,
                model_window_fallback=lambda provider, model: 16_384,
            ),
            tool_boundary=DurableToolBoundary(
                attempt=attempt,
                projector=projector,
                sink=sink,
            ),
            handoff_recorder=DurableHandoffRecorder(
                attempt=attempt,
                handoffs=handoffs,
                projector=projector,
                sink=sink,
            ),
            partial_attempt_sink=lambda event, error: partials.append(
                (event, error)
            ),
        )
        bundles[(execution_id, attempt.attempt_id)] = (
            bundle,
            compiler_capabilities,
        )
        return bundle

    runtime = ContextRuntime.from_factory(
        owner_id="context-v2-shadow",
        execution_factory=DurableContextRuntimeFactory(
            bundle_builder=build,
            generation_resolver=lambda context, execution_id: (
                f"generation-{execution_id}"
            ),
            current_input_resolver=_current_input,
        ),
    )
    return runtime, bundles, partials


def _prepared_agent(
    *,
    session_id: str,
    run_id: str,
    shadow_runtime: ContextRuntime | None = None,
    shadow_enabled: bool = False,
):
    messages = [
        {"role": "system", "content": "stable instructions"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "current question"},
    ]
    model_io = _CaptureModelIO()
    builder = AgentBuilder(
        agent=object(),
        spec=AgentSpec(
            name="shadow-agent",
            provider="openai",
            model=model_io.model,
        ),
        state=AgentState(),
        call_context=AgentCallContext(
            mode="run",
            input_messages=copy.deepcopy(messages),
            session_id=session_id,
            run_id=run_id,
            max_context_window_tokens=16_384,
        ),
        model_io_registry=ModelIOFactoryRegistry(),
        harnesses=[
            LastNOptimizer(LastNOptimizerConfig(last_n_turns=1)),
        ],
    )
    builder.set_model_io(model_io)
    builder.add_tool(_lookup_weather)
    if shadow_runtime is not None:
        ContextShadowModule(
            runtime=shadow_runtime,
            enabled=shadow_enabled,
        ).configure(builder)
    return builder.build(), model_io


def _bootstrap_context(*, execution_id: str, attempt_id: str) -> HarnessContext:
    state = RunState()
    state.session_state.session_id = execution_id
    return HarnessContext(
        state=state,
        phase="bootstrap",
        event={"run_id": attempt_id},
    )


def test_parent_runtime_cold_binds_prepared_child_across_separate_host_and_restart(
    tmp_path: Path,
) -> None:
    execution_id = "separate-host-execution"
    parent_attempt_id = "parent-attempt"
    child_run_id = "recipe-ref-child"
    call_id = "call-recipe-ref"
    parent_runtime, parent_bundles, _ = _shadow_runtime(tmp_path)
    parent_context = _bootstrap_context(
        execution_id=execution_id,
        attempt_id=parent_attempt_id,
    )
    parent_runtime.build_harnesses()[0].build_delta(parent_context)
    parent_bundle = parent_bundles[(execution_id, parent_attempt_id)][0]
    parent_bundle.durable_event_sink(
        {
            "type": "tool_call",
            "run_id": parent_attempt_id,
            "iteration": 0,
            "tool_name": "delegate_to_subagent",
            "call_id": call_id,
            "arguments": {"target": "recipe-ref", "task": "inspect"},
            "source_provider": "openai",
        }
    )
    sink = parent_runtime.prepare_subagent_completion_sink(
        parent_context,
        call_id=call_id,
    )
    assert sink is not None
    preparation = sink.prepare_input(
        child_run_id=child_run_id,
        child_id="recipe-ref-child",
        mode="delegate",
        lineage=["parent", "recipe-ref-child"],
        template_name="recipe-ref",
        input_messages="preserve the entire recipe task",
    )

    parent_child_bundle = parent_runtime.bind_prepared_subagent_input(
        child_run_id
    )
    replayed_parent_child_bundle = parent_runtime.bind_prepared_subagent_input(
        child_run_id
    )

    assert replayed_parent_child_bundle is parent_child_bundle
    assert parent_child_bundle.attempt == preparation.prepared.child_attempt
    assert not any(
        event.event_type in {"provider.request", "provider.response"}
        for event in parent_child_bundle.journal.capture_snapshot().events
    )

    graph_runtime, graph_bundles, _ = _shadow_runtime(tmp_path)
    graph_child_bundle = graph_runtime.bind_prepared_subagent_input(
        child_run_id,
        prepared=preparation.prepared,
    )
    graph_runtime.persist_event(
        {
            "type": "run_started",
            "run_id": child_run_id,
            "iteration": 0,
        }
    )
    graph_runtime.persist_event(
        {
            "type": "final_message",
            "run_id": child_run_id,
            "iteration": 1,
            "status": "completed",
        }
    )
    graph_snapshot = graph_child_bundle.journal.capture_snapshot()
    child_terminal = next(
        event
        for event in graph_snapshot.events
        if event.attempt == graph_child_bundle.attempt
        and event.event_type == "final_message"
    )
    assert child_terminal.payload["parent_run_id"] == parent_attempt_id
    graph_state = RunState()
    graph_state.session_state.session_id = execution_id
    graph_state.provider_state.provider = "openai"
    graph_state.provider_state.model = "gpt-shadow-test"
    graph_state.provider_state.max_context_window_tokens = 16_384
    graph_request = graph_child_bundle.request_factory(
        HarnessContext(
            state=graph_state,
            phase="before_model",
            event={"run_id": child_run_id, "toolkit": None},
        )
    )
    projected = project_canonical_journal_messages(graph_request)
    assert not any(
        message.get("role") == "assistant"
        and message.get("content") == "graph complete"
        for message in projected.source_messages
    )
    result = SubagentResult(
        mode="delegate",
        agent_name="recipe-ref-child",
        template_name="recipe-ref",
        status="completed",
        output="graph complete",
        summary="graph complete",
        messages=[{"role": "assistant", "content": "graph complete"}],
        lineage=["parent", "recipe-ref-child"],
    )

    persisted = sink.record(child_run_id=child_run_id, result=result)

    assert persisted.envelope.child_attempt == parent_child_bundle.attempt

    restarted_runtime, restarted_bundles, _ = _shadow_runtime(tmp_path)
    restarted_parent = _bootstrap_context(
        execution_id=execution_id,
        attempt_id=parent_attempt_id,
    )
    restarted_runtime.build_harnesses()[0].build_delta(restarted_parent)
    restarted_sink = restarted_runtime.prepare_subagent_completion_sink(
        restarted_parent,
        call_id=call_id,
    )
    assert restarted_sink is not None
    restarted_preparation = restarted_sink.prepare_input(
        child_run_id=child_run_id,
        child_id="recipe-ref-child",
        mode="delegate",
        lineage=["parent", "recipe-ref-child"],
        template_name="recipe-ref",
        input_messages="preserve the entire recipe task",
    )
    restarted_child_bundle = (
        restarted_runtime.bind_prepared_subagent_input(child_run_id)
    )

    assert restarted_preparation.recovered_result == result
    assert restarted_child_bundle.attempt == preparation.prepared.child_attempt
    assert restarted_bundles[(execution_id, child_run_id)][0] is (
        restarted_child_bundle
    )
    assert graph_bundles[(execution_id, child_run_id)][0] is graph_child_bundle


def test_shadow_module_is_strictly_default_closed(tmp_path: Path) -> None:
    runtime, _, _ = _shadow_runtime(tmp_path)
    prepared, _ = _prepared_agent(
        session_id="shadow-default-closed",
        run_id="attempt-default-closed",
        shadow_runtime=runtime,
    )

    assert prepared.semantic_context_owner is None
    assert prepared.context_runtime is None
    assert not any(
        harness.name.startswith("context_v2_shadow")
        or harness.name == "context_v2_execution_binding"
        for harness in prepared.loop.harnesses
    )


def test_shadow_compile_preserves_legacy_provider_input_and_is_durable(
    tmp_path: Path,
) -> None:
    baseline, baseline_model = _prepared_agent(
        session_id="baseline-session",
        run_id="baseline-attempt",
    )
    runtime, bundles, _ = _shadow_runtime(tmp_path)
    shadow, shadow_model = _prepared_agent(
        session_id="shadow-session",
        run_id="shadow-attempt",
        shadow_runtime=runtime,
        shadow_enabled=True,
    )

    baseline.run()
    shadow.run()

    assert shadow.semantic_context_owner is None
    assert shadow.context_runtime is None
    assert not any(
        getattr(harness, "semantic_context_owner", None) is not None
        for harness in shadow.loop.harnesses
    )
    assert len(baseline_model.requests) == len(shadow_model.requests) == 1
    baseline_request = baseline_model.requests[0]
    shadow_request = shadow_model.requests[0]
    assert _canonical(shadow_request.messages) == _canonical(
        baseline_request.messages
    )
    assert _canonical(shadow_request.toolkit.to_provider_json("openai")) == (
        _canonical(baseline_request.toolkit.to_provider_json("openai"))
    )

    _, capabilities = bundles[("shadow-session", "shadow-attempt")]
    recorded = capabilities.context_builds.latest(
        generation_id="generation-shadow-session"
    )
    assert recorded is not None
    assert recorded.status.value == "complete"

    reopened_store = SQLiteContextV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    )
    reopened_journal = reopened_store.bind_execution("shadow-session")
    reopened_artifacts = ArtifactService(
        reopened_journal,
        sanitizer=lambda content, media_type: content,
    )
    reopened = SQLiteContextCompilerV2Store(
        context_store=reopened_store
    ).bind_execution(
        "shadow-session",
        artifacts=reopened_artifacts,
    )
    assert reopened.context_builds.latest(
        generation_id="generation-shadow-session"
    ) == recorded


def test_shadow_build_failure_marks_partial_and_never_calls_model(
    tmp_path: Path,
) -> None:
    runtime, _, partials = _shadow_runtime(
        tmp_path,
        fail_context_build=True,
    )
    prepared, model_io = _prepared_agent(
        session_id="shadow-failure-session",
        run_id="shadow-failure-attempt",
        shadow_runtime=runtime,
        shadow_enabled=True,
    )

    with pytest.raises(OSError) as caught:
        prepared.run()

    assert find_durable_persistence_failure(caught.value) is caught.value
    assert len(partials) == 1
    assert partials[0][1] is caught.value
    assert model_io.requests == []


def test_shadow_harness_returns_diagnostics_without_message_ops(tmp_path: Path) -> None:
    runtime, _, _ = _shadow_runtime(tmp_path)
    prepared, _ = _prepared_agent(
        session_id="shadow-diagnostics-session",
        run_id="shadow-diagnostics-attempt",
        shadow_runtime=runtime,
        shadow_enabled=True,
    )
    assert any(
        harness.name == "context_v2_execution_binding"
        for harness in prepared.loop.harnesses
    )
    compiler = next(
        harness
        for harness in prepared.loop.harnesses
        if isinstance(harness, ContextShadowCompilerHarness)
    )
    state = prepared.loop.seed_state(
        prepared.call_context.input_messages,
        provider="openai",
        model="gpt-shadow-test",
        session_id="shadow-diagnostics-session",
        max_context_window_tokens=16_384,
    )
    prepared.loop._dispatch_bootstrap(
        state,
        payload={},
        response_format=None,
        callback=None,
        verbose=False,
        toolkit=prepared.toolkit,
        run_id="shadow-diagnostics-attempt",
        resume_mode=False,
    )
    from unchain.kernel import HarnessContext

    context = HarnessContext(
        state=state,
        phase="before_model",
        event={
            "run_id": "shadow-diagnostics-attempt",
            "toolkit": prepared.toolkit,
        },
    )
    before = _canonical(context.latest_messages())
    delta = compiler.build_delta(context)

    assert delta.ops == ()
    assert _canonical(context.latest_messages()) == before
    assert delta.state_updates["context_v2_shadow"]["mode"] == "shadow"
    assert delta.trace["context_shadow"] is True
    assert delta.trace["context_build_status"] == "complete"
