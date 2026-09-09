from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass

from unchain.context.models import PinnedTaskState
from unchain.journal import OperationRef, ResourceRef
from unchain.journal.models import _bounded_int, _required_text

from .models import (
    MemoryEntry,
    MemoryEntryPage,
    MemoryLink,
    MemorySpace,
    PromotionProposal,
    PromotionStatus,
)


class WorkspaceRepositoryError(RuntimeError):
    """Base error for a bound memory workspace capability."""


class RepositoryConflictError(WorkspaceRepositoryError):
    """The expected revision or operation payload conflicted."""


class RepositoryNotFoundError(WorkspaceRepositoryError):
    """A requested entry does not exist in the bound workspace."""


class RepositoryScopeError(RepositoryNotFoundError):
    """Hide records outside the capability's bound workspace."""


class RepositorySearchUnavailableError(WorkspaceRepositoryError):
    """The bound lexical index is temporarily unavailable."""


class _SpaceBoundCapability(ABC):
    def __init__(self, space: MemorySpace) -> None:
        if not isinstance(space, MemorySpace):
            raise TypeError("space must be a MemorySpace")
        self._space = space

    @property
    def space(self) -> MemorySpace:
        return self._space


@dataclass(frozen=True)
class WorkspaceMutationRequest:
    """One atomic entry/content mutation in an already-bound workspace."""

    entry: MemoryEntry
    expected_revision: int | None
    expected_space_revision: int
    operation: OperationRef
    content: bytes | None = None
    recursive: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.entry, MemoryEntry):
            raise TypeError("entry must be a MemoryEntry")
        if self.expected_revision is not None:
            object.__setattr__(
                self,
                "expected_revision",
                _bounded_int(
                    self.expected_revision,
                    "expected_revision",
                    minimum=1,
                ),
            )
        object.__setattr__(
            self,
            "expected_space_revision",
            _bounded_int(
                self.expected_space_revision,
                "expected_space_revision",
                minimum=1,
            ),
        )
        if not isinstance(self.operation, OperationRef):
            object.__setattr__(
                self,
                "operation",
                OperationRef.from_dict(self.operation),
            )
        if self.content is not None and not isinstance(self.content, bytes):
            raise TypeError("content must be bytes")
        if not isinstance(self.recursive, bool):
            raise TypeError("recursive must be a boolean")
        if self.recursive and (
            not self.entry.deleted or self.entry.kind.value != "folder"
        ):
            raise ValueError("recursive mutations require an archived folder entry")


@dataclass(frozen=True)
class WorkspaceContentPage:
    """A bounded byte range returned by a scope-bound content capability."""

    ref: ResourceRef
    media_type: str
    data: bytes
    offset: int
    total_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ResourceRef):
            object.__setattr__(self, "ref", ResourceRef.from_dict(self.ref))
        media_type = _required_text(self.media_type, "media_type", maximum=255)
        if "/" not in media_type:
            raise ValueError("media_type must be a MIME type")
        object.__setattr__(self, "media_type", media_type)
        if not isinstance(self.data, bytes):
            raise TypeError("data must be bytes")
        object.__setattr__(self, "offset", _bounded_int(self.offset, "offset"))
        object.__setattr__(
            self,
            "total_bytes",
            _bounded_int(self.total_bytes, "total_bytes"),
        )
        if self.offset > self.total_bytes or self.offset + len(self.data) > self.total_bytes:
            raise ValueError("content page exceeds the declared byte range")

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.data) < self.total_bytes

    @property
    def next_offset(self) -> int:
        return self.offset + len(self.data)


