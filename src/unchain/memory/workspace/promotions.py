from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Sequence

from unchain.journal import ModelValidationError, OperationRef, ResourceRef
from unchain.journal.models import _freeze_json, _required_text

from .models import PromotionProposal, PromotionStatus
from .operations import build_operation_ref
from .paths import canonical_entry_path
from .ports import (
    BoundMemoryWorkspaceRepository,
    BoundPromotionConfirmationAuthorizer,
    BoundPromotionDecisionRepository,
    BoundPromotionRepository,
    BoundWorkspaceReferenceAuthorizer,
    RepositoryConflictError,
    RepositoryScopeError,
    WorkspaceRepositoryError,
)


_PROMOTION_SOURCE_KINDS = frozenset(
    {"artifact", "checkpoint", "context_event", "handoff", "memory"}
)
_MAX_PROMOTION_SOURCE_REFS = 512


def _proposal_id(
    source_space_id: str,
    target_namespace: str,
    operation: OperationRef,
) -> str:
    digest = hashlib.sha256(
        f"{source_space_id}\0{target_namespace}\0{operation.operation_id}".encode("utf-8")
    ).hexdigest()
    return f"promotion-{digest[:32]}"


def _bound_source_ref(ref: ResourceRef, space_id: str) -> None:
    if (
        not isinstance(ref, ResourceRef)
        or ref.kind != "memory"
        or ref.fragment != space_id
    ):
        raise RepositoryScopeError("source reference belongs to another workspace")


@dataclass(frozen=True)
class UserConfirmationReceipt:
    """Opaque host receipt descriptor verified by a one-use bound capability."""

    confirmation_id: str
    approved: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "confirmation_id",
            _required_text(
                self.confirmation_id,
                "confirmation_id",
                identifier=True,
            ),
        )
        if not isinstance(self.approved, bool):
            raise TypeError("approved must be a boolean")


