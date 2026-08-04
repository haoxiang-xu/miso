from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from unchain.context import (
    ContextCompileCoordinator,
    ContextCompileCoordinatorError,
    ContextCompileRequest,
    CheckpointWriteStatus,
    ContextBuildReceipt,
    PreparedCheckpoint,
    SourceMessageCursor,
    resolve_context_budget,
)
from unchain.journal import (
    AttemptRef,
    BoundExecutionJournal,
    EventCursor,
    GenerationRef,
    JournalEvent,
    JournalSnapshotError,
    OperationRef,
    ResourceRef,
    SemanticEventDraft,
    capture_journal_snapshot,
)


def _attempt(attempt_id: str) -> AttemptRef:
    return AttemptRef(
        GenerationRef("execution-1", "generation-1"),
        attempt_id,
    )


def _event(
    *,
    event_id: str,
    event_type: str,
    store_seq: int,
    attempt_id: str,
    payload: dict,
    refs: tuple[ResourceRef, ...] = (),
) -> JournalEvent:
    attempt = _attempt(attempt_id)
    operation = SemanticEventDraft(
        event_id=event_id,
        event_type=event_type,
        attempt=attempt,
        operation_id=f"operation-{event_id}",
        payload=payload,
        resource_refs=refs,
    ).operation
    return JournalEvent(
        event_id=event_id,
        event_type=event_type,
        attempt=attempt,
        operation=operation,
        store_seq=store_seq,
        payload=payload,
        resource_refs=refs,
    )


SOURCE_REF = ResourceRef("artifact", "source-artifact", 1)
DEPENDENCY_REF = ResourceRef("artifact", "dependency-artifact", 1)
UNRELATED_REF = ResourceRef("artifact", "unrelated-artifact", 1)
CURRENT_REF = ResourceRef("artifact", "current-artifact", 1)


def _events() -> tuple[JournalEvent, ...]:
    return (
        _event(
            event_id="event-1",
            event_type="message.user",
            store_seq=1,
            attempt_id="attempt-history",
            payload={
                "run_id": "attempt-history",
                "message": {
                    "role": "user",
                    "content": "old " + ("x" * 30_000),
                },
            },
            refs=(SOURCE_REF,),
        ),
        _event(
            event_id="event-2",
            event_type="final_message",
            store_seq=2,
            attempt_id="attempt-history",
            payload={
                "run_id": "attempt-history",
                "content": "old answer",
                "workflow_node_id": "final-node",
                "workflow_step_index": 1,
                "workflow_step_count": 2,
                "iteration": 0,
            },
        ),
        _event(
            event_id="event-3",
            event_type="artifact.recorded",
            store_seq=3,
            attempt_id="attempt-history",
            payload={
                "run_id": "attempt-history",
                "artifact_ref": UNRELATED_REF.to_dict(),
            },
            refs=(UNRELATED_REF,),
        ),
        _event(
            event_id="event-4",
            event_type="run_completed",
            store_seq=4,
            attempt_id="attempt-history",
            payload={
                "run_id": "attempt-history",
                "status": "completed",
                "workflow_node_id": "final-node",
                "workflow_step_index": 1,
                "workflow_step_count": 2,
                "iteration": 0,
                "receipt_note": "authoritative",
            },
            refs=(DEPENDENCY_REF,),
        ),
        _event(
            event_id="event-5",
            event_type="message.user",
            store_seq=5,
            attempt_id="attempt-current",
            payload={
                "run_id": "attempt-current",
                "message": {"role": "user", "content": "current"},
            },
            refs=(CURRENT_REF,),
        ),
    )


class SnapshotJournal(BoundExecutionJournal):
    def __init__(self, events=None):
        super().__init__("execution-1")
        self.events = list(events or _events())
        self.capture_calls = 0

    def append(self, *, request):
        raise AssertionError(f"unexpected append: {request}")

    def read(self, *, after=None, limit=100):
        raise AssertionError(f"live pagination is forbidden: {after}, {limit}")

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        del max_events, max_bytes
        self.capture_calls += 1
        return capture_journal_snapshot(
            execution_id=self.execution_id,
            events=tuple(self.events),
        )


