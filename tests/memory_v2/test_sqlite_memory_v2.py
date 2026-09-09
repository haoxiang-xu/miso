from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest

from unchain.context import PinnedTaskState
from unchain.journal import OperationRef, ResourceRef
from unchain.memory.workspace import MemoryEntry, MemorySpace
from unchain.memory.workspace.operations import build_operation_ref
from unchain.memory.workspace.ports import (
    BoundMemoryWorkspaceRepository,
    BoundPinnedTaskStateRepository,
    BoundWorkspaceContentRepository,
    BoundWorkspaceHistoryRepository,
    BoundWorkspaceLinkRepository,
    BoundWorkspaceMutationRepository,
    RepositoryConflictError,
    RepositoryScopeError,
    RepositorySearchUnavailableError,
    WorkspaceMutationRequest,
)
from unchain.memory.workspace.service import MemoryWorkspaceService
from unchain.memory.workspace.task_state import TaskStateService
from unchain.persistence.sqlite_memory_v2 import (
    SQLiteMemoryV2Store,
    _canonical_json_bytes,
    _path_key,
    _sha256,
)
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store

from .fakes import FakeReferenceAuthorizer, entry_ref, source_event


def _space() -> MemorySpace:
    return MemorySpace(
        "space-chat-sqlite",
        "chat",
        "SQLite chat memory",
        "Durable chat workspace fixture",
        1,
    )


def _build(
    root: Path,
    *,
    space: MemorySpace | None = None,
    owner_chat_id: str = "chat-sqlite",
):
    store = SQLiteMemoryV2Store(
        database_path=root / "context_v2.sqlite3",
        object_directory=root / "objects",
    )
    repository = store.bind_workspace(
        space=space or _space(),
        owner_chat_id=owner_chat_id,
    )
    event = source_event()
    service = MemoryWorkspaceService(
        repository=repository,
        mutations=repository,
        content=repository,
        history=repository,
        links=repository,
        references=FakeReferenceAuthorizer(owner_chat_id, {event}),
    )
    return store, repository, service, event


def test_sqlite_workspace_uses_the_context_database_wal_and_bound_ports(
    tmp_path: Path,
) -> None:
    database = tmp_path / "context_v2.sqlite3"
    objects = tmp_path / "objects"
    SQLiteContextV2Store(database_path=database, object_directory=objects)

    store, repository, _, _ = _build(tmp_path)

    assert store.database_path == database
    assert store.object_directory == objects
    assert isinstance(repository, BoundMemoryWorkspaceRepository)
    assert isinstance(repository, BoundWorkspaceMutationRepository)
    assert isinstance(repository, BoundWorkspaceContentRepository)
    assert isinstance(repository, BoundWorkspaceHistoryRepository)
    assert isinstance(repository, BoundWorkspaceLinkRepository)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
    assert {
        "spaces",
        "entries",
        "entry_revisions",
        "links",
        "memory_operation_receipts",
        "index_state",
        "objects",
    } <= tables


def test_sqlite_workspace_create_update_move_archive_history_and_restart(
    tmp_path: Path,
) -> None:
    _, repository, service, event = _build(tmp_path)
    created = service.write_markdown(
        path="/notes/SQLite Design.md",
        description="Initial durable workspace design",
        content="revision one",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-lifecycle-create",
    )
    updated = service.write_markdown(
        entry_ref=entry_ref(created),
        path=created.path,
        description="Updated durable workspace design",
        content="revision two",
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="sqlite-lifecycle-update",
    )
    moved = service.move(
        ref=entry_ref(updated),
        new_path="/decisions/SQLite Design.md",
        expected_space_revision=3,
        source_refs=(event,),
        operation_id="sqlite-lifecycle-move",
    )
    archived = service.archive(
        ref=entry_ref(moved),
        expected_space_revision=4,
        source_refs=(event,),
        operation_id="sqlite-lifecycle-archive",
    )

    assert archived.deleted is True
    assert [
        item.revision for item in service.history(entry_ref(archived), limit=10)
    ] == [
        4,
        3,
        2,
        1,
    ]
    assert repository.space.revision == 5

    _, reopened, reopened_service, _ = _build(tmp_path)
    assert reopened.space.revision == 5
    assert reopened.read_current_entry(entry_id=created.entry_id) == archived
    assert reopened_service.read(entry_ref(updated)).data == b"revision two"
    assert reopened_service.list().entries == ()
    assert reopened_service.list(include_deleted=True).entries == (archived,)


