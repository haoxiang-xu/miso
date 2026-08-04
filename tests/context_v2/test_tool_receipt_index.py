from __future__ import annotations

import hashlib

import pytest

from unchain.journal import (
    AttemptRef,
    BoundExecutionJournal,
    BoundToolReceiptIndex,
    DurableEventSink,
    DurableJournalIntegrityError,
    EventCursor,
    GenerationRef,
    JournalAppendRequest,
    JournalAppendResult,
    JournalEvent,
    JournalPage,
    OperationRef,
    ResourceRef,
    SideEffectRecoveryState,
    ToolExecutionReceiptLookup,
    capture_journal_snapshot,
)


ATTEMPT = AttemptRef(
    GenerationRef("execution-1", "generation-1"),
    "attempt-1",
)


def _event(
    store_seq: int,
    event_type: str,
    payload: dict,
    *,
    refs: tuple[ResourceRef, ...] = (),
    attempt: AttemptRef = ATTEMPT,
) -> JournalEvent:
    digest = hashlib.sha256(
        f"{event_type}:{store_seq}".encode("utf-8")
    ).hexdigest()
    return JournalEvent(
        event_id=f"event-{store_seq}",
        event_type=event_type,
        attempt=attempt,
        operation=OperationRef(f"operation-{store_seq}", digest),
        store_seq=store_seq,
        payload=payload,
        resource_refs=refs,
    )


class _IndexedJournal(BoundToolReceiptIndex):
    def __init__(self, events=()):
        super().__init__(ATTEMPT.generation.execution_id)
        self.events = list(events)
        self.query_count = 0

    def append(self, *, request: JournalAppendRequest) -> JournalAppendResult:
        raise AssertionError(request)

    def read(self, *, after=None, limit=100) -> JournalPage:
        raise AssertionError("tool recovery must not scan journal pages")

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        return capture_journal_snapshot(
            execution_id=self.execution_id,
            events=tuple(self.events),
        )

    def lookup_tool_execution_receipts(self, *, attempt, call_id):
        self.query_count += 1
        candidates = tuple(
            event
            for event in self.events
            if event.attempt == attempt
            and event.payload.get("call_id") == call_id
            and event.event_type
            in {
                "tool_call",
                "tool.started",
                "tool.subagent_completion.sealed",
                "tool_result",
                "tool.result",
            }
        )
        return ToolExecutionReceiptLookup(
            attempt=attempt,
            call_id=call_id,
            events=candidates[:4],
            overflow=len(candidates) > 4,
        )


class _ScanOnlyJournal(BoundExecutionJournal):
    def __init__(self):
        super().__init__(ATTEMPT.generation.execution_id)

    def append(self, *, request):
        raise AssertionError(request)

    def read(self, *, after=None, limit=100):
        return JournalPage(events=(), has_more=False)

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        return capture_journal_snapshot(execution_id=self.execution_id, events=())


def _sink(journal):
    return DurableEventSink(journal, ATTEMPT, lambda event: None)


def test_recovery_uses_one_index_query_and_never_pages_the_journal() -> None:
    intent = _event(
        1,
        "tool_call",
        {
            "tool_name": "lookup",
            "call_id": "call-1",
            "iteration": 0,
            "arguments": {"query": "safe"},
        },
    )
    journal = _IndexedJournal((intent,))

    recovery = _sink(journal).recover_tool_side_effect("call-1")

    assert recovery.state is SideEffectRecoveryState.NOT_STARTED
    assert recovery.intent_event == intent
    assert journal.query_count == 1


def test_empty_index_lookup_cannot_authorize_execution_without_intent() -> None:
    recovery = _sink(_IndexedJournal()).recover_tool_side_effect("call-1")

    assert recovery.state is SideEffectRecoveryState.CORRUPT
    assert recovery.auto_execute_allowed is False
    assert "intent" in recovery.reason


def test_indexed_recovery_returns_exact_intent_started_and_result() -> None:
    ref = ResourceRef("artifact", "result-1", 1)
    events = (
        _event(
            1,
            "tool_call",
            {
                "tool_name": "lookup",
                "call_id": "call-1",
                "iteration": 0,
                "arguments": {},
            },
        ),
        _event(
            2,
            "tool.started",
            {
                "tool_name": "lookup",
                "call_id": "call-1",
                "iteration": 0,
            },
        ),
        _event(
            3,
            "tool_result",
            {
                "tool_name": "lookup",
                "call_id": "call-1",
                "iteration": 0,
                "result": {"ok": True},
                "full_output_ref": ref.to_dict(),
                "result_bytes": 2,
                "result_sha256": "a" * 64,
            },
            refs=(ref,),
        ),
    )

    recovery = _sink(_IndexedJournal(events)).recover_tool_side_effect(
        "call-1"
    )

    assert recovery.state is SideEffectRecoveryState.TERMINAL_RESULT_REUSABLE
    assert recovery.intent_event == events[0]
    assert recovery.started_event == events[1]
    assert recovery.result_event == events[2]


