from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from unchain.context import (
    ArtifactService,
    ContextCompiler,
    ContextInputIngress,
    ContextInputIngressError,
    HostResolvedAttachment,
    HostResolvedCurrentInput,
    JournalContextRequestFactory,
)
from unchain.context.ports import (
    BoundArtifactRepository,
    ContextConflictError,
    ContextScopeError,
)
from unchain.context.projector import (
    CanonicalSemanticEventProjector,
    SemanticEventProjectionError,
)
from unchain.journal import (
    ArtifactRef,
    AttemptRef,
    BoundExecutionJournal,
    DurableEventSink,
    EventCursor,
    GenerationRef,
    JournalAppendResult,
    JournalEvent,
    JournalPage,
    ResourceRef,
    SemanticEventDraft,
    capture_journal_snapshot,
)
from unchain.journal.runtime import DurableJournalIntegrityError
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


ATTACHMENT_SCHEMA = "unchain.host_resolved_attachment.v1"


class _ArtifactRepository(BoundArtifactRepository):
    def __init__(self, execution_id: str = "execution-1") -> None:
        super().__init__(execution_id)
        self.content: dict[str, bytes] = {}
        self.descriptors: dict[str, ArtifactRef] = {}
        self.operations = {}

    def put(self, *, content, media_type, operation, preview=""):
        previous = self.operations.get(operation.operation_id)
        if previous is not None:
            prior_operation, artifact = previous
            if prior_operation != operation:
                raise ContextConflictError("artifact operation payload changed")
            return artifact
        digest = hashlib.sha256(content).hexdigest()
        artifact = ArtifactRef(
            ref=ResourceRef(
                "artifact",
                f"{self.execution_id}-object-{digest}",
                1,
            ),
            media_type=media_type,
            byte_length=len(content),
            sha256=digest,
            preview=preview,
        )
        self.operations[operation.operation_id] = (operation, artifact)
        self.content[artifact.ref.resource_id] = content
        self.descriptors[artifact.ref.resource_id] = artifact
        return artifact

    def _verified(self, artifact: ArtifactRef) -> bytes:
        stored = self.descriptors.get(artifact.ref.resource_id)
        if stored is None:
            raise ContextScopeError("artifact is outside the bound execution")
        if stored != artifact:
            raise ContextScopeError("artifact descriptor is not bound")
        return self.content[artifact.ref.resource_id]

    def read_verified(self, *, artifact, offset=0, limit=65_536):
        return self._verified(artifact)[offset : offset + limit]

    def read_full_verified(self, *, artifact):
        return self._verified(artifact)


class _Journal(BoundExecutionJournal):
    def __init__(self, execution_id: str = "execution-1") -> None:
        super().__init__(execution_id)
        self.events: list[JournalEvent] = []
        self.operations = {}

    def append(self, *, request):
        previous = self.operations.get(request.operation.operation_id)
        if previous is not None:
            prior_request, event = previous
            if prior_request != request:
                raise RuntimeError("journal operation conflict")
            return JournalAppendResult(
                event=event,
                cursor=EventCursor(event.store_seq, event.event_id),
                duplicate=True,
            )
        event = JournalEvent(
            event_id=request.event_id,
            event_type=request.event_type,
            attempt=request.attempt,
            operation=request.operation,
            store_seq=len(self.events) + 1,
            payload=request.payload,
            resource_refs=request.resource_refs,
        )
        self.events.append(event)
        self.operations[request.operation.operation_id] = (request, event)
        return JournalAppendResult(
            event=event,
            cursor=EventCursor(event.store_seq, event.event_id),
        )

    def read(self, *, after=None, limit=100):
        start = after.store_seq if after is not None else 0
        return JournalPage(events=tuple(self.events[start : start + limit]))

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        del max_events, max_bytes
        return capture_journal_snapshot(
            execution_id=self.execution_id,
            events=tuple(self.events),
        )


def _attempt() -> AttemptRef:
    return AttemptRef(
        generation=GenerationRef("execution-1", "generation-1"),
        attempt_id="attempt-1",
    )


def _bound(*, repository=None, journal=None):
    attempt = _attempt()
    repository = repository or _ArtifactRepository()
    artifacts = ArtifactService(
        repository,
        sanitizer=lambda content, media_type: content,
    )
    projector = CanonicalSemanticEventProjector(
        attempt=attempt,
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )
    journal = journal or _Journal()
    sink = DurableEventSink(journal, attempt, projector)
    ingress = ContextInputIngress(
        attempt=attempt,
        projector=projector,
        sink=sink,
    )
    return artifacts, projector, journal, ingress