def test_sqlite_workspace_content_is_cas_backed_and_paginated(tmp_path: Path) -> None:
    _, _, service, event = _build(tmp_path)
    payload = ("Memory V2 durable body.\n" * 200).encode("utf-8")
    entry = service.write_markdown(
        path="/notes/Paginated Body.md",
        description="Large body kept outside SQLite rows",
        content=payload,
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-content-cas",
    )

    first = service.read(entry_ref(entry), offset=0, limit=97)
    second = service.read(entry_ref(entry), offset=first.next_offset, limit=113)
    digest = hashlib.sha256(payload).hexdigest()

    assert first.data + second.data == payload[:210]
    assert first.total_bytes == len(payload)
    assert (tmp_path / "objects" / digest).read_bytes() == payload
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        row = connection.execute(
            """
            SELECT object_sha256, byte_length
            FROM entry_revisions
            WHERE space_id = ? AND entry_id = ? AND revision = ?
            """,
            (entry.space_id, entry.entry_id, entry.revision),
        ).fetchone()
        columns = {
            item[1] for item in connection.execute("PRAGMA table_info(entry_revisions)")
        }
    assert row == (digest, len(payload))
    assert not {"content", "content_blob", "payload"} & columns


def test_sqlite_workspace_operation_replay_and_payload_drift(tmp_path: Path) -> None:
    _, _, service, event = _build(tmp_path)
    arguments = {
        "path": "/notes/Idempotent Write.md",
        "description": "Idempotent durable mutation",
        "content": "same bytes",
        "expected_space_revision": 1,
        "source_refs": (event,),
        "operation_id": "sqlite-idempotent-write",
    }

    created = service.write_markdown(**arguments)
    assert service.write_markdown(**arguments) == created
    with pytest.raises(RepositoryConflictError, match="operation"):
        service.write_markdown(**{**arguments, "content": "changed bytes"})


def test_sqlite_workspace_space_and_entry_cas_reject_stale_writers(
    tmp_path: Path,
) -> None:
    _, _, first, event = _build(tmp_path)
    _, _, stale, _ = _build(tmp_path)
    created = first.write_markdown(
        path="/notes/CAS Baseline.md",
        description="Baseline for concurrent revision protection",
        content="one",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-cas-baseline",
    )
    assert stale.space.revision == 2

    with pytest.raises(RepositoryConflictError, match="space revision"):
        stale.write_markdown(
            path="/notes/Stale Writer.md",
            description="This writer has a stale space revision",
            content="stale",
            expected_space_revision=1,
            source_refs=(event,),
            operation_id="sqlite-cas-stale-space",
        )

    current_store, current_repo, current, _ = _build(tmp_path)
    advanced = current.write_markdown(
        entry_ref=entry_ref(created),
        path=created.path,
        description="Advanced baseline",
        content="two",
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="sqlite-cas-advance",
    )
    stale_entry_payload = created.to_dict()
    stale_entry_payload.update(revision=2, updated_seq=4)
    stale_entry = MemoryEntry.from_dict(stale_entry_payload)
    operation = build_operation_ref(
        "sqlite-cas-stale-entry",
        domain="workspace.entry.direct-test",
        payload={"entry": stale_entry.to_dict()},
    )
    with pytest.raises(RepositoryConflictError, match="entry revision"):
        current_repo.compare_and_swap(
            entry=stale_entry,
            expected_revision=1,
            operation=operation,
        )
    assert (
        current_store.bind_workspace(
            space=_space(), owner_chat_id="chat-sqlite"
        ).read_current_entry(entry_id=created.entry_id)
        == advanced
    )


