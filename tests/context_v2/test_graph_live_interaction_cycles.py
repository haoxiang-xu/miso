"""Live interaction cycles inside one graph step attempt.

A live prompt (tool confirmation, max-iterations continuation, live human
input) is answered while the attempt keeps running: the kernel never exits, so
no ``graph.step.resume.admitted`` receipt can exist for it.  The scan must not
demand one — only durable pauses take admissions.  These tests drive the real
producer path (projector + durable sink) into the real consumer (the scan).
"""

from __future__ import annotations

import hashlib

import pytest

from unchain.context.artifacts import ArtifactService
from unchain.context.derived_handoff import DerivedHandoffInputIngress
from unchain.context.graph_checkpoint import (
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
from unchain.journal.interaction_cycles import (
    DURABLE_INTERACTION_REQUESTS,
    DURABLE_INTERACTION_RESOLUTIONS,
    INTERACTION_REQUESTS,
    INTERACTION_RESOLUTIONS,
    InteractionRequestFamily,
    interaction_request_family,
)
from unchain.persistence import SQLiteContextV2Store


GENERATION = GenerationRef("graph-live-execution", "graph-live-generation")
ORCHESTRATION = AttemptRef(GENERATION, "graph-live-orchestration")
STEP = AttemptRef(GENERATION, "graph-live-step")


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
            content="run a graph with live prompts",
        )
    )
    plan = GraphExecutionPlan(
        orchestration_attempt=ORCHESTRATION,
        topology_sha256=_digest("graph-live-topology"),
        initial_input_cursor=seed.cursor,
        steps=(
            GraphStepBinding(
                index=0,
                node_id="graph-live-node",
                attempt=STEP,
                source_attempt=ORCHESTRATION,
                provider="openai",
                model="gpt-live-test",
                configuration_sha256=_digest("graph-live-config"),
            ),
        ),
    )
    service.admit(plan)
    service.start_step(plan, 0)
    return store, journal, projectors, sinks, service, plan


def _live_request(sink, confirmation_id: str, iteration: int):
    receipt = sink(
        {
            "type": "tool_confirmation_requested",
            "run_id": STEP.attempt_id,
            "iteration": iteration,
            "confirmation_id": confirmation_id,
            "tool_name": "write_file",
        }
    )
    assert receipt is not None
    return receipt


def _live_outcome(sink, confirmation_id: str, iteration: int, approved=True):
    receipt = sink(
        {
            "type": "tool_confirmed" if approved else "tool_denied",
            "run_id": STEP.attempt_id,
            "iteration": iteration,
            "confirmation_id": confirmation_id,
            "tool_name": "write_file",
        }
    )
    assert receipt is not None
    return receipt


def _durable_request(sink, interaction_id: str, iteration: int):
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


def _durable_resolve(projector, sink, interaction_id: str, response="yes"):
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
            event_id=f"event-live-{sequence}-{event_type}",
            event_type=event_type,
            attempt=STEP,
            operation_id=f"operation-live-{sequence}-{event_type}",
            payload={"run_id": STEP.attempt_id, **payload},
        )
    )


def _finish_run(sink, *, output: str, base: int):
    _append_lifecycle(sink, "final_message", base, content=output)
    _append_lifecycle(sink, "run_completed", base + 1, status="completed")


def test_live_answered_durable_human_input_continuation_completes(tmp_path):
    """The production shape: kernel emits a durable-shaped request, the
    in-run callback answers it, the receipt lands only in session state, and
    the attempt keeps running with no resolution event and no admission."""

    _store, _journal, _projectors, sinks, service, plan = _bootstrap(tmp_path)
    _durable_request(sinks[STEP], "ask-user-1", 1)
    _append_lifecycle(sinks[STEP], "iteration_started", 5, iteration=2)
    _durable_request(sinks[STEP], "ask-user-2", 2)
    _append_lifecycle(sinks[STEP], "iteration_started", 6, iteration=3)
    _finish_run(sinks[STEP], output="asked twice live", base=10)

    completion = service.complete_step(
        plan,
        0,
        full_output={"output": "asked twice live"},
    )
    assert completion.step.index == 0


