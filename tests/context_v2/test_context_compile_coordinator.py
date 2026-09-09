from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from unchain.context import (
    CheckpointRequest,
    CheckpointWriteStatus,
    ContextBuildReceipt,
    ContextCompileRequest,
    ContextRuntime,
    PreparedCheckpoint,
    SourceMessageCursor,
    resolve_context_budget,
)
from unchain.context.coordinator import (
    ContextCompileCoordinator,
    ContextCompileCoordinatorError,
)
from unchain.durability import DurablePersistenceBoundaryError
from unchain.journal import (
    AttemptRef,
    BoundExecutionJournal,
    EventCursor,
    GenerationRef,
    JournalEvent,
    OperationRef,
    ResourceRef,
    SemanticEventDraft,
    capture_journal_snapshot,
)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _request(*, pressured: bool) -> ContextCompileRequest:
    if pressured:
        messages = (
            {"role": "user", "content": "old " + ("x" * 30_000)},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current"},
        )
    else:
        messages = (
            {"role": "user", "content": "constraint"},
            {"role": "assistant", "content": "acknowledged"},
            {"role": "user", "content": "current"},
        )
    return ContextCompileRequest(
        case="durable-coordinator",
        source_messages=messages,
        current_generation="generation-1",
        fixed_overhead_tokens=0,
        budget=resolve_context_budget(context_window_tokens=8_192),
        source_event_ids=("event-1", "event-2", "event-3"),
        source_event_store_seqs=(1, 2, 3),
        provider="openai",
        model="synthetic",
        build_id="build-1",
        execution_id="execution-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
    )


class SnapshotJournal(BoundExecutionJournal):
    def __init__(self, events):
        super().__init__("execution-1")
        self.events = tuple(events)
        self.capture_calls = 0

    def append(self, *, request):
        raise AssertionError(f"unexpected append: {request}")

    def read(self, *, after=None, limit=100):
        raise AssertionError(f"live read is forbidden: {after}, {limit}")

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        del max_events, max_bytes
        self.capture_calls += 1
        return capture_journal_snapshot(
            execution_id=self.execution_id,
            events=self.events,
        )


def _complete_test_journal_prefix(records):
    if not records:
        return records
    existing_sequences = {record.store_seq for record in records}
    for store_seq in range(1, max(existing_sequences) + 1):
        if store_seq in existing_sequences:
            continue
        attempt = AttemptRef(
            GenerationRef("execution-1", "generation-1"),
            "attempt-history",
        )
        event_id = f"filler-event-{store_seq}"
        payload = {
            "run_id": "attempt-history",
            "status": "started",
        }
        operation = SemanticEventDraft(
            event_id=event_id,
            event_type="run_started",
            attempt=attempt,
            operation_id=f"operation-{event_id}",
            payload=payload,
        ).operation
        records.append(
            JournalEvent(
                event_id=event_id,
                event_type="run_started",
                attempt=attempt,
                operation=operation,
                store_seq=store_seq,
                payload=payload,
            )
        )
    records.sort(key=lambda record: record.store_seq)
    return records