@dataclass(frozen=True)
class WorkspaceLinkRequest:
    """One idempotent relation-link creation in a bound workspace."""

    link: MemoryLink
    source_entry_ref: ResourceRef
    expected_space_revision: int
    source_refs: tuple[ResourceRef, ...]
    operation: OperationRef

    def __post_init__(self) -> None:
        if not isinstance(self.link, MemoryLink):
            raise TypeError("link must be a MemoryLink")
        if not isinstance(self.source_entry_ref, ResourceRef):
            object.__setattr__(
                self,
                "source_entry_ref",
                ResourceRef.from_dict(self.source_entry_ref),
            )
        object.__setattr__(
            self,
            "expected_space_revision",
            _bounded_int(
                self.expected_space_revision,
                "expected_space_revision",
                minimum=1,
            ),
        )
        object.__setattr__(
            self,
            "source_refs",
            tuple(
                item if isinstance(item, ResourceRef) else ResourceRef.from_dict(item)
                for item in self.source_refs
            ),
        )
        if not isinstance(self.operation, OperationRef):
            object.__setattr__(
                self,
                "operation",
                OperationRef.from_dict(self.operation),
            )


@dataclass(frozen=True)
class PromotionConfirmationGrant:
    """One host-authorized decision bound to an exact proposal revision."""

    confirmation_id: str
    proposal_ref: ResourceRef
    target_namespace: str
    approved: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "confirmation_id",
            _required_text(self.confirmation_id, "confirmation_id", identifier=True),
        )
        if not isinstance(self.proposal_ref, ResourceRef):
            object.__setattr__(
                self,
                "proposal_ref",
                ResourceRef.from_dict(self.proposal_ref),
            )
        if self.proposal_ref.kind != "promotion":
            raise ValueError("proposal_ref must be a promotion reference")
        object.__setattr__(
            self,
            "target_namespace",
            _required_text(
                self.target_namespace,
                "target_namespace",
                identifier=True,
            ),
        )
        if not isinstance(self.approved, bool):
            raise TypeError("approved must be a boolean")


class BoundMemoryWorkspaceRepository(ABC):
    """Workspace capability constructed for one immutable memory space."""

    def __init__(self, space: MemorySpace) -> None:
        if not isinstance(space, MemorySpace):
            raise TypeError("space must be a MemorySpace")
        self._space = space

    @property
    def space(self) -> MemorySpace:
        return self._space

    @abstractmethod
    def list_entries(
        self,
        *,
        parent_path: str = "/",
        include_deleted: bool = False,
        limit: int = 100,
        cursor: str | None = None,
    ) -> MemoryEntryPage:
        """List entries without allowing the caller to replace the bound space."""

    @abstractmethod
    def search(self, *, query: str, limit: int = 20) -> tuple[MemoryEntry, ...]:
        """Search only the bound workspace."""

    @abstractmethod
    def read_entry(self, *, ref: ResourceRef) -> MemoryEntry:
        """Resolve an entry reference only inside the bound workspace."""

    @abstractmethod
    def read_current_entry(self, *, entry_id: str) -> MemoryEntry:
        """Return the current revision for one entry in the bound workspace."""

    @abstractmethod
    def compare_and_swap(
        self,
        *,
        entry: MemoryEntry,
        expected_revision: int | None,
        operation: OperationRef,
    ) -> MemoryEntry:
        """Apply an idempotent entry mutation under a revision precondition."""


class BoundWorkspaceHierarchyRepository(_SpaceBoundCapability):
    """Direct-child hierarchy reads bound to one immutable workspace scope."""

    @abstractmethod
    def list_children(
        self,
        *,
        parent_path: str = "/",
        expected_space_revision: int,
        limit: int = 100,
        cursor: str | None = None,
    ):
        """Page live direct children without materializing the descendant tree."""


class BoundWorkspaceProjectionRepository(_SpaceBoundCapability):
    """Durable projection receipts bound to one immutable workspace scope."""

    @abstractmethod
    def supersede_projection(self, **kwargs): ...

    @abstractmethod
    def commit_projection(self, **kwargs): ...

    @abstractmethod
    def list_projection_points(self, **kwargs): ...


