from __future__ import annotations

import inspect

import pytest

from unchain.journal import ResourceRef
from unchain.memory.workspace import MemorySpace, PromotionStatus
from unchain.memory.workspace.ports import (
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryScopeError,
    WorkspaceRepositoryError,
)
from unchain.memory.workspace.promotions import (
    PromotionConfirmationService,
    PromotionService,
    UserConfirmationReceipt,
)
from unchain.memory.workspace.service import LongTermMemoryService, MemoryWorkspaceService

from .fakes import (
    FakePromotionRepository,
    FakeReferenceAuthorizer,
    FakeWorkspaceRepository,
    entry_ref,
    source_event,
)
from . import fakes as memory_fakes


def build_promotion_stack():
    chat_space = MemorySpace("space-chat", "chat", "Chat memory", "Synthetic", 1)
    chat_repository = FakeWorkspaceRepository(chat_space)
    event = source_event()
    references = FakeReferenceAuthorizer("chat-binding", {event})
    workspace = MemoryWorkspaceService(
        repository=chat_repository,
        mutations=chat_repository,
        content=chat_repository,
        history=chat_repository,
        links=chat_repository,
        references=references,
    )
    source = workspace.write_markdown(
        path="/preferences/provider.md",
        description="Provider preference confirmed by the user",
        content="Prefer the local provider.",
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="seed-promotion-source",
    )
    references.allowed.add(entry_ref(source))
    proposals = FakePromotionRepository(chat_repository.space, "user:synthetic")
    return workspace, source, proposals, references


def test_promotion_only_creates_a_pending_confirmation_gated_proposal() -> None:
    workspace, source, repository, references = build_promotion_stack()
    service = PromotionService(
        source_repository=workspace.repository,
        proposals=repository,
        references=references,
    )

    proposal = service.propose(
        source_ref=entry_ref(source),
        target_path="/preferences/provider.md",
        reason="Explicitly confirmed preference worth retaining",
        source_refs=(source_event(),),
        operation_id="propose-provider",
    )

    assert proposal.status is PromotionStatus.PENDING
    assert proposal.target_namespace == "user:synthetic"
    assert proposal.source_entry_ref == entry_ref(source)
    assert repository.decision_calls == 0
    assert repository.long_term_writes == 0
    assert "namespace" not in inspect.signature(service.propose).parameters


def test_promotion_confirmation_requires_an_explicit_user_receipt_and_cas() -> None:
    workspace, source, repository, references = build_promotion_stack()
    proposals = PromotionService(
        source_repository=workspace.repository,
        proposals=repository,
        references=references,
    )
    proposal = proposals.propose(
        source_ref=entry_ref(source),
        target_path="/preferences/provider.md",
        reason="Explicit preference",
        source_refs=(source_event(),),
        operation_id="propose-for-confirmation",
    )
    ref = ResourceRef(
        "promotion",
        proposal.proposal_id,
        proposal.revision,
        proposal.target_namespace,
    )
    authorizer = memory_fakes.FakePromotionConfirmationAuthorizer(
        repository.target_namespace
    )
    confirmation = PromotionConfirmationService(
        repository,
        confirmations=authorizer,
    )

    with pytest.raises(PermissionError):
        confirmation.decide(
            ref=ref,
            expected_revision=1,
            confirmation=UserConfirmationReceipt(
                confirmation_id="confirmation-forged",
                approved=False,
            ),
            operation_id="unconfirmed-decision",
        )
    receipt = authorizer.issue(proposal_ref=ref, approved=True)
    applied = confirmation.decide(
        ref=ref,
        expected_revision=1,
        confirmation=receipt,
        operation_id="confirmed-decision",
    )

    assert applied.status is PromotionStatus.APPLIED
    assert repository.long_term_writes == 1
    with pytest.raises(
        (RepositoryConflictError, RepositoryNotFoundError, RepositoryScopeError)
    ):
        stale_receipt = authorizer.issue(proposal_ref=ref, approved=True)
        confirmation.decide(
            ref=ref,
            expected_revision=1,
            confirmation=stale_receipt,
            operation_id="stale-decision",
        )


