from __future__ import annotations

import hashlib

import pytest

from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    GenerationRef,
    JournalAppendRequest,
    JournalAppendResult,
    JournalEvent,
    JournalPage,
    ResourceRef,
    ToolExecutionReceiptLookup,
)
from unchain.journal.ports import BoundToolReceiptIndex
from unchain.journal.runtime import (
    DurableEventSink,
    SideEffectRecoveryState,
    SemanticEventDraft,
)
from unchain.journal.snapshot import capture_journal_snapshot
from unchain.context.artifacts import ArtifactService
from unchain.context.ports import BoundArtifactRepository


class FaultJournal(BoundToolReceiptIndex):
    def __init__(self) -> None:
        super().__init__("execution-1")
        self.events: list[JournalEvent] = []
        self.operations: dict[str, JournalEvent] = {}
        self.fail_next = False

    def append(self, *, request: JournalAppendRequest) -> JournalAppendResult:
        if self.fail_next:
            self.fail_next = False
            raise OSError("durable write unavailable")
        previous = self.operations.get(request.operation.operation_id)
        if previous is not None:
            return JournalAppendResult(
                previous,
                EventCursor(previous.store_seq, previous.event_id),
                duplicate=True,
            )
        event = JournalEvent(
            request.event_id,
            request.event_type,
            request.attempt,
            request.operation,
            len(self.events) + 1,
            request.payload,
            request.resource_refs,
        )
        self.events.append(event)
        self.operations[request.operation.operation_id] = event
        return JournalAppendResult(
            event,
            EventCursor(event.store_seq, event.event_id),
            duplicate=False,
        )

    def read(self, *, after: EventCursor | None = None, limit: int = 100) -> JournalPage:
        start = after.store_seq if after else 0
        selected = tuple(self.events[start : start + limit])
        cursor = (
            EventCursor(selected[-1].store_seq, selected[-1].event_id)
            if selected
            else after
        )
        return JournalPage(
            selected,
            cursor,
            start + len(selected) < len(self.events),
        )

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        del max_bytes
        if len(self.events) > max_events:
            raise OSError("snapshot limit exceeded")
        return capture_journal_snapshot(
            execution_id=self.execution_id,
            events=tuple(self.events),
        )

    def lookup_tool_execution_receipts(self, *, attempt, call_id):
        candidates = tuple(
            event
            for event in self.events
            if event.attempt == attempt
            and event.payload.get("call_id") == call_id
            and event.event_type
            in {"tool_call", "tool.started", "tool_result", "tool.result"}
        )
        return ToolExecutionReceiptLookup(
            attempt=attempt,
            call_id=call_id,
            events=candidates[:3],
            overflow=len(candidates) > 3,
        )


ATTEMPT = AttemptRef(
    GenerationRef("execution-1", "generation-1"),
    "attempt-1",
)
RESULT_BYTES = b'{"ok":true}'
RESULT_REF = ResourceRef("artifact", "tool-result-1", 1)
RESULT_ARTIFACT = ArtifactRef(
    RESULT_REF,
    "application/json",
    len(RESULT_BYTES),
    hashlib.sha256(RESULT_BYTES).hexdigest(),
    '{"ok":true}',
)


class RecoveryArtifactRepository(BoundArtifactRepository):
    def __init__(self) -> None:
        super().__init__("execution-1")
        self.verified_artifacts: list[ArtifactRef] = []
        self.full_verified_artifacts: list[ArtifactRef] = []

    def put(self, *, content, media_type, operation, preview=""):
        raise AssertionError("recovery must not persist another artifact")

    def read(self, *, ref, offset=0, limit=65_536):
        raise AssertionError("recovery must not use an unverified artifact read")

    def read_verified(self, *, artifact, offset=0, limit=65_536):
        self.verified_artifacts.append(artifact)
        assert artifact.byte_length == len(RESULT_BYTES)
        assert artifact.sha256 == hashlib.sha256(RESULT_BYTES).hexdigest()
        return RESULT_BYTES[offset : offset + limit]

    def read_full_verified(self, *, artifact):
        self.full_verified_artifacts.append(artifact)
        assert artifact.byte_length == len(RESULT_BYTES)
        assert artifact.sha256 == hashlib.sha256(RESULT_BYTES).hexdigest()
        return RESULT_BYTES