class BoundWorkspaceMutationRepository(_SpaceBoundCapability):
    """Atomically persist an entry revision and any accompanying content.

    Host-backed implementations must validate their immutable host scope and
    provenance inside the same atomic transaction as operation replay and CAS.
    """

    @abstractmethod
    def apply(self, *, request: WorkspaceMutationRequest) -> MemoryEntry:
        """Apply one idempotent CAS mutation without accepting a caller scope.

        The bound host scope, source provenance, replay receipt, and revision
        preconditions are one indivisible persistence decision.
        """


class BoundWorkspaceContentRepository(_SpaceBoundCapability):
    """Read content only from one pre-authorized workspace."""

    @abstractmethod
    def read_content(
        self,
        *,
        ref: ResourceRef,
        offset: int = 0,
        limit: int = 32 * 1024,
    ) -> WorkspaceContentPage:
        """Read a bounded byte range."""


class BoundWorkspaceHistoryRepository(_SpaceBoundCapability):
    """Read immutable entry revisions in one bound workspace."""

    @abstractmethod
    def list_revisions(
        self,
        *,
        ref: ResourceRef,
        before_revision: int | None = None,
        limit: int = 20,
    ) -> tuple[MemoryEntry, ...]:
        """Return revisions newest first, never crossing the bound space."""


class BoundWorkspaceLinkRepository(_SpaceBoundCapability):
    """Store entry-to-entry relations separately from URL entries."""

    @abstractmethod
    def create_link(self, *, request: WorkspaceLinkRequest) -> MemoryLink:
        """Create or idempotently replay one relation link."""

    @abstractmethod
    def list_links(
        self,
        *,
        source_entry_ref: ResourceRef,
        limit: int = 100,
    ) -> tuple[MemoryLink, ...]:
        """List relations whose source belongs to this bound workspace."""

    @abstractmethod
    def list_backlinks(
        self,
        *,
        target_entry_ref: ResourceRef,
        limit: int = 100,
    ) -> tuple[MemoryLink, ...]:
        """List relations whose exact target revision is in this workspace."""


class BoundWorkspaceReferenceAuthorizer(ABC):
    """Authorize structured provenance refs inside one immutable host scope."""

    def __init__(self, binding_id: str) -> None:
        self._binding_id = _required_text(binding_id, "binding_id", identifier=True)

    @property
    def binding_id(self) -> str:
        return self._binding_id

    @abstractmethod
    def authorize(self, *, ref: ResourceRef) -> ResourceRef:
        """Return the canonical ref or fail without revealing a foreign record."""


class BoundPinnedTaskStateRepository(ABC):
    """Pinned task-state capability bound to one chat/execution lineage."""

    def __init__(self, binding_id: str, state_id: str | None = None) -> None:
        self._binding_id = _required_text(binding_id, "binding_id", identifier=True)
        default_state_id = "task-state-" + hashlib.sha256(
            self._binding_id.encode("utf-8")
        ).hexdigest()[:32]
        self._state_id = _required_text(
            state_id or default_state_id,
            "state_id",
            identifier=True,
        )

    @property
    def binding_id(self) -> str:
        return self._binding_id

    @property
    def state_id(self) -> str:
        return self._state_id

    @abstractmethod
    def current(self) -> PinnedTaskState | None:
        """Return the current pinned state in this binding."""

    @abstractmethod
    def replay(self, *, operation: OperationRef) -> PinnedTaskState | None:
        """Return an earlier result or fail when its operation payload changed."""

    @abstractmethod
    def compare_and_swap(
        self,
        *,
        state: PinnedTaskState,
        expected_revision: int | None,
        operation: OperationRef,
    ) -> PinnedTaskState:
        """Persist exactly one idempotent task-state revision."""


