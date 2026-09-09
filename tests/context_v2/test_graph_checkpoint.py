from __future__ import annotations

import hashlib

import pytest

from unchain.context.artifacts import ArtifactService
from unchain.context.derived_handoff import DerivedHandoffInputIngress
from unchain.context.graph_checkpoint import (
    GraphCheckpointConflict,
    GraphCheckpointError,
    GraphCheckpointService,
    GraphExecutionPlan,
    GraphStepBinding,
    GraphStepDisposition,
    JournalGraphCheckpointRepository,
)
from unchain.context.handoff import DurableHandoffRecorder, HandoffService
from unchain.context.ingress import ContextInputIngress, HostResolvedCurrentInput
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.journal import (
    AttemptRef,
    DurableEventSink,
    GenerationRef,
    SemanticEventDraft,
)
from unchain.persistence import SQLiteContextV2Store


GENERATION = GenerationRef("graph-execution", "graph-generation")
ORCHESTRATION = AttemptRef(GENERATION, "graph-orchestration")
STEP_0 = AttemptRef(GENERATION, "graph-step-0")
STEP_1 = AttemptRef(GENERATION, "graph-step-1")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _projector(attempt: AttemptRef, artifacts: ArtifactService):
    return CanonicalSemanticEventProjector(
        attempt=attempt,
        artifacts=artifacts,
        payload_sanitizer=lambda _event_type, payload: payload,
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
        attempt: _projector(attempt, artifacts)
        for attempt in (ORCHESTRATION, STEP_0, STEP_1)
    }
    sinks = {
        attempt: DurableEventSink(journal, attempt, projectors[attempt])
        for attempt in projectors
    }

    def derived_ingress(
        consumer_attempt: AttemptRef,
        source_attempt: AttemptRef,
    ) -> DerivedHandoffInputIngress:
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
        derived_ingress_resolver=derived_ingress,
    )
    return store, journal, artifacts, sinks, service


def _plan(initial_input_cursor, *, topology: str = "topology-v1"):
    return GraphExecutionPlan(
        orchestration_attempt=ORCHESTRATION,
        topology_sha256=_digest(topology),
        initial_input_cursor=initial_input_cursor,
        steps=(
            GraphStepBinding(
                index=0,
                node_id="node-0",
                attempt=STEP_0,
                source_attempt=ORCHESTRATION,
                provider="openai",
                model="gpt-test",
                configuration_sha256=_digest("node-0-config"),
            ),
            GraphStepBinding(
                index=1,
                node_id="node-1",
                attempt=STEP_1,
                source_attempt=STEP_0,
                provider="anthropic",
                model="claude-test",
                configuration_sha256=_digest("node-1-config"),
            ),
        ),
    )


def _bootstrap(tmp_path, *, topology: str = "topology-v1"):
    store, journal, artifacts, sinks, service = _open(tmp_path)
    ingress = ContextInputIngress(
        attempt=ORCHESTRATION,
        projector=sinks[ORCHESTRATION].projector,
        sink=sinks[ORCHESTRATION],
    )
    initial = ingress.persist(
        HostResolvedCurrentInput(
            attempt=ORCHESTRATION,
            content="run the graph",
            message_index=0,
        )
    )
    plan = _plan(initial.cursor, topology=topology)
    service.admit(plan)
    return store, journal, artifacts, sinks, service, plan


def _append(
    sink: DurableEventSink,
    attempt: AttemptRef,
    event_type: str,
    sequence: int,
    **payload,
):
    return sink.append_projected(
        SemanticEventDraft(
            event_id=f"event-{attempt.attempt_id}-{sequence}-{event_type}",
            event_type=event_type,
            attempt=attempt,
            operation_id=f"operation-{attempt.attempt_id}-{sequence}-{event_type}",
            payload={"run_id": attempt.attempt_id, **payload},
        )
    )


def _complete_runtime(sinks, attempt: AttemptRef, *, output: str, base: int = 1):
    _append(sinks[attempt], attempt, "run_started", base, status="running")
    final = _append(
        sinks[attempt],
        attempt,
        "final_message",
        base + 1,
        content=output,
    )
    terminal = _append(
        sinks[attempt],
        attempt,
        "run_completed",
        base + 2,
        status="completed",
    )
    return final, terminal


