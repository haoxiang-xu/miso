from __future__ import annotations

from dataclasses import replace

from unchain.context import PinnedTaskState
from unchain.journal import OperationRef, ResourceRef
from unchain.memory.workspace import (
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryPage,
    MemorySpace,
    PromotionProposal,
    PromotionStatus,
)
from unchain.memory.workspace.ports import (
    BoundMemoryWorkspaceRepository,
    BoundPinnedTaskStateRepository,
    BoundPromotionConfirmationAuthorizer,
    BoundPromotionDecisionRepository,
    BoundWorkspaceContentRepository,
    BoundWorkspaceHistoryRepository,
    BoundWorkspaceLinkRepository,
    BoundWorkspaceMutationRepository,
    BoundWorkspaceReferenceAuthorizer,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryScopeError,
    RepositorySearchUnavailableError,
    PromotionConfirmationGrant,
    WorkspaceContentPage,
    WorkspaceLinkRequest,
    WorkspaceMutationRequest,
)


class FakeWorkspaceRepository(
    BoundMemoryWorkspaceRepository,
    BoundWorkspaceMutationRepository,
    BoundWorkspaceContentRepository,
    BoundWorkspaceHistoryRepository,
    BoundWorkspaceLinkRepository,
):
    def __init__(self, space: MemorySpace) -> None:
        BoundMemoryWorkspaceRepository.__init__(self, space)
        BoundWorkspaceMutationRepository.__init__(self, space)
        BoundWorkspaceContentRepository.__init__(self, space)
        BoundWorkspaceHistoryRepository.__init__(self, space)
        BoundWorkspaceLinkRepository.__init__(self, space)
        self.entries: dict[str, MemoryEntry] = {}
        self.revisions: dict[str, list[MemoryEntry]] = {}
        self.contents: dict[tuple[str, int], bytes] = {}
        self.operations: dict[str, tuple[str, MemoryEntry]] = {}
        self.search_unavailable = False
        self.search_override: tuple[MemoryEntry, ...] | None = None
        self.links = {}
        self.link_operations: dict[str, tuple[str, object]] = {}
        self.link_provenance = {}
        self.ignore_link_limit = False
        self.diverge_persisted_description = False

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
            for entry in self.entries.values()
            if (parent_path == "/" or entry.path.startswith(prefix))
            and (include_deleted or not entry.deleted)
        ]
        ordered = sorted(matches, key=lambda entry: entry.path.casefold())
        start = 0
        if cursor is not None:
            positions = [
                index for index, entry in enumerate(ordered) if entry.entry_id == cursor
            ]
            if not positions:
                raise RepositoryScopeError("foreign cursor")
            start = positions[0] + 1
        selected = tuple(ordered[start : start + limit])
        has_more = start + len(selected) < len(ordered)
        return MemoryEntryPage(
            entries=selected,
            next_cursor=selected[-1].entry_id if selected and has_more else None,
            has_more=has_more,
        )

    def search(self, *, query: str, limit: int = 20) -> tuple[MemoryEntry, ...]:
        if self.search_unavailable:
            raise RepositorySearchUnavailableError("fts offline")
        if self.search_override is not None:
            return self.search_override[:limit]
        needle = query.casefold()
        matches = [
            entry
            for entry in self.entries.values()
            if not entry.deleted
            and needle
            in f"{entry.path} {entry.name} {entry.description}".casefold()
        ]
        return tuple(sorted(matches, key=lambda entry: entry.path.casefold())[:limit])

    def read_entry(self, *, ref: ResourceRef) -> MemoryEntry:
        self._require_memory_ref(ref)
        revisions = self.revisions.get(ref.resource_id, ())
        for entry in revisions:
            if entry.revision == ref.revision:
                return entry
        raise RepositoryNotFoundError("entry revision not found")

    def read_current_entry(self, *, entry_id: str) -> MemoryEntry:
        entry = self.entries.get(entry_id)
        if entry is None:
            raise RepositoryNotFoundError("entry not found")
        return entry

    def compare_and_swap(
        self,
        *,
        entry: MemoryEntry,
        expected_revision: int | None,
        operation: OperationRef,
    ) -> MemoryEntry:
        return self.apply(
            request=WorkspaceMutationRequest(
                entry=entry,
                expected_revision=expected_revision,
                expected_space_revision=self.space.revision,
                operation=operation,
            )
        )

    def apply(self, *, request: WorkspaceMutationRequest) -> MemoryEntry:
        entry = request.entry
        if entry.space_id != self.space.space_id:
            raise RepositoryScopeError("foreign space")
        previous = self.operations.get(request.operation.operation_id)
        if previous is not None:
            payload_hash, persisted = previous
            if payload_hash != request.operation.payload_sha256:
                raise RepositoryConflictError("operation payload changed")
            return persisted
        if request.expected_space_revision != self.space.revision:
            raise RepositoryConflictError("space revision changed")
        if entry.updated_seq != request.expected_space_revision + 1:
            raise RepositoryConflictError("updated sequence did not advance with the space")
        current = self.entries.get(entry.entry_id)
        actual_revision = current.revision if current is not None else None
        if actual_revision != request.expected_revision:
            raise RepositoryConflictError("entry revision changed")
        expected_next = 1 if current is None else current.revision + 1
        if entry.revision != expected_next:
            raise RepositoryConflictError("revision must advance once")
        descendants = [
            other
            for other in self.entries.values()
            if current is not None
            and current.kind is MemoryEntryKind.FOLDER
            and not other.deleted
            and other.path.startswith(current.path.rstrip("/") + "/")
        ]
        if entry.deleted and descendants and not request.recursive:
            raise RepositoryConflictError("folder contains active descendants")
        if entry.deleted and request.recursive:
            for descendant in descendants:
                tombstone = replace(
                    descendant,
                    revision=descendant.revision + 1,
                    updated_seq=entry.updated_seq,
                    source_refs=entry.source_refs,
                    deleted=True,
                )
                self.entries[tombstone.entry_id] = tombstone
                self.revisions.setdefault(tombstone.entry_id, []).insert(0, tombstone)
        if any(
            other.entry_id != entry.entry_id
            and not other.deleted
            and other.path.casefold() == entry.path.casefold()
            for other in self.entries.values()
        ):
            raise RepositoryConflictError("path collision")
        persisted = entry
        if request.content is not None:
            content_ref = ResourceRef(
                "memory_content",
                f"{entry.entry_id}-{entry.revision}",
                entry.revision,
                self.space.space_id,
            )
            self.contents[(content_ref.resource_id, content_ref.revision)] = request.content
            persisted = replace(entry, content_ref=content_ref)
        elif current is not None and persisted.content_ref is None:
            persisted = replace(persisted, content_ref=current.content_ref)
        self.entries[persisted.entry_id] = persisted
        self.revisions.setdefault(persisted.entry_id, []).insert(0, persisted)
        self.operations[request.operation.operation_id] = (
            request.operation.payload_sha256,
            persisted,
        )
        self._space = replace(self.space, revision=self.space.revision + 1)
        if self.diverge_persisted_description:
            return replace(persisted, description="Repository changed the description")
        return persisted

    def read_content(
        self,
        *,
        ref: ResourceRef,
        offset: int = 0,
        limit: int = 32 * 1024,
    ) -> WorkspaceContentPage:
        if ref.fragment != self.space.space_id or ref.kind != "memory_content":
            raise RepositoryScopeError("foreign content")
        content = self.contents.get((ref.resource_id, ref.revision))
        if content is None:
            raise RepositoryNotFoundError("content not found")
        chunk = content[offset : offset + limit]
        return WorkspaceContentPage(
            ref=ref,
            media_type="application/octet-stream",
            data=chunk,
            offset=offset,
            total_bytes=len(content),
        )

    def list_revisions(
        self,
        *,
        ref: ResourceRef,
        before_revision: int | None = None,
        limit: int = 20,
    ) -> tuple[MemoryEntry, ...]:
        self._require_memory_ref(ref)
        if ref.resource_id not in self.revisions:
            raise RepositoryNotFoundError("entry not found")
        ceiling = before_revision if before_revision is not None else ref.revision + 1
        return tuple(
            entry
            for entry in self.revisions[ref.resource_id]
            if entry.revision < ceiling
        )[:limit]

    def create_link(self, *, request: WorkspaceLinkRequest):
        previous = self.link_operations.get(request.operation.operation_id)
        if previous is not None:
            payload_hash, persisted = previous
            if payload_hash != request.operation.payload_sha256:
                raise RepositoryConflictError("operation payload changed")
            return persisted
        if request.expected_space_revision != self.space.revision:
            raise RepositoryConflictError("space revision changed")
        self._require_memory_ref(request.source_entry_ref)
        self._require_memory_ref(request.link.target_ref)
        if request.link.source_entry_ref != request.source_entry_ref:
            raise RepositoryScopeError("link source reference changed")
        for ref in (request.source_entry_ref, request.link.target_ref):
            current = self.read_current_entry(entry_id=ref.resource_id)
            if current.revision != ref.revision or current.deleted:
                raise RepositoryConflictError("link entry reference is not current")
        self.links[request.link.link_id] = request.link
        self.link_provenance[request.link.link_id] = request.source_refs
        self.link_operations[request.operation.operation_id] = (
            request.operation.payload_sha256,
            request.link,
        )
        self._space = replace(self.space, revision=self.space.revision + 1)
        return request.link

    def list_links(
        self,
        *,
        source_entry_ref: ResourceRef,
        limit: int = 100,
    ):
        self._require_memory_ref(source_entry_ref)
        return tuple(
            link
            for link in self.links.values()
            if link.source_entry_ref == source_entry_ref
        )[: None if self.ignore_link_limit else limit]

    def list_backlinks(
        self,
        *,
        target_entry_ref: ResourceRef,
        limit: int = 100,
    ):
        self._require_memory_ref(target_entry_ref)
        return tuple(
            link
            for link in self.links.values()
            if link.target_ref == target_entry_ref
        )[:limit]

    def _require_memory_ref(self, ref: ResourceRef) -> None:
        if ref.kind != "memory" or ref.fragment != self.space.space_id:
            raise RepositoryScopeError("foreign entry")