class BoundPromotionRepository(ABC):
    """Proposal store pre-bound to a chat workspace and long-term namespace."""

    def __init__(
        self,
        source_space: MemorySpace,
        target_namespace: str,
        target_space_id: str | None = None,
    ) -> None:
        if not isinstance(source_space, MemorySpace):
            raise TypeError("source_space must be a MemorySpace")
        self._source_space = source_space
        self._target_namespace = _required_text(
            target_namespace,
            "target_namespace",
            identifier=True,
        )
        default_target_space_id = "long-term-" + hashlib.sha256(
            self._target_namespace.encode("utf-8")
        ).hexdigest()[:32]
        self._target_space_id = _required_text(
            target_space_id or default_target_space_id,
            "target_space_id",
            identifier=True,
        )

    @property
    def source_space(self) -> MemorySpace:
        return self._source_space

    @property
    def target_namespace(self) -> str:
        return self._target_namespace

    @property
    def target_space_id(self) -> str:
        return self._target_space_id

    @abstractmethod
    def create(
        self,
        *,
        proposal: PromotionProposal,
        operation: OperationRef,
    ) -> PromotionProposal:
        """Persist a pending proposal; this method never applies it."""

    @abstractmethod
    def replay(self, *, operation: OperationRef) -> PromotionProposal | None:
        """Return an earlier proposal or fail when its operation payload changed."""

    @abstractmethod
    def read(self, *, ref: ResourceRef) -> PromotionProposal:
        """Read a revision in this bound proposal scope."""

    def list_current(
        self,
        *,
        status: PromotionStatus | None = None,
        limit: int = 100,
    ) -> tuple[PromotionProposal, ...]:
        """List current revisions in this exact source/namespace binding."""

        raise NotImplementedError(
            "this promotion repository does not provide current-revision listing"
        )

    @abstractmethod
    def validate_target_baseline(
        self,
        *,
        target_path: str,
        target_entry_ref: ResourceRef | None,
    ) -> MemoryEntry | None:
        """Resolve the exact current target or prove that the path is vacant."""

    @abstractmethod
    def read_target(self, *, ref: ResourceRef) -> MemoryEntry:
        """Read an applied target revision inside the bound long-term space."""


class BoundPromotionDecisionRepository(BoundPromotionRepository):
    """Host-only confirmation capability that may derive long-term content."""

    @abstractmethod
    def replay_decision(
        self,
        *,
        operation: OperationRef,
    ) -> PromotionProposal | None:
        """Return an earlier decision or fail when its operation payload changed."""

    @abstractmethod
    def decide(
        self,
        *,
        ref: ResourceRef,
        expected_revision: int,
        approved: bool,
        confirmation_id: str,
        operation: OperationRef,
    ) -> PromotionProposal:
        """Apply or reject a proposal backed by a host confirmation receipt."""


class BoundPromotionConfirmationAuthorizer(ABC):
    """Host-only, one-use user-confirmation capability for one namespace."""

    def __init__(self, target_namespace: str) -> None:
        self._target_namespace = _required_text(
            target_namespace,
            "target_namespace",
            identifier=True,
        )

    @property
    def target_namespace(self) -> str:
        return self._target_namespace

    @abstractmethod
    def consume(
        self,
        *,
        confirmation_id: str,
        proposal_ref: ResourceRef,
        approved: bool,
    ) -> PromotionConfirmationGrant:
        """Consume exactly one host receipt bound to the supplied decision."""


__all__ = [
    "BoundPinnedTaskStateRepository",
    "BoundMemoryWorkspaceRepository",
    "BoundPromotionConfirmationAuthorizer",
    "BoundPromotionDecisionRepository",
    "BoundPromotionRepository",
    "BoundWorkspaceContentRepository",
    "BoundWorkspaceHistoryRepository",
    "BoundWorkspaceLinkRepository",
    "BoundWorkspaceMutationRepository",
    "BoundWorkspaceReferenceAuthorizer",
    "RepositoryConflictError",
    "RepositoryNotFoundError",
    "RepositorySearchUnavailableError",
    "RepositoryScopeError",
    "PromotionConfirmationGrant",
    "WorkspaceContentPage",
    "WorkspaceLinkRequest",
    "WorkspaceMutationRequest",
    "WorkspaceRepositoryError",
]
