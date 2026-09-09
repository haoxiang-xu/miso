from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from unchain.context import (
    ArtifactService,
    ContextCompileCoordinator,
    ContextInputIngress,
    ContextModelProjectionError,
    HostResolvedAttachment,
    HostResolvedCurrentInput,
    JournalContextRequestFactory,
    ModelContextProjection,
)
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.journal import AttemptRef, DurableEventSink, GenerationRef
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.persistence.sqlite_context_compiler_v2 import (
    SQLiteContextCompilerV2Store,
)
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


def _attempt() -> AttemptRef:
    return AttemptRef(
        GenerationRef("execution-1", "generation-1"),
        "attempt-1",
    )


def _context(*, provider: str = "openai") -> HarnessContext:
    state = RunState()
    state.session_state.session_id = "execution-1"
    state.provider_state.provider = provider
    state.provider_state.model = "synthetic"
    state.provider_state.max_context_window_tokens = 16_384
    return HarnessContext(
        state=state,
        phase="before_model",
        event={"run_id": "attempt-1", "toolkit": None},
    )


def _stack(tmp_path: Path):
    store = SQLiteContextV2Store(
        database_path=tmp_path / "context_v2.sqlite3",
        object_directory=tmp_path / "objects",
    )
    repository = store.bind_execution("execution-1")
    artifacts = ArtifactService(
        repository,
        sanitizer=lambda content, media_type: content,
    )
    projector = CanonicalSemanticEventProjector(
        attempt=_attempt(),
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )
    sink = DurableEventSink(repository, _attempt(), projector)
    return store, repository, artifacts, projector, sink


def _attachment(
    artifacts: ArtifactService,
    *,
    content: bytes,
    media_type: str,
    kind: str,
    name: str,
    operation_id: str,
) -> HostResolvedAttachment:
    return HostResolvedAttachment(
        artifact=artifacts.persist(
            content,
            media_type=media_type,
            operation_id=operation_id,
        ),
        kind=kind,
        name=name,
        media_type=media_type,
    )


def test_coordinator_materializes_image_and_removes_top_level_provenance(
    tmp_path: Path,
) -> None:
    store, repository, artifacts, projector, sink = _stack(tmp_path)
    image = _attachment(
        artifacts,
        content=b"\x89PNG\r\n\x1a\nimage",
        media_type="image/png",
        kind="image",
        name="photo.png",
        operation_id="image-1",
    )
    ContextInputIngress(
        attempt=_attempt(),
        projector=projector,
        sink=sink,
    ).persist(
        HostResolvedCurrentInput(
            attempt=_attempt(),
            content="inspect this",
            attachments=(image,),
        )
    )
    request = JournalContextRequestFactory(
        attempt=_attempt(),
        journal=repository,
        model_window_fallback=lambda provider, model: 16_384,
    )(_context())
    capabilities = SQLiteContextCompilerV2Store(
        context_store=store,
    ).bind_execution("execution-1", artifacts=artifacts)

    result = ContextCompileCoordinator(
        journal=repository,
        checkpoint_repository=capabilities.checkpoints,
        build_repository=capabilities.context_builds,
        partial_attempt_sink=lambda request, error: None,
        model_projection=ModelContextProjection(artifacts),
    ).compile(request)

    message = result.messages[0]
    assert set(message) == {"role", "content"}
    assert message["content"] == (
        {"type": "text", "text": "inspect this"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode(
                    "ascii"
                ),
            },
        },
    )
    assert "attachments" not in message


