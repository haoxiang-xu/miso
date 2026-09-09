from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from pathlib import Path

import pytest

from unchain.journal import ResourceRef
from unchain.memory.workspace import MemorySpace, PromotionStatus
from unchain.memory.workspace.ports import RepositoryConflictError, RepositoryScopeError
from unchain.memory.workspace.promotions import (
    PromotionConfirmationService,
    PromotionService,
)
from unchain.memory.workspace.service import MemoryWorkspaceService
from unchain.persistence.sqlite_memory_v2 import SQLiteMemoryV2Store
from unchain.persistence.sqlite_promotion_v2 import SQLitePromotionV2Store

from .fakes import FakePromotionConfirmationAuthorizer, FakeReferenceAuthorizer


def _event() -> ResourceRef:
    return ResourceRef("context_event", "event-sqlite-promotion", 1)


def _entry_ref(entry) -> ResourceRef:
    return ResourceRef("memory", entry.entry_id, entry.revision, entry.space_id)


def _proposal_ref(proposal) -> ResourceRef:
    return ResourceRef(
        "promotion",
        proposal.proposal_id,
        proposal.revision,
        proposal.target_namespace,
    )


def _stack(root: Path, *, target_namespace: str = "user:sqlite"):
    memory_store = SQLiteMemoryV2Store(
        database_path=root / "context_v2.sqlite3",
        object_directory=root / "objects",
    )
    source_space = MemorySpace(
        "space-chat-promotion",
        "chat",
        "Promotion source chat",
        "Durable source workspace",
        1,
    )
    source_repository = memory_store.bind_workspace(
        space=source_space,
        owner_chat_id="chat-promotion",
    )
    event = _event()
    references = FakeReferenceAuthorizer("chat-promotion", {event})
    workspace = MemoryWorkspaceService(
        repository=source_repository,
        mutations=source_repository,
        content=source_repository,
        history=source_repository,
        links=source_repository,
        references=references,
    )
    source = workspace.write_markdown(
        path="/preferences/provider.md",
        description="Provider preference confirmed in this chat",
        content="Prefer the local provider.\n",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="seed-sqlite-promotion-source",
    )
    references.allowed.add(_entry_ref(source))

    promotion_store = SQLitePromotionV2Store(
        database_path=root / "context_v2.sqlite3",
        object_directory=root / "objects",
    )
    repository = promotion_store.bind(
        source_space=source_repository.space,
        source_owner_chat_id="chat-promotion",
        target_namespace=target_namespace,
    )
    proposals = PromotionService(
        source_repository=source_repository,
        proposals=repository,
        references=references,
    )
    return memory_store, workspace, source, references, repository, proposals


def test_sqlite_promotion_is_pending_restart_safe_and_writes_no_target(
    tmp_path: Path,
) -> None:
    _, _, source, _, repository, proposals = _stack(tmp_path)
    proposal = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/provider.md",
        reason="The user explicitly asked to retain this preference",
        source_refs=(),
        operation_id="sqlite-promotion-pending",
    )

    assert proposal.status is PromotionStatus.PENDING
    assert (
        repository.validate_target_baseline(
            target_path=proposal.target_path,
            target_entry_ref=None,
        )
        is None
    )

    reopened = SQLitePromotionV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    ).bind(
        source_space=repository.source_space,
        source_owner_chat_id="chat-promotion",
        target_namespace="user:sqlite",
    )
    assert reopened.read(ref=_proposal_ref(proposal)) == proposal
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "promotion_proposals",
        "promotion_revisions",
        "promotion_operation_receipts",
    } <= tables