def test_indexed_recovery_distinguishes_sealed_completion_from_uncertain_start(
) -> None:
    result_ref = ResourceRef("artifact", "result-1", 1)
    completion_ref = ResourceRef("artifact", "completion-1", 1)
    next_state_ref = ResourceRef("artifact", "next-state-1", 1)
    events = (
        _event(
            1,
            "tool_call",
            {
                "tool_name": "lookup",
                "call_id": "call-1",
                "iteration": 0,
                "arguments": {},
            },
        ),
        _event(
            2,
            "tool.started",
            {
                "tool_name": "lookup",
                "call_id": "call-1",
                "iteration": 0,
            },
        ),
        _event(
            3,
            "tool.subagent_completion.sealed",
            {
                "tool_name": "lookup",
                "call_id": "call-1",
                "iteration": 0,
                "execution_subject_sha256": "a" * 64,
                "result_artifact": {
                    "schema": "unchain.artifact_ref.v1",
                    "ref": result_ref.to_dict(),
                    "media_type": "application/json",
                    "byte_length": 2,
                    "sha256": "b" * 64,
                    "preview": "{}",
                },
                "completion_artifact": {
                    "schema": "unchain.artifact_ref.v1",
                    "ref": completion_ref.to_dict(),
                    "media_type": "application/json",
                    "byte_length": 2,
                    "sha256": "c" * 64,
                    "preview": "{}",
                },
                "next_state_artifact": {
                    "schema": "unchain.artifact_ref.v1",
                    "ref": next_state_ref.to_dict(),
                    "media_type": "application/json",
                    "byte_length": 2,
                    "sha256": "d" * 64,
                    "preview": "{}",
                },
                "handoff_refs": [],
            },
            refs=(result_ref, completion_ref, next_state_ref),
        ),
    )

    recovery = _sink(_IndexedJournal(events)).recover_tool_side_effect(
        "call-1"
    )

    assert (
        recovery.state
        is SideEffectRecoveryState.SEALED_COMPLETION_FINALIZABLE
    )
    assert recovery.sealed_event == events[2]
    assert recovery.result_event is None


def test_started_without_seal_remains_uncertain_after_start() -> None:
    events = (
        _event(
            1,
            "tool_call",
            {"tool_name": "lookup", "call_id": "call-1", "iteration": 0},
        ),
        _event(
            2,
            "tool.started",
            {"tool_name": "lookup", "call_id": "call-1", "iteration": 0},
        ),
    )

    recovery = _sink(_IndexedJournal(events)).recover_tool_side_effect(
        "call-1"
    )

    assert recovery.state is SideEffectRecoveryState.UNCERTAIN_AFTER_START


def test_scan_only_journal_is_rejected_without_fallback() -> None:
    with pytest.raises(DurableJournalIntegrityError, match="receipt index"):
        _sink(_ScanOnlyJournal()).recover_tool_side_effect("call-1")


def test_receipt_set_rejects_more_than_the_fixed_three_candidates() -> None:
    events = tuple(
        _event(
            index,
            "tool.started",
            {
                "tool_name": "lookup",
                "call_id": "call-1",
                "iteration": 0,
            },
        )
        for index in range(1, 6)
    )

    with pytest.raises(ValueError, match="at most four"):
        ToolExecutionReceiptLookup(
            attempt=ATTEMPT,
            call_id="call-1",
            events=events,
        )


@pytest.mark.parametrize(
    "events, error",
    [
        (
            (
                _event(
                    1,
                    "tool_call",
                    {"call_id": "call-other", "tool_name": "lookup"},
                ),
            ),
            "call identity",
        ),
        (
            (
                _event(
                    1,
                    "message.user",
                    {"call_id": "call-1", "tool_name": "lookup"},
                ),
            ),
            "unsupported event type",
        ),
        (
            (
                _event(
                    2,
                    "tool.started",
                    {"call_id": "call-1", "tool_name": "lookup"},
                ),
                _event(
                    1,
                    "tool_call",
                    {"call_id": "call-1", "tool_name": "lookup"},
                ),
            ),
            "strictly ordered",
        ),
    ],
)
def test_receipt_lookup_rejects_invalid_exact_subject_records(
    events: tuple[JournalEvent, ...],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        ToolExecutionReceiptLookup(
            attempt=ATTEMPT,
            call_id="call-1",
            events=events,
        )


def test_receipt_lookup_rejects_foreign_attempt_and_duplicate_operation() -> None:
    foreign_attempt = AttemptRef(ATTEMPT.generation, "attempt-2")
    with pytest.raises(ValueError, match="foreign attempt"):
        ToolExecutionReceiptLookup(
            attempt=ATTEMPT,
            call_id="call-1",
            events=(
                _event(
                    1,
                    "tool_call",
                    {"call_id": "call-1", "tool_name": "lookup"},
                    attempt=foreign_attempt,
                ),
            ),
        )

    first = _event(
        1,
        "tool_call",
        {"call_id": "call-1", "tool_name": "lookup"},
    )
    second = JournalEvent(
        event_id="event-2",
        event_type="tool.started",
        attempt=ATTEMPT,
        operation=first.operation,
        store_seq=2,
        payload={"call_id": "call-1", "tool_name": "lookup"},
    )
    with pytest.raises(ValueError, match="identity is duplicated"):
        ToolExecutionReceiptLookup(
            attempt=ATTEMPT,
            call_id="call-1",
            events=(first, second),
        )


def test_index_overflow_is_corrupt_and_never_auto_executes() -> None:
    events = (
        _event(
            1,
            "tool_call",
            {"call_id": "call-1", "tool_name": "lookup"},
        ),
        *(
            _event(
                index,
                "tool.started",
                {"call_id": "call-1", "tool_name": "lookup"},
            )
            for index in range(2, 6)
        ),
    )

    recovery = _sink(_IndexedJournal(events)).recover_tool_side_effect("call-1")

    assert recovery.state is SideEffectRecoveryState.CORRUPT
    assert recovery.auto_execute_allowed is False
    assert "indexed limit" in recovery.reason