def _persist_attachment(
    artifacts: ArtifactService,
    *,
    content: bytes = b"durable attachment",
    media_type: str = "application/pdf",
    operation_id: str = "attachment-1",
) -> ArtifactRef:
    return artifacts.persist(
        content,
        media_type=media_type,
        operation_id=operation_id,
    )


def _attachment(artifact: ArtifactRef) -> HostResolvedAttachment:
    return HostResolvedAttachment(
        artifact=artifact,
        kind="file",
        name="report.pdf",
        media_type="application/pdf",
    )


def _envelope(artifact: ArtifactRef) -> dict:
    return {
        "schema": ATTACHMENT_SCHEMA,
        "kind": "file",
        "name": "report.pdf",
        "media_type": "application/pdf",
        "artifact": artifact.to_dict(),
    }


def _context() -> HarnessContext:
    state = RunState()
    state.session_state.session_id = "execution-1"
    state.provider_state.provider = "openai"
    state.provider_state.model = "synthetic"
    state.provider_state.max_context_window_tokens = 16_384
    return HarnessContext(
        state=state,
        phase="before_model",
        event={"run_id": "attempt-1", "toolkit": None},
    )


def test_attachment_only_input_persists_safe_envelope_and_all_artifact_refs() -> None:
    artifacts, _projector, journal, ingress = _bound()
    artifact = _persist_attachment(artifacts)
    attachment = _attachment(artifact)

    receipt = ingress.persist(
        HostResolvedCurrentInput(
            attempt=_attempt(),
            content="",
            attachments=(attachment,),
        )
    )

    envelope = _envelope(artifact)
    assert receipt.event == journal.events[0]
    persisted_message = receipt.event.payload["message"]
    assert dict(persisted_message) | {
        "attachments": [dict(item) for item in persisted_message["attachments"]]
    } == {
        "role": "user",
        "content": "",
        "attachments": [envelope],
    }
    assert [dict(item) for item in receipt.event.payload["attachments"]] == [envelope]
    assert [dict(item) for item in receipt.event.payload["attachment_refs"]] == [
        artifact.ref.to_dict()
    ]
    message_ref = ResourceRef.from_dict(receipt.event.payload["content_ref"])
    assert receipt.event.resource_refs == (message_ref, artifact.ref)


def test_text_and_attachment_input_keeps_text_and_text_only_shape_is_unchanged() -> (
    None
):
    artifacts, _projector, _journal, ingress = _bound()
    artifact = _persist_attachment(artifacts)

    attached = ingress.persist(
        HostResolvedCurrentInput(
            attempt=_attempt(),
            content="review this",
            attachments=(_attachment(artifact),),
        )
    ).event
    assert attached.payload["message"]["content"] == "review this"
    assert [dict(item) for item in attached.payload["message"]["attachments"]] == [
        _envelope(artifact)
    ]

    _artifacts, _projector, _journal, text_ingress = _bound()
    text = text_ingress.persist(
        HostResolvedCurrentInput(attempt=_attempt(), content="text only")
    ).event
    assert set(text.payload) == {
        "run_id",
        "message",
        "content_ref",
        "content_bytes",
        "content_sha256",
        "preview",
        "preview_truncated",
    }
    assert text.payload["message"] == {"role": "user", "content": "text only"}
    assert len(text.resource_refs) == 1


@pytest.mark.parametrize("case", ["foreign", "missing", "tampered"])
def test_attachment_descriptor_must_resolve_exactly_in_the_bound_artifact_service(
    case: str,
) -> None:
    artifacts, _projector, _journal, ingress = _bound()
    persisted = _persist_attachment(artifacts)
    if case == "foreign":
        foreign_service = ArtifactService(
            _ArtifactRepository("execution-foreign"),
            sanitizer=lambda content, media_type: content,
        )
        candidate = _persist_attachment(
            foreign_service,
            operation_id="foreign-attachment",
        )
    elif case == "missing":
        candidate = replace(
            persisted,
            ref=ResourceRef("artifact", "execution-1-object-missing", 1),
        )
    else:
        candidate = replace(persisted, sha256="f" * 64)

    with pytest.raises(SemanticEventProjectionError, match="bound artifact service"):
        ingress.persist(
            HostResolvedCurrentInput(
                attempt=_attempt(),
                content="",
                attachments=(_attachment(candidate),),
            )
        )


