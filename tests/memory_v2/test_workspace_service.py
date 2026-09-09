from __future__ import annotations

import inspect
import json
from dataclasses import replace

import pytest

from unchain.context import PinnedTaskState
from unchain.journal import ModelValidationError, OperationRef, ResourceRef
from unchain.memory.workspace import MemoryEntry, MemoryEntryKind, MemorySpace
from unchain.memory.workspace.ports import (
    BoundPinnedTaskStateRepository,
    BoundPromotionDecisionRepository,
    BoundPromotionRepository,
    BoundWorkspaceContentRepository,
    BoundWorkspaceHistoryRepository,
    BoundWorkspaceLinkRepository,
    BoundWorkspaceMutationRepository,
    BoundWorkspaceReferenceAuthorizer,
    RepositoryConflictError,
    RepositoryScopeError,
    WorkspaceRepositoryError,
)
from unchain.memory.workspace.service import MemoryWorkspaceService, WorkspaceWriteDraft
from unchain.memory.workspace.task_state import TaskStateService

from .fakes import (
    FakeReferenceAuthorizer,
    FakeTaskStateRepository,
    FakeWorkspaceRepository,
    entry_ref,
    source_event,
)


def build_service() -> tuple[
    MemoryWorkspaceService,
    FakeWorkspaceRepository,
    FakeReferenceAuthorizer,
]:
    space = MemorySpace("space-chat", "chat", "Chat memory", "Synthetic", 1)
    repository = FakeWorkspaceRepository(space)
    event = source_event()
    authorizer = FakeReferenceAuthorizer("chat-binding", {event})
    service = MemoryWorkspaceService(
        repository=repository,
        mutations=repository,
        content=repository,
        history=repository,
        links=repository,
        references=authorizer,
    )
    return service, repository, authorizer


def test_workspace_redacts_before_hashing_persistence_and_return() -> None:
    space = MemorySpace("space-chat", "chat", "Chat memory", "Synthetic", 1)
    repository = FakeWorkspaceRepository(space)
    event = source_event()
    authorizer = FakeReferenceAuthorizer("chat-binding", {event})
    observed: list[WorkspaceWriteDraft] = []

    def redact(draft: WorkspaceWriteDraft) -> WorkspaceWriteDraft:
        observed.append(draft)
        return replace(
            draft,
            description=draft.description.replace("sk-secret", "[REDACTED]"),
            content=(
                draft.content.replace(b"sk-secret", b"[REDACTED]")
                if draft.content is not None
                else None
            ),
        )

    service = MemoryWorkspaceService(
        repository=repository,
        mutations=repository,
        content=repository,
        history=repository,
        links=repository,
        references=authorizer,
        content_redactor=redact,
    )
    arguments = {
        "path": "/notes/safe.md",
        "description": "Decision contained sk-secret",
        "content": "token=sk-secret",
        "expected_space_revision": 1,
        "source_refs": (event,),
        "operation_id": "redacted-write",
    }

    created = service.write_markdown(**arguments)
    replayed = service.write_markdown(**arguments)

    assert len(observed) == 2
    assert created == replayed
    assert created.description == "Decision contained [REDACTED]"
    content_key = (created.content_ref.resource_id, created.content_ref.revision)
    assert repository.contents[content_key] == b"token=[REDACTED]"
    assert b"sk-secret" not in repository.contents[content_key]


def test_workspace_redactor_failure_or_invalid_output_never_mutates() -> None:
    space = MemorySpace("space-chat", "chat", "Chat memory", "Synthetic", 1)
    event = source_event()
    authorizer = FakeReferenceAuthorizer("chat-binding", {event})

    for redactor in (
        lambda _draft: object(),
        lambda _draft: (_ for _ in ()).throw(RuntimeError("secret detail")),
    ):
        repository = FakeWorkspaceRepository(space)
        service = MemoryWorkspaceService(
            repository=repository,
            mutations=repository,
            content=repository,
            history=repository,
            links=repository,
            references=authorizer,
            content_redactor=redactor,
        )
        with pytest.raises(WorkspaceRepositoryError, match="redaction failed") as exc:
            service.write_markdown(
                path="/notes/fail.md",
                description="Never persisted",
                content="secret",
                expected_space_revision=1,
                source_refs=(event,),
                operation_id="failed-redaction",
            )
        assert "secret detail" not in str(exc.value)
        assert repository.entries == {}


