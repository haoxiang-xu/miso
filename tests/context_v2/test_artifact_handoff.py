from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

import unchain.context as context_api

from unchain.context.artifacts import (
    MAX_ARTIFACT_BYTES,
    MAX_INLINE_TOOL_RESULT_BYTES,
    MAX_PREVIEW_BYTES,
    ArtifactBudgetError,
    ArtifactIntegrityError,
    ArtifactService,
    ArtifactTooLargeError,
    _bounded_utf8_preview,
)
from unchain.context.handoff import DurableHandoffRecorder, HandoffService
from unchain.context.models import HandoffStatus
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.context.ports import (
    BoundArtifactRepository,
    ContextConflictError,
    ContextRepositoryError,
    ContextScopeError,
)
from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    EventRange,
    GenerationRef,
    JournalAppendRequest,
    JournalAppendResult,
    JournalEvent,
    JournalPage,
    OperationRef,
    ResourceRef,
)
from unchain.journal.ports import BoundExecutionJournal
from unchain.journal.runtime import DurableEventSink
from unchain.journal.snapshot import capture_journal_snapshot


class MemoryArtifactRepository(BoundArtifactRepository):
    def __init__(self, execution_id: str = "execution-1") -> None:
        super().__init__(execution_id)
        self.contents: dict[str, bytes] = {}
        self.refs: dict[str, ArtifactRef] = {}
        self.operations: dict[str, tuple[str, ArtifactRef]] = {}
        self.put_calls: list[tuple[bytes, str, OperationRef, str]] = []
        self.read_calls: list[tuple[ResourceRef, int, int]] = []
        self.verified_read_calls: list[tuple[ArtifactRef, int, int]] = []
        self.full_verified_read_calls: list[ArtifactRef] = []
        self.lie_about: str = ""
        self.short_reads = False

    def put(
        self,
        *,
        content: bytes,
        media_type: str,
        operation: OperationRef,
        preview: str = "",
    ) -> ArtifactRef:
        self.put_calls.append((content, media_type, operation, preview))
        previous = self.operations.get(operation.operation_id)
        if previous is not None:
            prior_hash, prior_ref = previous
            if prior_hash != operation.payload_sha256:
                raise ContextConflictError("operation payload changed")
            return prior_ref
        digest = hashlib.sha256(content).hexdigest()
        resource = ResourceRef("artifact", f"object-{len(self.contents) + 1}", 1)
        result = ArtifactRef(
            resource,
            "text/plain" if self.lie_about == "media_type" else media_type,
            len(content) + (1 if self.lie_about == "byte_length" else 0),
            "0" * 64 if self.lie_about == "sha256" else digest,
            preview,
        )
        self.contents[resource.resource_id] = content
        self.refs[resource.resource_id] = result
        self.operations[operation.operation_id] = (operation.payload_sha256, result)
        return result

    def read(self, *, ref: ResourceRef, offset: int = 0, limit: int = 65_536) -> bytes:
        self.read_calls.append((ref, offset, limit))
        return self.contents[ref.resource_id][offset : offset + limit]

    def read_verified(
        self,
        *,
        artifact: ArtifactRef,
        offset: int = 0,
        limit: int = 65_536,
    ) -> bytes:
        self.verified_read_calls.append((artifact, offset, limit))
        expected = self.refs.get(artifact.ref.resource_id)
        if expected is None or expected.ref != artifact.ref:
            raise ContextScopeError("artifact does not belong to the bound execution")
        content = self.contents[artifact.ref.resource_id]
        if len(content) != artifact.byte_length:
            raise ContextRepositoryError("artifact byte_length mismatch")
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ContextRepositoryError("artifact sha256 mismatch")
        page = content[offset : offset + limit]
        return page[:-1] if self.short_reads and page else page

    def read_full_verified(self, *, artifact: ArtifactRef) -> bytes:
        self.full_verified_read_calls.append(artifact)
        expected = self.refs.get(artifact.ref.resource_id)
        if expected is None or expected.ref != artifact.ref:
            raise ContextScopeError("artifact does not belong to the bound execution")
        content = self.contents[artifact.ref.resource_id]
        if len(content) != artifact.byte_length:
            raise ContextRepositoryError("artifact byte_length mismatch")
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ContextRepositoryError("artifact sha256 mismatch")
        return content


