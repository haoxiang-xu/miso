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
    JournalGraphCheckpointRepository,
)
from unchain.context.handoff import DurableHandoffRecorder, HandoffService
from unchain.context.ingress import (
    ContextInputIngress,
    HostResolvedCurrentInput,
    HostResolvedInteractionInput,
)
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.journal import (
    AttemptRef,
    DurableEventSink,
    GenerationRef,
    SemanticEventDraft,
)
from unchain.persistence import SQLiteContextV2Store


GENERATION = GenerationRef("graph-resume-execution", "graph-resume-generation")
ORCHESTRATION = AttemptRef(GENERATION, "graph-resume-orchestration")
STEP = AttemptRef(GENERATION, "graph-resume-step")
INTERACTION_ID = "graph-resume-interaction"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        return DerivedHandoffInputIngress(
            consumer_attempt=consumer_attempt,
            source_attempt=source_attempt,
            handoff_recorder=DurableHandoffRecorder(
                attempt=consumer_attempt,
                handoffs=HandoffService(artifacts),
                projector=projector,
                sink=sink,
            ),
            input_ingress=ContextInputIngress(
                attempt=consumer_attempt,
                projector=projector,
                sink=sink,
            ),
        )

    service = GraphCheckpointService(
        repository=JournalGraphCheckpointRepository(journal),
        artifacts=artifacts,
        derived_ingress_resolver=resolve_ingress,
    )
    return store, journal, projectors, sinks, service


def _bootstrap(tmp_path):
    store, journal, projectors, sinks, service = _open(tmp_path)
    seed = ContextInputIngress(
        attempt=ORCHESTRATION,
        projector=projectors[ORCHESTRATION],
        sink=sinks[ORCHESTRATION],
    ).persist(
        HostResolvedCurrentInput(
            attempt=ORCHESTRATION,
            content="run a resumable graph",
        )
    )
    plan = GraphExecutionPlan(
        orchestration_attempt=ORCHESTRATION,
        topology_sha256=_digest("graph-resume-topology"),
        initial_input_cursor=seed.cursor,
        steps=(
            GraphStepBinding(
                index=0,
                node_id="graph-resume-node",
                attempt=STEP,
                source_attempt=ORCHESTRATION,
                provider="openai",
                model="gpt-resume-test",
                configuration_sha256=_digest("graph-resume-config"),
            ),
        ),
    )
    service.admit(plan)
    service.start_step(plan, 0)
    return store, journal, projectors, sinks, service, plan


def _request(sink, interaction_id: str, iteration: int):
    receipt = sink(
        {
            "type": "interaction_requested",
            "run_id": STEP.attempt_id,
            "iteration": iteration,
            "interaction_id": interaction_id,
            "interaction_request": {
                "interaction_id": interaction_id,
                "kind": "human_input",
                "question": "Continue?",
            },
        }
    )
    assert receipt is not None
    return receipt


def _resolve(projector, sink, interaction_id: str, response="yes"):
    return ContextInputIngress(
        attempt=STEP,
        projector=projector,
        sink=sink,
    ).persist(
        HostResolvedInteractionInput(
            attempt=STEP,
            interaction_id=interaction_id,
            response={"answer": response},
            submitted_by="ui:test",
        )
    )


def _append_lifecycle(sink, event_type: str, sequence: int, **payload):
    return sink.append_projected(
        SemanticEventDraft(
            event_id=f"event-resume-{sequence}-{event_type}",
            event_type=event_type,
            attempt=STEP,
            operation_id=f"operation-resume-{sequence}-{event_type}",
            payload={"run_id": STEP.attempt_id, **payload},
        )
    )


