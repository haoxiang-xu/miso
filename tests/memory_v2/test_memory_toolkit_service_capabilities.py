from __future__ import annotations

import inspect

import pytest

from unchain.journal import ResourceRef
from unchain.memory.toolkit import (
    LongTermMemoryCapability,
    MemoryToolkitError,
    MemoryUpsertRequest,
    PromotionMemoryCapability,
    TaskStateMemoryCapability,
    TaskStateUpdateRequest,
    WorkspaceMemoryCapability,
)
from unchain.memory.workspace import (
    LongTermMemoryService,
    MemorySpace,
    MemoryWorkspaceService,
    PromotionService,
    TaskStateService,
)
from unchain.memory.workspace.ports import RepositoryConflictError, RepositoryScopeError

from .fakes import (
    FakePromotionRepository,
    FakeReferenceAuthorizer,
    FakeTaskStateRepository,
    FakeWorkspaceRepository,
    entry_ref,
)


def workspace_capability():
    space = MemorySpace(
        space_id="space-chat",
        namespace="chat",
        name="Chat memory",
        description="Bound chat memory",
        revision=1,
    )
    repository = FakeWorkspaceRepository(space)
    event = ResourceRef("context_event", "event-1", 1)
    references = FakeReferenceAuthorizer("binding-1", {event})
    service = MemoryWorkspaceService(
        repository=repository,
        mutations=repository,
        content=repository,
        history=repository,
        links=repository,
        references=references,
    )
    return (
        WorkspaceMemoryCapability(
            binding_id="binding-1",
            service=service,
            mutation_source_refs=(event,),
        ),
        repository,
        references,
        event,
    )


def markdown_request(
    event: ResourceRef,
    *,
    operation_id: str = "operation-1",
    content: bytes = b"hello memory",
) -> MemoryUpsertRequest:
    return MemoryUpsertRequest(
        path="/facts/release-window.md",
        description="Release timing used when planning the rollout",
        expected_space_revision=1,
        entry_ref=None,
        kind="markdown",
        content=content,
        media_type="text/markdown",
        url="",
        source_refs=(event,),
        operation_id=operation_id,
    )


def test_workspace_capability_delegates_to_service_and_replays_exact_mutation():
    capability, repository, _, event = workspace_capability()
    request = markdown_request(event)

    first = capability.upsert(request=request)
    replayed = capability.upsert(request=request)

    assert replayed == first
    assert first.space_id == "space-chat"
    assert first.content_ref is not None
    assert capability.space_revision == 2
    assert (
        capability.get_entry(
            ref=ResourceRef("memory", first.entry_id, first.revision, "space-chat")
        )
        == first
    )

    listing = capability.list_entries(path="", recursive=True, limit=20)
    assert listing["entries"] == (first,)
    assert listing["truncated"] is False
    search = capability.search_entries(query="release", limit=20)
    assert search["results"][0]["entry"] == first

    page = capability.read_content(
        ref=ResourceRef("memory", first.entry_id, first.revision, "space-chat"),
        offset=0,
        limit=32,
    )
    assert page.data == b"hello memory"
    assert page.media_type == "text/markdown"

    conflicting = markdown_request(event, content=b"changed")
    with pytest.raises(RepositoryConflictError, match="operation payload changed"):
        capability.upsert(request=conflicting)
    assert len(repository.entries) == 1


def test_workspace_capability_move_archive_and_history_remain_scope_bound():
    capability, _, _, event = workspace_capability()
    created = capability.upsert(request=markdown_request(event))
    ref = ResourceRef("memory", created.entry_id, 1, "space-chat")

    moved = capability.move(
        ref=ref,
        new_path="/facts/release-date.md",
        expected_space_revision=2,
        operation_id="move-1",
    )
    assert moved.path == "/facts/release-date.md"
    history = capability.history(
        ref=ResourceRef("memory", moved.entry_id, moved.revision, "space-chat"),
        limit=20,
    )
    assert [entry.revision for entry in history] == [2, 1]

    archived = capability.archive(
        ref=ResourceRef("memory", moved.entry_id, moved.revision, "space-chat"),
        expected_space_revision=3,
        recursive=False,
        operation_id="archive-1",
    )
    assert archived.deleted is True

    with pytest.raises(RepositoryScopeError):
        capability.get_entry(
            ref=ResourceRef("memory", moved.entry_id, moved.revision, "space-foreign")
        )