@pytest.mark.parametrize("changed_field", ("resource_id", "revision", "fragment"))
def test_sqlite_workspace_metadata_update_rejects_a_forged_content_reference(
    tmp_path: Path,
    changed_field: str,
) -> None:
    _, repository, service, event = _build(tmp_path)
    created = service.write_markdown(
        path="/notes/Content Reference Authority.md",
        description="Durable content reference baseline",
        content="canonical durable bytes",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-content-ref-baseline",
    )
    assert created.content_ref is not None
    forged_ref_data = created.content_ref.to_dict()
    if changed_field == "resource_id":
        forged_ref_data["id"] = "forged-content"
    elif changed_field == "revision":
        forged_ref_data["revision"] = created.content_ref.revision + 1
    else:
        forged_ref_data["fragment"] = "space-forged"
    forged_entry_data = created.to_dict()
    forged_entry_data.update(
        revision=2,
        updated_seq=3,
        description="Metadata-only update with a forged durable reference",
        content_ref=forged_ref_data,
    )
    forged_entry = MemoryEntry.from_dict(forged_entry_data)
    request = WorkspaceMutationRequest(
        entry=forged_entry,
        expected_revision=1,
        expected_space_revision=2,
        operation=build_operation_ref(
            f"sqlite-forged-content-ref-{changed_field}",
            domain="workspace.entry.direct-test",
            payload={"entry": forged_entry.to_dict()},
        ),
    )

    with pytest.raises(RepositoryConflictError, match="content reference"):
        repository.apply(request=request)

    assert repository.space.revision == 2
    assert repository.read_current_entry(entry_id=created.entry_id) == created
    assert service.read(entry_ref(created)).data == b"canonical durable bytes"


def test_sqlite_workspace_metadata_update_preserves_exact_content_ref_and_replays(
    tmp_path: Path,
) -> None:
    _, repository, service, event = _build(tmp_path)
    created = service.write_markdown(
        path="/notes/Metadata Replay.md",
        description="Durable content reference baseline",
        content="unchanged durable bytes",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-metadata-replay-baseline",
    )
    updated_data = created.to_dict()
    updated_data.update(
        revision=2,
        updated_seq=3,
        description="Metadata changed while bytes stay unchanged",
    )
    updated = MemoryEntry.from_dict(updated_data)
    request = WorkspaceMutationRequest(
        entry=updated,
        expected_revision=1,
        expected_space_revision=2,
        operation=build_operation_ref(
            "sqlite-metadata-replay-update",
            domain="workspace.entry.direct-test",
            payload={"entry": updated.to_dict()},
        ),
    )

    persisted = repository.apply(request=request)

    assert persisted.content_ref == created.content_ref
    assert repository.apply(request=request) == persisted
    assert service.read(entry_ref(persisted)).data == b"unchanged durable bytes"


def test_sqlite_workspace_recursive_folder_archive_is_revisioned(
    tmp_path: Path,
) -> None:
    _, repository, service, event = _build(tmp_path)
    folder = service.create_folder(
        path="/research",
        description="Research material grouped recursively",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-folder-create",
    )
    nested = service.create_folder(
        path="/research/context",
        description="Context compiler research notes",
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="sqlite-nested-folder-create",
    )
    child = service.write_markdown(
        path="/research/context/Pressure.md",
        description="Pressure threshold research",
        content="compress after ninety percent",
        expected_space_revision=3,
        source_refs=(event,),
        operation_id="sqlite-folder-child",
    )

    with pytest.raises(RepositoryConflictError, match="descendant"):
        service.archive(
            ref=entry_ref(folder),
            expected_space_revision=4,
            source_refs=(event,),
            operation_id="sqlite-folder-nonrecursive",
        )
    archived = service.archive(
        ref=entry_ref(folder),
        expected_space_revision=4,
        source_refs=(event,),
        recursive=True,
        operation_id="sqlite-folder-recursive",
    )

    assert archived.deleted is True
    nested_head = repository.read_current_entry(entry_id=nested.entry_id)
    child_head = repository.read_current_entry(entry_id=child.entry_id)
    assert (nested_head.deleted, nested_head.revision) == (True, 2)
    assert (child_head.deleted, child_head.revision) == (True, 2)
    assert [
        item.revision
        for item in repository.list_revisions(ref=entry_ref(child_head), limit=10)
    ] == [2, 1]