def test_live_answered_durable_tool_approval_with_outcome_completes(tmp_path):
    """durable_wait tool approvals emit a canonical request, answer through
    the in-run callback, and the resumed dispatch echoes a live outcome."""

    _store, _journal, _projectors, sinks, service, plan = _bootstrap(tmp_path)
    receipt = sinks[STEP](
        {
            "type": "interaction_requested",
            "run_id": STEP.attempt_id,
            "iteration": 1,
            "interaction_id": "approve-tool-1",
            "interaction_request": {
                "interaction_id": "approve-tool-1",
                "kind": "tool_approval",
                "payload": {"call_id": "call-live-1"},
            },
        }
    )
    assert receipt is not None
    _live_outcome(sinks[STEP], "call-live-1", 1)
    _durable_request(sinks[STEP], "ask-user-after", 2)
    _append_lifecycle(sinks[STEP], "iteration_started", 6, iteration=3)
    _finish_run(sinks[STEP], output="approved then asked", base=10)

    completion = service.complete_step(
        plan,
        0,
        full_output={"output": "approved then asked"},
    )
    assert completion.step.index == 0


def test_two_live_confirmations_in_one_step_attempt_complete(tmp_path):
    _store, _journal, _projectors, sinks, service, plan = _bootstrap(tmp_path)
    _live_request(sinks[STEP], "confirm-1", 1)
    _live_outcome(sinks[STEP], "confirm-1", 1)
    _live_request(sinks[STEP], "confirm-2", 2)
    _live_outcome(sinks[STEP], "confirm-2", 2)
    _finish_run(sinks[STEP], output="both tools ran", base=10)

    completion = service.complete_step(
        plan,
        0,
        full_output={"output": "both tools ran"},
    )
    assert completion.step.index == 0


def test_resolved_live_cycle_is_not_resume_ready(tmp_path):
    _store, _journal, _projectors, sinks, service, plan = _bootstrap(tmp_path)
    _live_request(sinks[STEP], "confirm-live", 1)
    _live_outcome(sinks[STEP], "confirm-live", 1)

    recovery = service.recover(plan)
    assert recovery.resume_ready_step_index is None
    assert recovery.suspended_step_index is None
    assert recovery.resuming_step_index is None
    assert recovery.uncertain_step_index == 0


def test_live_cycle_then_durable_pause_then_live_again_completes(tmp_path):
    _store, _journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    _live_request(sinks[STEP], "confirm-before", 1)
    _live_outcome(sinks[STEP], "confirm-before", 1)

    request = _durable_request(sinks[STEP], "pause-1", 2)
    assert service.recover(plan).suspended_step_index == 0
    resolution = _durable_resolve(projectors[STEP], sinks[STEP], "pause-1")
    assert service.recover(plan).resume_ready_step_index == 0
    service.resume_step(
        plan,
        0,
        interaction_id="pause-1",
        request_cursor=request.cursor,
        resolution_cursor=resolution.cursor,
    )
    assert service.recover(plan).resuming_step_index == 0

    _live_request(sinks[STEP], "confirm-after", 3)
    _live_outcome(sinks[STEP], "confirm-after", 3)
    _finish_run(sinks[STEP], output="mixed prompts ran", base=20)

    completion = service.complete_step(
        plan,
        0,
        full_output={"output": "mixed prompts ran"},
    )
    assert completion.step.index == 0