class PreparedCheckpointRepository:
    execution_id = "execution-1"

    def __init__(self, *, on_prepare=None):
        self.on_prepare = on_prepare
        self.prepare_calls = []
        self.commit_calls = []
        self.receipts = {}

    def prepare(self, **kwargs):
        self.prepare_calls.append(kwargs)
        if self.on_prepare is not None:
            self.on_prepare()
        receipt = PreparedCheckpoint(
            preparation_id="preparation-1",
            checkpoint_ref=ResourceRef("checkpoint", "checkpoint-1", 1),
            operation=kwargs["operation"],
        )
        self.receipts[receipt.operation.operation_id] = receipt
        return receipt

    def commit(self, *, prepared):
        self.commit_calls.append(prepared)
        receipt = replace(
            prepared,
            status=CheckpointWriteStatus.COMMITTED,
        )
        self.receipts[receipt.operation.operation_id] = receipt
        return receipt

    def get_by_operation(self, *, operation):
        receipt = self.receipts.get(operation.operation_id)
        if receipt is not None and receipt.operation != operation:
            raise RuntimeError("checkpoint operation conflict")
        return receipt


class BuildRepository:
    execution_id = "execution-1"

    def __init__(self):
        self.calls = []
        self.receipts = {}
        self.triggers = {}

    def record(self, **kwargs):
        self.calls.append(kwargs)
        receipt = ContextBuildReceipt(
            envelope=kwargs["envelope"],
            operation=kwargs["operation"],
            trigger_cursor=kwargs["trigger_cursor"],
        )
        prior_operation = self.receipts.get(receipt.operation.operation_id)
        if (
            prior_operation is not None
            and prior_operation.operation != receipt.operation
        ):
            raise RuntimeError("build operation conflict")
        previous = self.triggers.get(receipt.trigger_cursor)
        if (
            previous is not None
            and previous.envelope.build_id != receipt.envelope.build_id
        ):
            raise RuntimeError("input receipt already claimed")
        self.receipts[receipt.operation.operation_id] = receipt
        self.triggers[receipt.trigger_cursor] = receipt
        return receipt

    def get_by_operation(self, *, operation):
        receipt = self.receipts.get(operation.operation_id)
        if receipt is not None and receipt.operation != operation:
            raise RuntimeError("build operation conflict")
        return receipt

    def get_by_trigger(self, *, trigger_cursor):
        return self.triggers.get(trigger_cursor)


def _request() -> ContextCompileRequest:
    return ContextCompileRequest(
        case="checkpoint-coordinator-adversarial",
        source_messages=({"role": "user", "content": "current"},),
        current_generation="generation-1",
        source_message_cursors=(SourceMessageCursor(0, "event-5", 5),),
        budget=resolve_context_budget(context_window_tokens=8_192),
        provider="openai",
        model="synthetic",
        build_id="build-1",
        execution_id="execution-1",
        generation_id="generation-1",
        attempt_id="attempt-current",
    )


def _coordinator(*, journal, checkpoints=None, builds=None, partials=None):
    partials = partials if partials is not None else []
    return ContextCompileCoordinator(
        journal=journal,
        checkpoint_repository=checkpoints or PreparedCheckpointRepository(),
        build_repository=builds or BuildRepository(),
        partial_attempt_sink=lambda request, error: partials.append((request, error)),
    )


def test_two_pass_compile_uses_one_snapshot_and_exact_sparse_resource_refs() -> None:
    journal = SnapshotJournal()
    journal_events_before_prepare = len(journal.events)
    checkpoints = PreparedCheckpointRepository(
        on_prepare=lambda: journal.events.append(
            replace(journal.events[-1], event_id="event-6", store_seq=6)
        )
    )
    builds = BuildRepository()

    result = _coordinator(
        journal=journal,
        checkpoints=checkpoints,
        builds=builds,
    ).compile(_request())

    assert journal_events_before_prepare == 5
    assert journal.capture_calls == 1
    assert len(checkpoints.prepare_calls) == 1
    assert len(checkpoints.commit_calls) == 1
    assert len(builds.calls) == 1
    assert checkpoints.prepare_calls[0]["refs"] == (
        SOURCE_REF,
        DEPENDENCY_REF,
    )
    assert UNRELATED_REF not in checkpoints.prepare_calls[0]["refs"]
    assert CURRENT_REF not in checkpoints.prepare_calls[0]["refs"]
    payload = json.loads(checkpoints.prepare_calls[0]["summary"])
    assert payload["schema"] == "unchain.context_checkpoint_payload.v2"
    assert "snapshot_high_water" not in payload
    assert "snapshot_sha256" not in payload
    assert payload["checkpoint_request"]["dependency_count"] == 1
    assert result.checkpoint_requests == ()