def test_plan_requires_every_step_to_name_its_immediate_predecessor():
    cursor = type("Cursor", (), {"store_seq": 1, "event_id": "seed"})()
    from unchain.journal import EventCursor

    with pytest.raises(ValueError, match="immediate predecessor"):
        GraphExecutionPlan(
            orchestration_attempt=ORCHESTRATION,
            topology_sha256=_digest("topology"),
            initial_input_cursor=EventCursor(cursor.store_seq, cursor.event_id),
            steps=(
                GraphStepBinding(
                    index=0,
                    node_id="node-0",
                    attempt=STEP_0,
                    source_attempt=ORCHESTRATION,
                    provider="openai",
                    model="gpt-test",
                    configuration_sha256=_digest("node-0"),
                ),
                GraphStepBinding(
                    index=1,
                    node_id="node-1",
                    attempt=STEP_1,
                    source_attempt=ORCHESTRATION,
                    provider="openai",
                    model="gpt-test",
                    configuration_sha256=_digest("node-1"),
                ),
            ),
        )


def test_admit_and_start_zero_persist_handoff_input_before_step_start(tmp_path):
    _store, journal, _artifacts, _sinks, service, plan = _bootstrap(tmp_path)

    receipt = service.start_step(plan, 0)

    assert receipt.disposition is GraphStepDisposition.STARTED
    assert receipt.handoff is not None
    assert receipt.started_cursor is not None
    assert (
        receipt.handoff.handoff_cursor.store_seq
        < receipt.handoff.input_cursor.store_seq
        < receipt.started_cursor.store_seq
    )
    assert [event.event_type for event in journal.capture_snapshot().events] == [
        "message.user",
        "graph.execution.admitted",
        "handoff.recorded",
        "message.user",
        "graph.step.started",
    ]


def test_complete_zero_then_start_one_uses_immediate_predecessor_artifact(tmp_path):
    _store, journal, _artifacts, sinks, service, plan = _bootstrap(tmp_path)
    service.start_step(plan, 0)
    _complete_runtime(sinks, STEP_0, output="zero")
    completion = service.complete_step(
        plan,
        0,
        full_output={"output": "zero"},
    )

    started = service.start_step(plan, 1)

    assert started.step.source_attempt == STEP_0
    assert started.handoff is not None
    assert started.handoff.envelope.child_attempt == STEP_0
    assert started.handoff.envelope.artifact_refs == (
        completion.output_artifact.ref,
    )
    events = journal.capture_snapshot().events
    handoff_event = next(
        event
        for event in events
        if event.event_id == started.handoff.handoff_cursor.event_id
    )
    assert completion.output_artifact.ref in handoff_event.resource_refs
    assert started.started_cursor.store_seq > started.handoff.input_cursor.store_seq


def test_restart_returns_skip_for_a_completed_step(tmp_path):
    _store, _journal, _artifacts, sinks, service, plan = _bootstrap(tmp_path)
    service.start_step(plan, 0)
    _complete_runtime(sinks, STEP_0, output="zero")
    completion = service.complete_step(
        plan,
        0,
        full_output={"output": "zero"},
    )

    _store, _journal, _artifacts, _sinks, restarted = _open(tmp_path)
    replay = restarted.start_step(plan, 0)

    assert replay.disposition is GraphStepDisposition.SKIP_COMPLETED
    assert replay.completion == completion
    assert replay.started_cursor is None
    assert replay.handoff is None


def test_completed_output_reader_returns_only_the_canonical_envelope(tmp_path):
    _store, _journal, _artifacts, sinks, service, plan = _bootstrap(tmp_path)
    service.start_step(plan, 0)
    _complete_runtime(sinks, STEP_0, output="zero")
    service.complete_step(plan, 0, full_output={"output": "zero"})

    _store, _journal, _artifacts, _sinks, restarted = _open(tmp_path)

    assert restarted.read_completed_output(plan, 0) == {
        "schema": "unchain.graph_step_output.v1",
        "status": "completed",
        "output": "zero",
    }
    with pytest.raises(GraphCheckpointError, match="no completed output"):
        restarted.read_completed_output(plan, 1)


def test_restart_seals_canonical_terminal_left_before_graph_checkpoint(tmp_path):
    _store, journal, _artifacts, sinks, service, plan = _bootstrap(tmp_path)
    service.start_step(plan, 0)
    _complete_runtime(sinks, STEP_0, output="sealed-from-journal")
    assert "graph.step.completed" not in {
        event.event_type for event in journal.capture_snapshot().events
    }

    _store, reopened, _artifacts, _sinks, restarted = _open(tmp_path)
    recovery = restarted.recover(plan)

    assert len(recovery.completed_steps) == 1
    assert recovery.next_step_index == 1
    assert recovery.uncertain_step_index is None
    assert restarted._decode_artifact(
        recovery.completed_steps[0].output_artifact
    ) == {
        "schema": "unchain.graph_step_output.v1",
        "status": "completed",
        "output": "sealed-from-journal",
    }
    assert sum(
        event.event_type == "graph.step.completed"
        for event in reopened.capture_snapshot().events
    ) == 1