def test_coordinator_without_materializer_rejects_attachment_before_build(
    tmp_path: Path,
) -> None:
    store, repository, artifacts, projector, sink = _stack(tmp_path)
    pdf = _attachment(
        artifacts,
        content=b"%PDF-1.7\n",
        media_type="application/pdf",
        kind="pdf",
        name="report.pdf",
        operation_id="pdf-1",
    )
    ContextInputIngress(
        attempt=_attempt(),
        projector=projector,
        sink=sink,
    ).persist(
        HostResolvedCurrentInput(
            attempt=_attempt(),
            content="",
            attachments=(pdf,),
        )
    )
    request = JournalContextRequestFactory(
        attempt=_attempt(),
        journal=repository,
        model_window_fallback=lambda provider, model: 16_384,
    )(_context())
    capabilities = SQLiteContextCompilerV2Store(
        context_store=store,
    ).bind_execution("execution-1", artifacts=artifacts)

    with pytest.raises(ContextModelProjectionError) as raised:
        ContextCompileCoordinator(
            journal=repository,
            checkpoint_repository=capabilities.checkpoints,
            build_repository=capabilities.context_builds,
            partial_attempt_sink=lambda request, error: None,
        ).compile(request)

    assert raised.value.reason == "attachment_materializer_unavailable"
    assert capabilities.context_builds.get_by_trigger(
        trigger_cursor=repository.capture_snapshot().high_water
    ) is None


@pytest.mark.parametrize(
    ("provider", "reason"),
    (("", "provider_multimodal_unsupported"),),
)
def test_unsupported_provider_fails_closed_with_a_local_reason(
    tmp_path: Path,
    provider: str,
    reason: str,
) -> None:
    _store, _repository, artifacts, _projector, _sink = _stack(tmp_path)
    image = _attachment(
        artifacts,
        content=b"image",
        media_type="image/png",
        kind="image",
        name="image.png",
        operation_id="unsupported-image",
    )

    with pytest.raises(ContextModelProjectionError) as raised:
        ModelContextProjection(artifacts).project(
            [
                {
                    "role": "user",
                    "content": "inspect",
                    "attachments": [image.to_dict()],
                }
            ],
            provider=provider,
        )

    assert raised.value.reason == reason
    assert raised.value.boundary == "model_context_projection"
    assert raised.value.message_index == 0


def test_ollama_allows_only_base64_image_at_the_context_boundary(
    tmp_path: Path,
) -> None:
    _store, _repository, artifacts, _projector, _sink = _stack(tmp_path)
    image = _attachment(
        artifacts,
        content=b"image",
        media_type="image/png",
        kind="image",
        name="image.png",
        operation_id="ollama-image",
    )
    pdf = _attachment(
        artifacts,
        content=b"%PDF-1.7",
        media_type="application/pdf",
        kind="pdf",
        name="document.pdf",
        operation_id="ollama-pdf",
    )
    projection = ModelContextProjection(artifacts)

    image_result = projection.project(
        [
            {
                "role": "user",
                "content": "inspect",
                "attachments": [image.to_dict()],
            }
        ],
        provider="ollama",
    )

    assert image_result[0]["content"][1]["type"] == "image"
    assert image_result[0]["content"][1]["source"]["type"] == "base64"
    with pytest.raises(ContextModelProjectionError) as raised:
        projection.project(
            [
                {
                    "role": "user",
                    "content": "inspect",
                    "attachments": [pdf.to_dict()],
                }
            ],
            provider="ollama",
        )
    assert raised.value.reason == "provider_attachment_unsupported"


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            {"type": "url", "url": "https://example.test/image.png"},
            {
                "type": "image",
                "source": {
                    "type": "url",
                    "url": "https://example.test/image.png",
                },
            },
        ),
        (
            {"type": "file_id", "file_id": "file-123"},
            {
                "type": "pdf",
                "source": {"type": "file_id", "file_id": "file-123"},
            },
        ),
    ),
)
def test_remote_descriptor_decoder_is_bounded_to_canonical_source_fields(
    tmp_path: Path,
    source: dict,
    expected: dict,
) -> None:
    _store, _repository, artifacts, _projector, _sink = _stack(tmp_path)
    kind = "image" if source["type"] == "url" else "pdf"
    descriptor = json.dumps(source, sort_keys=True).encode("utf-8")
    attachment = _attachment(
        artifacts,
        content=descriptor,
        media_type="application/vnd.example.attachment+json",
        kind=kind,
        name="remote.json",
        operation_id=f"remote-{kind}",
    )
    projection = ModelContextProjection(
        artifacts,
        remote_source_decoder=lambda attachment, content: json.loads(content),
    )

    result = projection.project(
        [
            {
                "role": "user",
                "content": "",
                "attachments": [attachment.to_dict()],
            }
        ],
        provider="anthropic",
    )

    assert result[0]["content"] == [expected]
    assert "attachments" not in result[0]