def test_arbitrary_compiler_injection_is_not_a_supported_coordinator_surface() -> None:
    journal = SnapshotJournal()
    checkpoints = PreparedCheckpointRepository()
    builds = BuildRepository()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        ContextCompileCoordinator(
            journal=journal,
            checkpoint_repository=checkpoints,
            build_repository=builds,
            partial_attempt_sink=lambda request, error: None,
            compiler=object(),
        )

    assert journal.capture_calls == 0
    assert checkpoints.prepare_calls == []
    assert checkpoints.commit_calls == []
    assert builds.calls == []


def test_external_prebound_checkpoint_fails_before_snapshot_or_any_write() -> None:
    journal = SnapshotJournal()
    checkpoints = PreparedCheckpointRepository()
    builds = BuildRepository()

    with pytest.raises(ContextCompileCoordinatorError, match="pre-bound"):
        _coordinator(
            journal=journal,
            checkpoints=checkpoints,
            builds=builds,
        ).compile(
            replace(
                _request(),
                checkpoint_ref=ResourceRef("checkpoint", "external", 1),
                checkpoint_request_id="checkpoint-" + ("a" * 64),
            )
        )

    assert journal.capture_calls == 0
    assert checkpoints.prepare_calls == []
    assert checkpoints.commit_calls == []
    assert builds.calls == []


def test_snapshot_digest_tamper_fails_before_compiler_or_any_write() -> None:
    class TamperedSnapshotJournal(SnapshotJournal):
        def capture_snapshot(self, **kwargs):
            snapshot = super().capture_snapshot(**kwargs)
            object.__setattr__(snapshot, "snapshot_sha256", "f" * 64)
            return snapshot

    checkpoints = PreparedCheckpointRepository()
    builds = BuildRepository()

    with pytest.raises(JournalSnapshotError, match="digest"):
        _coordinator(
            journal=TamperedSnapshotJournal(),
            checkpoints=checkpoints,
            builds=builds,
        ).compile(_request())

    assert checkpoints.prepare_calls == []
    assert checkpoints.commit_calls == []
    assert builds.calls == []


def test_operation_receipt_tamper_fails_before_compiler_or_any_write() -> None:
    events = list(_events())
    events[3] = replace(
        events[3],
        operation=OperationRef("operation-event-4", "f" * 64),
    )
    checkpoints = PreparedCheckpointRepository()
    builds = BuildRepository()

    with pytest.raises(ContextCompileCoordinatorError, match="operation receipt"):
        _coordinator(
            journal=SnapshotJournal(events),
            checkpoints=checkpoints,
            builds=builds,
        ).compile(_request())

    assert checkpoints.prepare_calls == []
    assert checkpoints.commit_calls == []
    assert builds.calls == []


def test_unbound_non_system_source_message_is_never_compiled_from_request_body() -> (
    None
):
    run_started = _event(
        event_id="event-1",
        event_type="run_started",
        store_seq=1,
        attempt_id="attempt-current",
        payload={"run_id": "attempt-current", "status": "started"},
    )
    request = replace(
        _request(),
        source_messages=({"role": "user", "content": "unbound body"},),
        source_message_cursors=(),
    )
    builds = BuildRepository()

    with pytest.raises(ContextCompileCoordinatorError, match="unbound"):
        _coordinator(
            journal=SnapshotJournal((run_started,)),
            builds=builds,
        ).compile(request)

    assert builds.calls == []