def test_workspace_creates_folder_markdown_image_and_link_entries() -> None:
    service, repository, _ = build_service()
    source = source_event()

    folder = service.create_folder(
        path="/notes",
        description="Notes grouped by topic",
        expected_space_revision=1,
        source_refs=(source,),
        operation_id="create-folder",
    )
    markdown = service.write_markdown(
        path="/notes/design.md",
        description="Decisions for the memory workspace design",
        content="# Design\nKeep the service provider-neutral.",
        expected_space_revision=2,
        source_refs=(source,),
        operation_id="create-markdown",
    )
    image = service.write_image(
        path="/images/architecture.png",
        description="Architecture diagram for the workspace",
        content=b"\x89PNG\r\nsynthetic",
        media_type="image/png",
        expected_space_revision=3,
        source_refs=(source,),
        operation_id="create-image",
    )
    link = service.create_link(
        path="/links/reference.md",
        description="Reference documentation for the design",
        url="https://example.test/memory-v2",
        expected_space_revision=4,
        source_refs=(source,),
        operation_id="create-link",
    )

    assert [folder.kind, markdown.kind, image.kind, link.kind] == [
        MemoryEntryKind.FOLDER,
        MemoryEntryKind.MARKDOWN,
        MemoryEntryKind.IMAGE,
        MemoryEntryKind.LINK,
    ]
    assert markdown.name == "design.md"
    assert markdown.content_ref is not None
    assert image.media_type == "image/png"
    assert link.link_url == "https://example.test/memory-v2"
    assert all(entry.source_refs == (source,) for entry in repository.entries.values())


def test_workspace_operation_hash_is_bound_to_the_actual_mutation_payload() -> None:
    service, repository, _ = build_service()
    arguments = {
        "path": "/notes/idempotency.md",
        "description": "Idempotency semantics for workspace writes",
        "content": "first payload",
        "expected_space_revision": 1,
        "source_refs": (source_event(),),
        "operation_id": "same-operation",
    }

    created = service.write_markdown(**arguments)
    replay = service.write_markdown(**arguments)

    assert replay == created
    with pytest.raises(RepositoryConflictError):
        service.write_markdown(**{**arguments, "content": "different payload"})
    with pytest.raises(RepositoryConflictError):
        service.write_markdown(
            **{
                **arguments,
                "path": "/notes/different-path.md",
                "description": "A changed path with the same operation identifier",
            }
        )
    assert repository.entries[created.entry_id] == created


def test_workspace_normalizes_media_and_link_metadata_before_operation_hashing() -> None:
    image_service, _, _ = build_service()
    image_arguments = {
        "path": "/images/normalized.png",
        "description": "Normalized image metadata",
        "content": b"synthetic-image",
        "expected_space_revision": 1,
        "source_refs": (source_event(),),
        "operation_id": "normalized-image-operation",
    }
    image = image_service.write_image(
        **image_arguments,
        media_type=" IMAGE/PNG ",
    )
    assert image.media_type == "image/png"
    assert image_service.write_image(
        **image_arguments,
        media_type="image/png",
    ) == image

    link_service, _, _ = build_service()
    link_arguments = {
        "path": "/links/normalized.md",
        "description": "Normalized link metadata",
        "expected_space_revision": 1,
        "source_refs": (source_event(),),
        "operation_id": "normalized-link-operation",
    }
    link = link_service.create_link(
        **link_arguments,
        url=" https://example.test/reference ",
    )
    assert link.link_url == "https://example.test/reference"
    assert link_service.create_link(
        **link_arguments,
        url="https://example.test/reference",
    ) == link


def test_every_semantic_entry_revision_requires_fresh_current_provenance() -> None:
    service, _, references = build_service()
    first_source = source_event()
    second_source = source_event("event-current-revision")
    references.allowed.add(second_source)
    created = service.write_markdown(
        path="/notes/provenance.md",
        description="Revision provenance",
        content="v1",
        expected_space_revision=1,
        source_refs=(first_source,),
        operation_id="provenance-v1",
    )

    with pytest.raises(ModelValidationError, match="provenance"):
        service.write_markdown(
            entry_ref=entry_ref(created),
            path=created.path,
            description="Changed without evidence",
            content="v2",
            expected_space_revision=2,
            source_refs=(),
            operation_id="provenance-missing",
        )

    updated = service.write_markdown(
        entry_ref=entry_ref(created),
        path=created.path,
        description="Changed with current evidence",
        content="v2",
        expected_space_revision=2,
        source_refs=(second_source,),
        operation_id="provenance-v2",
    )
    moved = service.move(
        ref=entry_ref(updated),
        new_path="/notes/provenance-moved.md",
        expected_space_revision=3,
        source_refs=(second_source,),
        operation_id="provenance-move",
    )
    archived = service.archive(
        ref=entry_ref(moved),
        expected_space_revision=4,
        source_refs=(second_source,),
        operation_id="provenance-archive",
    )

    assert created.source_refs == (first_source,)
    assert updated.source_refs == (second_source,)
    assert moved.source_refs == (second_source,)
    assert archived.source_refs == (second_source,)