def test_sqlite_workspace_lists_direct_children_with_stable_snapshot_pages(
    tmp_path: Path,
) -> None:
    _, repository, service, event = _build(tmp_path)
    folder = service.create_folder(
        path="/research",
        description="Research root",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="children-folder",
    )
    service.write_markdown(
        path="/research/Alpha.md",
        description="Alpha",
        content="alpha",
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="children-alpha",
    )
    service.write_markdown(
        path="/research/Beta.md",
        description="Beta",
        content="beta",
        expected_space_revision=3,
        source_refs=(event,),
        operation_id="children-beta",
    )
    service.write_markdown(
        path="/missing/Orphan.md",
        description="Orphan",
        content="orphan",
        expected_space_revision=4,
        source_refs=(event,),
        operation_id="children-orphan",
    )

    revision = repository.space.revision
    root = repository.list_children(
        parent_path="/", expected_space_revision=revision, limit=10
    )
    assert [item.entry.entry_id for item in root.entries] == [
        root.entries[0].entry.entry_id,
        folder.entry_id,
    ]
    assert root.entries[0].entry.path == "/missing/Orphan.md"
    assert root.entries[0].orphaned is True
    assert root.entries[1].has_children is True

    first = repository.list_children(
        parent_path="/research", expected_space_revision=revision, limit=1
    )
    second = repository.list_children(
        parent_path="/research",
        expected_space_revision=revision,
        limit=1,
        cursor=first.next_cursor,
    )
    assert first.has_more is True
    assert [item.entry.name for item in first.entries + second.entries] == [
        "Alpha.md",
        "Beta.md",
    ]
    with pytest.raises(RepositoryConflictError, match="space revision"):
        repository.list_children(
            parent_path="/research",
            expected_space_revision=revision - 1,
            limit=10,
        )


def test_sqlite_workspace_pages_ten_thousand_direct_children_without_materializing_them(
    tmp_path: Path,
) -> None:
    _, repository, service, event = _build(tmp_path)
    template = service.create_folder(
        path="/template",
        description="Bulk fixture template",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="children-bulk-template",
    )
    entry_rows = []
    revision_rows = []
    for index in range(10_000):
        entry_id = f"bulk-{index:05d}"
        path = f"/bulk/{index:05d}"
        payload = template.to_dict()
        payload.update(entry_id=entry_id, path=path, name=f"{index:05d}")
        encoded = _canonical_json_bytes(payload)
        key = _path_key(path)
        entry_rows.append(
            (repository.space.space_id, entry_id, 1, key, f"{index:05d}", 0, 1)
        )
        revision_rows.append(
            (
                repository.space.space_id,
                entry_id,
                1,
                key,
                f"bulk-{index:05d}",
                encoded,
                _sha256(encoded),
                None,
                None,
                None,
            )
        )
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        connection.executemany(
            "INSERT INTO entries(space_id,entry_id,current_revision,path_key,name_key,deleted,updated_seq) VALUES (?,?,?,?,?,?,?)",
            entry_rows,
        )
        connection.executemany(
            "INSERT INTO entry_revisions(space_id,entry_id,revision,path_key,operation_id,entry_json,entry_sha256,content_resource_id,object_sha256,byte_length) VALUES (?,?,?,?,?,?,?,?,?,?)",
            revision_rows,
        )

    first = repository.list_children(
        parent_path="/bulk",
        expected_space_revision=repository.space.revision,
        limit=2,
    )
    second = repository.list_children(
        parent_path="/bulk",
        expected_space_revision=repository.space.revision,
        limit=2,
        cursor=first.next_cursor,
    )

    assert [item.entry.name for item in first.entries] == ["00000", "00001"]
    assert [item.entry.name for item in second.entries] == ["00002", "00003"]
    assert first.has_more is True


def test_sqlite_projection_receipts_are_current_restart_stable_and_partial_on_miss(
    tmp_path: Path,
) -> None:
    _, repository, service, event = _build(tmp_path)
    entry = service.write_markdown(
        path="/Projection.md",
        description="Projection receipt",
        content="same embedding batch",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="projection-entry",
    )
    ref = entry_ref(entry)
    repository.supersede_projection(
        entry_ref=ref,
        deleted=False,
        backend_identity="backend-test",
    )
    repository.commit_projection(
        entry_ref=ref,
        backend_identity="backend-test",
        chunker_version="unchain.workspace_index.v1",
        basis_id="basis-test",
        basis_version=1,
        algorithm="cosine_hash_2d_v1",
        dimension=3,
        content_digest="a" * 64,
        points=(
            {
                "chunk_id": "chunk-test-0",
                "ordinal": 0,
                "x": 0.25,
                "y": -0.5,
                "embedding_digest": "b" * 64,
                "external_receipt_id": "external-test-0",
            },
        ),
    )
    page = repository.list_projection_points(
        backend_identity="backend-test",
        chunker_version="unchain.workspace_index.v1",
        basis_id="basis-test",
        basis_version=1,
        algorithm="cosine_hash_2d_v1",
        dimension=3,
        corpus_epoch=2,
    )
    assert page.status == "complete"
    assert [(point.chunk_id, point.x, point.y) for point in page.points] == [
        ("chunk-test-0", 0.25, -0.5)
    ]

    _, reopened, reopened_service, _ = _build(tmp_path)
    repeated = reopened.list_projection_points(
        backend_identity="backend-test",
        chunker_version="unchain.workspace_index.v1",
        basis_id="basis-test",
        basis_version=1,
        algorithm="cosine_hash_2d_v1",
        dimension=3,
        corpus_epoch=2,
    )
    assert repeated == page
    reopened_service.write_markdown(
        entry_ref=ref,
        path=entry.path,
        description=entry.description,
        content="new revision without a receipt",
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="projection-entry-update",
    )
    partial = reopened.list_projection_points(
        backend_identity="backend-test",
        chunker_version="unchain.workspace_index.v1",
        basis_id="basis-test",
        basis_version=1,
        algorithm="cosine_hash_2d_v1",
        dimension=3,
        corpus_epoch=3,
    )
    assert partial.status == "partial"
    assert partial.points == ()