def test_task_state_capability_uses_structured_refs_and_replays_after_revision_advance():
    event = ResourceRef("context_event", "event-1", 1)
    repository = FakeTaskStateRepository("binding-1")
    references = FakeReferenceAuthorizer("binding-1", {event})
    service = TaskStateService(repository=repository, references=references)
    initial = service.update(
        expected_revision=None,
        patch={"objective": "Ship Memory V2 P0 safely"},
        source_event_refs=(event,),
        operation_id="bootstrap-1",
    )
    assert initial.revision == 1
    capability = TaskStateMemoryCapability(
        binding_id="binding-1",
        service=service,
    )
    request = TaskStateUpdateRequest(
        expected_revision=1,
        patch={"constraints": ("Never store secret plaintext",)},
        source_refs=(event,),
        operation_id="task-update-1",
    )

    first = capability.update(request=request)
    replayed = capability.update(request=request)

    assert replayed == first
    assert first.revision == 2
    assert first.constraints == ("Never store secret plaintext",)


def test_promotion_capability_can_only_create_idempotent_pending_proposals():
    workspace, repository, references, event = workspace_capability()
    source = workspace.upsert(request=markdown_request(event))
    source_ref = entry_ref(source)
    references.allowed.add(source_ref)
    proposals = FakePromotionRepository(repository.space, "user-1")
    target = proposals.seed_target(
        path="/preferences/provider.md",
        revision=2,
    )
    service = PromotionService(
        source_repository=repository,
        proposals=proposals,
        references=references,
    )
    capability = PromotionMemoryCapability(
        binding_id="binding-1",
        target_namespace="user-1",
        service=service,
        mutation_source_refs=(event,),
    )
    target_ref = entry_ref(target)

    first = capability.propose(
        source_ref=source_ref,
        target_path="/preferences/provider.md",
        target_entry_ref=target_ref,
        operation_id="promotion-1",
    )
    replayed = capability.propose(
        source_ref=source_ref,
        target_path="/preferences/provider.md",
        target_entry_ref=target_ref,
        operation_id="promotion-1",
    )

    assert replayed == first
    assert first.status.value == "pending"
    assert proposals.decision_calls == 0
    assert proposals.long_term_writes == 0
    assert first.diff["target_entry_ref"] == target_ref.to_dict()


@pytest.mark.parametrize(
    ("source_refs", "message"),
    (
        ((), "mutation_source_refs must include current-run event provenance"),
        (
            (ResourceRef("artifact", "artifact-1", 1),),
            "mutation_source_refs must contain bare context_event references",
        ),
        (
            (ResourceRef("context_event", "event-1", 1, "content"),),
            "mutation_source_refs must contain bare context_event references",
        ),
        (
            (
                ResourceRef("context_event", "event-1", 1),
                ResourceRef("context_event", "event-1", 1),
            ),
            "mutation_source_refs must not contain duplicates",
        ),
    ),
)
def test_workspace_capability_requires_unique_current_run_event_provenance(
    source_refs,
    message,
):
    capability, _, _, _ = workspace_capability()

    with pytest.raises(MemoryToolkitError, match=f"^{message}$"):
        WorkspaceMemoryCapability(
            binding_id="binding-1",
            service=capability.service,
            mutation_source_refs=source_refs,
        )


def test_workspace_capability_merges_bound_provenance_into_every_semantic_mutation():
    original, _, references, bound_event = workspace_capability()
    cited_event = ResourceRef("context_event", "event-cited", 1)
    references.allowed.add(cited_event)
    capability = WorkspaceMemoryCapability(
        binding_id="binding-1",
        service=original.service,
        mutation_source_refs=(bound_event,),
    )

    markdown = capability.upsert(request=markdown_request(cited_event))
    assert markdown.source_refs == (bound_event, cited_event)

    link = capability.upsert(
        request=MemoryUpsertRequest(
            path="/references/provider-docs.link",
            description="Provider documentation used for this implementation",
            expected_space_revision=2,
            entry_ref=None,
            kind="link",
            content=None,
            media_type="",
            url="https://example.test/docs",
            source_refs=(),
            operation_id="link-1",
        )
    )
    assert link.source_refs == (bound_event,)

    superseded = capability.upsert(
        request=MemoryUpsertRequest(
            path=markdown.path,
            description="Updated release timing used for the current rollout",
            expected_space_revision=3,
            entry_ref=entry_ref(markdown),
            kind="markdown",
            content=b"updated memory",
            media_type="text/markdown",
            url="",
            source_refs=(),
            operation_id="supersede-1",
        )
    )
    assert superseded.source_refs == (bound_event,)

    moved = capability.move(
        ref=entry_ref(superseded),
        new_path="/facts/release-date.md",
        expected_space_revision=4,
        operation_id="move-1",
    )
    assert moved.source_refs == (bound_event,)

    archived = capability.archive(
        ref=entry_ref(moved),
        expected_space_revision=5,
        recursive=False,
        operation_id="archive-1",
    )
    assert archived.source_refs == (bound_event,)