def test_boundary_resolved_live_interactions_complete(tmp_path):
    """The exact shape the active host boundary journals for live-answered
    interactions: canonical request + canonical resolution back to back, the
    run keeps executing, and no admission ever exists."""

    _store, _journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    _durable_request(sinks[STEP], "live-answered-1", 1)
    _durable_resolve(projectors[STEP], sinks[STEP], "live-answered-1")
    _append_lifecycle(sinks[STEP], "iteration_started", 5, iteration=2)
    _durable_request(sinks[STEP], "live-answered-2", 2)
    _durable_resolve(projectors[STEP], sinks[STEP], "live-answered-2")
    _append_lifecycle(sinks[STEP], "tool_result", 6, call_id="call-after")
    _durable_request(sinks[STEP], "live-answered-3", 3)
    _durable_resolve(projectors[STEP], sinks[STEP], "live-answered-3")
    _finish_run(sinks[STEP], output="boundary answered three", base=10)

    completion = service.complete_step(
        plan,
        0,
        full_output={"output": "boundary answered three"},
    )
    assert completion.step.index == 0


def test_request_before_a_pending_admission_still_fails(tmp_path):
    """An admission recorded later in the journal marks its cycle as a real
    durable resume; a request slipping in before that admission is the
    ordering violation the guard keeps rejecting."""

    _store, _journal, projectors, sinks, service, plan = _bootstrap(tmp_path)
    request = _durable_request(sinks[STEP], "pause-admitted-late", 1)
    resolution = _durable_resolve(
        projectors[STEP],
        sinks[STEP],
        "pause-admitted-late",
    )
    _live_request(sinks[STEP], "confirm-overrun", 2)
    sinks[STEP].append_projected(
        SemanticEventDraft(
            event_id="event-live-late-admission",
            event_type="graph.step.resume.admitted",
            attempt=STEP,
            operation_id="operation-live-late-admission",
            payload={
                "run_id": STEP.attempt_id,
                "graph_plan_id": plan.plan_id,
                "graph_scope_id": plan.scope_id,
                "step": plan.steps[0].to_dict(),
                "interaction_id": "pause-admitted-late",
                "request_cursor": request.cursor.to_dict(),
                "resolution_cursor": resolution.cursor.to_dict(),
            },
        )
    )

    with pytest.raises(
        GraphCheckpointError,
        match="before the previous interaction resumed",
    ):
        service.recover(plan)


def test_interaction_request_family_classification():
    assert (
        interaction_request_family("interaction.requested", {})
        is InteractionRequestFamily.DURABLE
    )
    assert (
        interaction_request_family("interaction_requested", None)
        is InteractionRequestFamily.DURABLE
    )
    for live_type in (
        "tool_confirmation_requested",
        "continuation_request",
        "input_requested",
        "human_input_requested",
    ):
        assert (
            interaction_request_family(live_type, {"confirmation_id": "x"})
            is InteractionRequestFamily.LIVE
        )
    # A durable-shaped payload always classifies as the stricter family.
    assert (
        interaction_request_family(
            "human_input_requested",
            {"interaction_request": {"interaction_id": "x"}},
        )
        is InteractionRequestFamily.DURABLE
    )
    assert interaction_request_family("tool_call", {}) is None


def test_rebase_and_scan_share_one_interaction_vocabulary():
    from unchain.persistence import sqlite_generation_rebase_v2 as rebase
    from unchain.context import graph_checkpoint as checkpoint

    assert rebase._INTERACTION_REQUEST_EVENT_TYPES == (
        DURABLE_INTERACTION_REQUESTS
    )
    assert rebase._INTERACTION_RESOLUTION_EVENT_TYPES == (
        DURABLE_INTERACTION_RESOLUTIONS
    )
    assert rebase._GRAPH_INTERACTION_REQUEST_EVENT_TYPES == INTERACTION_REQUESTS
    assert rebase._GRAPH_INTERACTION_RESOLUTION_EVENT_TYPES == (
        INTERACTION_RESOLUTIONS
    )
    assert checkpoint._INTERACTION_REQUESTS == INTERACTION_REQUESTS
    assert checkpoint._INTERACTION_RESOLUTIONS == INTERACTION_RESOLUTIONS