@pytest.mark.parametrize(
    "second_path",
    (
        "/notes/CAFÉ.md",
        "/notes/Cafe\u0301.md",
        "/notes/Ｆｕｌｌｗｉｄｔｈ.md",
    ),
)
def test_sqlite_workspace_rejects_unicode_and_casefold_path_collisions(
    tmp_path: Path,
    second_path: str,
) -> None:
    _, _, service, event = _build(tmp_path)
    first_path = "/notes/Fullwidth.md" if "Ｆ" in second_path else "/notes/Café.md"
    service.write_markdown(
        path=first_path,
        description="Canonical path collision baseline",
        content="one",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-unicode-baseline",
    )

    with pytest.raises(RepositoryConflictError, match="path collision"):
        service.write_markdown(
            path=second_path,
            description="Conflicting canonical path",
            content="two",
            expected_space_revision=2,
            source_refs=(event,),
            operation_id="sqlite-unicode-conflict",
        )


def test_sqlite_workspace_fts_exact_and_description_search_survive_restart(
    tmp_path: Path,
) -> None:
    _, _, service, event = _build(tmp_path)
    roadmap = service.write_markdown(
        path="/plans/Release Roadmap.md",
        description="Canary milestones and recovery gates",
        content="shadow then canary",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-search-roadmap",
    )
    service.write_markdown(
        path="/notes/Unrelated.md",
        description="Other durable notes",
        content="unrelated",
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="sqlite-search-unrelated",
    )

    _, repository, _, _ = _build(tmp_path)
    assert repository.search(query="/plans/Release Roadmap.md", limit=10)[0] == roadmap
    assert repository.search(query="Release Roadmap.md", limit=10)[0] == roadmap
    assert repository.search(query="recovery gates", limit=10)[0] == roadmap


def test_sqlite_workspace_fts_outage_falls_back_without_blocking_io(
    tmp_path: Path,
) -> None:
    _, repository, service, event = _build(tmp_path)
    existing = service.write_markdown(
        path="/notes/FTS Fallback.md",
        description="Lexical fallback when FTS is offline",
        content="durable body",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-fts-existing",
    )
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        connection.execute("DROP TABLE workspace_entries_fts")

    created = service.write_markdown(
        path="/notes/Still Writable.md",
        description="Writes remain available without FTS",
        content="still durable",
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="sqlite-fts-offline-write",
    )

    assert service.read(entry_ref(created)).data == b"still durable"
    assert {item.entry_id for item in service.list().entries} == {
        existing.entry_id,
        created.entry_id,
    }
    with pytest.raises(RepositorySearchUnavailableError):
        repository.search(query="durable", limit=10)
    fallback = service.search("Lexical fallback", limit=10)
    assert fallback.hits[0].entry == existing
    assert fallback.lexical_fallback is True