class FakeReferenceAuthorizer(BoundWorkspaceReferenceAuthorizer):
    def __init__(self, binding_id: str, allowed: set[ResourceRef]) -> None:
        super().__init__(binding_id)
        self.allowed = allowed

    def authorize(self, *, ref: ResourceRef) -> ResourceRef:
        if ref not in self.allowed:
            raise RepositoryScopeError("foreign reference")
        return ref


class FakeTaskStateRepository(BoundPinnedTaskStateRepository):
    def __init__(self, binding_id: str, state_id: str | None = None) -> None:
        super().__init__(binding_id, state_id)
        self.state: PinnedTaskState | None = None
        self.operations: dict[str, tuple[str, PinnedTaskState]] = {}

    def current(self) -> PinnedTaskState | None:
        return self.state

    def replay(self, *, operation: OperationRef) -> PinnedTaskState | None:
        previous = self.operations.get(operation.operation_id)
        if previous is None:
            return None
        payload_hash, persisted = previous
        if payload_hash != operation.payload_sha256:
            raise RepositoryConflictError("operation payload changed")
        return persisted

    def compare_and_swap(
        self,
        *,
        state: PinnedTaskState,
        expected_revision: int | None,
        operation: OperationRef,
    ) -> PinnedTaskState:
        previous = self.operations.get(operation.operation_id)
        if previous is not None:
            payload_hash, persisted = previous
            if payload_hash != operation.payload_sha256:
                raise RepositoryConflictError("operation payload changed")
            return persisted
        actual = self.state.revision if self.state is not None else None
        if actual != expected_revision:
            raise RepositoryConflictError("task state revision changed")
        expected_next = 1 if actual is None else actual + 1
        if state.revision != expected_next:
            raise RepositoryConflictError("task state revision must advance once")
        self.state = state
        self.operations[operation.operation_id] = (operation.payload_sha256, state)
        return state