def test_workspace_rejects_a_repository_that_changes_persisted_entry_fields() -> None:
    service, repository, _ = build_service()
    repository.diverge_persisted_description = True

    with pytest.raises(RuntimeError, match="divergent"):
        service.write_markdown(
            path="/notes/divergence.md",
            description="Expected description",
            content="safe",
            expected_space_revision=1,
            source_refs=(source_event(),),
            operation_id="divergent-repository",
        )


def test_workspace_rejects_a_divergent_outgoing_update_sequence_before_mutation_or_index() -> None:
    service, repository, _ = build_service()
    mutation_calls = []
    index_calls = []

    def accept_divergent_request(*, request):
        mutation_calls.append(request)
        return request.entry

    def record_index(entry, *, content=None):
        index_calls.append((entry, content))
        return True

    repository.apply = accept_divergent_request
    service._search.index_entry = record_index
    entry = MemoryEntry(
        entry_id="memory-divergent-outgoing-sequence",
        space_id=repository.space.space_id,
        path="/notes/divergent-outgoing-sequence",
        name="divergent-outgoing-sequence",
        description="Outgoing sequence validation",
        kind=MemoryEntryKind.FOLDER,
        revision=1,
        updated_seq=3,
        source_refs=(source_event(),),
    )

    with pytest.raises(WorkspaceRepositoryError, match="divergent"):
        service._apply(
            entry=entry,
            expected_revision=None,
            expected_space_revision=1,
            operation=OperationRef("divergent-outgoing-sequence", "a" * 64),
        )

    assert mutation_calls == []
    assert index_calls == []


def test_workspace_rejects_a_divergent_persisted_update_sequence() -> None:
    service, repository, _ = build_service()
    persist = repository.apply
    index_calls = []

    def persist_with_divergent_sequence(*, request):
        entry = persist(request=request)
        return replace(entry, updated_seq=entry.updated_seq + 1)

    def record_index(entry, *, content=None):
        index_calls.append((entry, content))
        return True

    repository.apply = persist_with_divergent_sequence
    service._search.index_entry = record_index

    with pytest.raises(RuntimeError, match="divergent"):
        service.write_markdown(
            path="/notes/divergent-sequence.md",
            description="Persisted sequence validation",
            content="safe",
            expected_space_revision=1,
            source_refs=(source_event(),),
            operation_id="divergent-sequence",
        )

    assert index_calls == []


def test_workspace_revision_move_archive_and_history_are_cas_protected() -> None:
    service, repository, _ = build_service()
    source = source_event()
    created = service.write_markdown(
        path="/notes/draft.md",
        description="Draft implementation notes",
        content="v1",
        expected_space_revision=1,
        source_refs=(source,),
        operation_id="create-draft",
    )
    updated = service.write_markdown(
        entry_ref=entry_ref(created),
        path=created.path,
        description="Updated implementation notes",
        content="v2",
        expected_space_revision=2,
        source_refs=(source,),
        operation_id="update-draft",
    )
    moved = service.move(
        ref=entry_ref(updated),
        new_path="/notes/final-design.md",
        expected_space_revision=3,
        source_refs=(source,),
        operation_id="move-draft",
    )
    archived = service.archive(
        ref=entry_ref(moved),
        expected_space_revision=4,
        source_refs=(source,),
        operation_id="archive-draft",
    )

    assert [created.revision, updated.revision, moved.revision, archived.revision] == [
        1,
        2,
        3,
        4,
    ]
    assert moved.path == "/notes/final-design.md"
    assert moved.name == "final-design.md"
    assert archived.deleted is True
    assert [entry.revision for entry in service.history(entry_ref(archived), limit=4)] == [
        4,
        3,
        2,
        1,
    ]

    with pytest.raises((RepositoryConflictError, RepositoryScopeError)):
        service.move(
            ref=entry_ref(created),
            new_path="/notes/stale.md",
            expected_space_revision=5,
            source_refs=(source,),
            operation_id="stale-move",
        )
    assert repository.entries[created.entry_id].revision == 4