def test_sqlite_workspace_links_backlinks_replay_and_scope(tmp_path: Path) -> None:
    _, repository, service, event = _build(tmp_path)
    source = service.write_markdown(
        path="/notes/Decision Source.md",
        description="Source architecture decision",
        content="source",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="sqlite-link-source",
    )
    target = service.write_markdown(
        path="/notes/Evidence Target.md",
        description="Evidence for the architecture decision",
        content="target",
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="sqlite-link-target",
    )
    link = service.link(
        source_ref=entry_ref(source),
        target_ref=entry_ref(target),
        relation="supports",
        expected_space_revision=3,
        source_refs=(event,),
        operation_id="sqlite-link-create",
    )
    replay = service.link(
        source_ref=entry_ref(source),
        target_ref=entry_ref(target),
        relation="supports",
        expected_space_revision=3,
        source_refs=(event,),
        operation_id="sqlite-link-create",
    )

    assert replay == link
    assert repository.list_links(source_entry_ref=entry_ref(source)) == (link,)
    assert repository.list_backlinks(target_entry_ref=entry_ref(target)) == (link,)
    foreign = SQLiteMemoryV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    ).bind_workspace(
        space=MemorySpace("space-foreign", "chat", "Foreign", "Foreign workspace", 1),
        owner_chat_id="chat-foreign",
    )
    with pytest.raises(RepositoryScopeError):
        foreign.read_entry(ref=entry_ref(source))
    with pytest.raises(RepositoryScopeError):
        foreign.list_backlinks(target_entry_ref=entry_ref(target))


def test_sqlite_workspace_owner_binding_cannot_be_rebound(tmp_path: Path) -> None:
    store, _, _, _ = _build(tmp_path)

    with pytest.raises(RepositoryScopeError, match="owner"):
        store.bind_workspace(space=_space(), owner_chat_id="another-chat")


def test_sqlite_workspace_concurrent_space_writes_have_one_cas_winner(
    tmp_path: Path,
) -> None:
    _, _, first, event = _build(tmp_path)
    _, _, second, _ = _build(tmp_path)
    barrier = threading.Barrier(2)
    successes = []
    failures = []

    def write(service: MemoryWorkspaceService, ordinal: int) -> None:
        barrier.wait()
        try:
            successes.append(
                service.write_markdown(
                    path=f"/notes/Concurrent {ordinal}.md",
                    description=f"Concurrent writer number {ordinal}",
                    content=f"writer {ordinal}",
                    expected_space_revision=1,
                    source_refs=(event,),
                    operation_id=f"sqlite-concurrent-{ordinal}",
                )
            )
        except Exception as exc:  # noqa: BLE001 - the assertion diagnoses type below
            failures.append(exc)

    threads = (
        threading.Thread(target=write, args=(first, 1)),
        threading.Thread(target=write, args=(second, 2)),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], RepositoryConflictError)
    assert _build(tmp_path)[1].space.revision == 2


def test_sqlite_pinned_task_state_is_cas_idempotent_and_restart_safe(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    )
    repository = store.bind_task_state(binding_id="chat-sqlite")
    assert isinstance(repository, BoundPinnedTaskStateRepository)
    state = PinnedTaskState(
        state_id=repository.state_id,
        revision=1,
        objective="Ship Memory V2 P0",
        source_event_refs=(source_event(),),
    )
    operation = OperationRef("sqlite-task-state-create", "a" * 64)

    assert (
        repository.compare_and_swap(
            state=state,
            expected_revision=None,
            operation=operation,
        )
        == state
    )
    assert repository.replay(operation=operation) == state
    with pytest.raises(RepositoryConflictError, match="operation"):
        repository.replay(operation=OperationRef("sqlite-task-state-create", "b" * 64))

    reopened = SQLiteMemoryV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    ).bind_task_state(binding_id="chat-sqlite")
    assert reopened.current() == state
    with pytest.raises(RepositoryConflictError, match="revision"):
        reopened.compare_and_swap(
            state=state,
            expected_revision=None,
            operation=OperationRef("sqlite-task-state-stale", "c" * 64),
        )


def test_sqlite_task_state_service_round_trip(tmp_path: Path) -> None:
    store = SQLiteMemoryV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    )
    repository = store.bind_task_state(binding_id="chat-sqlite-service")
    event = source_event()
    service = TaskStateService(
        repository=repository,
        references=FakeReferenceAuthorizer("chat-sqlite-service", {event}),
    )

    created = service.update(
        expected_revision=None,
        patch={"objective": "Keep the full task picture pinned"},
        source_event_refs=(event,),
        operation_id="sqlite-task-service-create",
    )
    reopened = store.bind_task_state(binding_id="chat-sqlite-service")

    assert reopened.current() == created
