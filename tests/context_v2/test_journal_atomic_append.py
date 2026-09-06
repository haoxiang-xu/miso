"""append_with_artifacts is a versioned journal capability, not a silent
fallback: BoundExecutionJournal refuses it by default, and PendingArtifact
validates its own shape before any journal ever sees it."""

import pytest

from unchain.journal import (
    AttemptRef,
    GenerationRef,
    JournalAppendRequest,
    JournalRepositoryError,
    OperationRef,
    PendingArtifact,
    build_operation_ref,
)
from unchain.journal.ports import BoundExecutionJournal


class _MinimalJournal(BoundExecutionJournal):
    """The smallest possible conformant journal: only the abstract methods."""

    def append(self, *, request):  # pragma: no cover - unused in this test
        raise NotImplementedError

    def read(self, *, after=None, limit=100):  # pragma: no cover - unused
        raise NotImplementedError

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        from unchain.journal import capture_journal_snapshot

        return capture_journal_snapshot(execution_id=self.execution_id, events=())


def _attempt() -> AttemptRef:
    return AttemptRef(GenerationRef("exec-1", "gen-1"), "attempt-1")


def _operation() -> OperationRef:
    return build_operation_ref(
        "op-1", domain="context.artifact.put", payload={"sha256": "x"}
    )


def test_pending_artifact_requires_exact_bytes_and_operation():
    operation = _operation()
    artifact = PendingArtifact(
        content=b"{}", media_type="application/json", operation=operation
    )
    assert artifact.preview == ""
    assert artifact.operation == operation
    with pytest.raises(TypeError):
        PendingArtifact(content="{}", media_type="application/json", operation=operation)


def test_pending_artifact_rejects_non_text_media_type_and_preview():
    operation = _operation()
    with pytest.raises((TypeError, Exception)):
        PendingArtifact(content=b"{}", media_type=123, operation=operation)
    with pytest.raises(TypeError):
        PendingArtifact(
            content=b"{}", media_type="application/json", operation=operation, preview=1
        )


def test_default_journal_refuses_atomic_append():
    journal = _MinimalJournal("exec-1")
    attempt = _attempt()
    operation = _operation()
    request = JournalAppendRequest(
        event_id="evt-1",
        event_type="interaction.resolved",
        attempt=attempt,
        operation=operation,
        payload={},
    )
    artifact = PendingArtifact(
        content=b"{}", media_type="application/json", operation=operation
    )
    with pytest.raises(JournalRepositoryError, match="atomically"):
        journal.append_with_artifacts(request=request, artifacts=(artifact,))


def _store(tmp_path):
    from unchain.persistence import SQLiteContextV2Store

    return SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )


def _artifact(content=b'{"answer":"vue"}', operation_id="artifact.interaction-resolution.q1"):
    import hashlib

    operation = build_operation_ref(
        operation_id,
        domain="context.artifact.put",
        payload={
            "media_type": "application/json",
            "byte_length": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "preview": content.decode(),
            "binding": {},
        },
    )
    return PendingArtifact(
        content=content, media_type="application/json", operation=operation, preview=content.decode()
    ), operation


def _request(journal, artifact_id, content_sha256, event_id="evt-q1", operation_id="interaction.resolved.q1"):
    from unchain.journal.models import ResourceRef

    return JournalAppendRequest(
        event_id=event_id,
        event_type="interaction.resolved",
        attempt=_attempt(),
        operation=build_operation_ref(
            operation_id, domain="journal.semantic_event", payload={"sha": content_sha256}
        ),
        payload={
            "interaction_id": "q1",
            "content_ref": {"kind": "artifact", "resource_id": artifact_id, "revision": 1},
            "content_sha256": content_sha256,
        },
        resource_refs=(ResourceRef("artifact", artifact_id, 1),),
    )


def test_precondition_rejection_persists_nothing(tmp_path):
    journal = _store(tmp_path).bind_execution("exec-1")
    pending, operation = _artifact()
    artifact_id = journal.artifact_id_for(
        logical_kind="artifact", logical_key=operation.operation_id
    )
    request = _request(journal, artifact_id, operation.payload_sha256)

    def reject(snapshot):
        raise RuntimeError("already answered")

    with pytest.raises(RuntimeError, match="already answered"):
        journal.append_with_artifacts(request=request, artifacts=(pending,), precondition=reject)
    assert journal.capture_snapshot().events == ()
    with journal._store._transaction(immediate=False) as connection:
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 0


def test_failure_after_artifact_rows_rolls_back_the_claim(tmp_path, monkeypatch):
    from unchain.persistence.sqlite_v2 import _SQLiteBoundContextV2Repository

    journal = _store(tmp_path).bind_execution("exec-1")
    pending, operation = _artifact()
    artifact_id = journal.artifact_id_for(
        logical_kind="artifact", logical_key=operation.operation_id
    )
    request = _request(journal, artifact_id, operation.payload_sha256)
    original = _SQLiteBoundContextV2Repository._append_with_connection

    def explode(self, connection, req):
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
        raise RuntimeError("interrupted between artifact and event")

    monkeypatch.setattr(_SQLiteBoundContextV2Repository, "_append_with_connection", explode)
    with pytest.raises(RuntimeError, match="interrupted"):
        journal.append_with_artifacts(request=request, artifacts=(pending,))
    monkeypatch.setattr(_SQLiteBoundContextV2Repository, "_append_with_connection", original)

    other, other_operation = _artifact(content=b'{"answer":"react"}')
    other_request = _request(journal, artifact_id, other_operation.payload_sha256)
    result = journal.append_with_artifacts(request=other_request, artifacts=(other,))
    assert result.duplicate is False and result.event.event_type == "interaction.resolved"


def test_same_answer_replays_and_different_answer_conflicts(tmp_path):
    journal = _store(tmp_path).bind_execution("exec-1")
    pending, operation = _artifact()
    artifact_id = journal.artifact_id_for(
        logical_kind="artifact", logical_key=operation.operation_id
    )
    request = _request(journal, artifact_id, operation.payload_sha256)
    first = journal.append_with_artifacts(request=request, artifacts=(pending,))
    calls = []
    again = journal.append_with_artifacts(
        request=request, artifacts=(pending,), precondition=calls.append
    )
    assert again.duplicate is True and again.event == first.event and calls == []

    other, other_operation = _artifact(content=b'{"answer":"react"}')
    other_request = _request(journal, artifact_id, other_operation.payload_sha256)
    from unchain.journal import JournalConflictError

    with pytest.raises(JournalConflictError):
        journal.append_with_artifacts(request=other_request, artifacts=(other,))
    assert [e.event_type for e in journal.capture_snapshot().events] == ["interaction.resolved"]


def test_precondition_sees_the_in_transaction_snapshot(tmp_path):
    journal = _store(tmp_path).bind_execution("exec-1")
    pending, operation = _artifact()
    artifact_id = journal.artifact_id_for(
        logical_kind="artifact", logical_key=operation.operation_id
    )
    seen = []
    journal.append_with_artifacts(
        request=_request(journal, artifact_id, operation.payload_sha256),
        artifacts=(pending,),
        precondition=lambda snapshot: seen.append((snapshot.execution_id, snapshot.high_water)),
    )
    assert seen == [("exec-1", None)]