def test_sqlite_approved_promotion_atomically_derives_content_and_preserves_source(
    tmp_path: Path,
) -> None:
    memory_store, workspace, source, _, repository, proposals = _stack(tmp_path)
    proposal = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/provider.md",
        reason="Retain the confirmed provider preference",
        source_refs=(),
        operation_id="sqlite-promotion-propose-apply",
    )
    authorizer = FakePromotionConfirmationAuthorizer(repository.target_namespace)
    confirmation = PromotionConfirmationService(
        repository,
        confirmations=authorizer,
    )
    receipt = authorizer.issue(proposal_ref=_proposal_ref(proposal), approved=True)

    applied = confirmation.decide(
        ref=_proposal_ref(proposal),
        expected_revision=1,
        confirmation=receipt,
        operation_id="sqlite-promotion-confirm-apply",
    )

    assert applied.status is PromotionStatus.APPLIED
    assert applied.applied_entry_ref is not None
    target = repository.read_target(ref=applied.applied_entry_ref)
    assert target.space_id == repository.target_space_id
    assert target.revision == 1
    assert target.source_refs == proposal.source_refs
    target_repository = memory_store.bind_workspace(
        space=MemorySpace(
            repository.target_space_id,
            repository.target_namespace,
            "Long-term memory",
            "Namespaced durable long-term memory",
            1,
        ),
        owner_chat_id=None,
    )
    assert (
        target_repository.read_content(
            ref=target.content_ref,
            offset=0,
            limit=4096,
        ).data
        == b"Prefer the local provider.\n"
    )
    assert workspace.read(_entry_ref(source)).data == b"Prefer the local provider.\n"


def test_sqlite_promotion_decision_replay_is_exact_and_never_duplicates_target(
    tmp_path: Path,
) -> None:
    _, _, source, _, repository, proposals = _stack(tmp_path)
    proposal = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/provider.md",
        reason="Stable decision replay",
        source_refs=(),
        operation_id="sqlite-promotion-propose-replay",
    )
    authorizer = FakePromotionConfirmationAuthorizer(repository.target_namespace)
    confirmation = PromotionConfirmationService(repository, confirmations=authorizer)
    receipt = authorizer.issue(proposal_ref=_proposal_ref(proposal), approved=True)
    arguments = {
        "ref": _proposal_ref(proposal),
        "expected_revision": 1,
        "confirmation": receipt,
        "operation_id": "sqlite-promotion-decision-replay",
    }

    first = confirmation.decide(**arguments)
    assert confirmation.decide(**arguments) == first
    with pytest.raises(RepositoryConflictError, match="operation"):
        confirmation.decide(
            **{
                **arguments,
                "operation_id": "sqlite-promotion-decision-replay",
                "confirmation": type(receipt)(receipt.confirmation_id, False),
            }
        )
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        target_revisions = connection.execute(
            "SELECT COUNT(*) FROM entry_revisions WHERE space_id = ?",
            (repository.target_space_id,),
        ).fetchone()[0]
    assert target_revisions == 1


def test_sqlite_rejected_promotion_writes_no_long_term_revision(
    tmp_path: Path,
) -> None:
    _, _, source, _, repository, proposals = _stack(tmp_path)
    proposal = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/provider.md",
        reason="The user may reject this proposal",
        source_refs=(),
        operation_id="sqlite-promotion-propose-reject",
    )
    authorizer = FakePromotionConfirmationAuthorizer(repository.target_namespace)
    confirmation = PromotionConfirmationService(repository, confirmations=authorizer)
    receipt = authorizer.issue(proposal_ref=_proposal_ref(proposal), approved=False)

    rejected = confirmation.decide(
        ref=_proposal_ref(proposal),
        expected_revision=1,
        confirmation=receipt,
        operation_id="sqlite-promotion-confirm-reject",
    )

    assert rejected.status is PromotionStatus.REJECTED
    assert rejected.applied_entry_ref is None
    assert (
        repository.validate_target_baseline(
            target_path=proposal.target_path,
            target_entry_ref=None,
        )
        is None
    )


def test_sqlite_promotion_binding_rejects_owner_or_namespace_rebinding(
    tmp_path: Path,
) -> None:
    _, _, _, _, repository, _ = _stack(tmp_path)
    store = SQLitePromotionV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    )

    with pytest.raises(RepositoryScopeError):
        store.bind(
            source_space=repository.source_space,
            source_owner_chat_id="another-chat",
            target_namespace=repository.target_namespace,
        )
    with pytest.raises(RepositoryScopeError):
        store.bind(
            source_space=repository.source_space,
            source_owner_chat_id="chat-promotion",
            target_namespace="user:another",
            target_space_id=repository.target_space_id,
        )