def test_resolution_becomes_resume_ready_and_admission_becomes_resuming(tmp_path):
    _store, journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    request = _request(sinks[STEP], INTERACTION_ID, 1)

    suspended = service.recover(plan)
    assert suspended.suspended_step_index == 0
    assert suspended.resume_ready_step_index is None
    assert suspended.resuming_step_index is None
    with pytest.raises(GraphCheckpointError, match="no resolved resumable"):
        service.resolved_interaction_for_step(
            plan,
            0,
            interaction_id=INTERACTION_ID,
        )

    resolution = _resolve(projectors[STEP], sinks[STEP], INTERACTION_ID)
    ready = service.recover(plan)
    assert ready.suspended_step_index is None
    assert ready.resume_ready_step_index == 0
    assert ready.resuming_step_index is None
    evidence = service.resolved_interaction_for_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
    )
    assert evidence.graph_plan_id == plan.plan_id
    assert evidence.graph_scope_id == plan.scope_id
    assert evidence.step == plan.steps[0]
    assert evidence.request_cursor == request.cursor
    assert evidence.resolution_cursor == resolution.cursor
    with pytest.raises(GraphCheckpointConflict, match="identity changed"):
        service.resolved_interaction_for_step(
            plan,
            0,
            interaction_id="different-interaction",
        )

    receipt = service.resume_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    )
    replay = service.resume_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    )

    assert replay == receipt
    assert receipt.graph_plan_id == plan.plan_id
    assert receipt.graph_scope_id == plan.scope_id
    assert receipt.step == plan.steps[0]
    assert receipt.request_cursor == request.cursor
    assert receipt.resolution_cursor == resolution.cursor
    assert service.recover(plan).resuming_step_index == 0
    event_types = [event.event_type for event in journal.capture_snapshot().events]
    assert event_types.count("handoff.recorded") == 1
    assert event_types.count("graph.step.started") == 1
    assert event_types.count("graph.step.resume.admitted") == 1
    with pytest.raises(GraphCheckpointError, match="already resuming"):
        service.start_step(plan, 0)


def test_cold_restart_replays_resume_receipt_without_rewriting_start(tmp_path):
    _store, journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    request = _request(sinks[STEP], INTERACTION_ID, 1)
    resolution = _resolve(projectors[STEP], sinks[STEP], INTERACTION_ID)
    receipt = service.resume_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    )

    _store, reopened, _projectors, reopened_sinks, restarted = _open(tmp_path)
    assert restarted.recover(plan).resuming_step_index == 0
    assert restarted.resolved_interaction_for_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
    ).resolution_cursor == resolution.cursor
    assert restarted.resume_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    ) == receipt
    assert sum(
        event.event_type == "graph.step.resume.admitted"
        for event in reopened.capture_snapshot().events
    ) == 1
    assert sum(
        event.event_type == "graph.step.started"
        for event in reopened.capture_snapshot().events
    ) == 1

    _append_lifecycle(reopened_sinks[STEP], "run_started", 20, status="running")
    _append_lifecycle(
        reopened_sinks[STEP],
        "final_message",
        21,
        content="resumed output",
    )
    _append_lifecycle(
        reopened_sinks[STEP],
        "run_completed",
        22,
        status="completed",
    )
    completed = restarted.recover(plan)
    assert len(completed.completed_steps) == 1
    assert completed.resuming_step_index is None


def test_resume_rejects_interaction_or_cursor_drift(tmp_path):
    _store, _journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    request = _request(sinks[STEP], INTERACTION_ID, 1)
    resolution = _resolve(projectors[STEP], sinks[STEP], INTERACTION_ID)

    with pytest.raises(GraphCheckpointConflict, match="interaction evidence"):
        service.resume_step(
            plan,
            0,
            interaction_id="different-interaction",
            request_cursor=request.cursor,
            resolution_cursor=resolution.cursor,
        )
    with pytest.raises(GraphCheckpointConflict, match="interaction evidence"):
        service.resume_step(
            plan,
            0,
            interaction_id=INTERACTION_ID,
            request_cursor=plan.initial_input_cursor,
            resolution_cursor=resolution.cursor,
        )

    service.resume_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    )
    with pytest.raises(GraphCheckpointConflict, match="durable evidence"):
        service.resume_step(
            plan,
            0,
            interaction_id=INTERACTION_ID,
            request_cursor=plan.initial_input_cursor,
            resolution_cursor=resolution.cursor,
        )