def test_workspace_relation_links_are_scope_bound_cas_and_idempotent() -> None:
    service, repository, _ = build_service()
    source = source_event()
    first = service.write_markdown(
        path="/notes/decision.md",
        description="The selected architecture decision",
        content="Use bound capabilities.",
        expected_space_revision=1,
        source_refs=(source,),
        operation_id="create-link-source",
    )
    second = service.write_markdown(
        path="/notes/evidence.md",
        description="Evidence supporting the architecture decision",
        content="Scope checks are enforced by repositories.",
        expected_space_revision=2,
        source_refs=(source,),
        operation_id="create-link-target",
    )

    linked = service.link(
        source_ref=entry_ref(first),
        target_ref=entry_ref(second),
        relation="supports",
        expected_space_revision=3,
        source_refs=(source,),
        operation_id="create-relation",
    )
    replay = service.link(
        source_ref=entry_ref(first),
        target_ref=entry_ref(second),
        relation="supports",
        expected_space_revision=3,
        source_refs=(source,),
        operation_id="create-relation",
    )

    assert replay == linked
    assert linked.source_entry_ref == entry_ref(first)
    assert linked.source_entry_id == first.entry_id
    assert linked.target_ref == entry_ref(second)
    assert service.list_links(entry_ref(first)) == (linked,)
    assert repository.link_provenance[linked.link_id] == (source,)

    with pytest.raises(RepositoryScopeError):
        service.link(
            source_ref=entry_ref(first),
            target_ref=ResourceRef("memory", second.entry_id, 1, "space-foreign"),
            relation="supports",
            expected_space_revision=4,
            source_refs=(source,),
            operation_id="foreign-relation-target",
        )


def test_workspace_relation_links_reject_stale_entry_revisions() -> None:
    service, repository, _ = build_service()
    source = source_event()
    first = service.write_markdown(
        path="/notes/current-source.md",
        description="Relation source revision",
        content="source v1",
        expected_space_revision=1,
        source_refs=(source,),
        operation_id="relation-source-v1",
    )
    second = service.write_markdown(
        path="/notes/current-target.md",
        description="Relation target revision",
        content="target v1",
        expected_space_revision=2,
        source_refs=(source,),
        operation_id="relation-target-v1",
    )
    current_first = service.write_markdown(
        entry_ref=entry_ref(first),
        path=first.path,
        description=first.description,
        content="source v2",
        expected_space_revision=3,
        source_refs=(source,),
        operation_id="relation-source-v2",
    )

    with pytest.raises(RepositoryConflictError, match="current"):
        service.link(
            source_ref=entry_ref(first),
            target_ref=entry_ref(second),
            relation="supports",
            expected_space_revision=4,
            source_refs=(source,),
            operation_id="stale-relation-source",
        )

    current_second = service.write_markdown(
        entry_ref=entry_ref(second),
        path=second.path,
        description=second.description,
        content="target v2",
        expected_space_revision=4,
        source_refs=(source,),
        operation_id="relation-target-v2",
    )
    with pytest.raises(RepositoryConflictError, match="current"):
        service.link(
            source_ref=entry_ref(current_first),
            target_ref=entry_ref(second),
            relation="supports",
            expected_space_revision=5,
            source_refs=(source,),
            operation_id="stale-relation-target",
        )

    assert current_second.revision == 2
    assert repository.links == {}


def test_workspace_rejects_link_repository_limit_overflow() -> None:
    service, repository, _ = build_service()
    source = source_event()
    first = service.write_markdown(
        path="/notes/limited-source.md",
        description="Bounded relation source",
        content="source",
        expected_space_revision=1,
        source_refs=(source,),
        operation_id="limited-source",
    )
    second = service.write_markdown(
        path="/notes/limited-target.md",
        description="Bounded relation target",
        content="target",
        expected_space_revision=2,
        source_refs=(source,),
        operation_id="limited-target",
    )
    for index in range(2):
        service.link(
            source_ref=entry_ref(first),
            target_ref=entry_ref(second),
            relation=f"relation-{index}",
            expected_space_revision=3 + index,
            source_refs=(source,),
            operation_id=f"limited-relation-{index}",
        )
    repository.ignore_link_limit = True

    with pytest.raises(RuntimeError, match="exceeded"):
        service.list_links(entry_ref(first), limit=1)
    with pytest.raises(RepositoryConflictError):
        service.link(
            source_ref=entry_ref(first),
            target_ref=entry_ref(second),
            relation="duplicates",
            expected_space_revision=3,
            source_refs=(source,),
            operation_id="stale-relation",
        )


