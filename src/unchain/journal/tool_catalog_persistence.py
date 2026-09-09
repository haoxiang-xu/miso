"""Durable write path for one provider-visible tool catalog snapshot."""

from __future__ import annotations

from unchain.context.ports import BoundArtifactRepository
from unchain.context.tool_catalog import ToolCatalogEnvelope, ToolCatalogSnapshot

from .models import (
    EventCursor,
    JournalAppendRequest,
    JournalAppendResult,
    OperationRef,
)
from .tool_catalog import BoundToolCatalogIndex, TOOL_CATALOG_SNAPSHOT_EVENT_TYPE


class ToolCatalogPersistenceError(RuntimeError):
    """The durable catalog bytes or receipt disagreed with the source."""


def _same_append_request(
    request: JournalAppendRequest,
    result: JournalAppendResult,
) -> bool:
    event = result.event
    return (
        event.event_id == request.event_id
        and event.event_type == request.event_type
        and event.attempt == request.attempt
        and event.operation == request.operation
        and dict(event.payload) == dict(request.payload)
        and event.resource_refs == request.resource_refs
        and result.cursor == EventCursor(event.store_seq, event.event_id)
    )


def persist_tool_catalog_snapshot(
    *,
    journal: BoundToolCatalogIndex,
    artifacts: BoundArtifactRepository,
    envelope: ToolCatalogEnvelope,
    artifact_operation: OperationRef,
    event_operation: OperationRef,
    event_id: str,
) -> ToolCatalogSnapshot:
    """Write bytes, verify full readback, then append the exact receipt."""

    if not isinstance(journal, BoundToolCatalogIndex):
        raise TypeError("journal must be a BoundToolCatalogIndex")
    if not isinstance(artifacts, BoundArtifactRepository):
        raise TypeError("artifacts must be a BoundArtifactRepository")
    if type(envelope) is not ToolCatalogEnvelope:
        raise TypeError("envelope must be an exact ToolCatalogEnvelope")
    if type(artifact_operation) is not OperationRef:
        raise TypeError("artifact_operation must be an exact OperationRef")
    if type(event_operation) is not OperationRef:
        raise TypeError("event_operation must be an exact OperationRef")
    if journal.execution_id != artifacts.execution_id:
        raise ToolCatalogPersistenceError(
            "catalog journal and artifact scopes do not match"
        )
    if journal.execution_id != envelope.attempt.generation.execution_id:
        raise ToolCatalogPersistenceError(
            "catalog persistence scope does not match its attempt"
        )

    content = envelope.canonical_bytes()
    artifact = artifacts.put(
        content=content,
        media_type="application/json",
        preview="",
        operation=artifact_operation,
    )
    readback = artifacts.read_full_verified(artifact=artifact)
    if type(readback) is not bytes or readback != content:
        raise ToolCatalogPersistenceError(
            "tool catalog artifact readback differs from canonical bytes"
        )

    request = JournalAppendRequest(
        event_id=event_id,
        event_type=TOOL_CATALOG_SNAPSHOT_EVENT_TYPE,
        attempt=envelope.attempt,
        operation=event_operation,
        payload={
            "iteration": envelope.iteration,
            "catalog_sha256": envelope.catalog_sha256,
            "catalog_artifact": artifact.to_dict(),
        },
        resource_refs=(artifact.ref,),
    )
    result = journal.append(request=request)
    if type(result) is not JournalAppendResult or not _same_append_request(
        request,
        result,
    ):
        raise ToolCatalogPersistenceError(
            "persisted tool catalog receipt differs from its append request"
        )
    return ToolCatalogSnapshot(
        envelope=envelope,
        event_cursor=result.cursor,
        artifact=artifact,
    )


__all__ = [
    "ToolCatalogPersistenceError",
    "persist_tool_catalog_snapshot",
]