def test_promotion_proposal_and_decision_replays_are_idempotent() -> None:
    workspace, source, repository, references = build_promotion_stack()
    proposals = PromotionService(
        source_repository=workspace.repository,
        proposals=repository,
        references=references,
    )
    propose_arguments = {
        "source_ref": entry_ref(source),
        "target_path": "/preferences/provider.md",
        "reason": "Stable replay semantics",
        "source_refs": (source_event(),),
        "operation_id": "replayed-proposal",
    }

    proposal = proposals.propose(**propose_arguments)
    assert proposals.propose(**propose_arguments) == proposal
    with pytest.raises(RepositoryConflictError, match="operation payload changed"):
        proposals.propose(
            **{
                **propose_arguments,
                "target_path": "/preferences/changed-provider.md",
            }
        )

    ref = ResourceRef(
        "promotion",
        proposal.proposal_id,
        proposal.revision,
        proposal.target_namespace,
    )
    authorizer = memory_fakes.FakePromotionConfirmationAuthorizer(
        repository.target_namespace
    )
    confirmation = PromotionConfirmationService(
        repository,
        confirmations=authorizer,
    )
    receipt = authorizer.issue(proposal_ref=ref, approved=True)
    decision_arguments = {
        "ref": ref,
        "expected_revision": 1,
        "confirmation": receipt,
        "operation_id": "replayed-decision",
    }

    applied = confirmation.decide(**decision_arguments)
    assert confirmation.decide(**decision_arguments) == applied
    assert repository.decision_calls == 1
    assert repository.long_term_writes == 1
    with pytest.raises(RepositoryConflictError, match="operation payload changed"):
        confirmation.decide(
            **{
                **decision_arguments,
                "confirmation": UserConfirmationReceipt(
                    confirmation_id=receipt.confirmation_id,
                    approved=False,
                ),
            }
        )


def test_promotion_rejects_cross_scope_source_and_target_paths() -> None:
    workspace, source, repository, references = build_promotion_stack()
    service = PromotionService(
        source_repository=workspace.repository,
        proposals=repository,
        references=references,
    )

    with pytest.raises(RepositoryScopeError):
        service.propose(
            source_ref=ResourceRef("memory", source.entry_id, 1, "space-foreign"),
            target_path="/preferences/provider.md",
            reason="Cross-boundary source",
            source_refs=(source_event(),),
            operation_id="foreign-promotion",
        )
    with pytest.raises(ValueError):
        service.propose(
            source_ref=entry_ref(source),
            target_path="../../host-secret.md",
            reason="Invalid target",
            source_refs=(source_event(),),
            operation_id="invalid-target",
        )


def test_namespaced_long_term_service_is_read_only_and_has_no_scope_parameters() -> None:
    space = MemorySpace(
        "space-long-term",
        "user:synthetic",
        "Long-term memory",
        "Synthetic",
        1,
    )
    repository = FakeWorkspaceRepository(space)
    references = FakeReferenceAuthorizer("long-term-binding", {source_event()})
    writer = MemoryWorkspaceService(
        repository=repository,
        mutations=repository,
        content=repository,
        history=repository,
        links=repository,
        references=references,
    )
    entry = writer.write_markdown(
        path="/preferences/editor.md",
        description="Editor preference",
        content="Use inline diffs.",
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="seed-long-term",
    )
    long_term = LongTermMemoryService(
        binding_id="binding-a",
        repository=repository,
        content=repository,
        history=repository,
    )

    assert long_term.namespace == "user:synthetic"
    assert long_term.read(entry_ref(entry)).data == b"Use inline diffs."
    assert not hasattr(long_term, "write_markdown")
    forbidden = {"user_id", "owner_chat_id", "chat_id", "namespace", "space_id", "scope"}
    for name in ("list", "read", "history", "search"):
        assert not forbidden.intersection(
            inspect.signature(getattr(long_term, name)).parameters
        )


def _proposal_ref(proposal):
    return ResourceRef(
        "promotion",
        proposal.proposal_id,
        proposal.revision,
        proposal.target_namespace,
    )