def test_unrelated_snapshot_event_does_not_change_checkpoint_operation_identity() -> (
    None
):
    baseline_checkpoints = PreparedCheckpointRepository()
    extended_checkpoints = PreparedCheckpointRepository()
    _coordinator(
        journal=SnapshotJournal(),
        checkpoints=baseline_checkpoints,
    ).compile(_request())
    extended_events = (
        *_events(),
        _event(
            event_id="event-6",
            event_type="run_started",
            store_seq=6,
            attempt_id="attempt-current",
            payload={"run_id": "attempt-current", "status": "started"},
        ),
    )
    _coordinator(
        journal=SnapshotJournal(extended_events),
        checkpoints=extended_checkpoints,
    ).compile(_request())

    baseline = baseline_checkpoints.prepare_calls[0]
    extended = extended_checkpoints.prepare_calls[0]
    assert extended["summary"] == baseline["summary"]
    assert extended["operation"] == baseline["operation"]


def test_user_checkpoint_prefix_does_not_collide_with_internal_consumption() -> None:
    user_text = "[MEMORY_V2_CHECKPOINT] user-authored text"
    events = list(_events())
    events[-1] = _event(
        event_id="event-5",
        event_type="message.user",
        store_seq=5,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "message": {"role": "user", "content": user_text},
        },
        refs=(CURRENT_REF,),
    )
    checkpoints = PreparedCheckpointRepository()
    builds = BuildRepository()
    partials = []

    result = _coordinator(
        journal=SnapshotJournal(events),
        checkpoints=checkpoints,
        builds=builds,
        partials=partials,
    ).compile(
        replace(
            _request(),
            source_messages=({"role": "user", "content": user_text},),
        )
    )

    assert result.messages[-1]["content"] == user_text
    assert (
        sum(
            str(message.get("content") or "").startswith("[MEMORY_V2_CHECKPOINT]")
            for message in result.messages
        )
        == 2
    )
    assert len(checkpoints.prepare_calls) == 1
    assert len(checkpoints.commit_calls) == 1
    assert len(builds.calls) == 1
    assert partials == []


def test_request_attempt_must_have_current_durable_receipt_before_any_write() -> None:
    checkpoints = PreparedCheckpointRepository()
    builds = BuildRepository()
    partials = []

    with pytest.raises(ContextCompileCoordinatorError, match="attempt receipt"):
        _coordinator(
            journal=SnapshotJournal(),
            checkpoints=checkpoints,
            builds=builds,
            partials=partials,
        ).compile(
            replace(
                _request(),
                attempt_id="attempt-does-not-exist",
                build_id="build-fake-attempt",
            )
        )

    assert checkpoints.prepare_calls == []
    assert checkpoints.commit_calls == []
    assert builds.calls == []
    assert len(partials) == 1


def test_historical_attempt_cannot_claim_the_current_input_receipt() -> None:
    checkpoints = PreparedCheckpointRepository()
    builds = BuildRepository()
    partials = []

    with pytest.raises(
        ContextCompileCoordinatorError,
        match="terminal|attempt receipt",
    ):
        _coordinator(
            journal=SnapshotJournal(),
            checkpoints=checkpoints,
            builds=builds,
            partials=partials,
        ).compile(
            replace(
                _request(),
                attempt_id="attempt-history",
                build_id="build-history-attempt",
            )
        )

    assert checkpoints.prepare_calls == []
    assert checkpoints.commit_calls == []
    assert builds.calls == []
    assert len(partials) == 1


def test_orphan_interaction_receipt_cannot_authorize_a_model_build() -> None:
    interaction = _event(
        event_id="event-1",
        event_type="interaction.resolved",
        store_seq=1,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "interaction_id": "interaction-1",
            "outcome": "approved",
        },
    )
    request = replace(
        _request(),
        source_messages=(),
        source_message_cursors=(),
    )
    builds = BuildRepository()
    partials = []

    with pytest.raises(ContextCompileCoordinatorError, match="attempt receipt"):
        _coordinator(
            journal=SnapshotJournal((interaction,)),
            builds=builds,
            partials=partials,
        ).compile(request)

    assert builds.calls == []
    assert len(partials) == 1