class ParentJournal(BoundExecutionJournal):
    def __init__(self, artifacts: MemoryArtifactRepository) -> None:
        super().__init__(artifacts.execution_id)
        self.artifacts = artifacts
        self.events: list[JournalEvent] = []
        self.operations: dict[str, JournalEvent] = {}
        self.fail_next = False

    def append(self, *, request: JournalAppendRequest) -> JournalAppendResult:
        assert self.artifacts.put_calls, "handoff output must persist first"
        if self.fail_next:
            self.fail_next = False
            raise OSError("parent journal unavailable")
        previous = self.operations.get(request.operation.operation_id)
        if previous is not None:
            assert previous.operation == request.operation
            return JournalAppendResult(
                previous,
                EventCursor(previous.store_seq, previous.event_id),
                duplicate=True,
            )
        event = JournalEvent(
            request.event_id,
            request.event_type,
            request.attempt,
            request.operation,
            len(self.events) + 1,
            request.payload,
            request.resource_refs,
        )
        self.events.append(event)
        self.operations[request.operation.operation_id] = event
        return JournalAppendResult(
            event,
            EventCursor(event.store_seq, event.event_id),
            duplicate=False,
        )

    def read(self, *, after=None, limit=100):
        start = after.store_seq if after is not None else 0
        events = tuple(self.events[start : start + limit])
        cursor = (
            EventCursor(events[-1].store_seq, events[-1].event_id)
            if events
            else after
        )
        return JournalPage(
            events,
            cursor,
            start + len(events) < len(self.events),
        )

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        del max_events, max_bytes
        return capture_journal_snapshot(
            execution_id=self.execution_id,
            events=tuple(self.events),
        )


def identity_sanitizer(content: bytes, media_type: str) -> bytes:
    assert media_type
    return content


def event_range() -> EventRange:
    return EventRange(
        EventCursor(10, "event-10"),
        EventCursor(17, "event-17"),
    )


def child_attempt() -> AttemptRef:
    return AttemptRef(
        GenerationRef("child-execution-1", "child-generation-1"),
        "child-run-1",
    )


def test_tool_result_persists_full_sanitized_bytes_before_inline_reduction() -> None:
    repository = MemoryArtifactRepository()

    def sanitizer(content: bytes, media_type: str) -> bytes:
        assert media_type == "application/json"
        return content.replace(b"REMOVE", b"SAFE")

    service = ArtifactService(repository, sanitizer=sanitizer)
    result = {"output": "REMOVE" + ("x" * (MAX_INLINE_TOOL_RESULT_BYTES + 100))}

    artifactized = service.artifactize_tool_result(
        result,
        operation_id="tool-result-call-1",
    )

    stored = repository.put_calls[0][0]
    assert b"REMOVE" not in stored
    assert b"SAFE" in stored
    assert len(stored) > MAX_INLINE_TOOL_RESULT_BYTES
    assert artifactized.artifact.byte_length == len(stored)
    assert artifactized.artifact.sha256 == hashlib.sha256(stored).hexdigest()
    assert artifactized.full_output_ref == artifactized.artifact.ref
    assert artifactized.result_bytes == len(stored)
    assert artifactized.result_sha256 == artifactized.artifact.sha256
    assert artifactized.visible_result["full_output_ref"] == artifactized.artifact.ref.to_dict()
    assert artifactized.visible_result["content_bytes"] == len(stored)
    assert artifactized.visible_result["content_sha256"] == artifactized.artifact.sha256
    assert len(artifactized.visible_result["preview"].encode("utf-8")) <= MAX_PREVIEW_BYTES


def test_durable_context_services_and_compile_coordinator_are_publicly_exported() -> None:
    assert context_api.ArtifactService is ArtifactService
    assert context_api.HandoffService is HandoffService
    assert context_api.ContextCompileCoordinator is not None
    assert context_api.ContextCompileCoordinatorError is not None
    assert context_api.MAX_CHECKPOINT_PAYLOAD_BYTES == 32 * 1024 * 1024


def test_small_tool_result_still_has_a_full_durable_ref_and_uses_sanitized_value() -> None:
    repository = MemoryArtifactRepository()
    service = ArtifactService(
        repository,
        sanitizer=lambda content, media_type: content.replace(b"before", b"after!"),
    )

    artifactized = service.artifactize_tool_result(
        {"value": "before"},
        operation_id="tool-result-call-1",
    )

    assert artifactized.visible_result == {"value": "after!"}
    assert artifactized.event_fields() == {
        "result": {"value": "after!"},
        "full_output_ref": artifactized.artifact.ref.to_dict(),
        "result_bytes": artifactized.artifact.byte_length,
        "result_sha256": artifactized.artifact.sha256,
    }