def test_nested_child_events_do_not_break_parent_completion_or_next_handoff(
    tmp_path,
):
    _store, journal, artifacts, sinks, service, plan = _bootstrap(tmp_path)
    service.start_step(plan, 0)
    _append(sinks[STEP_0], STEP_0, "run_started", 1, status="running")

    child_attempt = AttemptRef(GENERATION, "graph-step-0-child")
    child_projector = _projector(child_attempt, artifacts)
    child_sink = DurableEventSink(journal, child_attempt, child_projector)
    _append(child_sink, child_attempt, "run_started", 1, status="running")
    _append(
        child_sink,
        child_attempt,
        "final_message",
        2,
        content="nested child output",
    )
    _append(
        child_sink,
        child_attempt,
        "run_completed",
        3,
        status="completed",
    )

    _append(
        sinks[STEP_0],
        STEP_0,
        "final_message",
        2,
        content="zero",
    )
    _append(
        sinks[STEP_0],
        STEP_0,
        "run_completed",
        3,
        status="completed",
    )
    completion = service.complete_step(
        plan,
        0,
        full_output={"output": "zero"},
    )

    assert completion.source_event_range.start == completion.checkpoint_cursor
    assert completion.source_event_range.end == completion.checkpoint_cursor
    checkpoint_event = next(
        event
        for event in journal.capture_snapshot().events
        if event.event_id == completion.checkpoint_cursor.event_id
    )
    assert checkpoint_event.event_type == "graph.step.completed"
    assert checkpoint_event.attempt == STEP_0
    assert checkpoint_event.resource_refs == (completion.output_artifact.ref,)

    next_step = service.start_step(plan, 1)
    assert next_step.disposition is GraphStepDisposition.STARTED
    assert next_step.handoff is not None
    assert next_step.handoff.envelope.child_attempt == STEP_0
    assert next_step.handoff.envelope.source_event_range == (
        completion.source_event_range
    )


def test_started_without_canonical_terminal_fails_closed(tmp_path):
    _store, _journal, _artifacts, _sinks, service, plan = _bootstrap(tmp_path)
    service.start_step(plan, 0)

    recovery = service.recover(plan)

    assert recovery.uncertain_step_index == 0
    assert recovery.next_step_index is None
    with pytest.raises(GraphCheckpointError, match="replay is forbidden"):
        service.start_step(plan, 0)


def test_pending_interaction_is_reported_as_suspended_not_rerunnable(tmp_path):
    _store, _journal, _artifacts, sinks, service, plan = _bootstrap(tmp_path)
    service.start_step(plan, 0)
    _append(
        sinks[STEP_0],
        STEP_0,
        "interaction_requested",
        10,
        interaction_id="interaction-1",
        status="pending",
    )

    recovery = service.recover(plan)

    assert recovery.suspended_step_index == 0
    assert recovery.uncertain_step_index is None
    assert recovery.next_step_index is None
    with pytest.raises(GraphCheckpointError, match="interaction resume"):
        service.start_step(plan, 0)


def test_same_orchestration_attempt_cannot_change_topology_after_admission(tmp_path):
    _store, _journal, _artifacts, _sinks, service, plan = _bootstrap(tmp_path)
    changed = _plan(plan.initial_input_cursor, topology="topology-v2")

    with pytest.raises(GraphCheckpointConflict):
        service.admit(changed)


def test_finalize_is_idempotent_and_preserves_the_last_output_ref(tmp_path):
    _store, journal, _artifacts, sinks, service, plan = _bootstrap(tmp_path)
    service.start_step(plan, 0)
    _complete_runtime(sinks, STEP_0, output="zero")
    service.complete_step(plan, 0, full_output={"output": "zero"})
    service.start_step(plan, 1)
    _complete_runtime(sinks, STEP_1, output="one", base=20)
    final_completion = service.complete_step(
        plan,
        1,
        full_output="one",
    )

    first = service.finalize(plan)
    replay = service.finalize(plan)

    assert first.is_complete is True
    assert replay == first
    graph_terminals = [
        event
        for event in journal.capture_snapshot().events
        if event.event_type == "graph.execution.completed"
    ]
    assert len(graph_terminals) == 1
    assert graph_terminals[0].resource_refs == (
        final_completion.output_artifact.ref,
    )


def test_completed_step_replay_rejects_changed_full_output(tmp_path):
    _store, _journal, _artifacts, sinks, service, plan = _bootstrap(tmp_path)
    service.start_step(plan, 0)
    _complete_runtime(sinks, STEP_0, output="zero")
    service.complete_step(
        plan,
        0,
        full_output={"output": "zero"},
    )

    with pytest.raises(GraphCheckpointConflict, match="output"):
        service.complete_step(
            plan,
            0,
            full_output={"output": "changed"},
        )