def test_pending_task_input_must_resolve_to_the_stable_journal_snapshot() -> None:
    request = replace(
        _request(),
        pending_task_inputs=(
            {
                "event_id": "event-does-not-exist",
                "store_seq": 999,
                "type": "interaction_resolved",
                "preview": "FORGED",
                "content_ref": ResourceRef(
                    "artifact",
                    "foreign-artifact",
                    1,
                ).to_dict(),
                "content_bytes": 6,
                "content_sha256": "f" * 64,
            },
        ),
    )
    checkpoints = PreparedCheckpointRepository()
    builds = BuildRepository()
    partials = []

    with pytest.raises(ContextCompileCoordinatorError, match="pending task input"):
        _coordinator(
            journal=SnapshotJournal(),
            checkpoints=checkpoints,
            builds=builds,
            partials=partials,
        ).compile(request)

    assert checkpoints.prepare_calls == []
    assert checkpoints.commit_calls == []
    assert builds.calls == []
    assert len(partials) == 1


def test_resolved_pending_input_requires_a_matching_interaction_request() -> None:
    content = b"ORPHAN-APPROVAL"
    content_ref = ResourceRef("artifact", "interaction-response", 1)
    resolved = _event(
        event_id="event-1",
        event_type="interaction.resolved",
        store_seq=1,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "interaction_id": "interaction-1",
            "outcome": "approved",
            "preview": content.decode("utf-8"),
            "content_bytes": len(content),
            "content_sha256": hashlib.sha256(content).hexdigest(),
        },
        refs=(content_ref,),
    )
    request = replace(
        _request(),
        source_messages=({"role": "system", "content": "policy"},),
        source_message_cursors=(),
        pending_task_inputs=(
            {
                "event_id": "event-1",
                "store_seq": 1,
                "type": "interaction_resolved",
                "preview": content.decode("utf-8"),
                "content_ref": content_ref.to_dict(),
                "content_bytes": len(content),
                "content_sha256": hashlib.sha256(content).hexdigest(),
            },
        ),
    )
    builds = BuildRepository()
    partials = []

    with pytest.raises(ContextCompileCoordinatorError, match="interaction request"):
        _coordinator(
            journal=SnapshotJournal((resolved,)),
            builds=builds,
            partials=partials,
        ).compile(request)

    assert builds.calls == []
    assert len(partials) == 1


def test_duplicate_interaction_resolutions_cannot_overwrite_a_decision() -> None:
    content = b"APPROVE"
    content_ref = ResourceRef("artifact", "interaction-approve", 1)
    requested = _event(
        event_id="event-1",
        event_type="interaction.requested",
        store_seq=1,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "interaction_id": "interaction-1",
            "kind": "approval",
        },
    )
    rejected = _event(
        event_id="event-2",
        event_type="interaction.resolved",
        store_seq=2,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "interaction_id": "interaction-1",
            "outcome": "rejected",
        },
    )
    approved = _event(
        event_id="event-3",
        event_type="interaction.resolved",
        store_seq=3,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "interaction_id": "interaction-1",
            "outcome": "approved",
            "preview": content.decode("utf-8"),
            "content_bytes": len(content),
            "content_sha256": hashlib.sha256(content).hexdigest(),
        },
        refs=(content_ref,),
    )
    request = replace(
        _request(),
        source_messages=({"role": "system", "content": "policy"},),
        source_message_cursors=(),
        pending_task_inputs=(
            {
                "event_id": "event-3",
                "store_seq": 3,
                "type": "interaction_resolved",
                "preview": content.decode("utf-8"),
                "content_ref": content_ref.to_dict(),
                "content_bytes": len(content),
                "content_sha256": hashlib.sha256(content).hexdigest(),
            },
        ),
    )
    builds = BuildRepository()

    with pytest.raises(ContextCompileCoordinatorError, match="unique interaction"):
        _coordinator(
            journal=SnapshotJournal((requested, rejected, approved)),
            builds=builds,
        ).compile(request)

    assert builds.calls == []


def test_terminal_attempt_cannot_authorize_another_context_build() -> None:
    current_user = _event(
        event_id="event-1",
        event_type="message.user",
        store_seq=1,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "message": {"role": "user", "content": "current"},
        },
    )
    failed = _event(
        event_id="event-2",
        event_type="run_failed",
        store_seq=2,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "status": "failed",
            "error_code": "synthetic",
        },
    )
    request = replace(
        _request(),
        source_message_cursors=(SourceMessageCursor(0, "event-1", 1),),
    )
    builds = BuildRepository()
    partials = []

    with pytest.raises(ContextCompileCoordinatorError, match="terminal"):
        _coordinator(
            journal=SnapshotJournal((current_user, failed)),
            builds=builds,
            partials=partials,
        ).compile(request)

    assert builds.calls == []
    assert len(partials) == 1