class PromotionService:
    """Curator-safe service: it can create proposals and can never apply them."""

    def __init__(
        self,
        *,
        source_repository: BoundMemoryWorkspaceRepository,
        proposals: BoundPromotionRepository,
        references: BoundWorkspaceReferenceAuthorizer,
    ) -> None:
        if not isinstance(source_repository, BoundMemoryWorkspaceRepository):
            raise TypeError("source_repository must be bound")
        if not isinstance(proposals, BoundPromotionRepository):
            raise TypeError("proposals must be a BoundPromotionRepository")
        if not isinstance(references, BoundWorkspaceReferenceAuthorizer):
            raise TypeError("references must be a bound authorizer")
        if proposals.source_space.space_id != source_repository.space.space_id:
            raise RepositoryScopeError("proposal capability has another source workspace")
        self._source_repository = source_repository
        self._proposals = proposals
        self._references = references

    @property
    def binding_id(self) -> str:
        return self._references.binding_id

    @property
    def target_namespace(self) -> str:
        return self._proposals.target_namespace

    @property
    def target_space_id(self) -> str:
        return self._proposals.target_space_id

    def propose(
        self,
        *,
        source_ref: ResourceRef,
        target_path: str,
        reason: str,
        source_refs: Sequence[ResourceRef],
        operation_id: str,
        target_entry_ref: ResourceRef | None = None,
        diff: Mapping[str, object] | None = None,
    ) -> PromotionProposal:
        _bound_source_ref(source_ref, self._source_repository.space.space_id)
        source = self._source_repository.read_entry(ref=source_ref)
        if (
            source.space_id != self._source_repository.space.space_id
            or source.entry_id != source_ref.resource_id
            or source.revision != source_ref.revision
        ):
            raise RepositoryScopeError("source repository returned another entry")
        if source.deleted:
            raise RepositoryConflictError("archived entries cannot be promoted")
        current_source = self._source_repository.read_current_entry(
            entry_id=source.entry_id
        )
        if current_source != source:
            raise RepositoryConflictError("source entry reference is not current")
        path = canonical_entry_path(target_path)
        normalized_reason = _required_text(reason, "reason", maximum=8192)
        effective_refs = self._authorize_provenance(
            (*source.source_refs, source_ref, *source_refs)
        )
        baseline = self._validate_target_baseline(path, target_entry_ref)
        authoritative_diff: dict[str, object] = {
            "op": "replace" if baseline is not None else "derive",
            "source_entry_ref": source_ref.to_dict(),
            "target_path": path,
        }
        if baseline is not None:
            authoritative_diff["target_entry_ref"] = target_entry_ref.to_dict()
        if diff is not None and _freeze_json(diff, path="diff") != _freeze_json(
            authoritative_diff,
            path="diff",
        ):
            raise ModelValidationError("promotion diff does not match its bound inputs")
        proposal_diff: Mapping[str, object] = authoritative_diff
        operation = build_operation_ref(
            operation_id,
            domain="workspace.promotion.propose",
            payload={
                "source_ref": source_ref,
                "target_path": path,
                "target_entry_ref": target_entry_ref,
                "reason": normalized_reason,
                "source_refs": effective_refs,
                "diff": proposal_diff,
            },
        )
        proposal = PromotionProposal(
            proposal_id=_proposal_id(
                self._source_repository.space.space_id,
                self._proposals.target_namespace,
                operation,
            ),
            source_entry_ref=source_ref,
            target_namespace=self._proposals.target_namespace,
            target_path=path,
            diff=proposal_diff,
            reason=normalized_reason,
            status=PromotionStatus.PENDING,
            revision=1,
            source_refs=effective_refs,
            target_entry_ref=target_entry_ref,
        )
        replayed = self._proposals.replay(operation=operation)
        if replayed is not None:
            if replayed != proposal or replayed.status is not PromotionStatus.PENDING:
                raise WorkspaceRepositoryError("proposal replay returned a divergent record")
            return replayed
        persisted = self._proposals.create(proposal=proposal, operation=operation)
        if persisted != proposal or persisted.status is not PromotionStatus.PENDING:
            raise WorkspaceRepositoryError("proposal capability bypassed confirmation")
        return persisted

    def _authorize_provenance(
        self,
        source_refs: Sequence[ResourceRef],
    ) -> tuple[ResourceRef, ...]:
        if isinstance(source_refs, (str, bytes, bytearray)):
            raise TypeError("source_refs must be an array")
        if len(source_refs) > _MAX_PROMOTION_SOURCE_REFS:
            raise ModelValidationError("promotion provenance exceeds the reference limit")
        authorized: list[ResourceRef] = []
        for raw_ref in source_refs:
            ref = raw_ref if isinstance(raw_ref, ResourceRef) else ResourceRef.from_dict(raw_ref)
            if ref.kind not in _PROMOTION_SOURCE_KINDS:
                raise RepositoryScopeError("promotion provenance contains a disallowed kind")
            canonical = self._references.authorize(ref=ref)
            if canonical != ref:
                raise RepositoryScopeError("reference authorizer changed promotion provenance")
            if ref not in authorized:
                authorized.append(ref)
        return tuple(authorized)

    def _validate_target_baseline(
        self,
        target_path: str,
        target_entry_ref: ResourceRef | None,
    ):
        if target_entry_ref is not None and (
            not isinstance(target_entry_ref, ResourceRef)
            or target_entry_ref.kind != "memory"
            or target_entry_ref.fragment != self._proposals.target_space_id
        ):
            raise RepositoryScopeError("target baseline belongs to another long-term space")
        baseline = self._proposals.validate_target_baseline(
            target_path=target_path,
            target_entry_ref=target_entry_ref,
        )
        if target_entry_ref is None:
            if baseline is not None:
                raise RepositoryConflictError("an existing target requires an exact baseline")
            return None
        if (
            baseline is None
            or baseline.deleted
            or baseline.space_id != self._proposals.target_space_id
            or baseline.entry_id != target_entry_ref.resource_id
            or baseline.revision != target_entry_ref.revision
            or baseline.path != target_path
        ):
            raise RepositoryConflictError("target baseline is stale or divergent")
        return baseline