def test_sqlite_approval_fails_atomically_when_source_object_is_unreadable(
    tmp_path: Path,
) -> None:
    _, _, source, _, repository, proposals = _stack(tmp_path)
    proposal = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/provider.md",
        reason="Do not partially apply a corrupted source",
        source_refs=(),
        operation_id="sqlite-promotion-propose-corrupt-source",
    )
    assert source.content_ref is not None
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        digest = connection.execute(
            """
            SELECT object_sha256 FROM entry_revisions
            WHERE space_id = ? AND entry_id = ? AND revision = ?
            """,
            (source.space_id, source.entry_id, source.revision),
        ).fetchone()[0]
    (tmp_path / "objects" / digest).unlink()
    authorizer = FakePromotionConfirmationAuthorizer(repository.target_namespace)
    confirmation = PromotionConfirmationService(repository, confirmations=authorizer)
    receipt = authorizer.issue(proposal_ref=_proposal_ref(proposal), approved=True)

    with pytest.raises(Exception):
        confirmation.decide(
            ref=_proposal_ref(proposal),
            expected_revision=1,
            confirmation=receipt,
            operation_id="sqlite-promotion-confirm-corrupt-source",
        )

    assert (
        repository.read(ref=_proposal_ref(proposal)).status is PromotionStatus.PENDING
    )
    assert (
        repository.validate_target_baseline(
            target_path=proposal.target_path,
            target_entry_ref=None,
        )
        is None
    )


def test_sqlite_replacement_requires_and_advances_the_exact_target_baseline(
    tmp_path: Path,
) -> None:
    memory_store, _, source, references, repository, proposals = _stack(tmp_path)
    target_repository = memory_store.bind_workspace(
        space=MemorySpace(
            repository.target_space_id,
            repository.target_namespace,
            "Long-term memory",
            "Namespaced durable long-term memory",
            1,
        ),
        owner_chat_id=None,
    )
    target_workspace = MemoryWorkspaceService(
        repository=target_repository,
        mutations=target_repository,
        content=target_repository,
        history=target_repository,
        links=target_repository,
        references=references,
    )
    baseline = target_workspace.write_markdown(
        path="/preferences/provider.md",
        description="Previous provider preference",
        content="Prefer the hosted provider.\n",
        expected_space_revision=1,
        source_refs=(_event(),),
        operation_id="seed-sqlite-promotion-target",
    )
    references.allowed.add(_entry_ref(baseline))
    proposal = proposals.propose(
        source_ref=_entry_ref(source),
        target_path=baseline.path,
        target_entry_ref=_entry_ref(baseline),
        reason="Replace the exact prior preference after user confirmation",
        source_refs=(),
        operation_id="sqlite-promotion-propose-replacement",
    )
    authorizer = FakePromotionConfirmationAuthorizer(repository.target_namespace)
    confirmation = PromotionConfirmationService(repository, confirmations=authorizer)
    receipt = authorizer.issue(proposal_ref=_proposal_ref(proposal), approved=True)

    applied = confirmation.decide(
        ref=_proposal_ref(proposal),
        expected_revision=proposal.revision,
        confirmation=receipt,
        operation_id="sqlite-promotion-confirm-replacement",
    )

    assert applied.applied_entry_ref is not None
    assert applied.applied_entry_ref.resource_id == baseline.entry_id
    assert applied.applied_entry_ref.revision == baseline.revision + 1
    replacement = repository.read_target(ref=applied.applied_entry_ref)
    assert (
        target_repository.read_content(
            ref=replacement.content_ref,
            offset=0,
            limit=4096,
        ).data
        == b"Prefer the local provider.\n"
    )


