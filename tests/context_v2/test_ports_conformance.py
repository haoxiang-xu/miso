from __future__ import annotations

import inspect

import pytest

from unchain.context import (
    CheckpointWriteStatus,
    ContextBudget,
    ContextBuildEnvelope,
    ContextBuildReceipt,
    ContextBuildStatus,
    PreparedCheckpoint,
)
from unchain.context.ports import (
    BoundArtifactRepository,
    BoundCheckpointRepository,
    BoundContextBuildRepository,
)
from unchain.journal import (
    AttemptRef,
    EventCursor,
    GenerationRef,
    JournalAppendResult,
    JournalAppendRequest,
    JournalEvent,
    JournalPage,
    OperationRef,
    ResourceRef,
    capture_journal_snapshot,
)
from unchain.journal.ports import BoundExecutionJournal, JournalConflictError, JournalScopeError
from unchain.memory.workspace import MemoryEntry, MemoryEntryKind, MemoryEntryPage, MemorySpace
from unchain.memory.workspace.ports import (
    BoundMemoryWorkspaceRepository,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryScopeError,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
FORBIDDEN_OPERATION_PARAMETERS = {
    "user_id",
    "owner_chat_id",
    "chat_id",
    "namespace",
    "space_id",
    "scope",
}


class _FakeJournal(BoundExecutionJournal):
    def __init__(self, execution_id: str) -> None:
        super().__init__(execution_id)
        self._events: list[JournalEvent] = []
        self._operations: dict[str, tuple[str, JournalEvent]] = {}

    def append(self, *, request: JournalAppendRequest) -> JournalAppendResult:
        if request.attempt.generation.execution_id != self.execution_id:
            raise JournalScopeError("event belongs to a different execution")
        previous = self._operations.get(request.operation.operation_id)
        if previous is not None:
            previous_hash, persisted = previous
            if previous_hash != request.operation.payload_sha256:
                raise JournalConflictError("operation payload changed")
            cursor = EventCursor(persisted.store_seq, persisted.event_id)
            return JournalAppendResult(event=persisted, cursor=cursor, duplicate=True)
        persisted = JournalEvent(
            event_id=request.event_id,
            event_type=request.event_type,
            attempt=request.attempt,
            operation=request.operation,
            store_seq=len(self._events) + 1,
            payload=request.payload,
            resource_refs=request.resource_refs,
        )
        cursor = EventCursor(store_seq=persisted.store_seq, event_id=persisted.event_id)
        self._events.append(persisted)
        self._operations[request.operation.operation_id] = (
            request.operation.payload_sha256,
            persisted,
        )
        return JournalAppendResult(event=persisted, cursor=cursor, duplicate=False)

    def read(self, *, after: EventCursor | None = None, limit: int = 100) -> JournalPage:
        offset = after.store_seq if after else 0
        if after is not None and (
            offset < 1
            or offset > len(self._events)
            or self._events[offset - 1].event_id != after.event_id
        ):
            raise JournalScopeError("cursor does not belong to this execution")
        events = tuple(self._events[offset : offset + limit])
        next_seq = offset + len(events)
        has_more = next_seq < len(self._events)
        next_cursor = (
            EventCursor(next_seq, events[-1].event_id)
            if events
            else after
        )
        return JournalPage(events=events, next_cursor=next_cursor, has_more=has_more)

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        del max_bytes
        if len(self._events) > max_events:
            raise ValueError("snapshot limit exceeded")
        return capture_journal_snapshot(
            execution_id=self.execution_id,
            events=tuple(self._events),
        )


class _FakeWorkspace(BoundMemoryWorkspaceRepository):
    def __init__(self, space: MemorySpace) -> None:
        super().__init__(space)
        self._entries: dict[str, MemoryEntry] = {}
        self._operations: dict[str, tuple[str, MemoryEntry]] = {}

    def list_entries(
        self,
        *,
        parent_path: str = "/",
        include_deleted: bool = False,
        limit: int = 100,
        cursor: str | None = None,
    ) -> MemoryEntryPage:
        prefix = parent_path.rstrip("/") + "/"
        matches = [
            entry
            for entry in self._entries.values()
            if entry.path.startswith(prefix) and (include_deleted or not entry.deleted)
        ]
        ordered = sorted(matches, key=lambda item: item.path)
        start = 0
        if cursor is not None:
            positions = [index for index, entry in enumerate(ordered) if entry.entry_id == cursor]
            if not positions:
                raise RepositoryScopeError("cursor does not belong to the bound workspace")
            start = positions[0] + 1
        entries = tuple(ordered[start : start + limit])
        has_more = start + len(entries) < len(ordered)
        next_cursor = entries[-1].entry_id if entries and has_more else None
        return MemoryEntryPage(entries=entries, next_cursor=next_cursor, has_more=has_more)

    def search(self, *, query: str, limit: int = 20) -> tuple[MemoryEntry, ...]:
        needle = query.casefold()
        matches = [
            entry
            for entry in self._entries.values()
            if needle in f"{entry.path} {entry.name} {entry.description}".casefold()
        ]
        return tuple(sorted(matches, key=lambda item: item.path)[:limit])

    def read_entry(self, *, ref: ResourceRef) -> MemoryEntry:
        if ref.kind != "memory":
            raise RepositoryScopeError("reference does not belong to the bound workspace")
        entry = self._entries.get(ref.resource_id)
        if entry is None or entry.revision != ref.revision:
            raise RepositoryNotFoundError("entry revision was not found")
        return entry

    def read_current_entry(self, *, entry_id: str) -> MemoryEntry:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise RepositoryNotFoundError("entry was not found")
        return entry

    def compare_and_swap(
        self,
        *,
        entry: MemoryEntry,
        expected_revision: int | None,
        operation: OperationRef,
    ) -> MemoryEntry:
        if entry.space_id != self.space.space_id:
            raise RepositoryScopeError("entry belongs to a different bound space")
        prior_operation = self._operations.get(operation.operation_id)
        if prior_operation is not None:
            prior_hash, prior_entry = prior_operation
            if prior_hash != operation.payload_sha256:
                raise RepositoryConflictError("operation payload changed")
            return prior_entry
        current = self._entries.get(entry.entry_id)
        current_revision = current.revision if current else None
        if current_revision != expected_revision:
            raise RepositoryConflictError("revision changed")
        required_revision = 1 if current is None else current.revision + 1
        if entry.revision != required_revision:
            raise RepositoryConflictError("revision must advance exactly once")
        self._entries[entry.entry_id] = entry
        self._operations[operation.operation_id] = (operation.payload_sha256, entry)
        return entry


def _event(
    *, operation_id: str, payload_hash: str, execution_id: str = "execution-1"
) -> JournalAppendRequest:
    return JournalAppendRequest(
        event_id=f"event-{operation_id}",
        event_type="message.user",
        attempt=AttemptRef(GenerationRef(execution_id, "generation-1"), "attempt-1"),
        operation=OperationRef(operation_id, payload_hash),
        payload={"content": "synthetic"},
    )


def _entry(
    *,
    entry_id: str = "entry-1",
    path: str = "/notes/a.md",
    space_id: str = "space-1",
    revision: int = 1,
) -> MemoryEntry:
    return MemoryEntry(
        entry_id=entry_id,
        space_id=space_id,
        path=path,
        name="A",
        description="Synthetic",
        kind=MemoryEntryKind.MARKDOWN,
        revision=revision,
    )


def test_bound_port_operations_do_not_accept_caller_controlled_scope() -> None:
    operations = {
        BoundExecutionJournal: ("append", "read", "capture_snapshot"),
        BoundMemoryWorkspaceRepository: (
            "list_entries",
            "search",
            "read_entry",
            "compare_and_swap",
        ),
        BoundArtifactRepository: ("put", "read_verified", "read_full_verified"),
        BoundCheckpointRepository: (
            "prepare",
            "commit",
            "get_by_operation",
            "read",
        ),
        BoundContextBuildRepository: (
            "record",
            "get_by_operation",
            "get_by_trigger",
            "latest",
        ),
    }
    for port, method_names in operations.items():
        for method_name in method_names:
            parameters = set(inspect.signature(getattr(port, method_name)).parameters)
            assert not parameters.intersection(FORBIDDEN_OPERATION_PARAMETERS), (
                port,
                method_name,
                parameters,
            )


def test_checkpoint_and_build_receipts_bind_exact_operation_identity() -> None:
    operation = OperationRef("operation-checkpoint", SHA_A)
    prepared = PreparedCheckpoint(
        preparation_id="preparation-1",
        checkpoint_ref=ResourceRef("checkpoint", "checkpoint-1", 1),
        operation=operation,
    )
    committed = PreparedCheckpoint(
        preparation_id=prepared.preparation_id,
        checkpoint_ref=prepared.checkpoint_ref,
        operation=operation,
        status=CheckpointWriteStatus.COMMITTED,
        duplicate=True,
    )
    envelope = ContextBuildEnvelope(
        build_id="build-1",
        execution_id="execution-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
        provider="openai",
        model="synthetic",
        budget=ContextBudget(8192, 2048, 512, 5632, 5068),
        status=ContextBuildStatus.COMPLETE,
    )
    build = ContextBuildReceipt(
        envelope=envelope,
        operation=OperationRef("operation-build", SHA_B),
        trigger_cursor=EventCursor(store_seq=1, event_id="event-trigger"),
        duplicate=True,
    )

    assert prepared.status is CheckpointWriteStatus.PREPARED
    assert committed.status is CheckpointWriteStatus.COMMITTED
    assert committed.duplicate is True
    assert build.envelope == envelope
    assert build.trigger_cursor == EventCursor(
        store_seq=1,
        event_id="event-trigger",
    )
    assert build.duplicate is True


def test_bound_journal_enforces_scope_and_idempotent_operations() -> None:
    journal = _FakeJournal("execution-1")
    first = journal.append(request=_event(operation_id="operation-1", payload_hash=SHA_A))
    replay = journal.append(request=_event(operation_id="operation-1", payload_hash=SHA_A))

    assert first.duplicate is False
    assert replay.duplicate is True
    assert replay.cursor == first.cursor
    assert replay.event == first.event
    assert first.event.store_seq == first.cursor.store_seq == 1
    assert journal.read().events == (first.event,)

    with pytest.raises(JournalConflictError):
        journal.append(request=_event(operation_id="operation-1", payload_hash=SHA_B))
    with pytest.raises(JournalScopeError):
        journal.append(
            request=_event(
                operation_id="operation-2",
                payload_hash=SHA_A,
                execution_id="execution-2",
            )
        )
    with pytest.raises(JournalScopeError):
        journal.read(after=EventCursor(1, "forged-event"))


def test_bound_workspace_enforces_scope_cas_and_idempotency() -> None:
    space = MemorySpace("space-1", "session:synthetic", "Synthetic", "", 1)
    repository = _FakeWorkspace(space)
    operation = OperationRef("operation-1", SHA_A)

    created = repository.compare_and_swap(
        entry=_entry(revision=1),
        expected_revision=None,
        operation=operation,
    )
    replay = repository.compare_and_swap(
        entry=_entry(revision=1),
        expected_revision=None,
        operation=operation,
    )
    assert replay == created
    assert repository.read_entry(ref=ResourceRef("memory", "entry-1", 1)) == created

    with pytest.raises(RepositoryScopeError):
        repository.read_entry(ref=ResourceRef("artifact", "entry-1", 1))
    with pytest.raises(RepositoryNotFoundError):
        repository.read_entry(ref=ResourceRef("memory", "entry-1", 2))

    with pytest.raises(RepositoryConflictError):
        repository.compare_and_swap(
            entry=_entry(revision=1),
            expected_revision=None,
            operation=OperationRef("operation-1", SHA_B),
        )
    with pytest.raises(RepositoryConflictError):
        repository.compare_and_swap(
            entry=_entry(revision=2),
            expected_revision=0,
            operation=OperationRef("operation-2", SHA_B),
        )
    with pytest.raises(RepositoryScopeError):
        repository.compare_and_swap(
            entry=_entry(space_id="space-2"),
            expected_revision=None,
            operation=OperationRef("operation-3", SHA_B),
        )


def test_bound_workspace_pagination_returns_a_valid_continuation() -> None:
    repository = _FakeWorkspace(
        MemorySpace("space-1", "session:synthetic", "Synthetic", "", 1)
    )
    repository.compare_and_swap(
        entry=_entry(entry_id="entry-1", path="/notes/a.md"),
        expected_revision=None,
        operation=OperationRef("operation-1", SHA_A),
    )
    repository.compare_and_swap(
        entry=_entry(entry_id="entry-2", path="/notes/b.md"),
        expected_revision=None,
        operation=OperationRef("operation-2", SHA_B),
    )

    first = repository.list_entries(limit=1)
    assert [entry.entry_id for entry in first.entries] == ["entry-1"]
    assert first.has_more is True
    assert first.next_cursor == "entry-1"
    second = repository.list_entries(limit=1, cursor=first.next_cursor)
    assert [entry.entry_id for entry in second.entries] == ["entry-2"]
    assert second.has_more is False
    assert second.next_cursor is None

    with pytest.raises(RepositoryScopeError):
        repository.list_entries(limit=1, cursor="foreign-entry")


def test_bound_workspace_cas_requires_exact_revision_evolution() -> None:
    space = MemorySpace("space-1", "session:synthetic", "Synthetic", "", 1)

    with pytest.raises(RepositoryConflictError):
        _FakeWorkspace(space).compare_and_swap(
            entry=_entry(revision=999),
            expected_revision=None,
            operation=OperationRef("operation-create-jump", SHA_A),
        )

    repository = _FakeWorkspace(space)
    repository.compare_and_swap(
        entry=_entry(revision=1),
        expected_revision=None,
        operation=OperationRef("operation-create", SHA_A),
    )
    with pytest.raises(RepositoryConflictError):
        repository.compare_and_swap(
            entry=_entry(revision=1),
            expected_revision=1,
            operation=OperationRef("operation-same", SHA_B),
        )
    with pytest.raises(RepositoryConflictError):
        repository.compare_and_swap(
            entry=_entry(revision=3),
            expected_revision=1,
            operation=OperationRef("operation-jump", SHA_B),
        )

    updated = repository.compare_and_swap(
        entry=_entry(revision=2),
        expected_revision=1,
        operation=OperationRef("operation-update", SHA_B),
    )
    assert updated.revision == 2
    assert repository.read_entry(ref=ResourceRef("memory", "entry-1", 2)) == updated


def test_context_ports_are_execution_bound() -> None:
    budget = ContextBudget(10_000, 1_000, 500, 8_500, 7_650)
    envelope = ContextBuildEnvelope(
        build_id="build-1",
        execution_id="execution-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
        provider="synthetic",
        model="synthetic",
        budget=budget,
        estimated_input_tokens=100,
        status=ContextBuildStatus.COMPLETE,
    )
    assert envelope.execution_id == "execution-1"
    assert inspect.isabstract(BoundArtifactRepository)
    assert inspect.isabstract(BoundCheckpointRepository)
    assert inspect.isabstract(BoundContextBuildRepository)
