from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from unchain.journal import ResourceRef
from unchain.journal.models import _required_text
from unchain.memory.workspace import (
    LongTermMemoryService,
    MemoryEntry,
    MemoryWorkspaceService,
    PromotionService,
    RepositoryConflictError,
    RepositoryScopeError,
    TaskStateService,
)

from .models import (
    MemoryToolContentPage,
    MemoryToolkitError,
    MemoryUpsertRequest,
    TaskStateUpdateRequest,
)


def _bound_mutation_source_refs(
    values: Sequence[ResourceRef],
) -> tuple[ResourceRef, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise MemoryToolkitError("mutation_source_refs must be a sequence")
    refs = tuple(values)
    if not refs:
        raise MemoryToolkitError(
            "mutation_source_refs must include current-run event provenance"
        )
    if any(
        not isinstance(ref, ResourceRef)
        or ref.kind != "context_event"
        or bool(ref.fragment)
        for ref in refs
    ):
        raise MemoryToolkitError(
            "mutation_source_refs must contain bare context_event references"
        )
    if len(set(refs)) != len(refs):
        raise MemoryToolkitError("mutation_source_refs must not contain duplicates")
    return refs


def _merge_mutation_source_refs(
    bound_refs: tuple[ResourceRef, ...],
    cited_refs: Sequence[ResourceRef],
) -> tuple[ResourceRef, ...]:
    if isinstance(cited_refs, (str, bytes, bytearray)) or not isinstance(
        cited_refs, Sequence
    ):
        raise MemoryToolkitError("source_refs must be a sequence")
    merged = list(bound_refs)
    for ref in cited_refs:
        if (
            not isinstance(ref, ResourceRef)
            or ref.kind != "context_event"
            or bool(ref.fragment)
        ):
            raise MemoryToolkitError(
                "source_refs must contain bare context_event references"
            )
        if ref not in merged:
            merged.append(ref)
    return tuple(merged)


class _WorkspaceReadCapability:
    def __init__(
        self,
        *,
        binding_id: str,
        service: MemoryWorkspaceService | LongTermMemoryService,
    ) -> None:
        if not isinstance(service, (MemoryWorkspaceService, LongTermMemoryService)):
            raise TypeError("service must be a bound memory workspace service")
        self.binding_id = _required_text(
            binding_id,
            "binding_id",
            maximum=512,
            identifier=True,
        )
        if self.binding_id != service.binding_id:
            raise MemoryToolkitError(
                "workspace service binding does not match the capability"
            )
        self._service = service

    @property
    def space_id(self) -> str:
        return self._service.space.space_id

    @property
    def space_revision(self) -> int:
        return self._service.space.revision

    def list_entries(
        self,
        *,
        path: str,
        recursive: bool,
        limit: int,
    ) -> dict[str, Any]:
        page = self._service.list(
            parent_path=path or "/",
            recursive=recursive,
            limit=limit,
        )
        return {
            "entries": page.entries,
            "truncated": page.has_more,
            "next_cursor": page.next_cursor,
        }

    def search_entries(self, *, query: str, limit: int) -> dict[str, Any]:
        result = self._service.search(query, limit=limit)
        return {
            "query": query,
            "backend": ("lexical_fallback" if result.lexical_fallback else "hybrid"),
            "vector_status": "degraded" if result.vector_error else "ready",
            "results": tuple(
                {
                    "entry": hit.entry,
                    "score": hit.score,
                    "matched_by": hit.matched_by,
                    "source_refs": hit.source_refs,
                }
                for hit in result.hits
            ),
        }

    def read_content(
        self,
        *,
        ref: ResourceRef,
        offset: int,
        limit: int,
    ) -> MemoryToolContentPage:
        page = self._service.read(ref, offset=offset, limit=limit)
        digest = (
            hashlib.sha256(page.data).hexdigest()
            if page.offset == 0 and len(page.data) == page.total_bytes
            else ""
        )
        return MemoryToolContentPage(
            ref=ref,
            media_type=page.media_type,
            data=page.data,
            offset=page.offset,
            total_bytes=page.total_bytes,
            sha256=digest,
        )

    def get_entry(self, *, ref: ResourceRef) -> MemoryEntry:
        self._require_bound_ref(ref)
        entry = self._service.repository.read_entry(ref=ref)
        if (
            not isinstance(entry, MemoryEntry)
            or entry.space_id != self.space_id
            or entry.entry_id != ref.resource_id
            or entry.revision != ref.revision
        ):
            raise RepositoryScopeError(
                "workspace repository returned a foreign entry revision"
            )
        return entry

    def history(self, *, ref: ResourceRef, limit: int):
        self._require_bound_ref(ref)
        return self._service.history(ref, limit=limit)

    def _require_bound_ref(self, ref: ResourceRef) -> None:
        if (
            not isinstance(ref, ResourceRef)
            or ref.kind != "memory"
            or ref.fragment != self.space_id
        ):
            raise RepositoryScopeError(
                "reference does not belong to the bound workspace"
            )


class WorkspaceMemoryCapability(_WorkspaceReadCapability):
    """Toolkit-facing adapter over one bound chat workspace service."""

    def __init__(
        self,
        *,
        binding_id: str,
        service: MemoryWorkspaceService,
        mutation_source_refs: Sequence[ResourceRef],
    ) -> None:
        if not isinstance(service, MemoryWorkspaceService):
            raise TypeError("service must be a MemoryWorkspaceService")
        super().__init__(binding_id=binding_id, service=service)
        self._mutation_source_refs = _bound_mutation_source_refs(mutation_source_refs)

    @property
    def service(self) -> MemoryWorkspaceService:
        return self._service

    def upsert(self, *, request: MemoryUpsertRequest) -> MemoryEntry:
        if not isinstance(request, MemoryUpsertRequest):
            raise TypeError("request must be a MemoryUpsertRequest")
        if request.entry_ref is not None:
            self._require_bound_ref(request.entry_ref)
        arguments = {
            "path": request.path,
            "description": request.description,
            "expected_space_revision": request.expected_space_revision,
            "source_refs": _merge_mutation_source_refs(
                self._mutation_source_refs,
                request.source_refs,
            ),
            "operation_id": request.operation_id,
        }
        if request.kind == "folder":
            if request.entry_ref is not None:
                raise RepositoryConflictError(
                    "folder entry metadata updates are unavailable"
                )
            return self.service.create_folder(**arguments)
        if request.kind == "markdown":
            return self.service.write_markdown(
                **arguments,
                content=request.content if request.content is not None else b"",
                entry_ref=request.entry_ref,
            )
        if request.kind == "image":
            return self.service.write_image(
                **arguments,
                content=request.content if request.content is not None else b"",
                media_type=request.media_type,
                entry_ref=request.entry_ref,
            )
        if request.kind == "link":
            return self.service.create_link(
                **arguments,
                url=request.url,
                entry_ref=request.entry_ref,
            )
        raise ValueError("kind must be folder, markdown, image, or link")

    def move(
        self,
        *,
        ref: ResourceRef,
        new_path: str,
        expected_space_revision: int,
        operation_id: str,
    ) -> MemoryEntry:
        self._require_bound_ref(ref)
        return self.service.move(
            ref=ref,
            new_path=new_path,
            expected_space_revision=expected_space_revision,
            source_refs=self._mutation_source_refs,
            operation_id=operation_id,
        )

    def archive(
        self,
        *,
        ref: ResourceRef,
        expected_space_revision: int,
        recursive: bool,
        operation_id: str,
    ) -> MemoryEntry:
        self._require_bound_ref(ref)
        return self.service.archive(
            ref=ref,
            expected_space_revision=expected_space_revision,
            source_refs=self._mutation_source_refs,
            recursive=recursive,
            operation_id=operation_id,
        )


class LongTermMemoryCapability(_WorkspaceReadCapability):
    """Toolkit-facing read-only adapter over one namespaced long-term service."""

    def __init__(self, *, binding_id: str, service: LongTermMemoryService) -> None:
        if not isinstance(service, LongTermMemoryService):
            raise TypeError("service must be a LongTermMemoryService")
        super().__init__(binding_id=binding_id, service=service)


class TaskStateMemoryCapability:
    """Toolkit-facing adapter over one CAS-only pinned task-state service."""

    def __init__(self, *, binding_id: str, service: TaskStateService) -> None:
        if not isinstance(service, TaskStateService):
            raise TypeError("service must be a TaskStateService")
        self.binding_id = _required_text(
            binding_id,
            "binding_id",
            maximum=512,
            identifier=True,
        )
        if self.binding_id != service.binding_id:
            raise MemoryToolkitError(
                "task-state service binding does not match the capability"
            )
        self._service = service

    def update(self, *, request: TaskStateUpdateRequest):
        if not isinstance(request, TaskStateUpdateRequest):
            raise TypeError("request must be a TaskStateUpdateRequest")
        return self._service.update(
            expected_revision=request.expected_revision,
            patch=request.patch,
            source_event_refs=request.source_refs,
            operation_id=request.operation_id,
        )


class PromotionMemoryCapability:
    """Proposal-only adapter; it intentionally exposes no decision operation."""

    def __init__(
        self,
        *,
        binding_id: str,
        target_namespace: str,
        service: PromotionService,
        mutation_source_refs: Sequence[ResourceRef],
    ) -> None:
        if not isinstance(service, PromotionService):
            raise TypeError("service must be a PromotionService")
        self.binding_id = _required_text(
            binding_id,
            "binding_id",
            maximum=512,
            identifier=True,
        )
        self.target_namespace = _required_text(
            target_namespace,
            "target_namespace",
            maximum=255,
            identifier=True,
        )
        if self.binding_id != service.binding_id:
            raise MemoryToolkitError(
                "promotion service binding does not match the capability"
            )
        if self.target_namespace != service.target_namespace:
            raise MemoryToolkitError(
                "promotion target namespace does not match the capability"
            )
        self._service = service
        self._mutation_source_refs = _bound_mutation_source_refs(mutation_source_refs)

    def propose(
        self,
        *,
        source_ref: ResourceRef,
        target_path: str,
        target_entry_ref: ResourceRef | None,
        operation_id: str,
    ):
        diff: dict[str, Any] = {
            "op": "replace" if target_entry_ref is not None else "derive",
            "source_entry_ref": source_ref.to_dict(),
            "target_path": target_path,
        }
        if target_entry_ref is not None:
            diff["target_entry_ref"] = target_entry_ref.to_dict()
        return self._service.propose(
            source_ref=source_ref,
            target_path=target_path,
            reason="Promote curated chat memory for reuse",
            source_refs=self._mutation_source_refs,
            operation_id=operation_id,
            target_entry_ref=target_entry_ref,
            diff=diff,
        )


__all__ = [
    "LongTermMemoryCapability",
    "PromotionMemoryCapability",
    "TaskStateMemoryCapability",
    "WorkspaceMemoryCapability",
]