def _journal_for_request(request):
    if request.semantic_events:
        records = []
        for raw in request.semantic_events:
            event = dict(raw)
            event_id = event.pop("event_id")
            event_type = event.pop("type")
            store_seq = event.pop("store_seq")
            attempt_id = event.pop("attempt_id")
            event.pop("execution_id", None)
            event.pop("generation_id", None)
            attempt = AttemptRef(
                GenerationRef("execution-1", "generation-1"),
                attempt_id,
            )
            operation_id = f"operation-{event_id}"
            operation = SemanticEventDraft(
                event_id=event_id,
                event_type=event_type,
                attempt=attempt,
                operation_id=operation_id,
                payload=event,
            ).operation
            records.append(
                JournalEvent(
                    event_id=event_id,
                    event_type=event_type,
                    attempt=attempt,
                    operation=operation,
                    store_seq=store_seq,
                    payload=event,
                )
            )
        return SnapshotJournal(_complete_test_journal_prefix(records))
    cursor_map = {
        cursor.message_index: (cursor.event_id, cursor.store_seq)
        for cursor in request.source_message_cursors
    }
    if not cursor_map:
        cursor_map = {
            index: (event_id, request.source_event_store_seqs[index])
            for index, event_id in enumerate(request.source_event_ids)
        }
    records = []
    cursor_indexes = tuple(sorted(cursor_map))
    for position, message_index in enumerate(cursor_indexes):
        event_id, store_seq = cursor_map[message_index]
        message = dict(request.source_messages[message_index])
        attempt_id = (
            "attempt-1"
            if position == len(cursor_indexes) - 1 and message["role"] == "user"
            else "attempt-history"
        )
        attempt = AttemptRef(
            GenerationRef("execution-1", "generation-1"),
            attempt_id,
        )
        payload = {"run_id": attempt_id, "message": message}
        operation = SemanticEventDraft(
            event_id=event_id,
            event_type=f"message.{message['role']}",
            attempt=attempt,
            operation_id=f"operation-{event_id}",
            payload=payload,
        ).operation
        records.append(
            JournalEvent(
                event_id=event_id,
                event_type=f"message.{message['role']}",
                attempt=attempt,
                operation=operation,
                store_seq=store_seq,
                payload=payload,
            )
        )
    return SnapshotJournal(_complete_test_journal_prefix(records))