def test_workspace_list_preserves_recursive_and_direct_child_semantics() -> None:
    service, _, _ = build_service()
    source = source_event()
    service.create_folder(
        path="/notes",
        description="Top-level notes",
        expected_space_revision=1,
        source_refs=(source,),
        operation_id="list-root-folder",
    )
    nested = service.create_folder(
        path="/notes/nested",
        description="Nested notes",
        expected_space_revision=2,
        source_refs=(source,),
        operation_id="list-nested-folder",
    )
    direct = service.write_markdown(
        path="/notes/direct.md",
        description="Direct child note",
        content="direct",
        expected_space_revision=3,
        source_refs=(source,),
        operation_id="list-direct-note",
    )
    deep = service.write_markdown(
        path="/notes/nested/deep.md",
        description="Nested child note",
        content="deep",
        expected_space_revision=4,
        source_refs=(source,),
        operation_id="list-deep-note",
    )

    shallow = service.list(parent_path="/notes", recursive=False)
    recursive = service.list(parent_path="/notes", recursive=True)

    assert {entry.entry_id for entry in shallow.entries} == {
        nested.entry_id,
        direct.entry_id,
    }
    assert deep.entry_id not in {entry.entry_id for entry in shallow.entries}
    assert deep.entry_id in {entry.entry_id for entry in recursive.entries}


def test_folder_archive_requires_explicit_recursive_cascade() -> None:
    service, repository, _ = build_service()
    source = source_event()
    folder = service.create_folder(
        path="/archive",
        description="Folder with content to archive",
        expected_space_revision=1,
        source_refs=(source,),
        operation_id="archive-folder",
    )
    child = service.write_markdown(
        path="/archive/child.md",
        description="Child that must not be orphaned",
        content="child",
        expected_space_revision=2,
        source_refs=(source,),
        operation_id="archive-child",
    )

    with pytest.raises(RepositoryConflictError):
        service.archive(
            ref=entry_ref(folder),
            expected_space_revision=3,
            source_refs=(source,),
            recursive=False,
            operation_id="archive-folder-nonrecursive",
        )
    archived = service.archive(
        ref=entry_ref(folder),
        expected_space_revision=3,
        source_refs=(source,),
        recursive=True,
        operation_id="archive-folder-recursive",
    )

    assert archived.deleted is True
    assert repository.entries[child.entry_id].deleted is True


def test_workspace_reads_are_bounded_and_paginated() -> None:
    service, _, _ = build_service()
    entry = service.write_markdown(
        path="/notes/large.md",
        description="A bounded read fixture",
        content="0123456789" * 20,
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="create-large",
    )

    first = service.read(entry_ref(entry), offset=0, limit=16)
    second = service.read(entry_ref(entry), offset=first.next_offset, limit=16)

    assert first.data == b"0123456789012345"
    assert first.has_more is True
    assert first.next_offset == 16
    assert second.offset == 16
    assert len(second.data) == 16
    with pytest.raises(ValueError):
        service.read(entry_ref(entry), limit=32 * 1024 + 1)
    with pytest.raises(ValueError):
        service.read(entry_ref(entry), offset=-1, limit=16)


def test_workspace_rejects_cross_scope_refs_stale_revisions_and_invalid_metadata() -> None:
    service, _, _ = build_service()
    created = service.write_markdown(
        path="/notes/security.md",
        description="Security boundary notes",
        content="safe",
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="create-security",
    )

    with pytest.raises(RepositoryScopeError):
        service.read(ResourceRef("memory", created.entry_id, 1, "space-foreign"))
    with pytest.raises(RepositoryScopeError):
        service.write_markdown(
            path="/notes/foreign-source.md",
            description="Must never accept foreign provenance",
            content="safe",
            expected_space_revision=2,
            source_refs=(ResourceRef("context_event", "foreign-event", 1),),
            operation_id="foreign-source",
        )
    with pytest.raises(ModelValidationError):
        service.create_link(
            path="/links/credential.md",
            description="Credential-bearing links are forbidden",
            url="https://user:password@example.test/private",
            expected_space_revision=2,
            source_refs=(source_event(),),
            operation_id="credential-link",
        )
    with pytest.raises(ModelValidationError):
        service.write_markdown(
            path="/notes/valid.md",
            description="",
            content="safe",
            expected_space_revision=2,
            source_refs=(source_event(),),
            operation_id="empty-description",
        )