def test_requested_and_resolved_receipts_are_a_model_visible_resume_input() -> None:
    content = b"approved"
    content_ref = ResourceRef("artifact", "interaction-response", 1)
    requested = _event(
        event_id="event-1",
        event_type="interaction.requested",
        store_seq=1,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "interaction_id": "interaction-1",
            "kind": "approval",
        },
    )
    resolved = _event(
        event_id="event-2",
        event_type="interaction.resolved",
        store_seq=2,
        attempt_id="attempt-current",
        payload={
            "run_id": "attempt-current",
            "interaction_id": "interaction-1",
            "outcome": "approved",
            "content_ref": content_ref.to_dict(),
            "preview": content.decode("utf-8"),
            "preview_truncated": False,
            "content_bytes": len(content),
            "content_sha256": hashlib.sha256(content).hexdigest(),
        },
        refs=(content_ref,),
    )
    request = replace(
        _request(),
        source_messages=({"role": "system", "content": "policy"},),
        source_message_cursors=(),
        pending_task_inputs=(
            {
                "event_id": "event-2",
                "store_seq": 2,
                "type": "interaction_resolved",
                "preview": content.decode("utf-8"),
                "content_ref": content_ref.to_dict(),
                "content_bytes": len(content),
                "content_sha256": hashlib.sha256(content).hexdigest(),
            },
        ),
    )
    builds = BuildRepository()
    partials = []

    result = _coordinator(
        journal=SnapshotJournal((requested, resolved)),
        builds=builds,
        partials=partials,
    ).compile(request)

    assert result.envelope is not None
    assert result.envelope.attempt_id == "attempt-current"
    assert any(
        "approved" in str(message.get("content") or "") for message in result.messages
    )
    assert len(builds.calls) == 1
    assert partials == []


def test_durable_tool_result_authorizes_a_new_resume_attempt() -> None:
    content = b"approved tool result"
    content_sha256 = hashlib.sha256(content).hexdigest()
    content_ref = ResourceRef("artifact", "tool-result", 1)
    tool_call = _event(
        event_id="event-1",
        event_type="tool_call",
        store_seq=1,
        attempt_id="attempt-original",
        payload={
            "run_id": "attempt-original",
            "call_id": "call-1",
            "tool_name": "lookup",
            "arguments": {"query": "durable"},
        },
    )
    requested = _event(
        event_id="event-2",
        event_type="interaction.requested",
        store_seq=2,
        attempt_id="attempt-original",
        payload={
            "run_id": "attempt-original",
            "interaction_id": "interaction-1",
            "call_id": "call-1",
            "kind": "approval",
        },
    )
    resolved = _event(
        event_id="event-3",
        event_type="interaction.resolved",
        store_seq=3,
        attempt_id="attempt-resume",
        payload={
            "run_id": "attempt-resume",
            "interaction_id": "interaction-1",
            "call_id": "call-1",
            "outcome": "approved",
        },
    )
    tool_result = _event(
        event_id="event-4",
        event_type="tool_result",
        store_seq=4,
        attempt_id="attempt-resume",
        payload={
            "run_id": "attempt-resume",
            "call_id": "call-1",
            "tool_name": "lookup",
            "result": {"preview": content.decode("utf-8")},
            "full_output_ref": content_ref.to_dict(),
            "result_bytes": len(content),
            "result_sha256": content_sha256,
            "preview": content.decode("utf-8"),
        },
        refs=(content_ref,),
    )
    request = replace(
        _request(),
        source_messages=({"role": "system", "content": "policy"},),
        source_message_cursors=(),
        attempt_id="attempt-resume",
        build_id="build-resume",
        pending_task_inputs=(
            {
                "event_id": "event-4",
                "store_seq": 4,
                "type": "tool_result",
                "preview": content.decode("utf-8"),
                "content_ref": content_ref.to_dict(),
                "content_bytes": len(content),
                "content_sha256": content_sha256,
            },
        ),
    )
    builds = BuildRepository()
    partials = []

    result = _coordinator(
        journal=SnapshotJournal((tool_call, requested, resolved, tool_result)),
        builds=builds,
        partials=partials,
    ).compile(request)

    assert result.envelope is not None
    assert result.envelope.attempt_id == "attempt-resume"
    assert any(
        "approved tool result" in str(message.get("content") or "")
        for message in result.messages
    )
    assert len(builds.calls) == 1
    assert partials == []


