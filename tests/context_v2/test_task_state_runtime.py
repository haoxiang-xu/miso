from __future__ import annotations

import json
from pathlib import Path

import pytest

from unchain.context import (
    ArtifactService,
    BoundContextTaskStateReader,
    ContextBuildUnavailableError,
    ContextCompileCoordinator,
    ContextExecutionBundle,
    ContextInputIngress,
    ContextRuntime,
    ContextTaskStateReadOutcome,
    DurableContextRuntimeFactory,
    DurableHandoffRecorder,
    DurableToolBoundary,
    HandoffService,
    HostResolvedCurrentInput,
    JournalContextRequestFactory,
    PinnedTaskState,
)
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.context.task_state_request_factory import (
    TaskStateContextRequestFactory,
)
from unchain.context.task_state_runtime import (
    TaskStateContextRuntime,
    TaskStateContextRuntimeError,
    TaskStateContextSubagentCompletionSink,
)
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.journal import DurableEventSink
from unchain.persistence.sqlite_context_compiler_v2 import (
    SQLiteContextCompilerV2Store,
)
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


class _Reader(BoundContextTaskStateReader):
    def __init__(self, binding_id: str, outcome) -> None:
        super().__init__(binding_id)
        self.outcome = outcome
        self.calls = 0

    def read_for_context(self):
        self.calls += 1
        return self.outcome


def _state(*, oversized: bool = False) -> PinnedTaskState:
    constraints = (
        tuple(f"classified constraint {index}" for index in range(257))
        if oversized
        else ("keep the production gate closed",)
    )
    return PinnedTaskState(
        state_id="task-state-a",
        revision=5,
        objective="wire pinned task state through the official compiler",
        success_criteria=("request survives restart",),
        constraints=constraints,
        confirmed_decisions=("Unchain owns context",),
        active_plan=("compose the request factory",),
    )


def _context(
    *,
    phase: str,
    session_id: str = "execution-a",
    run_id: str = "attempt-a",
    current_input: str = "continue",
) -> HarnessContext:
    state = RunState()
    state.session_state.session_id = session_id
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-test"
    state.provider_state.max_context_window_tokens = 16_384
    return HarnessContext(
        state=state,
        phase=phase,
        event={
            "run_id": run_id,
            "generation_id": "generation-a",
            "current_input": current_input,
        },
    )


def _runtime_stack(root: Path, reader_resolver):
    database_path = root / "context_v2.sqlite3"
    object_directory = root / "objects"
    context_store = SQLiteContextV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    compiler_store = SQLiteContextCompilerV2Store(context_store=context_store)
    bundles = {}

    def build(attempt):
        journal = context_store.bind_execution(attempt.generation.execution_id)
        artifacts = ArtifactService(
            journal,
            sanitizer=lambda content, media_type: content,
        )
        projector = CanonicalSemanticEventProjector(
            attempt=attempt,
            artifacts=artifacts,
            payload_sanitizer=lambda event_type, payload: payload,
        )
        sink = DurableEventSink(journal, attempt, projector)
        compiler = compiler_store.bind_execution(
            attempt.generation.execution_id,
            artifacts=artifacts,
        )
        coordinator = ContextCompileCoordinator(
            journal=journal,
            checkpoint_repository=compiler.checkpoints,
            build_repository=compiler.context_builds,
            partial_attempt_sink=lambda request, error: None,
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
            partial_attempt_sink=lambda event, error: None,
        )
        bundles[(attempt.generation.execution_id, attempt.attempt_id)] = bundle
        return bundle

    factory = DurableContextRuntimeFactory(
        bundle_builder=build,
        generation_resolver=lambda context, execution_id: context.event[
            "generation_id"
        ],
        current_input_resolver=lambda context, attempt: HostResolvedCurrentInput(
            attempt=attempt,
            content=context.event["current_input"],
        ),
    )
    runtime = TaskStateContextRuntime.from_factory(
        owner_id="context-v2-task-state",
        execution_factory=factory,
        task_state_reader_resolver=reader_resolver,
    )
    return runtime, bundles