def test_promotion_binds_target_baseline_and_complete_authorized_provenance() -> None:
    workspace, source, repository, references = build_promotion_stack()
    source_ref = entry_ref(source)
    extra = ResourceRef("artifact", "artifact-promotion", 1)
    references.allowed.update({source_ref, extra})
    target = repository.seed_target(
        path="/preferences/provider.md",
        revision=2,
    )
    service = PromotionService(
        source_repository=workspace.repository,
        proposals=repository,
        references=references,
    )

    proposal = service.propose(
        source_ref=source_ref,
        target_path=target.path,
        target_entry_ref=entry_ref(target),
        reason="Replace the confirmed provider preference",
        source_refs=(extra,),
        operation_id="proposal-with-baseline",
    )

    assert proposal.target_entry_ref == entry_ref(target)
    assert proposal.applied_entry_ref is None
    assert proposal.source_refs == (
        source_event(),
        source_ref,
        extra,
    )

    repository.advance_target(target)
    with pytest.raises(RepositoryConflictError, match="baseline"):
        service.propose(
            source_ref=source_ref,
            target_path=target.path,
            target_entry_ref=entry_ref(target),
            reason="Stale target revision",
            source_refs=(extra,),
            operation_id="proposal-stale-baseline",
        )


def test_promotion_reauthorizes_original_and_rejects_disallowed_provenance() -> None:
    workspace, source, repository, references = build_promotion_stack()
    source_ref = entry_ref(source)
    references.allowed.add(source_ref)
    references.allowed.remove(source_event())
    service = PromotionService(
        source_repository=workspace.repository,
        proposals=repository,
        references=references,
    )

    with pytest.raises(RepositoryScopeError):
        service.propose(
            source_ref=source_ref,
            target_path="/preferences/provider.md",
            reason="Original provenance is no longer authorized",
            source_refs=(),
            operation_id="proposal-unauthorized-original",
        )

    disallowed = ResourceRef("secret", "secret-handle", 1)
    references.allowed.update({source_event(), disallowed})
    with pytest.raises(RepositoryScopeError, match="kind"):
        service.propose(
            source_ref=source_ref,
            target_path="/preferences/provider.md",
            reason="Secret handles are never promotion provenance",
            source_refs=(disallowed,),
            operation_id="proposal-disallowed-provenance",
        )


def test_promotion_confirmation_is_one_use_bound_and_returns_applied_target_ref() -> None:
    workspace, source, repository, references = build_promotion_stack()
    source_ref = entry_ref(source)
    references.allowed.add(source_ref)
    proposals = PromotionService(
        source_repository=workspace.repository,
        proposals=repository,
        references=references,
    )
    proposal = proposals.propose(
        source_ref=source_ref,
        target_path="/preferences/provider.md",
        reason="Apply after a host-confirmed decision",
        source_refs=(),
        operation_id="proposal-host-confirmed",
    )
    ref = _proposal_ref(proposal)
    authorizer = memory_fakes.FakePromotionConfirmationAuthorizer(
        repository.target_namespace
    )
    receipt = authorizer.issue(
        proposal_ref=ref,
        approved=True,
    )
    confirmation = PromotionConfirmationService(
        repository,
        confirmations=authorizer,
    )

    applied = confirmation.decide(
        ref=ref,
        expected_revision=1,
        confirmation=receipt,
        operation_id="host-confirmed-decision",
    )
    replayed = confirmation.decide(
        ref=ref,
        expected_revision=1,
        confirmation=receipt,
        operation_id="host-confirmed-decision",
    )

    assert replayed == applied
    assert applied.status is PromotionStatus.APPLIED
    assert applied.applied_entry_ref is not None
    target = repository.read_target(ref=applied.applied_entry_ref)
    assert target.path == proposal.target_path
    assert authorizer.consume_calls == 1
    assert repository.long_term_writes == 1
    with pytest.raises(PermissionError, match="consumed"):
        confirmation.decide(
            ref=ref,
            expected_revision=1,
            confirmation=receipt,
            operation_id="reuse-confirmation-for-new-operation",
        )