def test_promotion_capability_binds_fresh_provenance_and_exact_target_baseline():
    workspace, repository, references, event = workspace_capability()
    source = workspace.upsert(request=markdown_request(event))
    source_ref = entry_ref(source)
    mutation_event = ResourceRef("context_event", "event-promotion", 1)
    references.allowed.update({source_ref, mutation_event})
    proposals = FakePromotionRepository(repository.space, "user-1")
    target = proposals.seed_target(
        path="/preferences/provider.md",
        revision=2,
    )
    target_ref = entry_ref(target)
    service = PromotionService(
        source_repository=repository,
        proposals=proposals,
        references=references,
    )
    capability = PromotionMemoryCapability(
        binding_id="binding-1",
        target_namespace="user-1",
        service=service,
        mutation_source_refs=(mutation_event,),
    )

    proposal = capability.propose(
        source_ref=source_ref,
        target_path=target.path,
        target_entry_ref=target_ref,
        operation_id="promotion-with-baseline-1",
    )

    assert proposal.target_entry_ref == target_ref
    assert proposal.diff["target_entry_ref"] == target_ref.to_dict()
    assert proposal.source_refs == (event, source_ref, mutation_event)
    assert proposals.decision_calls == 0
    assert proposals.long_term_writes == 0


def test_mutation_capability_constructors_have_no_provenance_compatibility_default():
    for capability_type in (WorkspaceMemoryCapability, PromotionMemoryCapability):
        parameter = inspect.signature(capability_type).parameters[
            "mutation_source_refs"
        ]
        assert parameter.default is inspect.Parameter.empty


def test_workspace_replay_rejects_changed_host_bound_provenance():
    original, repository, references, first_event = workspace_capability()
    request = markdown_request(first_event)
    first = original.upsert(request=request)
    second_event = ResourceRef("context_event", "event-2", 1)
    references.allowed.add(second_event)
    changed_binding = WorkspaceMemoryCapability(
        binding_id="binding-1",
        service=original.service,
        mutation_source_refs=(second_event,),
    )

    with pytest.raises(RepositoryConflictError, match="operation payload changed"):
        changed_binding.upsert(request=request)
    assert len(repository.entries) == 1
    assert next(iter(repository.entries.values())) == first


def test_workspace_capability_rejects_a_mislabeled_service_binding():
    original, _, _, event = workspace_capability()

    with pytest.raises(MemoryToolkitError, match="service binding"):
        WorkspaceMemoryCapability(
            binding_id="binding-foreign",
            service=original.service,
            mutation_source_refs=(event,),
        )


def test_task_state_capability_rejects_a_mislabeled_service_binding():
    event = ResourceRef("context_event", "event-1", 1)
    service = TaskStateService(
        repository=FakeTaskStateRepository("binding-1"),
        references=FakeReferenceAuthorizer("binding-1", {event}),
    )

    with pytest.raises(MemoryToolkitError, match="service binding"):
        TaskStateMemoryCapability(
            binding_id="binding-foreign",
            service=service,
        )


def test_long_term_capability_rejects_a_mislabeled_service_binding():
    repository = FakeWorkspaceRepository(
        MemorySpace(
            space_id="space-long-term",
            namespace="user-1",
            name="Long-term memory",
            description="Bound long-term memory",
            revision=1,
        )
    )
    service = LongTermMemoryService(
        binding_id="binding-1",
        repository=repository,
        content=repository,
        history=repository,
    )

    with pytest.raises(MemoryToolkitError, match="service binding"):
        LongTermMemoryCapability(
            binding_id="binding-foreign",
            service=service,
        )


def test_promotion_capability_rejects_mislabeled_binding_and_namespace():
    workspace, repository, references, event = workspace_capability()
    source = workspace.upsert(request=markdown_request(event))
    references.allowed.add(entry_ref(source))
    service = PromotionService(
        source_repository=repository,
        proposals=FakePromotionRepository(repository.space, "user-1"),
        references=references,
    )

    with pytest.raises(MemoryToolkitError, match="service binding"):
        PromotionMemoryCapability(
            binding_id="binding-foreign",
            target_namespace="user-1",
            service=service,
            mutation_source_refs=(event,),
        )
    with pytest.raises(MemoryToolkitError, match="target namespace"):
        PromotionMemoryCapability(
            binding_id="binding-1",
            target_namespace="user-foreign",
            service=service,
            mutation_source_refs=(event,),
        )