def test_sanctioned_runtime_keeps_the_exact_bundle_and_decorates_compilation(
    tmp_path: Path,
) -> None:
    state = _state()
    reader = _Reader(
        "binding-a",
        ContextTaskStateReadOutcome.from_state(state),
    )
    runtime, bundles = _runtime_stack(tmp_path, lambda bundle: reader)
    bootstrap = _context(phase="bootstrap")

    runtime.bind_context(bootstrap)
    result = runtime.compile_context(_context(phase="before_model"))

    bundle = bundles[("execution-a", "attempt-a")]
    assert type(bundle) is ContextExecutionBundle
    assert type(bundle.request_factory) is JournalContextRequestFactory
    assert bundle.request_factory.attempt == bundle.attempt
    assert bundle.request_factory.journal is bundle.journal
    assert isinstance(runtime, ContextRuntime)
    assert reader.calls == 1
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    assert state.objective in serialized
    compile_runtime = runtime._task_state_compile_runtimes[
        ("execution-a", "attempt-a")
    ]
    assert type(compile_runtime) is ContextRuntime
    assert isinstance(
        compile_runtime.request_factory,
        TaskStateContextRequestFactory,
    )
    assert compile_runtime.request_factory.base_factory is bundle.request_factory
    assert compile_runtime.request_factory.attempt == bundle.attempt
    assert compile_runtime.request_factory.journal is bundle.journal


def test_repeated_bootstrap_keeps_one_compile_binding_and_subagent_sink(
    tmp_path: Path,
) -> None:
    readers = []

    def resolve(bundle):
        reader = _Reader(
            "binding-a",
            ContextTaskStateReadOutcome.from_state(_state()),
        )
        readers.append((bundle, reader))
        return reader

    runtime, bundles = _runtime_stack(tmp_path, resolve)
    bootstrap = _context(phase="bootstrap")

    runtime.bind_context(bootstrap)
    runtime.bind_context(bootstrap)
    sink = runtime.prepare_subagent_completion_sink(
        _context(phase="before_model"),
        call_id="call-child-a",
    )

    assert len(bundles) == 1
    assert len(runtime._task_state_compile_runtimes) == 1
    assert len(readers) == 2
    assert readers[0][1].binding_id == readers[1][1].binding_id
    assert isinstance(sink, TaskStateContextSubagentCompletionSink)
    assert sink.parent_attempt == bundles[("execution-a", "attempt-a")].attempt


def test_unavailable_task_state_uses_the_official_runtime_failure_path(
    tmp_path: Path,
) -> None:
    outcome = ContextTaskStateReadOutcome.from_state(_state(oversized=True))
    reader = _Reader("binding-a", outcome)
    runtime, bundles = _runtime_stack(tmp_path, lambda bundle: reader)
    runtime.bind_context(_context(phase="bootstrap"))

    with pytest.raises(ContextBuildUnavailableError) as raised:
        runtime.compile_context(_context(phase="before_model"))

    assert raised.value.result.envelope is not None
    assert raised.value.result.envelope.status.value == "unavailable"
    assert raised.value.result.messages == ()
    serialized = json.dumps(raised.value.result.to_dict(), sort_keys=True)
    assert "classified constraint" not in serialized
    assert _state().objective not in serialized
    assert type(
        bundles[("execution-a", "attempt-a")].request_factory
    ) is JournalContextRequestFactory


@pytest.mark.parametrize(
    "resolver",
    (
        lambda bundle: object(),
        lambda bundle: (_ for _ in ()).throw(OSError("reader offline")),
    ),
)
def test_reader_resolution_fails_closed_after_exact_bundle_validation(
    tmp_path: Path,
    resolver,
) -> None:
    runtime, bundles = _runtime_stack(tmp_path, resolver)

    with pytest.raises(TaskStateContextRuntimeError, match="reader"):
        runtime.bind_context(_context(phase="bootstrap"))

    bundle = bundles[("execution-a", "attempt-a")]
    assert type(bundle) is ContextExecutionBundle
    assert type(bundle.request_factory) is JournalContextRequestFactory
    assert [
        event.event_type for event in bundle.journal.capture_snapshot().events
    ] == ["message.user"]
    assert runtime._task_state_compile_runtimes == {}