def test_task_state_updates_use_cas_and_authorized_source_provenance() -> None:
    event = source_event("event-task")
    artifact = ResourceRef("artifact", "artifact-1", 1)
    memory = ResourceRef("memory", "entry-1", 1, "space-chat")
    references = FakeReferenceAuthorizer(
        "chat-binding",
        {event, artifact, memory},
    )
    repository = FakeTaskStateRepository("chat-binding")
    service = TaskStateService(repository=repository, references=references)

    initial = service.update(
        expected_revision=None,
        patch={
            "objective": "Ship Memory V2 safely",
            "constraints": ("Never store secret plaintext",),
            "artifact_refs": (artifact,),
            "memory_refs": (memory,),
        },
        source_event_refs=(event,),
        operation_id="task-state-create",
    )
    updated = service.update(
        expected_revision=1,
        patch={"confirmed_decisions": ("Use one canonical journal",)},
        source_event_refs=(event,),
        operation_id="task-state-update",
    )

    assert isinstance(initial, PinnedTaskState)
    assert initial.revision == 1
    assert updated.revision == 2
    assert updated.objective == initial.objective
    assert updated.source_event_refs == (event,)
    with pytest.raises(RepositoryConflictError):
        service.update(
            expected_revision=1,
            patch={"status": "complete"},
            source_event_refs=(event,),
            operation_id="task-state-stale",
        )
    with pytest.raises(RepositoryScopeError):
        service.update(
            expected_revision=2,
            patch={"constraints": ("foreign provenance",)},
            source_event_refs=(ResourceRef("context_event", "foreign", 1),),
            operation_id="task-state-foreign",
        )

    forbidden = {"user_id", "owner_chat_id", "chat_id", "namespace", "space_id", "scope"}
    for method in (service.update, service.get):
        assert not forbidden.intersection(inspect.signature(method).parameters)


def test_task_state_redacts_before_operation_hash_and_cas() -> None:
    event = source_event("event-task-redaction")
    references = FakeReferenceAuthorizer("chat-binding", {event})
    repository = FakeTaskStateRepository("chat-binding")
    observed: list[dict[str, object]] = []

    def redact(patch):
        observed.append(dict(patch))
        return {
            **patch,
            "objective": patch["objective"].replace("sk-secret", "[REDACTED]"),
            "constraints": tuple(
                item.replace("sk-secret", "[REDACTED]")
                for item in patch["constraints"]
            ),
        }

    service = TaskStateService(
        repository=repository,
        references=references,
        patch_redactor=redact,
    )
    arguments = {
        "expected_revision": None,
        "patch": {
            "objective": "Never persist sk-secret",
            "constraints": ("token=sk-secret",),
        },
        "source_event_refs": (event,),
        "operation_id": "task-state-redacted",
    }

    created = service.update(**arguments)
    replayed = service.update(**arguments)

    assert len(observed) == 6
    assert created == replayed
    assert created.objective == "Never persist [REDACTED]"
    assert created.constraints == ("token=[REDACTED]",)
    assert "sk-secret" not in json.dumps(created.to_dict())


def test_task_state_redactor_failure_never_mutates() -> None:
    event = source_event("event-task-redaction-failure")
    references = FakeReferenceAuthorizer("chat-binding", {event})

    for redactor in (
        lambda _patch: {"objective": "changed keys", "status": "complete"},
        lambda _patch: (_ for _ in ()).throw(RuntimeError("secret detail")),
    ):
        repository = FakeTaskStateRepository("chat-binding")
        service = TaskStateService(
            repository=repository,
            references=references,
            patch_redactor=redactor,
        )
        with pytest.raises(WorkspaceRepositoryError, match="redaction failed") as exc:
            service.update(
                expected_revision=None,
                patch={"objective": "Initial objective"},
                source_event_refs=(event,),
                operation_id="task-state-redaction-failed",
            )
        assert "secret detail" not in str(exc.value)
        assert repository.state is None
        assert repository.operations == {}


