from __future__ import annotations

from pathlib import Path

import pytest

from unchain.journal import ResourceRef
from unchain.memory.long_term_recall_v2 import (
    LongTermFirstMessageRecall,
    LongTermRecallDisposition,
)
from unchain.memory.workspace import MemorySpace
from unchain.memory.workspace.ports import RepositoryNotFoundError
from unchain.memory.workspace.promotions import (
    PromotionConfirmationService,
    PromotionService,
)
from unchain.memory.workspace.service import MemoryWorkspaceService
from unchain.persistence.sqlite_long_term_memory_v2 import (
    SQLiteLongTermMemoryV2ReadScope,
    open_sqlite_long_term_memory_v2,
)
from unchain.persistence.sqlite_memory_v2 import SQLiteMemoryV2Store
from unchain.persistence.sqlite_promotion_v2 import SQLitePromotionV2Store

from .fakes import FakePromotionConfirmationAuthorizer, FakeReferenceAuthorizer


class _OfflineVectorIndex:
    def supersede(self, *, entry_ref: ResourceRef, deleted: bool) -> None:
        return None

    def upsert(self, chunks) -> None:
        return None

    def search(self, query: str, *, limit: int):
        raise RuntimeError("vector service offline")


def _entry_ref(entry) -> ResourceRef:
    return ResourceRef("memory", entry.entry_id, entry.revision, entry.space_id)


def _proposal_ref(proposal) -> ResourceRef:
    return ResourceRef(
        "promotion",
        proposal.proposal_id,
        proposal.revision,
        proposal.target_namespace,
    )


def _stack(
    root: Path,
    *,
    suffix: str,
    namespace: str,
    tags: tuple[str, ...] = (),
):
    database = root / "context_v2.sqlite3"
    objects = root / "objects"
    memory = SQLiteMemoryV2Store(
        database_path=database,
        object_directory=objects,
    )
    source_repository = memory.bind_workspace(
        space=MemorySpace(
            f"chat-space-{suffix}",
            "chat",
            f"Chat {suffix}",
            "Long-term read fixture",
            1,
        ),
        owner_chat_id=f"chat-{suffix}",
    )
    event = ResourceRef("context_event", f"event-{suffix}", 1)
    references = FakeReferenceAuthorizer(f"binding-{suffix}", {event})
    workspace = MemoryWorkspaceService(
        repository=source_repository,
        mutations=source_repository,
        content=source_repository,
        history=source_repository,
        links=source_repository,
        references=references,
    )
    source = workspace.write_markdown(
        path=f"/preferences/{suffix}-provider.md",
        description=f"Provider preference for {suffix}",
        content=f"Prefer provider {suffix}.\n",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id=f"seed-long-term-read-{suffix}",
        tags=tags,
    )
    references.allowed.add(_entry_ref(source))
    repository = SQLitePromotionV2Store(
        database_path=database,
        object_directory=objects,
    ).bind(
        source_space=source_repository.space,
        source_owner_chat_id=f"chat-{suffix}",
        target_namespace=namespace,
    )
    promotions = PromotionService(
        source_repository=source_repository,
        proposals=repository,
        references=references,
    )
    return workspace, source, references, repository, promotions


def _apply(
    *,
    source,
    repository,
    promotions,
    target_path: str,
    operation_suffix: str,
):
    proposal = promotions.propose(
        source_ref=_entry_ref(source),
        target_path=target_path,
        reason=f"Durable provider preference {operation_suffix}",
        source_refs=(),
        operation_id=f"propose-{operation_suffix}",
    )
    authorizer = FakePromotionConfirmationAuthorizer(repository.target_namespace)
    confirmation = PromotionConfirmationService(
        repository,
        confirmations=authorizer,
    )
    receipt = authorizer.issue(
        proposal_ref=_proposal_ref(proposal),
        approved=True,
    )
    return confirmation.decide(
        ref=_proposal_ref(proposal),
        expected_revision=proposal.revision,
        confirmation=receipt,
        operation_id=f"apply-{operation_suffix}",
    )


def _open(
    root: Path,
    *,
    namespace: str,
    binding_id: str,
    vector_index=None,
):
    return LongTermFirstMessageRecall(
        memory=open_sqlite_long_term_memory_v2(
            database_path=root / "context_v2.sqlite3",
            object_directory=root / "objects",
            scope=SQLiteLongTermMemoryV2ReadScope(namespace, binding_id),
            vector_index=vector_index,
        )
    )