class FakePromotionRepository(BoundPromotionDecisionRepository):
    def __init__(
        self,
        source_space: MemorySpace,
        target_namespace: str,
        target_space_id: str = "space-long-term",
    ) -> None:
        super().__init__(source_space, target_namespace, target_space_id)
        self.proposals: dict[str, PromotionProposal] = {}
        self.proposal_revisions: dict[str, list[PromotionProposal]] = {}
        self.proposal_operations: dict[str, tuple[str, PromotionProposal]] = {}
        self.decision_operations: dict[str, tuple[str, PromotionProposal]] = {}
        self.target_entries: dict[str, MemoryEntry] = {}
        self.target_revisions: dict[str, list[MemoryEntry]] = {}
        self.decision_calls = 0
        self.long_term_writes = 0
        self.return_unwritten_applied = False
        self.mutate_decision_diff = False
        self.mutate_decision_provenance = False

    def create(
        self,
        *,
        proposal: PromotionProposal,
        operation: OperationRef,
    ) -> PromotionProposal:
        previous = self.replay(operation=operation)
        if previous is not None:
            return previous
        if proposal.target_namespace != self.target_namespace:
            raise RepositoryScopeError("foreign target namespace")
        self.proposals[proposal.proposal_id] = proposal
        self.proposal_revisions.setdefault(proposal.proposal_id, []).insert(0, proposal)
        self.proposal_operations[operation.operation_id] = (
            operation.payload_sha256,
            proposal,
        )
        return proposal

    def replay(self, *, operation: OperationRef) -> PromotionProposal | None:
        previous = self.proposal_operations.get(operation.operation_id)
        if previous is None:
            return None
        payload_hash, persisted = previous
        if payload_hash != operation.payload_sha256:
            raise RepositoryConflictError("operation payload changed")
        return persisted

    def replay_decision(self, *, operation: OperationRef) -> PromotionProposal | None:
        previous = self.decision_operations.get(operation.operation_id)
        if previous is None:
            return None
        payload_hash, persisted = previous
        if payload_hash != operation.payload_sha256:
            raise RepositoryConflictError("operation payload changed")
        return persisted

    def read(self, *, ref: ResourceRef) -> PromotionProposal:
        if ref.kind != "promotion" or ref.fragment != self.target_namespace:
            raise RepositoryScopeError("foreign proposal")
        for proposal in self.proposal_revisions.get(ref.resource_id, ()):
            if proposal.revision == ref.revision:
                return proposal
        raise RepositoryNotFoundError("proposal not found")

    def validate_target_baseline(
        self,
        *,
        target_path: str,
        target_entry_ref: ResourceRef | None,
    ) -> MemoryEntry | None:
        current = self.target_entries.get(target_path.casefold())
        if target_entry_ref is None:
            return current
        if (
            target_entry_ref.kind != "memory"
            or target_entry_ref.fragment != self.target_space_id
        ):
            raise RepositoryScopeError("foreign target baseline")
        if (
            current is None
            or current.entry_id != target_entry_ref.resource_id
            or current.revision != target_entry_ref.revision
            or current.deleted
        ):
            raise RepositoryConflictError("target baseline changed")
        return current

    def read_target(self, *, ref: ResourceRef) -> MemoryEntry:
        if ref.kind != "memory" or ref.fragment != self.target_space_id:
            raise RepositoryScopeError("foreign target")
        for entry in self.target_revisions.get(ref.resource_id, ()):
            if entry.revision == ref.revision:
                return entry
        raise RepositoryNotFoundError("target was not durably written")

    def seed_target(self, *, path: str, revision: int = 1) -> MemoryEntry:
        entry = MemoryEntry(
            entry_id=f"long-term-{len(self.target_entries) + 1}",
            space_id=self.target_space_id,
            path=path,
            name=path.rsplit("/", 1)[-1],
            description="Existing long-term target",
            kind=MemoryEntryKind.MARKDOWN,
            revision=revision,
            source_refs=(ResourceRef("context_event", "seed-target", 1),),
        )
        self._store_target(entry)
        return entry

    def advance_target(self, target: MemoryEntry) -> MemoryEntry:
        current = self.target_entries.get(target.path.casefold())
        if current != target:
            raise RepositoryConflictError("target baseline changed")
        advanced = replace(target, revision=target.revision + 1)
        self._store_target(advanced)
        return advanced

    def _store_target(self, entry: MemoryEntry) -> None:
        self.target_entries[entry.path.casefold()] = entry
        self.target_revisions.setdefault(entry.entry_id, []).insert(0, entry)

    def decide(
        self,
        *,
        ref: ResourceRef,
        expected_revision: int,
        approved: bool,
        confirmation_id: str,
        operation: OperationRef,
    ) -> PromotionProposal:
        previous = self.replay_decision(operation=operation)
        if previous is not None:
            return previous
        self.decision_calls += 1
        proposal = self.read(ref=ref)
        if proposal.revision != expected_revision:
            raise RepositoryConflictError("promotion revision changed")
        status = PromotionStatus.APPLIED if approved else PromotionStatus.REJECTED
        applied_ref = None
        if approved:
            baseline = proposal.target_entry_ref
            current = self.target_entries.get(proposal.target_path.casefold())
            if baseline is None:
                if current is not None:
                    raise RepositoryConflictError("target baseline changed")
                target = MemoryEntry(
                    entry_id=f"promoted-{proposal.proposal_id}",
                    space_id=self.target_space_id,
                    path=proposal.target_path,
                    name=proposal.target_path.rsplit("/", 1)[-1],
                    description=proposal.reason,
                    kind=MemoryEntryKind.MARKDOWN,
                    revision=1,
                    source_refs=proposal.source_refs,
                )
            else:
                if (
                    current is None
                    or current.entry_id != baseline.resource_id
                    or current.revision != baseline.revision
                ):
                    raise RepositoryConflictError("target baseline changed")
                target = replace(
                    current,
                    revision=current.revision + 1,
                    description=proposal.reason,
                    source_refs=proposal.source_refs,
                )
            applied_ref = entry_ref(target)
            if not self.return_unwritten_applied:
                self._store_target(target)
                self.long_term_writes += 1
        decided = replace(
            proposal,
            status=status,
            revision=proposal.revision + 1,
            applied_entry_ref=applied_ref,
        )
        if self.mutate_decision_diff:
            decided = replace(decided, diff={"op": "mutated"})
        if self.mutate_decision_provenance:
            decided = replace(decided, source_refs=decided.source_refs[:-1])
        self.proposals[proposal.proposal_id] = decided
        self.proposal_revisions.setdefault(proposal.proposal_id, []).insert(0, decided)
        self.decision_operations[operation.operation_id] = (
            operation.payload_sha256,
            decided,
        )
        return decided