def test_text_preview_normalizes_a_whitespace_truncation_boundary() -> None:
    repository = MemoryArtifactRepository()
    service = ArtifactService(repository, sanitizer=identity_sanitizer)
    content = (b"a" * (MAX_PREVIEW_BYTES - 1)) + b" " + b"tail"

    artifact = service.persist(
        content,
        media_type="text/plain",
        operation_id="artifact-whitespace-preview",
    )

    assert artifact.preview == "a" * (MAX_PREVIEW_BYTES - 1)
    assert repository.put_calls[0][3] == artifact.preview


def test_text_preview_matches_artifact_ref_nfc_canonicalization() -> None:
    repository = MemoryArtifactRepository()
    service = ArtifactService(repository, sanitizer=identity_sanitizer)
    content = " e\u0301 ".encode("utf-8")

    artifact = service.persist(
        content,
        media_type="text/plain",
        operation_id="artifact-nfc-preview",
    )

    assert artifact.preview == "é"
    assert repository.put_calls[0][3] == artifact.preview


@pytest.mark.parametrize(
    ("content", "limit", "expected"),
    [
        (b" \xffe\xcc\x81 \xfe", 99, "é"),
        ("xe\u0301".encode("utf-8"), 3, "xe"),
        ("xe\u0301".encode("utf-8"), 4, "xé"),
    ],
)
def test_bounded_text_preview_normalizes_after_lossy_utf8_decode(
    content: bytes,
    limit: int,
    expected: str,
) -> None:
    assert _bounded_utf8_preview(content, limit) == expected


def test_artifact_size_limit_is_enforced_before_repository_write() -> None:
    repository = MemoryArtifactRepository()
    service = ArtifactService(repository, sanitizer=identity_sanitizer)

    with pytest.raises(ArtifactTooLargeError, match="32 MiB"):
        service.persist(
            b"x" * (MAX_ARTIFACT_BYTES + 1),
            media_type="application/octet-stream",
            operation_id="oversized-artifact",
        )

    assert repository.put_calls == []


@pytest.mark.parametrize("lie_about", ("media_type", "byte_length", "sha256"))
def test_artifact_service_fails_closed_on_repository_metadata_lies(lie_about: str) -> None:
    repository = MemoryArtifactRepository()
    repository.lie_about = lie_about
    service = ArtifactService(repository, sanitizer=identity_sanitizer)

    with pytest.raises(ArtifactIntegrityError, match=lie_about):
        service.persist(
            b"synthetic",
            media_type="application/json",
            operation_id="artifact-1",
        )


def test_artifact_retry_uses_exact_operation_hash_and_reference() -> None:
    repository = MemoryArtifactRepository()
    service = ArtifactService(repository, sanitizer=identity_sanitizer)

    first = service.persist(
        b"synthetic",
        media_type="application/json",
        operation_id="artifact-1",
    )
    replay = service.persist(
        b"synthetic",
        media_type="application/json",
        operation_id="artifact-1",
    )

    assert replay == first
    assert repository.put_calls[0][2] == repository.put_calls[1][2]
    with pytest.raises(ContextConflictError):
        service.persist(
            b"changed",
            media_type="application/json",
            operation_id="artifact-1",
        )


def test_full_read_is_budget_gated_paged_and_integrity_checked() -> None:
    repository = MemoryArtifactRepository()
    service = ArtifactService(repository, sanitizer=identity_sanitizer)
    content = b"x" * 150_000
    artifact = service.persist(
        content,
        media_type="application/octet-stream",
        operation_id="artifact-1",
    )

    with pytest.raises(ArtifactBudgetError, match="remaining budget"):
        service.read_full(artifact, remaining_budget_bytes=len(content) - 1)
    assert repository.read_calls == []

    restored = service.read_full(artifact, remaining_budget_bytes=len(content))
    assert restored == content
    assert repository.read_calls == []
    assert repository.verified_read_calls == []
    assert repository.full_verified_read_calls == [artifact]

    repository.contents[artifact.ref.resource_id] = content[:-1] + b"y"
    with pytest.raises(ArtifactIntegrityError, match="sha256"):
        service.read_full(artifact, remaining_budget_bytes=len(content))

    foreign = ArtifactRef(
        ResourceRef("memory", "entry-1", 1),
        "application/octet-stream",
        0,
        hashlib.sha256(b"").hexdigest(),
    )
    with pytest.raises(ArtifactIntegrityError, match="whole artifact"):
        service.read_full(foreign, remaining_budget_bytes=0)