@pytest.mark.parametrize(
    ("kind", "source"),
    (
        ("image", {"type": "file_id", "file_id": "file-image"}),
        (
            "image",
            {
                "type": "url",
                "url": "https://example.test/image.pdf",
                "media_type": "application/pdf",
            },
        ),
        (
            "pdf",
            {
                "type": "url",
                "url": "https://example.test/photo.png",
                "media_type": "image/png",
            },
        ),
    ),
)
def test_remote_source_cannot_change_attachment_modality(
    tmp_path: Path,
    kind: str,
    source: dict,
) -> None:
    _store, _repository, artifacts, _projector, _sink = _stack(tmp_path)
    descriptor = json.dumps(source, sort_keys=True).encode("utf-8")
    attachment = _attachment(
        artifacts,
        content=descriptor,
        media_type="application/vnd.example.attachment+json",
        kind=kind,
        name="remote.json",
        operation_id=f"modality-{kind}-{source['type']}",
    )

    with pytest.raises(ContextModelProjectionError) as raised:
        ModelContextProjection(
            artifacts,
            remote_source_decoder=lambda attachment, content: json.loads(content),
        ).project(
            [
                {
                    "role": "user",
                    "content": "",
                    "attachments": [attachment.to_dict()],
                }
            ],
            provider="openai",
        )
    assert raised.value.reason == "attachment_media_type_mismatch"


@pytest.mark.parametrize(
    "source",
    (
        {"type": "url", "url": "javascript:alert(1)"},
        {"type": "url", "url": "file:///etc/passwd"},
        {"type": "url", "url": "https://user@example.test/image.png"},
        {"type": "url", "url": "https://example.test/%0d%0aX-Test:yes"},
        {"type": "file_id", "file_id": "file-1\nheader"},
    ),
)
def test_remote_source_rejects_unsafe_transport_identifiers(
    tmp_path: Path,
    source: dict,
) -> None:
    _store, _repository, artifacts, _projector, _sink = _stack(tmp_path)
    descriptor = json.dumps(source, sort_keys=True).encode("utf-8")
    kind = "pdf" if source["type"] == "file_id" else "image"
    attachment = _attachment(
        artifacts,
        content=descriptor,
        media_type="application/vnd.example.attachment+json",
        kind=kind,
        name="remote.json",
        operation_id=f"unsafe-{len(descriptor)}",
    )

    with pytest.raises(ContextModelProjectionError) as raised:
        ModelContextProjection(
            artifacts,
            remote_source_decoder=lambda attachment, content: json.loads(content),
        ).project(
            [
                {
                    "role": "user",
                    "content": "",
                    "attachments": [attachment.to_dict()],
                }
            ],
            provider="openai",
        )
    assert raised.value.reason in {
        "remote_source_url_invalid",
        "remote_source_file_id_invalid",
    }


def test_handoff_attachment_is_provenance_only_and_never_read(
    tmp_path: Path,
) -> None:
    _store, _repository, artifacts, _projector, _sink = _stack(tmp_path)
    handoff = _attachment(
        artifacts,
        content=b'{"handoff":"complete"}',
        media_type="application/json",
        kind="handoff",
        name="handoff.json",
        operation_id="handoff-1",
    )
    result = ModelContextProjection(artifacts).project(
        [
            {
                "role": "user",
                "content": "derived handoff descriptor",
                "attachments": [handoff.to_dict()],
            }
        ],
        provider="openai",
    )

    assert result == (
        {"role": "user", "content": "derived handoff descriptor"},
    )
