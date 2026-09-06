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