def test_zero_byte_full_read_still_verifies_scope_and_durability() -> None:
    repository = MemoryArtifactRepository()
    service = ArtifactService(repository, sanitizer=identity_sanitizer)
    artifact = service.persist(
        b"",
        media_type="application/octet-stream",
        operation_id="artifact-empty",
    )

    assert service.read_full(artifact, remaining_budget_bytes=0) == b""
    assert repository.full_verified_read_calls == [artifact]
    assert repository.verified_read_calls == []

    foreign = replace(
        artifact,
        ref=ResourceRef("artifact", "foreign-empty-object", 1),
    )
    with pytest.raises(ContextScopeError, match="bound execution"):
        service.read_full(foreign, remaining_budget_bytes=0)


def test_page_read_verifies_the_whole_artifact_before_returning_a_slice() -> None:
    repository = MemoryArtifactRepository()
    service = ArtifactService(repository, sanitizer=identity_sanitizer)
    content = b"verified whole object"
    artifact = service.persist(
        content,
        media_type="application/octet-stream",
        operation_id="artifact-verified-page",
    )

    page = service.read_page(artifact, offset=9, limit=5)

    assert page.data == b"whole"
    assert repository.read_calls == []
    assert repository.verified_read_calls == [(artifact, 9, 5)]

    eof = service.read_page(artifact, offset=len(content), limit=5)
    assert eof.data == b""
    assert repository.verified_read_calls[-1] == (artifact, len(content), 0)

    repository.short_reads = True
    with pytest.raises(ArtifactIntegrityError, match="short read"):
        service.read_page(artifact, offset=0, limit=5)
    repository.short_reads = False

    repository.contents[artifact.ref.resource_id] = b"tampered whole object"
    with pytest.raises(ArtifactIntegrityError, match="sha256"):
        service.read_page(artifact, offset=0, limit=1)


def test_page_read_fails_closed_on_stale_and_foreign_descriptors() -> None:
    repository = MemoryArtifactRepository()
    service = ArtifactService(repository, sanitizer=identity_sanitizer)
    content = b"descriptor-bound"
    artifact = service.persist(
        content,
        media_type="application/octet-stream",
        operation_id="artifact-descriptor-bound",
    )
    stale = replace(artifact, sha256="0" * 64)

    with pytest.raises(ArtifactIntegrityError, match="sha256"):
        service.read_page(stale, offset=0, limit=1)

    foreign = replace(
        artifact,
        ref=ResourceRef("artifact", "foreign-object", 1),
    )
    with pytest.raises(ContextScopeError, match="bound execution"):
        service.read_page(foreign, offset=0, limit=1)