def test_sqlite_pending_proposal_can_be_decided_after_store_restart(
    tmp_path: Path,
) -> None:
    _, _, source, _, repository, proposals = _stack(tmp_path)
    proposal = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/provider.md",
        reason="Apply after reopening the durable store",
        source_refs=(),
        operation_id="sqlite-promotion-propose-before-restart",
    )
    reopened = SQLitePromotionV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    ).bind(
        source_space=repository.source_space,
        source_owner_chat_id="chat-promotion",
        target_namespace=repository.target_namespace,
        target_space_id=repository.target_space_id,
    )
    authorizer = FakePromotionConfirmationAuthorizer(reopened.target_namespace)
    confirmation = PromotionConfirmationService(reopened, confirmations=authorizer)
    receipt = authorizer.issue(proposal_ref=_proposal_ref(proposal), approved=True)

    applied = confirmation.decide(
        ref=_proposal_ref(proposal),
        expected_revision=proposal.revision,
        confirmation=receipt,
        operation_id="sqlite-promotion-confirm-after-restart",
    )

    assert applied.status is PromotionStatus.APPLIED
    assert applied.applied_entry_ref is not None
    assert reopened.read_target(ref=applied.applied_entry_ref).revision == 1


def test_sqlite_pending_proposal_replay_rejects_payload_drift(
    tmp_path: Path,
) -> None:
    _, _, source, _, _, proposals = _stack(tmp_path)
    arguments = {
        "source_ref": _entry_ref(source),
        "target_path": "/preferences/provider.md",
        "reason": "Remember the confirmed provider preference",
        "source_refs": (),
        "operation_id": "sqlite-promotion-pending-replay",
    }

    first = proposals.propose(**arguments)
    assert proposals.propose(**arguments) == first
    with pytest.raises(RepositoryConflictError, match="operation"):
        proposals.propose(
            **{
                **arguments,
                "reason": "A changed reason must not reuse the operation",
            }
        )
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        revisions = connection.execute(
            "SELECT COUNT(*) FROM promotion_revisions WHERE proposal_id = ?",
            (first.proposal_id,),
        ).fetchone()[0]
    assert revisions == 1


def test_sqlite_lists_only_current_revisions_in_its_exact_bound_scope(
    tmp_path: Path,
) -> None:
    _, _, source, _, repository, proposals = _stack(tmp_path)
    pending = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/pending.md",
        reason="Keep this proposal pending",
        source_refs=(),
        operation_id="sqlite-promotion-list-pending",
    )
    rejected_pending = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/rejected.md",
        reason="Reject this proposal",
        source_refs=(),
        operation_id="sqlite-promotion-list-rejected",
    )
    authorizer = FakePromotionConfirmationAuthorizer(repository.target_namespace)
    rejected = PromotionConfirmationService(
        repository,
        confirmations=authorizer,
    ).decide(
        ref=_proposal_ref(rejected_pending),
        expected_revision=rejected_pending.revision,
        confirmation=authorizer.issue(
            proposal_ref=_proposal_ref(rejected_pending),
            approved=False,
        ),
        operation_id="sqlite-promotion-list-reject-decision",
    )

    current = repository.list_current(limit=100)

    assert {item.proposal_id for item in current} == {
        pending.proposal_id,
        rejected.proposal_id,
    }
    assert rejected_pending not in current
    assert repository.list_current(
        status=PromotionStatus.PENDING,
        limit=100,
    ) == (pending,)
    assert repository.list_current(
        status=PromotionStatus.REJECTED,
        limit=100,
    ) == (rejected,)


def test_sqlite_current_listing_is_restart_safe_and_limit_bounded(
    tmp_path: Path,
) -> None:
    _, _, source, _, repository, proposals = _stack(tmp_path)
    created = tuple(
        proposals.propose(
            source_ref=_entry_ref(source),
            target_path=f"/preferences/list-{index}.md",
            reason=f"List proposal {index}",
            source_refs=(),
            operation_id=f"sqlite-promotion-list-{index}",
        )
        for index in range(3)
    )
    reopened = SQLitePromotionV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    ).bind(
        source_space=repository.source_space,
        source_owner_chat_id="chat-promotion",
        target_namespace=repository.target_namespace,
        target_space_id=repository.target_space_id,
    )

    assert reopened.list_current(limit=2) == tuple(reversed(created[-2:]))
    assert {
        item.proposal_id for item in reopened.list_current(limit=100)
    } == {item.proposal_id for item in created}
    with pytest.raises(ValueError, match="between 1 and 1000"):
        reopened.list_current(limit=0)
    with pytest.raises(ValueError, match="between 1 and 1000"):
        reopened.list_current(limit=1_001)
    with pytest.raises(TypeError, match="PromotionStatus"):
        reopened.list_current(status="pending", limit=100)