def test_recovery_state_values_are_the_explicit_durable_protocol_labels() -> None:
    assert {state.value for state in SideEffectRecoveryState} == {
        "not-started",
        "uncertain-after-start",
        "sealed-completion-finalizable",
        "terminal-result-reusable",
        "corrupt",
    }


def project(event):
    return SemanticEventDraft(
        event_id=event["event_id"],
        event_type=event["event_type"],
        attempt=ATTEMPT,
        operation_id=event["operation_id"],
        payload=event["payload"],
        resource_refs=event.get("resource_refs", ()),
    )


def started() -> dict:
    return {
        "type": "semantic",
        "event_id": "tool-started-1",
        "event_type": "tool.started",
        "operation_id": "tool-started-call-1",
        "payload": {"call_id": "call-1", "tool_name": "external.synthetic"},
    }


def intent() -> dict:
    return {
        "type": "semantic",
        "event_id": "tool-call-1",
        "event_type": "tool_call",
        "operation_id": "tool-call-intent-1",
        "payload": {"call_id": "call-1", "tool_name": "external.synthetic"},
    }


def result() -> dict:
    return {
        "type": "semantic",
        "event_id": "tool-result-1",
        "event_type": "tool.result",
        "operation_id": "tool-result-call-1",
        "payload": {
            "call_id": "call-1",
            "tool_name": "external.synthetic",
            "full_output_ref": RESULT_REF.to_dict(),
            "result_bytes": RESULT_ARTIFACT.byte_length,
            "result_sha256": RESULT_ARTIFACT.sha256,
        },
        "resource_refs": (RESULT_REF,),
    }


def test_boundary_1_failure_before_started_persistence_never_exposes_the_effect() -> None:
    repository = FaultJournal()
    sink = DurableEventSink(repository, ATTEMPT, project)
    sink(intent())
    repository.fail_next = True
    external_effects = 0

    with pytest.raises(OSError, match="durable write unavailable"):
        sink(started())
        external_effects += 1

    assert external_effects == 0
    recovery = sink.recover_tool_side_effect("call-1", page_size=1)
    assert recovery.state is SideEffectRecoveryState.NOT_STARTED
    assert recovery.auto_execute_allowed is True


def test_tool_call_intent_without_started_receipt_remains_safe_to_execute() -> None:
    repository = FaultJournal()
    sink = DurableEventSink(repository, ATTEMPT, project)
    sink(intent())

    recovery = sink.recover_tool_side_effect("call-1", page_size=1)

    assert recovery.state is SideEffectRecoveryState.NOT_STARTED
    assert recovery.auto_execute_allowed is True


def test_boundary_2_started_without_external_completion_is_uncertain_after_restart() -> None:
    repository = FaultJournal()
    sink = DurableEventSink(repository, ATTEMPT, project)
    sink(intent())
    sink(started())

    restarted = DurableEventSink(repository, ATTEMPT, project)
    recovery = restarted.recover_tool_side_effect("call-1", page_size=1)

    assert recovery.state is SideEffectRecoveryState.UNCERTAIN_AFTER_START
    assert recovery.auto_execute_allowed is False
    assert recovery.reusable_result_ref is None


def test_boundary_3_external_completion_before_result_persistence_is_not_replayed() -> None:
    repository = FaultJournal()
    sink = DurableEventSink(repository, ATTEMPT, project)
    sink(intent())
    sink(started())
    external_effects = 1
    repository.fail_next = True

    with pytest.raises(OSError, match="durable write unavailable"):
        sink(result())

    restarted = DurableEventSink(repository, ATTEMPT, project)
    recovery = restarted.recover_tool_side_effect("call-1", page_size=1)
    if recovery.auto_execute_allowed:
        external_effects += 1
    assert external_effects == 1
    assert recovery.state is SideEffectRecoveryState.UNCERTAIN_AFTER_START