def test_promotion_confirmation_rejects_wrong_or_forged_receipt_binding() -> None:
    workspace, source, repository, references = build_promotion_stack()
    source_ref = entry_ref(source)
    references.allowed.add(source_ref)
    proposals = PromotionService(
        source_repository=workspace.repository,
        proposals=repository,
        references=references,
    )
    proposal = proposals.propose(
        source_ref=source_ref,
        target_path="/preferences/provider.md",
        reason="Require an exact host receipt binding",
        source_refs=(),
        operation_id="proposal-receipt-binding",
    )
    ref = _proposal_ref(proposal)
    authorizer = memory_fakes.FakePromotionConfirmationAuthorizer(
        repository.target_namespace
    )
    wrong = authorizer.issue(
        proposal_ref=ResourceRef(
            "promotion",
            "another-proposal",
            1,
            repository.target_namespace,
        ),
        approved=True,
    )
    confirmation = PromotionConfirmationService(
        repository,
        confirmations=authorizer,
    )

    with pytest.raises(PermissionError, match="binding"):
        confirmation.decide(
            ref=ref,
            expected_revision=1,
            confirmation=wrong,
            operation_id="wrong-receipt-binding",
        )
    with pytest.raises(PermissionError):
        confirmation.decide(
            ref=ref,
            expected_revision=1,
            confirmation=UserConfirmationReceipt(
                confirmation_id="forged-confirmation",
                approved=True,
            ),
            operation_id="forged-receipt",
        )


def test_promotion_decision_rejects_stale_target_and_unproven_or_mutated_apply() -> None:
    workspace, source, repository, references = build_promotion_stack()
    source_ref = entry_ref(source)
    references.allowed.add(source_ref)
    target = repository.seed_target(
        path="/preferences/provider.md",
        revision=1,
    )
    proposals = PromotionService(
        source_repository=workspace.repository,
        proposals=repository,
        references=references,
    )
    proposal = proposals.propose(
        source_ref=source_ref,
        target_path=target.path,
        target_entry_ref=entry_ref(target),
        reason="CAS replace the target",
        source_refs=(),
        operation_id="proposal-stale-at-decision",
    )
    ref = _proposal_ref(proposal)
    authorizer = memory_fakes.FakePromotionConfirmationAuthorizer(
        repository.target_namespace
    )
    receipt = authorizer.issue(proposal_ref=ref, approved=True)
    repository.advance_target(target)
    confirmation = PromotionConfirmationService(
        repository,
        confirmations=authorizer,
    )

    with pytest.raises(RepositoryConflictError, match="baseline"):
        confirmation.decide(
            ref=ref,
            expected_revision=1,
            confirmation=receipt,
            operation_id="stale-target-decision",
        )
    assert repository.long_term_writes == 0

    for flag in ("return_unwritten_applied", "mutate_decision_diff", "mutate_decision_provenance"):
        fresh_workspace, fresh_source, fresh_repository, fresh_references = (
            build_promotion_stack()
        )
        fresh_source_ref = entry_ref(fresh_source)
        fresh_references.allowed.add(fresh_source_ref)
        fresh_service = PromotionService(
            source_repository=fresh_workspace.repository,
            proposals=fresh_repository,
            references=fresh_references,
        )
        fresh_proposal = fresh_service.propose(
            source_ref=fresh_source_ref,
            target_path="/preferences/provider.md",
            reason="Reject divergent decision output",
            source_refs=(),
            operation_id=f"proposal-{flag}",
        )
        fresh_ref = _proposal_ref(fresh_proposal)
        fresh_authorizer = memory_fakes.FakePromotionConfirmationAuthorizer(
            fresh_repository.target_namespace
        )
        fresh_receipt = fresh_authorizer.issue(
            proposal_ref=fresh_ref,
            approved=True,
        )
        setattr(fresh_repository, flag, True)

        with pytest.raises(WorkspaceRepositoryError):
            PromotionConfirmationService(
                fresh_repository,
                confirmations=fresh_authorizer,
            ).decide(
                ref=fresh_ref,
                expected_revision=1,
                confirmation=fresh_receipt,
                operation_id=f"decision-{flag}",
            )