def test_sqlite_replacement_rejects_a_stale_target_without_partial_decision(
    tmp_path: Path,
) -> None:
    memory_store, _, source, references, repository, proposals = _stack(tmp_path)
    target_repository = memory_store.bind_workspace(
        space=MemorySpace(
            repository.target_space_id,
            repository.target_namespace,
            "Long-term memory",
            "Namespaced durable long-term memory",
            1,
        ),
        owner_chat_id=None,
    )
    target_workspace = MemoryWorkspaceService(
        repository=target_repository,
        mutations=target_repository,
        content=target_repository,
        history=target_repository,
        links=target_repository,
        references=references,
    )
    baseline = target_workspace.write_markdown(
        path="/preferences/provider.md",
        description="Previous provider preference",
        content="Prefer the hosted provider.\n",
        expected_space_revision=1,
        source_refs=(_event(),),
        operation_id="seed-sqlite-stale-promotion-target",
    )
    references.allowed.add(_entry_ref(baseline))
    proposal = proposals.propose(
        source_ref=_entry_ref(source),
        target_path=baseline.path,
        target_entry_ref=_entry_ref(baseline),
        reason="This exact baseline may be replaced after confirmation",
        source_refs=(),
        operation_id="sqlite-promotion-propose-stale-target",
    )
    advanced = target_workspace.write_markdown(
        path=baseline.path,
        description="A concurrent confirmed update",
        content="Prefer a different provider.\n",
        expected_space_revision=target_repository.space.revision,
        source_refs=(_event(),),
        operation_id="advance-sqlite-stale-promotion-target",
        entry_ref=_entry_ref(baseline),
    )
    authorizer = FakePromotionConfirmationAuthorizer(repository.target_namespace)
    confirmation = PromotionConfirmationService(repository, confirmations=authorizer)
    receipt = authorizer.issue(proposal_ref=_proposal_ref(proposal), approved=True)

    with pytest.raises(RepositoryConflictError, match="baseline"):
        confirmation.decide(
            ref=_proposal_ref(proposal),
            expected_revision=proposal.revision,
            confirmation=receipt,
            operation_id="sqlite-promotion-confirm-stale-target",
        )

    assert (
        repository.read(ref=_proposal_ref(proposal)).status is PromotionStatus.PENDING
    )
    assert repository.read_target(ref=_entry_ref(advanced)) == advanced


def test_sqlite_concurrent_approvals_have_one_exact_target_winner(
    tmp_path: Path,
) -> None:
    _, _, source, _, repository, proposals = _stack(tmp_path)
    proposal_a = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/provider.md",
        reason="First competing proposal",
        source_refs=(),
        operation_id="sqlite-promotion-propose-concurrent-a",
    )
    proposal_b = proposals.propose(
        source_ref=_entry_ref(source),
        target_path="/preferences/provider.md",
        reason="Second competing proposal",
        source_refs=(),
        operation_id="sqlite-promotion-propose-concurrent-b",
    )

    def decide(proposal, suffix: str):
        rebound = SQLitePromotionV2Store(
            database_path=tmp_path / "context_v2.sqlite3",
            object_directory=tmp_path / "objects",
        ).bind(
            source_space=repository.source_space,
            source_owner_chat_id="chat-promotion",
            target_namespace=repository.target_namespace,
            target_space_id=repository.target_space_id,
        )
        authorizer = FakePromotionConfirmationAuthorizer(rebound.target_namespace)
        confirmation = PromotionConfirmationService(
            rebound,
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
            operation_id=f"sqlite-promotion-concurrent-decision-{suffix}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(decide, proposal_a, "a"),
            executor.submit(decide, proposal_b, "b"),
        )
    outcomes = []
    failures = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except RepositoryConflictError as exc:
            failures.append(exc)

    assert len(outcomes) == 1
    assert outcomes[0].status is PromotionStatus.APPLIED
    assert len(failures) == 1
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        target_heads = connection.execute(
            "SELECT COUNT(*) FROM entries WHERE space_id = ? AND deleted = 0",
            (repository.target_space_id,),
        ).fetchone()[0]
    assert target_heads == 1