class FakePromotionConfirmationAuthorizer(BoundPromotionConfirmationAuthorizer):
    def __init__(self, target_namespace: str) -> None:
        super().__init__(target_namespace)
        self.grants: dict[str, tuple[ResourceRef, bool, bool]] = {}
        self.consume_calls = 0

    def issue(self, *, proposal_ref: ResourceRef, approved: bool):
        from unchain.memory.workspace.promotions import UserConfirmationReceipt

        confirmation_id = f"confirmation-{len(self.grants) + 1}"
        self.grants[confirmation_id] = (proposal_ref, approved, False)
        return UserConfirmationReceipt(confirmation_id, approved)

    def consume(
        self,
        *,
        confirmation_id: str,
        proposal_ref: ResourceRef,
        approved: bool,
    ) -> PromotionConfirmationGrant:
        record = self.grants.get(confirmation_id)
        if record is None:
            raise PermissionError("confirmation receipt is unknown")
        expected_ref, expected_approved, consumed = record
        if consumed:
            raise PermissionError("confirmation receipt was already consumed")
        if expected_ref != proposal_ref or expected_approved is not approved:
            raise PermissionError("confirmation receipt binding does not match")
        self.grants[confirmation_id] = (expected_ref, expected_approved, True)
        self.consume_calls += 1
        return PromotionConfirmationGrant(
            confirmation_id=confirmation_id,
            proposal_ref=proposal_ref,
            target_namespace=self.target_namespace,
            approved=approved,
        )


def entry_ref(entry: MemoryEntry) -> ResourceRef:
    return ResourceRef("memory", entry.entry_id, entry.revision, entry.space_id)


def source_event(identifier: str = "event-1") -> ResourceRef:
    return ResourceRef("context_event", identifier, 1)