def test_handoff_persists_full_child_output_before_returning_typed_envelope() -> None:
    repository = MemoryArtifactRepository()
    artifacts = ArtifactService(repository, sanitizer=identity_sanitizer)
    service = HandoffService(artifacts)
    child_artifact = ResourceRef("artifact", "child-artifact-1", 2)
    output = {"answer": "x" * 20_000}

    envelope = service.persist(
        child_attempt=child_attempt(),
        status=HandoffStatus.COMPLETE,
        full_output=output,
        source_event_range=event_range(),
        artifact_refs=(child_artifact,),
        operation_id="handoff-child-run-1",
    )

    expected_content = json.dumps(
        output,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert repository.put_calls[0][0] == expected_content
    assert envelope.child_run_id == "child-run-1"
    assert envelope.child_attempt == child_attempt()
    assert envelope.status is HandoffStatus.COMPLETE
    assert envelope.source_event_range == event_range()
    assert envelope.artifact_refs == (child_artifact,)
    assert envelope.byte_length == len(expected_content)
    assert envelope.sha256 == hashlib.sha256(expected_content).hexdigest()
    assert envelope.full_output_ref == repository.refs["object-1"].ref
    assert len(envelope.summary.encode("utf-8")) <= MAX_PREVIEW_BYTES


def test_handoff_ambiguous_notification_retry_has_one_parent_visible_effect() -> None:
    repository = MemoryArtifactRepository()
    service = HandoffService(
        ArtifactService(repository, sanitizer=identity_sanitizer)
    )
    class AmbiguousNotifier:
        def __init__(self) -> None:
            self.receipts = {}
            self.visible = []
            self.calls = []

        def notify(self, *, operation, envelope):
            self.calls.append(operation)
            prior = self.receipts.get(operation.operation_id)
            if prior is not None:
                assert prior[0] == operation.payload_sha256
                return prior[1]
            self.visible.append(envelope)
            self.receipts[operation.operation_id] = (
                operation.payload_sha256,
                envelope,
            )
            raise RuntimeError("ambiguous parent acknowledgement")

    notifier = AmbiguousNotifier()

    kwargs = {
        "child_attempt": child_attempt(),
        "status": HandoffStatus.PARTIAL,
        "full_output": {"partial": True},
        "source_event_range": event_range(),
        "artifact_refs": (),
        "operation_id": "handoff-child-run-1",
    }
    with pytest.raises(RuntimeError, match="ambiguous parent acknowledgement"):
        service.persist_then_notify(notifier=notifier, **kwargs)

    persisted_ref = repository.refs["object-1"].ref
    restarted = HandoffService(
        ArtifactService(repository, sanitizer=identity_sanitizer)
    )
    delivered = restarted.persist_then_notify(
        notifier=notifier,
        **kwargs,
    )

    assert len(notifier.visible) == 1
    assert len(notifier.calls) == 2
    assert notifier.calls[0] == notifier.calls[1]
    assert delivered.full_output_ref == persisted_ref
    assert repository.put_calls[0][2] == repository.put_calls[1][2]
    assert len(repository.contents) == 1


def test_handoff_rejects_non_artifact_refs_and_binds_status_to_operation() -> None:
    repository = MemoryArtifactRepository()
    service = HandoffService(
        ArtifactService(repository, sanitizer=identity_sanitizer)
    )
    kwargs = {
        "child_attempt": child_attempt(),
        "full_output": {"answer": 42},
        "source_event_range": event_range(),
        "operation_id": "handoff-child-run-1",
    }

    with pytest.raises(ValueError, match="artifact_refs"):
        service.persist(
            status=HandoffStatus.COMPLETE,
            artifact_refs=(ResourceRef("memory", "entry-1", 1),),
            **kwargs,
        )

    service.persist(
        status=HandoffStatus.COMPLETE,
        artifact_refs=(),
        **kwargs,
    )
    with pytest.raises(ContextConflictError):
        service.persist(
            status=HandoffStatus.PARTIAL,
            artifact_refs=(),
            **kwargs,
        )


def test_durable_handoff_records_parent_receipt_after_full_child_output() -> None:
    repository = MemoryArtifactRepository()
    artifacts = ArtifactService(repository, sanitizer=identity_sanitizer)
    parent_attempt = AttemptRef(
        GenerationRef("execution-1", "generation-1"),
        "parent-run-1",
    )
    projector = CanonicalSemanticEventProjector(
        attempt=parent_attempt,
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )
    journal = ParentJournal(repository)
    sink = DurableEventSink(journal, parent_attempt, projector)
    recorder = DurableHandoffRecorder(
        attempt=parent_attempt,
        handoffs=HandoffService(artifacts),
        projector=projector,
        sink=sink,
    )

    first = recorder.record(
        child_attempt=child_attempt(),
        status=HandoffStatus.COMPLETE,
        full_output={"answer": "complete child output"},
        source_event_range=event_range(),
        artifact_refs=(ResourceRef("artifact", "child-file-1", 1),),
        operation_id="handoff-child-run-1",
    )
    replay = recorder.record(
        child_attempt=child_attempt(),
        status=HandoffStatus.COMPLETE,
        full_output={"answer": "complete child output"},
        source_event_range=event_range(),
        artifact_refs=(ResourceRef("artifact", "child-file-1", 1),),
        operation_id="handoff-child-run-1",
    )

    assert first.duplicate is False
    assert replay.duplicate is True
    assert replay.envelope == first.envelope
    assert len(repository.contents) == 1
    assert len(journal.events) == 1
    event = journal.events[0]
    assert event.event_type == "handoff.recorded"
    assert event.payload["handoff_envelope"]["child_attempt"] == (
        child_attempt().to_dict()
    )
    assert event.payload["handoff_envelope"]["child_run_id"] == "child-run-1"
    assert event.payload["handoff_envelope"]["full_output_ref"] == (
        first.envelope.full_output_ref.to_dict()
    )
    assert event.resource_refs == (
        first.envelope.full_output_ref,
        ResourceRef("artifact", "child-file-1", 1),
    )