def test_task_state_redacts_carried_fields_before_full_state_cas() -> None:
    event = source_event("event-task-carried-redaction")
    references = FakeReferenceAuthorizer("chat-binding", {event})
    repository = FakeTaskStateRepository("chat-binding")
    repository.state = PinnedTaskState(
        state_id=repository.state_id,
        revision=1,
        objective="classified objective",
        source_event_refs=(event,),
    )

    def redact(patch):
        return {
            key: (
                value.replace("classified", "[STRICT]")
                if isinstance(value, str)
                else tuple(
                    item.replace("classified", "[STRICT]")
                    if isinstance(item, str)
                    else item
                    for item in value
                )
                if isinstance(value, (list, tuple))
                else value
            )
            for key, value in patch.items()
        }

    service = TaskStateService(
        repository=repository,
        references=references,
        patch_redactor=redact,
    )
    updated = service.update(
        expected_revision=1,
        patch={"constraints": ("New constraint",)},
        source_event_refs=(event,),
        operation_id="task-state-carried-redaction",
    )

    assert updated.objective == "[STRICT] objective"
    assert updated.constraints == ("New constraint",)
    assert repository.state == updated


def test_task_state_validates_carried_protected_fields_before_cas() -> None:
    event = source_event("event-task-carried-protected-redaction")
    references = FakeReferenceAuthorizer("chat-binding", {event})
    repository = FakeTaskStateRepository("chat-binding")
    repository.state = PinnedTaskState(
        state_id=repository.state_id,
        revision=1,
        objective="Keep protected state stable",
        source_event_refs=(event,),
        status="in_progress",
    )

    def redact(patch):
        return {
            **patch,
            **({"status": "complete"} if "status" in patch else {}),
        }

    service = TaskStateService(
        repository=repository,
        references=references,
        patch_redactor=redact,
    )

    with pytest.raises(WorkspaceRepositoryError, match="redaction failed"):
        service.update(
            expected_revision=1,
            patch={"constraints": ("Do not bypass full-state validation",)},
            source_event_refs=(event,),
            operation_id="task-state-carried-protected-redaction",
        )

    assert repository.state.revision == 1
    assert repository.state.status == "in_progress"
    assert repository.operations == {}


def test_task_state_non_idempotent_redactor_fails_before_cas() -> None:
    event = source_event("event-task-non-idempotent-redaction")
    references = FakeReferenceAuthorizer("chat-binding", {event})
    repository = FakeTaskStateRepository("chat-binding")

    def redact(patch):
        return {
            key: f"{value}!" if isinstance(value, str) else value
            for key, value in patch.items()
        }

    service = TaskStateService(
        repository=repository,
        references=references,
        patch_redactor=redact,
    )
    with pytest.raises(WorkspaceRepositoryError, match="redaction failed"):
        service.update(
            expected_revision=None,
            patch={"objective": "Initial objective"},
            source_event_refs=(event,),
            operation_id="task-state-non-idempotent-redaction",
        )

    assert repository.state is None
    assert repository.operations == {}


def test_task_state_idempotent_replay_survives_later_revisions() -> None:
    event = source_event("event-task-replay")
    references = FakeReferenceAuthorizer("chat-binding", {event})
    repository = FakeTaskStateRepository("chat-binding")
    service = TaskStateService(repository=repository, references=references)
    create_arguments = {
        "expected_revision": None,
        "patch": {"objective": "Preserve idempotent task-state writes"},
        "source_event_refs": (event,),
        "operation_id": "task-state-replay",
    }

    created = service.update(**create_arguments)
    service.update(
        expected_revision=1,
        patch={"active_plan": ("Advance the revision",)},
        source_event_refs=(event,),
        operation_id="task-state-advance",
    )

    assert service.update(**create_arguments) == created
    with pytest.raises(RepositoryConflictError, match="operation payload changed"):
        service.update(
            **{
                **create_arguments,
                "patch": {"objective": "Changed payload under the same operation"},
            }
        )


