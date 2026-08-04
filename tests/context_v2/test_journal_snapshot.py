from __future__ import annotations

from dataclasses import replace

import pytest

from unchain.journal import (
    AttemptRef,
    EventCursor,
    GenerationRef,
    JournalEvent,
    JournalSnapshot,
    JournalSnapshotError,
    OperationRef,
    capture_journal_snapshot,
    journal_event_sha256,
)


def _event(*, event_id: str, store_seq: int) -> JournalEvent:
    return JournalEvent(
        event_id=event_id,
        event_type="message.user",
        attempt=AttemptRef(
            GenerationRef("execution-1", "generation-1"),
            "attempt-1",
        ),
        operation=OperationRef(f"operation-{store_seq}", f"{store_seq}" * 64),
        store_seq=store_seq,
        payload={"message": {"role": "user", "content": event_id}},
    )


def test_journal_snapshot_binds_exact_events_and_high_water() -> None:
    first = _event(event_id="event-1", store_seq=1)
    second = _event(event_id="event-2", store_seq=2)

    snapshot = capture_journal_snapshot(
        execution_id="execution-1",
        events=(first, second),
    )

    assert snapshot.events == (first, second)
    assert snapshot.high_water == EventCursor(store_seq=2, event_id="event-2")
    assert snapshot.event_sha256s == (
        journal_event_sha256(first),
        journal_event_sha256(second),
    )
    assert JournalSnapshot.from_dict(snapshot.to_dict()) == snapshot


def test_journal_snapshot_rejects_digest_or_high_water_tampering() -> None:
    snapshot = capture_journal_snapshot(
        execution_id="execution-1",
        events=(_event(event_id="event-1", store_seq=1),),
    )

    with pytest.raises(JournalSnapshotError, match="digest"):
        replace(snapshot, snapshot_sha256="f" * 64)
    with pytest.raises(JournalSnapshotError, match="high water"):
        replace(
            snapshot,
            high_water=EventCursor(store_seq=2, event_id="event-2"),
        )


def test_journal_snapshot_rejects_foreign_execution_or_cursor_alias() -> None:
    first = _event(event_id="event-1", store_seq=1)
    foreign = JournalEvent(
        event_id="event-2",
        event_type="message.user",
        attempt=AttemptRef(
            GenerationRef("execution-2", "generation-1"),
            "attempt-1",
        ),
        operation=OperationRef("operation-2", "2" * 64),
        store_seq=2,
        payload={},
    )

    with pytest.raises(JournalSnapshotError, match="execution"):
        capture_journal_snapshot(
            execution_id="execution-1",
            events=(first, foreign),
        )
    with pytest.raises(JournalSnapshotError, match="unique"):
        capture_journal_snapshot(
            execution_id="execution-1",
            events=(first, replace(first, store_seq=2)),
        )


def test_journal_snapshot_requires_a_complete_contiguous_execution_prefix() -> None:
    with pytest.raises(JournalSnapshotError, match="contiguous prefix"):
        capture_journal_snapshot(
            execution_id="execution-1",
            events=(
                _event(event_id="event-1", store_seq=1),
                _event(event_id="event-3", store_seq=3),
            ),
        )

    with pytest.raises(JournalSnapshotError, match="contiguous prefix"):
        capture_journal_snapshot(
            execution_id="execution-1",
            events=(_event(event_id="event-2", store_seq=2),),
        )