class RecordingCheckpointRepository:
    execution_id = "execution-1"

    def __init__(self, *, failure: Exception | None = None):
        self.failure = failure
        self.calls = []
        self.commit_calls = []
        self.receipts = {}

    def prepare(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.failure is not None:
            raise self.failure
        operation = kwargs["operation"]
        receipt = PreparedCheckpoint(
            preparation_id="preparation-" + operation.payload_sha256[:32],
            checkpoint_ref=ResourceRef(
                "checkpoint",
                "checkpoint-" + operation.payload_sha256[:32],
                1,
            ),
            operation=operation,
        )
        self.receipts[operation.operation_id] = receipt
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


class RecordingBuildRepository:
    execution_id = "execution-1"

    def __init__(self, *, failure: Exception | None = None):
        self.failure = failure
        self.calls = []
        self.receipts = {}
        self.triggers = {}

    def record(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.failure is not None:
            raise self.failure
        receipt = ContextBuildReceipt(
            envelope=kwargs["envelope"],
            operation=kwargs["operation"],
            trigger_cursor=kwargs["trigger_cursor"],
        )
        prior_operation = self.receipts.get(receipt.operation.operation_id)
        if prior_operation is not None and prior_operation.operation != receipt.operation:
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


def _coordinator(
    *,
    request,
    journal=None,
    checkpoints=None,
    builds=None,
    partials=None,
):
    partials = partials if partials is not None else []
    return ContextCompileCoordinator(
        journal=journal or _journal_for_request(request),
        checkpoint_repository=checkpoints or RecordingCheckpointRepository(),
        build_repository=builds or RecordingBuildRepository(),
        partial_attempt_sink=lambda request, error: partials.append((request, error)),
    )


def test_below_pressure_records_the_final_build_without_a_checkpoint() -> None:
    checkpoints = RecordingCheckpointRepository()
    builds = RecordingBuildRepository()
    request = _request(pressured=False)

    result = _coordinator(
        request=request,
        checkpoints=checkpoints,
        builds=builds,
    ).compile(request)

    assert checkpoints.calls == []
    assert len(builds.calls) == 1
    assert builds.calls[0]["envelope"] == result.envelope
    assert builds.calls[0]["operation"].operation_id.startswith(
        "context-build-trigger."
    )
    assert builds.calls[0]["trigger_cursor"] == EventCursor(
        store_seq=3,
        event_id="event-3",
    )


def test_pressure_persists_exact_checkpoint_then_recompiles_and_records_build() -> None:
    checkpoints = RecordingCheckpointRepository()
    builds = RecordingBuildRepository()
    request = _request(pressured=True)

    result = _coordinator(
        request=request,
        checkpoints=checkpoints,
        builds=builds,
    ).compile(request)

    assert result.checkpoint_requests == ()
    assert len(checkpoints.calls) == 1
    payload = json.loads(checkpoints.calls[0]["summary"])
    checkpoint_request = CheckpointRequest.from_dict(
        payload["checkpoint_request"]
    )
    assert checkpoints.calls[0]["source_range"] == checkpoint_request.source_range
    assert result.envelope.checkpoint_refs == (
        checkpoints.commit_calls[0].checkpoint_ref,
    )
    assert payload["schema"] == "unchain.context_checkpoint_payload.v2"
    assert payload["checkpoint_request"] == checkpoint_request.to_dict()
    assert payload["source_messages"] == list(request.source_messages[:2])
    assert payload["source_messages_sha256"] == checkpoint_request.source_messages_sha256
    assert (
        checkpoints.calls[0]["operation"].operation_id
        == f"context-checkpoint.{checkpoint_request.request_id}"
    )
    assert len(builds.calls) == 1


def test_journal_projected_history_survives_the_checkpoint_round_trip() -> None:
    checkpoints = RecordingCheckpointRepository()
    builds = RecordingBuildRepository()
    request = ContextCompileRequest(
        case="durable-projected-history",
        source_messages=({"role": "user", "content": "current"},),
        current_generation="generation-1",
        semantic_events=(
            {
                "type": "message.user",
                "event_id": "event-1",
                "store_seq": 1,
                "generation_id": "generation-1",
                "attempt_id": "attempt-history",
                "run_id": "attempt-history",
                "message": {
                    "role": "user",
                    "content": "old " + ("x" * 30_000),
                },
            },
            {
                "type": "message.assistant",
                "event_id": "event-2",
                "store_seq": 2,
                "generation_id": "generation-1",
                "attempt_id": "attempt-history",
                "run_id": "attempt-history",
                "message": {"role": "assistant", "content": "old answer"},
            },
            {
                "type": "message.user",
                "event_id": "event-3",
                "store_seq": 3,
                "generation_id": "generation-1",
                "attempt_id": "attempt-1",
                "run_id": "attempt-1",
                "message": {"role": "user", "content": "current"},
            },
        ),
        budget=resolve_context_budget(context_window_tokens=8_192),
        source_message_cursors=(SourceMessageCursor(0, "event-3", 3),),
        provider="openai",
        model="synthetic",
        build_id="build-projected",
        execution_id="execution-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
    )

    result = _coordinator(
        request=request,
        checkpoints=checkpoints,
        builds=builds,
    ).compile(request)

    assert len(checkpoints.calls) == 1
    payload = json.loads(checkpoints.calls[0]["summary"])
    assert [message["content"] for message in payload["source_messages"]] == [
        "old " + ("x" * 30_000),
        "old answer",
    ]
    assert result.messages[-1]["content"] == "current"
    assert result.checkpoint_requests == ()


def test_context_runtime_can_use_the_coordinator_as_its_single_compiler() -> None:
    checkpoints = RecordingCheckpointRepository()
    builds = RecordingBuildRepository()
    request = _request(pressured=True)
    coordinator = _coordinator(
        request=request,
        checkpoints=checkpoints,
        builds=builds,
    )
    runtime = ContextRuntime(
        owner_id="context-v2",
        request_factory=lambda context: request,
        durable_event_sink=lambda event: None,
        partial_attempt_sink=lambda event, error: None,
        compiler=coordinator,
    )

    result = runtime.compile_context(
        SimpleNamespace(
            event={"session_id": "execution-1", "attempt_id": "attempt-1"}
        )
    )

    assert result.checkpoint_requests == ()
    assert len(result.envelope.checkpoint_refs) == 1
    assert len(checkpoints.calls) == 1
    assert len(builds.calls) == 1


def test_repeated_compile_uses_identical_checkpoint_and_build_operations() -> None:
    checkpoints = RecordingCheckpointRepository()
    builds = RecordingBuildRepository()
    request = _request(pressured=True)
    coordinator = _coordinator(
        request=request,
        checkpoints=checkpoints,
        builds=builds,
    )

    first = coordinator.compile(request)
    second = coordinator.compile(request)

    assert first.to_dict() == second.to_dict()
    assert len(checkpoints.calls) == 1
    assert len(checkpoints.commit_calls) == 1
    assert len(builds.calls) == 1


def test_one_input_receipt_cannot_authorize_two_distinct_model_builds() -> None:
    checkpoints = RecordingCheckpointRepository()
    builds = RecordingBuildRepository()
    partials = []
    request = _request(pressured=True)
    coordinator = _coordinator(
        request=request,
        checkpoints=checkpoints,
        builds=builds,
        partials=partials,
    )

    first = coordinator.compile(request)
    assert first.envelope is not None

    with pytest.raises(ContextCompileCoordinatorError, match="input receipt"):
        coordinator.compile(
            replace(request, build_id="build-distinct-invocation")
        )

    assert len(checkpoints.calls) == 1
    assert len(checkpoints.commit_calls) == 1
    assert len(builds.calls) == 1
    assert len(partials) == 1


def test_input_trigger_operation_identity_closes_concurrent_claim_race() -> None:
    class RacingBuildRepository(RecordingBuildRepository):
        def get_by_trigger(self, *, trigger_cursor):
            return None

    builds = RacingBuildRepository()
    partials = []
    request = _request(pressured=True)
    coordinator = _coordinator(
        request=request,
        builds=builds,
        partials=partials,
    )

    coordinator.compile(request)

    with pytest.raises(RuntimeError, match="build operation conflict"):
        coordinator.compile(
            replace(request, build_id="build-concurrent-claim")
        )

    assert len(builds.calls) == 1
    assert len(partials) == 1


def test_prepare_success_then_transport_error_recovers_by_operation_receipt() -> None:
    failure = RuntimeError("prepare response lost")

    class UncertainPrepareRepository(RecordingCheckpointRepository):
        def prepare(self, **kwargs):
            receipt = super().prepare(**kwargs)
            raise failure

    checkpoints = UncertainPrepareRepository()
    builds = RecordingBuildRepository()
    partials = []
    request = _request(pressured=True)

    result = _coordinator(
        request=request,
        checkpoints=checkpoints,
        builds=builds,
        partials=partials,
    ).compile(request)

    assert result.checkpoint_requests == ()
    assert len(checkpoints.calls) == 1
    assert len(checkpoints.commit_calls) == 1
    assert len(builds.calls) == 1
    assert partials == []


def test_commit_success_then_transport_error_recovers_without_recommit() -> None:
    failure = RuntimeError("commit response lost")

    class UncertainCommitRepository(RecordingCheckpointRepository):
        def commit(self, *, prepared):
            super().commit(prepared=prepared)
            raise failure

    checkpoints = UncertainCommitRepository()
    builds = RecordingBuildRepository()
    partials = []
    request = _request(pressured=True)

    result = _coordinator(
        request=request,
        checkpoints=checkpoints,
        builds=builds,
        partials=partials,
    ).compile(request)

    assert result.checkpoint_requests == ()
    assert len(checkpoints.commit_calls) == 1
    assert len(builds.calls) == 1
    assert partials == []


def test_build_success_then_transport_error_recovers_recorded_envelope() -> None:
    failure = RuntimeError("build response lost")

    class UncertainBuildRepository(RecordingBuildRepository):
        def record(self, **kwargs):
            super().record(**kwargs)
            raise failure

    checkpoints = RecordingCheckpointRepository()
    builds = UncertainBuildRepository()
    partials = []
    request = _request(pressured=True)

    result = _coordinator(
        request=request,
        checkpoints=checkpoints,
        builds=builds,
        partials=partials,
    ).compile(request)

    assert result.envelope == next(iter(builds.receipts.values())).envelope
    assert len(builds.calls) == 1
    assert partials == []


def test_checkpoint_persistence_failure_is_marked_partial_and_preserves_identity() -> None:
    failure = RuntimeError("checkpoint store unavailable")
    checkpoints = RecordingCheckpointRepository(failure=failure)
    builds = RecordingBuildRepository()
    partials = []
    request = _request(pressured=True)

    with pytest.raises(RuntimeError) as raised:
        _coordinator(
            request=request,
            checkpoints=checkpoints,
            builds=builds,
            partials=partials,
        ).compile(request)

    assert raised.value is failure
    assert getattr(failure, "_unchain_durable_persistence_failure") is True
    assert partials == [(_request(pressured=True), failure)]
    assert builds.calls == []


def test_context_build_failure_is_marked_partial_and_never_returns_model_input() -> None:
    failure = RuntimeError("context build store unavailable")
    builds = RecordingBuildRepository(failure=failure)
    partials = []
    request = _request(pressured=False)

    with pytest.raises(RuntimeError) as raised:
        _coordinator(
            request=request,
            builds=builds,
            partials=partials,
        ).compile(request)

    assert raised.value is failure
    assert getattr(failure, "_unchain_durable_persistence_failure") is True
    assert partials == [(_request(pressured=False), failure)]


def test_unmarkable_repository_failure_uses_returned_safe_boundary_wrapper() -> None:
    class UnmarkableRepositoryError(Exception):
        def __setattr__(self, name, value):
            if name == "_unchain_durable_persistence_failure":
                raise TypeError("immutable exception")
            super().__setattr__(name, value)

    failure = UnmarkableRepositoryError("repository secret")
    builds = RecordingBuildRepository(failure=failure)
    partials = []
    request = _request(pressured=False)

    with pytest.raises(DurablePersistenceBoundaryError) as raised:
        _coordinator(
            request=request,
            builds=builds,
            partials=partials,
        ).compile(request)

    assert raised.value.original is failure
    assert partials == [(_request(pressured=False), raised.value)]


def test_repository_scope_mismatch_fails_before_compilation_or_persistence() -> None:
    checkpoints = RecordingCheckpointRepository()
    checkpoints.execution_id = "different-execution"
    request = _request(pressured=False)

    with pytest.raises(ContextCompileCoordinatorError, match="execution scope"):
        _coordinator(
            request=request,
            checkpoints=checkpoints,
        ).compile(request)

    assert checkpoints.calls == []


def test_checkpoint_payload_hash_matches_the_bytes_sent_to_storage() -> None:
    checkpoints = RecordingCheckpointRepository()
    request = _request(pressured=True)
    journal = _journal_for_request(request)
    _coordinator(
        request=request,
        journal=journal,
        checkpoints=checkpoints,
    ).compile(request)

    call = checkpoints.calls[0]
    summary = json.loads(call["summary"])
    expected_payload = {
        "checkpoint_request": summary["checkpoint_request"],
        "summary_sha256": hashlib.sha256(call["summary"].encode("utf-8")).hexdigest(),
        "refs": [ref.to_dict() for ref in call["refs"]],
    }
    assert call["operation"].payload_sha256 == _canonical_sha256(expected_payload)


def test_checkpoint_payload_uses_exact_sparse_message_cursors() -> None:
    request = replace(
        _request(pressured=True),
        source_messages=(
            {"role": "system", "content": "current policy"},
            {"role": "user", "content": "old " + ("x" * 30_000)},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current"},
        ),
        source_event_ids=(),
        source_event_store_seqs=(),
        source_message_cursors=(
            SourceMessageCursor(1, "event-1", 11),
            SourceMessageCursor(2, "event-2", 14),
            SourceMessageCursor(3, "event-3", 19),
        ),
    )
    checkpoints = RecordingCheckpointRepository()

    _coordinator(request=request, checkpoints=checkpoints).compile(request)

    payload = json.loads(checkpoints.calls[0]["summary"])
    assert payload["source_messages"] == list(request.source_messages[1:3])
    assert payload["checkpoint_request"]["source_event_store_seqs"] == [11, 14]