def test_task_state_is_bound_to_repository_expected_identity() -> None:
    event = source_event("event-task-identity")
    references = FakeReferenceAuthorizer("chat-binding", {event})
    repository = FakeTaskStateRepository(
        "chat-binding",
        state_id="task-state-chat",
    )
    service = TaskStateService(repository=repository, references=references)
    created = service.update(
        expected_revision=None,
        patch={"objective": "Bind task state identity"},
        source_event_refs=(event,),
        operation_id="task-state-identity",
    )

    assert created.state_id == "task-state-chat"
    repository.state = replace(created, state_id="task-state-foreign")
    with pytest.raises(RepositoryScopeError, match="identity"):
        service.get()

    repository.state = created
    repository.operations["task-state-identity"] = (
        repository.operations["task-state-identity"][0],
        replace(created, state_id="task-state-replay-foreign"),
    )
    with pytest.raises(RepositoryScopeError, match="identity"):
        service.update(
            expected_revision=None,
            patch={"objective": "Bind task state identity"},
            source_event_refs=(event,),
            operation_id="task-state-identity",
        )


def test_task_state_keeps_only_current_revision_provenance_across_hundreds_of_updates() -> None:
    events = tuple(source_event(f"event-task-{index}") for index in range(301))
    references = FakeReferenceAuthorizer("chat-binding", set(events))
    repository = FakeTaskStateRepository(
        "chat-binding",
        state_id="task-state-bounded",
    )
    service = TaskStateService(repository=repository, references=references)
    state = service.update(
        expected_revision=None,
        patch={"objective": "Keep task state bounded"},
        source_event_refs=(events[0],),
        operation_id="task-state-bounded-0",
    )

    for index, event in enumerate(events[1:], start=1):
        state = service.update(
            expected_revision=index,
            patch={"active_plan": (f"Revision {index}",)},
            source_event_refs=(event,),
            operation_id=f"task-state-bounded-{index}",
        )

    assert state.revision == 301
    assert state.source_event_refs == (events[-1],)
    assert len(json.dumps(state.to_dict()).encode("utf-8")) <= 256 * 1024


def test_task_state_rejects_aggregate_item_and_byte_budget_overflow() -> None:
    event = source_event("event-task-budget")
    references = FakeReferenceAuthorizer("chat-binding", {event})
    repository = FakeTaskStateRepository(
        "chat-binding",
        state_id="task-state-budget",
    )
    service = TaskStateService(repository=repository, references=references)

    with pytest.raises(ModelValidationError, match="item budget"):
        service.update(
            expected_revision=None,
            patch={
                "objective": "Bound aggregate task state",
                "active_plan": tuple(f"Item {index}" for index in range(300)),
            },
            source_event_refs=(event,),
            operation_id="task-state-too-many-items",
        )
    with pytest.raises(ModelValidationError, match="byte budget"):
        service.update(
            expected_revision=None,
            patch={
                "objective": "Bound aggregate task state",
                "active_plan": tuple("x" * 10_000 for _ in range(30)),
            },
            source_event_refs=(event,),
            operation_id="task-state-too-many-bytes",
        )


def test_workspace_capabilities_never_accept_caller_selected_scope() -> None:
    forbidden = {
        "user_id",
        "owner_chat_id",
        "chat_id",
        "namespace",
        "space_id",
        "scope",
    }
    capability_methods = (
        (BoundWorkspaceMutationRepository, "apply"),
        (BoundWorkspaceContentRepository, "read_content"),
        (BoundWorkspaceHistoryRepository, "list_revisions"),
        (BoundWorkspaceLinkRepository, "create_link"),
        (BoundWorkspaceLinkRepository, "list_links"),
        (BoundWorkspaceLinkRepository, "list_backlinks"),
        (BoundWorkspaceReferenceAuthorizer, "authorize"),
        (BoundPinnedTaskStateRepository, "current"),
        (BoundPinnedTaskStateRepository, "replay"),
        (BoundPinnedTaskStateRepository, "compare_and_swap"),
        (BoundPromotionRepository, "create"),
        (BoundPromotionRepository, "replay"),
        (BoundPromotionRepository, "read"),
        (BoundPromotionDecisionRepository, "replay_decision"),
        (BoundPromotionDecisionRepository, "decide"),
    )
    service_methods = (
        "list",
        "search",
        "read",
        "history",
        "create_folder",
        "write_markdown",
        "write_image",
        "create_link",
        "move",
        "archive",
        "link",
        "list_links",
    )
    service, _, _ = build_service()

    for capability, method_name in capability_methods:
        assert not forbidden.intersection(
            inspect.signature(getattr(capability, method_name)).parameters
        )
    for method_name in service_methods:
        assert not forbidden.intersection(
            inspect.signature(getattr(service, method_name)).parameters
        )