@pytest.mark.parametrize(
    "case",
    ("tool_name_mismatch", "result_before_call", "duplicate_result"),
)
def test_tool_result_input_requires_one_ordered_exact_tool_call_pair(case) -> None:
    content = b"result"
    content_sha256 = hashlib.sha256(content).hexdigest()
    content_ref = ResourceRef("artifact", "tool-result", 1)

    def result_event(*, event_id, store_seq, tool_name="lookup"):
        return _event(
            event_id=event_id,
            event_type="tool_result",
            store_seq=store_seq,
            attempt_id="attempt-resume",
            payload={
                "run_id": "attempt-resume",
                "call_id": "call-1",
                "tool_name": tool_name,
                "result": {"preview": content.decode("utf-8")},
                "full_output_ref": content_ref.to_dict(),
                "result_bytes": len(content),
                "result_sha256": content_sha256,
                "preview": content.decode("utf-8"),
            },
            refs=(content_ref,),
        )

    call = _event(
        event_id="event-call",
        event_type="tool_call",
        store_seq=1 if case != "result_before_call" else 2,
        attempt_id="attempt-original",
        payload={
            "run_id": "attempt-original",
            "call_id": "call-1",
            "tool_name": "lookup",
            "arguments": {},
        },
    )
    if case == "result_before_call":
        selected = result_event(event_id="event-result", store_seq=1)
        events = (selected, call)
    elif case == "duplicate_result":
        first = result_event(event_id="event-result-1", store_seq=2)
        selected = result_event(event_id="event-result-2", store_seq=3)
        events = (call, first, selected)
    else:
        selected = result_event(
            event_id="event-result",
            store_seq=2,
            tool_name="delete_all",
        )
        events = (call, selected)
    request = replace(
        _request(),
        source_messages=({"role": "system", "content": "policy"},),
        source_message_cursors=(),
        attempt_id="attempt-resume",
        build_id=f"build-{case}",
        pending_task_inputs=(
            {
                "event_id": selected.event_id,
                "store_seq": selected.store_seq,
                "type": "tool_result",
                "preview": content.decode("utf-8"),
                "content_ref": content_ref.to_dict(),
                "content_bytes": len(content),
                "content_sha256": content_sha256,
            },
        ),
    )
    builds = BuildRepository()

    with pytest.raises(ContextCompileCoordinatorError, match="tool result causal"):
        _coordinator(
            journal=SnapshotJournal(events),
            builds=builds,
        ).compile(request)

    assert builds.calls == []


def test_context_build_operation_hash_binds_snapshot_and_consumption_proof() -> None:
    first_builds = BuildRepository()
    repeated_builds = BuildRepository()
    changed_builds = BuildRepository()
    _coordinator(journal=SnapshotJournal(), builds=first_builds).compile(_request())
    _coordinator(journal=SnapshotJournal(), builds=repeated_builds).compile(_request())
    changed_events = list(_events())
    changed_receipt_payload = dict(changed_events[3].payload)
    changed_receipt_payload["receipt_note"] = "changed-authoritative"
    changed_events[3] = _event(
        event_id="event-4",
        event_type="run_completed",
        store_seq=4,
        attempt_id="attempt-history",
        payload=changed_receipt_payload,
        refs=(DEPENDENCY_REF,),
    )
    _coordinator(
        journal=SnapshotJournal(changed_events),
        builds=changed_builds,
    ).compile(_request())

    first_hash = first_builds.calls[0]["operation"].payload_sha256
    assert repeated_builds.calls[0]["operation"].payload_sha256 == first_hash
    assert changed_builds.calls[0]["operation"].payload_sha256 != first_hash