class PromotionConfirmationService:
    """Host-only decision service separated from curator-safe proposal creation."""

    def __init__(
        self,
        repository: BoundPromotionDecisionRepository,
        *,
        confirmations: BoundPromotionConfirmationAuthorizer,
    ) -> None:
        if not isinstance(repository, BoundPromotionDecisionRepository):
            raise TypeError("repository must be a confirmation-capable bound repository")
        if not isinstance(confirmations, BoundPromotionConfirmationAuthorizer):
            raise TypeError("confirmations must be a bound confirmation authorizer")
        if confirmations.target_namespace != repository.target_namespace:
            raise RepositoryScopeError("confirmation capability binds another namespace")
        self._repository = repository
        self._confirmations = confirmations

    def decide(
        self,
        *,
        ref: ResourceRef,
        expected_revision: int,
        confirmation: UserConfirmationReceipt,
        operation_id: str,
    ) -> PromotionProposal:
        if not isinstance(confirmation, UserConfirmationReceipt):
            raise TypeError("confirmation must be a UserConfirmationReceipt")
        if (
            not isinstance(ref, ResourceRef)
            or ref.kind != "promotion"
            or ref.fragment != self._repository.target_namespace
        ):
            raise RepositoryScopeError("proposal reference belongs to another bound namespace")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise ValueError("expected_revision must be a positive integer")
        proposal = self._repository.read(ref=ref)
        if (
            proposal.proposal_id != ref.resource_id
            or proposal.target_namespace != self._repository.target_namespace
        ):
            raise RepositoryScopeError("proposal capability returned a foreign record")
        if proposal.revision != expected_revision or ref.revision != expected_revision:
            raise RepositoryConflictError("promotion revision changed")
        if proposal.status is not PromotionStatus.PENDING:
            raise RepositoryConflictError("promotion is already decided")
        operation = build_operation_ref(
            operation_id,
            domain="workspace.promotion.decide",
            payload={
                "ref": ref,
                "expected_revision": expected_revision,
                "confirmation_id": confirmation.confirmation_id,
                "approved": confirmation.approved,
            },
        )
        required_status = (
            PromotionStatus.APPLIED
            if confirmation.approved
            else PromotionStatus.REJECTED
        )
        replayed = self._repository.replay_decision(operation=operation)
        if replayed is not None:
            return self._validate_decided(
                proposal,
                replayed,
                required_status=required_status,
            )
        grant = self._confirmations.consume(
            confirmation_id=confirmation.confirmation_id,
            proposal_ref=ref,
            approved=confirmation.approved,
        )
        if (
            grant.confirmation_id != confirmation.confirmation_id
            or grant.proposal_ref != ref
            or grant.target_namespace != proposal.target_namespace
            or grant.approved is not confirmation.approved
        ):
            raise PermissionError("confirmation grant does not match the proposal binding")
        decided = self._repository.decide(
            ref=ref,
            expected_revision=expected_revision,
            approved=confirmation.approved,
            confirmation_id=confirmation.confirmation_id,
            operation=operation,
        )
        return self._validate_decided(
            proposal,
            decided,
            required_status=required_status,
        )

    def _validate_decided(
        self,
        proposal: PromotionProposal,
        decided: PromotionProposal,
        *,
        required_status: PromotionStatus,
    ) -> PromotionProposal:
        if not isinstance(decided, PromotionProposal):
            raise WorkspaceRepositoryError("promotion decision returned an invalid record")
        immutable_fields = (
            "proposal_id",
            "source_entry_ref",
            "target_namespace",
            "target_path",
            "target_entry_ref",
            "diff",
            "reason",
            "source_refs",
        )
        if (
            decided.status is not required_status
            or decided.revision != proposal.revision + 1
            or any(
                getattr(decided, field_name) != getattr(proposal, field_name)
                for field_name in immutable_fields
            )
        ):
            raise WorkspaceRepositoryError("promotion decision changed immutable fields")
        if required_status is PromotionStatus.REJECTED:
            if decided.applied_entry_ref is not None:
                raise WorkspaceRepositoryError("rejected promotion returned an applied target")
            return decided
        applied_ref = decided.applied_entry_ref
        if (
            applied_ref is None
            or applied_ref.kind != "memory"
            or applied_ref.fragment != self._repository.target_space_id
        ):
            raise WorkspaceRepositoryError("applied promotion lacks a bound target reference")
        target = self._repository.validate_target_baseline(
            target_path=proposal.target_path,
            target_entry_ref=applied_ref,
        )
        if (
            target is None
            or target.deleted
            or target.entry_id != applied_ref.resource_id
            or target.revision != applied_ref.revision
            or target.path != proposal.target_path
        ):
            raise WorkspaceRepositoryError("applied target reference was not durably written")
        baseline = proposal.target_entry_ref
        if baseline is None:
            if applied_ref.revision != 1:
                raise WorkspaceRepositoryError("new promotion target did not start at revision one")
        elif (
            applied_ref.resource_id != baseline.resource_id
            or applied_ref.revision != baseline.revision + 1
        ):
            raise WorkspaceRepositoryError("replacement promotion did not advance its baseline")
        return decided


__all__ = [
    "PromotionConfirmationService",
    "PromotionService",
    "UserConfirmationReceipt",
]