def test_boundary_4_persisted_result_is_reused_after_restart_without_external_replay() -> None:
    repository = FaultJournal()
    sink = DurableEventSink(repository, ATTEMPT, project)
    sink(intent())
    sink(started())
    sink(result())
    external_effects = 1

    restarted = DurableEventSink(repository, ATTEMPT, project)
    recovery = restarted.recover_tool_side_effect("call-1", page_size=1)
    if recovery.auto_execute_allowed:
        external_effects += 1

    assert external_effects == 1
    assert recovery.state is SideEffectRecoveryState.TERMINAL_RESULT_REUSABLE
    assert recovery.auto_execute_allowed is False
    assert recovery.reusable_result_ref == RESULT_REF
    assert recovery.reusable_result_artifact == ArtifactRef(
        RESULT_REF,
        "application/json",
        len(RESULT_BYTES),
        hashlib.sha256(RESULT_BYTES).hexdigest(),
    )
    assert recovery.result_event is not None
    assert recovery.result_event.event_id == "tool-result-1"

    artifacts = RecoveryArtifactRepository()
    restored = ArtifactService(
        artifacts,
        sanitizer=lambda content, media_type: content,
    ).read_full(
        recovery.reusable_result_artifact,
        remaining_budget_bytes=len(RESULT_BYTES),
    )
    assert restored == RESULT_BYTES
    assert artifacts.verified_artifacts == []
    assert artifacts.full_verified_artifacts == [recovery.reusable_result_artifact]


@pytest.mark.parametrize(
    ("started_tool_name", "result_tool_name"),
    [
        ("", "external.synthetic"),
        ("external.synthetic", ""),
        ("external.synthetic", "external.other"),
    ],
)
def test_recovery_rejects_missing_or_mismatched_tool_identity(
    started_tool_name: str,
    result_tool_name: str,
) -> None:
    repository = FaultJournal()
    sink = DurableEventSink(repository, ATTEMPT, project)
    sink(intent())
    start_event = started()
    start_event["payload"]["tool_name"] = started_tool_name
    result_event = result()
    result_event["payload"]["tool_name"] = result_tool_name
    sink(start_event)
    sink(result_event)

    recovery = sink.recover_tool_side_effect("call-1")

    assert recovery.state is SideEffectRecoveryState.CORRUPT
    assert recovery.auto_execute_allowed is False
    assert recovery.reusable_result_artifact is None
    assert "tool identity" in recovery.reason


def test_result_without_started_and_incomplete_result_receipts_are_corrupt() -> None:
    repository = FaultJournal()
    sink = DurableEventSink(repository, ATTEMPT, project)
    sink(intent())
    sink(result())
    recovery = sink.recover_tool_side_effect("call-1")
    assert recovery.state is SideEffectRecoveryState.CORRUPT
    assert recovery.auto_execute_allowed is False

    repository = FaultJournal()
    sink = DurableEventSink(repository, ATTEMPT, project)
    sink(intent())
    sink(started())
    incomplete = result()
    incomplete["payload"] = {"call_id": "call-1"}
    incomplete["resource_refs"] = ()
    sink(incomplete)
    recovery = sink.recover_tool_side_effect("call-1")
    assert recovery.state is SideEffectRecoveryState.CORRUPT


def test_duplicate_distinct_started_events_are_corrupt_even_across_pages() -> None:
    repository = FaultJournal()
    sink = DurableEventSink(repository, ATTEMPT, project)
    sink(intent())
    sink(started())
    duplicate = started()
    duplicate["event_id"] = "tool-started-2"
    duplicate["operation_id"] = "tool-started-call-1-duplicate"
    sink(duplicate)

    recovery = sink.recover_tool_side_effect("call-1", page_size=1)

    assert recovery.state is SideEffectRecoveryState.CORRUPT
    assert recovery.auto_execute_allowed is False