def test_applied_promotion_is_recalled_after_cold_restart(tmp_path: Path) -> None:
    _, source, _, repository, promotions = _stack(
        tmp_path,
        suffix="alpha",
        namespace="user:alpha",
    )
    applied = _apply(
        source=source,
        repository=repository,
        promotions=promotions,
        target_path="/preferences/provider.md",
        operation_suffix="alpha-provider",
    )

    first = _open(
        tmp_path,
        namespace="user:alpha",
        binding_id="binding-alpha",
    ).recall_first_message("provider preference")
    reopened = _open(
        tmp_path,
        namespace="user:alpha",
        binding_id="binding-alpha",
    ).recall_first_message("provider preference")

    assert first.to_dict() == reopened.to_dict()
    assert first.disposition is LongTermRecallDisposition.CONTEXT_REFERENCES
    assert first.references[0].entry_ref == applied.applied_entry_ref
    assert first.references[0].provenance_refs


def test_empty_bound_namespace_returns_none(tmp_path: Path) -> None:
    _stack(
        tmp_path,
        suffix="empty",
        namespace="user:empty",
    )

    envelope = _open(
        tmp_path,
        namespace="user:empty",
        binding_id="binding-empty",
    ).recall_first_message("nothing matches this request")

    assert envelope.disposition is LongTermRecallDisposition.NONE
    assert envelope.references == ()


def test_namespace_binding_cannot_read_another_namespace(tmp_path: Path) -> None:
    _stack(tmp_path, suffix="alpha", namespace="user:alpha")
    _, source, _, repository, promotions = _stack(
        tmp_path,
        suffix="beta",
        namespace="user:beta",
    )
    _apply(
        source=source,
        repository=repository,
        promotions=promotions,
        target_path="/facts/foreign-nebula.md",
        operation_suffix="beta-nebula",
    )

    envelope = _open(
        tmp_path,
        namespace="user:alpha",
        binding_id="binding-alpha",
    ).recall_first_message("foreign nebula")

    assert envelope.disposition is LongTermRecallDisposition.NONE
    with pytest.raises(RepositoryNotFoundError):
        _open(
            tmp_path,
            namespace="user:missing",
            binding_id="binding-alpha",
        )


def test_vector_outage_falls_back_to_durable_fts(tmp_path: Path) -> None:
    _, source, _, repository, promotions = _stack(
        tmp_path,
        suffix="offline",
        namespace="user:offline",
    )
    _apply(
        source=source,
        repository=repository,
        promotions=promotions,
        target_path="/preferences/provider.md",
        operation_suffix="offline-provider",
    )

    envelope = _open(
        tmp_path,
        namespace="user:offline",
        binding_id="binding-offline",
        vector_index=_OfflineVectorIndex(),
    ).recall_first_message("provider preference")

    assert envelope.disposition is LongTermRecallDisposition.CONTEXT_REFERENCES
    assert envelope.vector_unavailable is True
    assert "fts" in envelope.references[0].matched_by


def test_conflicting_promoted_semantic_keys_require_curator(tmp_path: Path) -> None:
    workspace, source, references, repository, promotions = _stack(
        tmp_path,
        suffix="conflict",
        namespace="user:conflict",
        tags=("semantic:provider-choice",),
    )
    _apply(
        source=source,
        repository=repository,
        promotions=promotions,
        target_path="/preferences/provider-current.md",
        operation_suffix="conflict-current",
    )
    second = workspace.write_markdown(
        path="/preferences/conflict-provider-previous.md",
        description="Previous durable provider preference",
        content="Prefer the previous provider.\n",
        expected_space_revision=2,
        source_refs=source.source_refs,
        operation_id="seed-long-term-read-conflict-previous",
        tags=("semantic:provider-choice",),
    )
    references.allowed.add(_entry_ref(second))
    _apply(
        source=second,
        repository=repository,
        promotions=promotions,
        target_path="/preferences/provider-previous.md",
        operation_suffix="conflict-previous",
    )

    envelope = _open(
        tmp_path,
        namespace="user:conflict",
        binding_id="binding-conflict",
    ).recall_first_message("provider preference")

    assert envelope.disposition is LongTermRecallDisposition.CURATOR_REQUIRED
    assert envelope.reason == "semantic_key_conflict"
    assert len(envelope.references) == 2