def test_multiple_interactions_require_one_admission_per_exact_cycle(tmp_path):
    _store, journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    first_request = _request(sinks[STEP], INTERACTION_ID, 1)
    first_resolution = _resolve(projectors[STEP], sinks[STEP], INTERACTION_ID)
    service.resume_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
        request_cursor=first_request.cursor,
        resolution_cursor=first_resolution.cursor,
    )

    second_id = "graph-resume-interaction-2"
    second_request = _request(sinks[STEP], second_id, 2)
    assert service.recover(plan).suspended_step_index == 0
    second_resolution = _resolve(projectors[STEP], sinks[STEP], second_id, "again")
    assert service.recover(plan).resume_ready_step_index == 0
    service.resume_step(
        plan,
        0,
        interaction_id=second_id,
        request_cursor=second_request.cursor,
        resolution_cursor=second_resolution.cursor,
    )

    assert service.recover(plan).resuming_step_index == 0
    assert sum(
        event.event_type == "graph.step.resume.admitted"
        for event in journal.capture_snapshot().events
    ) == 2


def test_resolution_without_exact_step_request_fails_closed(tmp_path):
    _store, _journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    _resolve(projectors[STEP], sinks[STEP], INTERACTION_ID)

    with pytest.raises(GraphCheckpointError, match="no exact request"):
        service.recover(plan)


@pytest.mark.parametrize(
    ("request_type", "identity_field"),
    [
        ("tool_confirmation_requested", "call_id"),
        ("continuation_request", "confirmation_id"),
    ],
)
def test_compatibility_interactions_keep_exact_durable_identity(
    tmp_path,
    request_type,
    identity_field,
):
    _store, _journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    interaction_id = f"compatibility-{request_type}"
    request = _append_lifecycle(
        sinks[STEP],
        request_type,
        30,
        **{identity_field: interaction_id},
    )
    assert service.recover(plan).suspended_step_index == 0
    resolution = _resolve(
        projectors[STEP],
        sinks[STEP],
        interaction_id,
    )
    evidence = service.resolved_interaction_for_step(
        plan,
        0,
        interaction_id=interaction_id,
    )

    assert evidence.request_cursor == request.cursor
    assert evidence.resolution_cursor == resolution.cursor
    service.resume_step(
        plan,
        0,
        interaction_id=interaction_id,
        request_cursor=evidence.request_cursor,
        resolution_cursor=evidence.resolution_cursor,
    )
    assert service.recover(plan).resuming_step_index == 0


def test_tool_outcome_echo_does_not_replace_authoritative_resolution(tmp_path):
    _store, _journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    call_id = "call-resumed-tool"
    request = sinks[STEP](
        {
            "type": "interaction_requested",
            "run_id": STEP.attempt_id,
            "iteration": 1,
            "interaction_id": INTERACTION_ID,
            "interaction_request": {
                "interaction_id": INTERACTION_ID,
                "kind": "tool_approval",
                "payload": {"call_id": call_id},
            },
        }
    )
    resolution = _resolve(
        projectors[STEP],
        sinks[STEP],
        INTERACTION_ID,
        response="deny",
    )
    service.resume_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    )

    _append_lifecycle(
        sinks[STEP],
        "tool_denied",
        40,
        call_id=call_id,
        reason="user denied",
    )
    _append_lifecycle(
        sinks[STEP],
        "tool_confirmed",
        41,
        call_id="unrelated-tool-call",
    )
    evidence = service.resolved_interaction_for_step(
        plan,
        0,
        interaction_id=INTERACTION_ID,
    )
    assert evidence.resolution_cursor == resolution.cursor
    assert service.recover(plan).resuming_step_index == 0

    _append_lifecycle(sinks[STEP], "run_started", 42, status="running")
    _append_lifecycle(
        sinks[STEP],
        "final_message",
        43,
        content="denied safely",
    )
    _append_lifecycle(sinks[STEP], "run_completed", 44, status="completed")
    assert len(service.recover(plan).completed_steps) == 1