def test_attachment_input_rejects_duplicates_and_empty_input_without_attachments() -> (
    None
):
    artifacts, _projector, _journal, _ingress = _bound()
    artifact = _persist_attachment(artifacts)
    attachment = _attachment(artifact)

    with pytest.raises(ValueError, match="non-empty text or one attachment"):
        HostResolvedCurrentInput(attempt=_attempt(), content="")
    with pytest.raises(ValueError, match="duplicate attachment artifact ref"):
        HostResolvedCurrentInput(
            attempt=_attempt(),
            content="",
            attachments=(attachment, attachment),
        )


def test_ingress_rejects_projected_or_persisted_attachment_ref_drift() -> None:
    artifacts, _projector, _journal, _ingress = _bound()
    artifact = _persist_attachment(artifacts)
    attachment = _attachment(artifact)

    class _MalformedProjector(CanonicalSemanticEventProjector):
        def project_user_message(self, message, *, message_index=0, attachments=()):
            del message, message_index, attachments
            return SemanticEventDraft(
                event_id="event-malformed-attachment",
                event_type="message.user",
                attempt=_attempt(),
                operation_id="operation-malformed-attachment",
                payload={
                    "run_id": "attempt-1",
                    "message": {
                        "role": "user",
                        "content": "",
                        "attachments": [_envelope(artifact)],
                    },
                    "content_ref": artifact.ref.to_dict(),
                    "attachments": [_envelope(artifact)],
                    "attachment_refs": [artifact.ref.to_dict()],
                },
                resource_refs=(artifact.ref,),
            )

    malformed = _MalformedProjector(
        attempt=_attempt(),
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )
    journal = _Journal()
    ingress = ContextInputIngress(
        attempt=_attempt(),
        projector=malformed,
        sink=DurableEventSink(journal, _attempt(), malformed),
    )
    with pytest.raises(ContextInputIngressError, match="attachment references"):
        ingress.persist(
            HostResolvedCurrentInput(
                attempt=_attempt(),
                content="",
                attachments=(attachment,),
            )
        )
    assert journal.events == []

    class _ReceiptDriftJournal(_Journal):
        def append(self, *, request):
            result = super().append(request=request)
            event = replace(
                result.event,
                resource_refs=result.event.resource_refs[:-1],
            )
            return JournalAppendResult(
                event=event,
                cursor=EventCursor(event.store_seq, event.event_id),
                duplicate=result.duplicate,
            )

    drift_journal = _ReceiptDriftJournal()
    projector = CanonicalSemanticEventProjector(
        attempt=_attempt(),
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )
    drift_ingress = ContextInputIngress(
        attempt=_attempt(),
        projector=projector,
        sink=DurableEventSink(drift_journal, _attempt(), projector),
    )
    with pytest.raises(DurableJournalIntegrityError, match="persisted event"):
        drift_ingress.persist(
            HostResolvedCurrentInput(
                attempt=_attempt(),
                content="",
                attachments=(attachment,),
            )
        )


def test_attachment_only_input_survives_sqlite_restart_and_compiler_projection(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "context_v2.sqlite3"
    object_directory = tmp_path / "objects"
    store = SQLiteContextV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    repository = store.bind_execution("execution-1")
    artifacts = ArtifactService(
        repository,
        sanitizer=lambda content, media_type: content,
    )
    artifact = _persist_attachment(artifacts)
    projector = CanonicalSemanticEventProjector(
        attempt=_attempt(),
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )
    ContextInputIngress(
        attempt=_attempt(),
        projector=projector,
        sink=DurableEventSink(repository, _attempt(), projector),
    ).persist(
        HostResolvedCurrentInput(
            attempt=_attempt(),
            content="",
            attachments=(_attachment(artifact),),
        )
    )

    reopened = SQLiteContextV2Store(
        database_path=database_path,
        object_directory=object_directory,
    ).bind_execution("execution-1")
    request = JournalContextRequestFactory(
        attempt=_attempt(),
        journal=reopened,
        model_window_fallback=lambda provider, model: 8_192,
    )(_context())
    result = ContextCompiler().compile(request)

    expected = {
        "role": "user",
        "content": "",
        "attachments": [_envelope(artifact)],
    }
    assert request.source_messages[0]["role"] == expected["role"]
    assert request.source_messages[0]["content"] == expected["content"]
    assert HostResolvedAttachment.from_dict(
        request.source_messages[0]["attachments"][0]
    ) == _attachment(artifact)
    assert result.messages[0]["role"] == expected["role"]
    assert result.messages[0]["content"] == expected["content"]
    assert HostResolvedAttachment.from_dict(
        result.messages[0]["attachments"][0]
    ) == _attachment(artifact)
    assert tuple(
        ResourceRef.from_dict(value)
        for value in request.semantic_events[-1]["attachment_refs"]
    ) == (artifact.ref,)
