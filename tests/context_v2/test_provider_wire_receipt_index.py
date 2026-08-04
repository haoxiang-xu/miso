from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from dataclasses import replace

import pytest

from unchain.context.tool_catalog import ToolCatalogEnvelope
from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    GenerationRef,
    JournalAppendRequest,
    JournalAppendResult,
    JournalEvent,
    JournalPage,
    OperationRef,
    ResourceRef,
)
from unchain.journal.snapshot import capture_journal_snapshot
from unchain.providers.wire_envelope import ProviderWireEnvelope, ProviderWireRoute


ATTEMPT = AttemptRef(
    GenerationRef("execution-wire-receipt", "generation-wire-receipt"),
    "attempt-wire-receipt",
)
FOREIGN_ATTEMPT = AttemptRef(
    GenerationRef("execution-wire-receipt", "generation-wire-receipt"),
    "attempt-wire-foreign",
)
ITERATION = 7
ADAPTER_REVISION = "unchain.openai.responses.request.v1"
TRANSPORT_KIND = "openai.responses.create"
TARGET_SHA256 = "2" * 64


def _api():
    try:
        return importlib.import_module("unchain.journal.provider_wire")
    except ModuleNotFoundError:
        pytest.fail("provider wire journal receipt API is not implemented")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_sha256(value: object) -> str:
    return _sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _catalog(*, provider: str = "openai") -> ToolCatalogEnvelope:
    return ToolCatalogEnvelope(
        attempt=ATTEMPT,
        iteration=ITERATION,
        provider=provider,
        model="frontier-model",
        semantic_schemas=[],
        entries=[],
        required_betas_sha256=_json_sha256([]),
        prompt_sha256="0" * 64,
        exposure_plan_sha256="1" * 64,
    )


def _envelope(
    *,
    catalog: ToolCatalogEnvelope | None = None,
    adapter_revision: str = ADAPTER_REVISION,
) -> ProviderWireEnvelope:
    resolved_catalog = catalog or _catalog()
    return ProviderWireEnvelope(
        attempt=ATTEMPT,
        iteration=ITERATION,
        provider="openai",
        configured_model="frontier-model",
        request_model="frontier-model",
        adapter_revision=adapter_revision,
        transport_kind=TRANSPORT_KIND,
        transport_target_sha256=TARGET_SHA256,
        source_request_sha256="3" * 64,
        source_payload_sha256="4" * 64,
        catalog_sha256=resolved_catalog.catalog_sha256,
        prompt_sha256=resolved_catalog.prompt_sha256,
        tool_schema_sha256=resolved_catalog.tool_schema_sha256,
        required_betas=(),
        base_anthropic_betas=(),
        routes=[
            ProviderWireRoute(
                name="primary",
                request={
                    "model": "frontier-model",
                    "input": [{"role": "user", "content": "hello"}],
                    "stream": True,
                    "store": False,
                },
            )
        ],
    )


def _artifact(envelope: ProviderWireEnvelope) -> ArtifactRef:
    content = envelope.canonical_bytes()
    return ArtifactRef(
        ref=ResourceRef("artifact", "provider-wire-1", 1),
        media_type="application/json",
        byte_length=len(content),
        sha256=_sha256(content),
        preview="",
    )


def _event(
    envelope: ProviderWireEnvelope,
    artifact: ArtifactRef,
    *,
    store_seq: int = 41,
    attempt: AttemptRef = ATTEMPT,
    event_type: str = "provider.wire_snapshot",
    iteration: object = ITERATION,
    provider: object = "openai",
    adapter_revision: object = ADAPTER_REVISION,
    catalog_sha256: object | None = None,
    envelope_sha256: object | None = None,
    refs: tuple[ResourceRef, ...] | None = None,
    extra_payload: dict[str, object] | None = None,
) -> JournalEvent:
    payload: dict[str, object] = {
        "iteration": iteration,
        "provider": provider,
        "adapter_revision": adapter_revision,
        "catalog_sha256": catalog_sha256 or envelope.catalog_sha256,
        "envelope_sha256": envelope_sha256 or envelope.envelope_sha256,
        "wire_artifact": artifact.to_dict(),
    }
    if extra_payload:
        payload.update(extra_payload)
    return JournalEvent(
        event_id=f"provider-wire-event-{store_seq}",
        event_type=event_type,
        attempt=attempt,
        operation=OperationRef(f"provider-wire-event-op-{store_seq}", "8" * 64),
        store_seq=store_seq,
        payload=payload,
        resource_refs=refs if refs is not None else (artifact.ref,),
    )


def _store(
    *,
    envelope: ProviderWireEnvelope,
    artifact: ArtifactRef | None = None,
    raw_bytes: bytes | None = None,
    events: tuple[JournalEvent, ...] | None = None,
    overflow: bool = False,
    returned_attempt: AttemptRef = ATTEMPT,
    returned_iteration: int = ITERATION,
    execution_id: str = ATTEMPT.generation.execution_id,
):
    api = _api()
    resolved_artifact = artifact or _artifact(envelope)
    resolved_bytes = envelope.canonical_bytes() if raw_bytes is None else raw_bytes
    resolved_events = (
        (_event(envelope, resolved_artifact),) if events is None else events
    )

    class Store(api.BoundProviderWireStore):
        def __init__(self) -> None:
            super().__init__(execution_id)
            self.calls: list[object] = []
            self.lookup_count = 0

        def write_provider_wire_cas(
            self,
            *,
            content,
            media_type,
            preview,
            operation,
            expected_revision,
        ):
            self.calls.append(
                (
                    "write",
                    content,
                    media_type,
                    preview,
                    operation,
                    expected_revision,
                )
            )
            return resolved_artifact

        def read_provider_wire_full_verified(self, *, artifact):
            self.calls.append(("read", artifact))
            return resolved_bytes

        def append(self, *, request: JournalAppendRequest) -> JournalAppendResult:
            self.calls.append(("append", request))
            persisted = JournalEvent(
                event_id=request.event_id,
                event_type=request.event_type,
                attempt=request.attempt,
                operation=request.operation,
                store_seq=41,
                payload=request.payload,
                resource_refs=request.resource_refs,
            )
            return JournalAppendResult(
                event=persisted,
                cursor=EventCursor(persisted.store_seq, persisted.event_id),
            )

        def lookup_provider_wire_receipts(self, *, attempt, iteration):
            self.lookup_count += 1
            self.calls.append(("lookup", attempt, iteration))
            return api.ProviderWireReceiptLookup(
                attempt=returned_attempt,
                iteration=returned_iteration,
                events=resolved_events,
                overflow=overflow,
            )

        def read(self, *, after=None, limit=100) -> JournalPage:
            raise AssertionError("provider wire recovery must not scan pages")

        def capture_snapshot(
            self,
            *,
            max_events=10_000,
            max_bytes=32 * 1024 * 1024,
        ):
            return capture_journal_snapshot(
                execution_id=self.execution_id,
                events=(),
            )

    return Store()


def _recover(store, envelope, catalog, artifact, **overrides):
    api = _api()
    event = _event(envelope, artifact)
    arguments = {
        "attempt": ATTEMPT,
        "iteration": ITERATION,
        "catalog": catalog,
        "expected_provider": envelope.provider,
        "expected_adapter_revision": envelope.adapter_revision,
        "expected_envelope_sha256": envelope.envelope_sha256,
        "expected_artifact": artifact,
        "expected_cursor": EventCursor(event.store_seq, event.event_id),
    }
    arguments.update(overrides)
    return api.recover_provider_wire_authority(store, **arguments)


def test_persist_writes_cas_reads_back_exact_bytes_then_appends_one_exact_event() -> (
    None
):
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)
    store = _store(envelope=envelope, artifact=artifact)
    artifact_operation = OperationRef("provider-wire-artifact-op", "6" * 64)
    event_operation = OperationRef("provider-wire-event-op", "7" * 64)

    receipt = api.persist_provider_wire_snapshot(
        store,
        envelope=envelope,
        catalog=catalog,
        artifact_operation=artifact_operation,
        event_operation=event_operation,
        event_id="provider-wire-event-41",
        expected_artifact_revision=0,
    )

    assert [call[0] for call in store.calls] == ["write", "read", "append"]
    assert store.calls[0][1:] == (
        envelope.canonical_bytes(),
        "application/json",
        "",
        artifact_operation,
        0,
    )
    assert type(receipt) is api.ProviderWireSnapshotReceipt
    assert receipt.envelope == envelope
    assert receipt.artifact == artifact
    assert receipt.cursor == EventCursor(41, "provider-wire-event-41")
    assert receipt.event.event_type == "provider.wire_snapshot"
    assert dict(receipt.event.payload) == {
        "iteration": ITERATION,
        "provider": "openai",
        "adapter_revision": ADAPTER_REVISION,
        "catalog_sha256": catalog.catalog_sha256,
        "envelope_sha256": envelope.envelope_sha256,
        "wire_artifact": artifact.to_dict(),
    }
    assert receipt.event.resource_refs == (artifact.ref,)


@pytest.mark.parametrize(
    ("artifact_mutation", "raw_mutation", "message"),
    [
        (lambda artifact: replace(artifact, media_type="text/plain"), None, "JSON"),
        (lambda artifact: replace(artifact, preview="leak"), None, "preview"),
        (lambda artifact: replace(artifact, byte_length=1), None, "byte"),
        (lambda artifact: replace(artifact, sha256="f" * 64), None, "sha256|digest"),
        (None, lambda content: content + b" ", "readback|bytes"),
    ],
)
def test_persist_fails_before_append_if_cas_receipt_or_readback_is_not_exact(
    artifact_mutation,
    raw_mutation,
    message: str,
) -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)
    if artifact_mutation:
        artifact = artifact_mutation(artifact)
    raw = envelope.canonical_bytes()
    if raw_mutation:
        raw = raw_mutation(raw)
    store = _store(envelope=envelope, artifact=artifact, raw_bytes=raw)

    with pytest.raises(api.ProviderWireReceiptIntegrityError, match=message):
        api.persist_provider_wire_snapshot(
            store,
            envelope=envelope,
            catalog=catalog,
            artifact_operation=OperationRef("provider-wire-artifact-op", "6" * 64),
            event_operation=OperationRef("provider-wire-event-op", "7" * 64),
            event_id="provider-wire-event-41",
            expected_artifact_revision=0,
        )
    assert all(call[0] != "append" for call in store.calls)


def test_recovery_uses_one_bounded_lookup_and_exact_raw_read_before_authority() -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)
    event = _event(envelope, artifact)
    store = _store(envelope=envelope, artifact=artifact, events=(event,))

    recovered = _recover(store, envelope, catalog, artifact)

    assert type(recovered) is api.RecoveredProviderWireAuthority
    assert recovered.envelope == envelope
    assert recovered.catalog == catalog
    assert recovered.artifact == artifact
    assert recovered.event is event
    assert recovered.cursor == EventCursor(event.store_seq, event.event_id)
    assert store.lookup_count == 1
    assert [call[0] for call in store.calls] == ["lookup", "read"]


@pytest.mark.parametrize(
    ("events_factory", "overflow", "error", "message"),
    [
        (lambda envelope, artifact: (), False, "not_found", "not found"),
        (
            lambda envelope, artifact: (
                _event(envelope, artifact, store_seq=41),
                _event(envelope, artifact, store_seq=42),
            ),
            False,
            "integrity",
            "exactly one|conflict",
        ),
        (
            lambda envelope, artifact: (_event(envelope, artifact),),
            True,
            "integrity",
            "overflow",
        ),
    ],
)
def test_recovery_distinguishes_missing_from_duplicate_or_overflow_receipts(
    events_factory,
    overflow: bool,
    error: str,
    message: str,
) -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)
    events = events_factory(envelope, artifact)
    store = _store(
        envelope=envelope,
        artifact=artifact,
        events=events,
        overflow=overflow,
    )
    exception = (
        api.ProviderWireReceiptNotFound
        if error == "not_found"
        else api.ProviderWireReceiptIntegrityError
    )

    with pytest.raises(exception, match=message):
        _recover(store, envelope, catalog, artifact)
    assert all(call[0] != "read" for call in store.calls)


def test_lookup_is_exact_bounded_ordered_and_round_trips() -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)
    lookup = api.ProviderWireReceiptLookup(
        attempt=ATTEMPT,
        iteration=ITERATION,
        events=(
            _event(envelope, artifact, store_seq=41),
            _event(envelope, artifact, store_seq=42),
        ),
    )
    assert api.ProviderWireReceiptLookup.from_dict(lookup.to_dict()) == lookup

    with pytest.raises(ValueError, match="at most two"):
        api.ProviderWireReceiptLookup(
            attempt=ATTEMPT,
            iteration=ITERATION,
            events=lookup.events + (_event(envelope, artifact, store_seq=43),),
        )
    with pytest.raises(ValueError, match="strictly ordered"):
        api.ProviderWireReceiptLookup(
            attempt=ATTEMPT,
            iteration=ITERATION,
            events=tuple(reversed(lookup.events)),
        )


@pytest.mark.parametrize(
    ("event_factory", "message"),
    [
        (
            lambda envelope, artifact: _event(
                envelope, artifact, attempt=FOREIGN_ATTEMPT
            ),
            "foreign attempt",
        ),
        (
            lambda envelope, artifact: _event(
                envelope, artifact, event_type="tool.started"
            ),
            "event type",
        ),
        (
            lambda envelope, artifact: _event(
                envelope, artifact, iteration=ITERATION + 1
            ),
            "iteration",
        ),
    ],
)
def test_lookup_rejects_scope_type_and_subject_tamper(
    event_factory, message: str
) -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)
    with pytest.raises(
        (TypeError, ValueError, api.ProviderWireReceiptIntegrityError),
        match=message,
    ):
        api.ProviderWireReceiptLookup(
            attempt=ATTEMPT,
            iteration=ITERATION,
            events=(event_factory(envelope, artifact),),
        )


@pytest.mark.parametrize(
    ("event_mutation", "message"),
    [
        (lambda event: replace(event, resource_refs=()), "resource refs"),
        (
            lambda event: replace(
                event,
                payload={**dict(event.payload), "unexpected": True},
            ),
            "payload fields",
        ),
        (
            lambda event: replace(
                event,
                payload={
                    **dict(event.payload),
                    "adapter_revision": "foreign.adapter.v1",
                },
            ),
            "revision|adapter",
        ),
        (
            lambda event: replace(
                event,
                payload={**dict(event.payload), "catalog_sha256": "f" * 64},
            ),
            "catalog",
        ),
        (
            lambda event: replace(
                event,
                payload={**dict(event.payload), "envelope_sha256": "f" * 64},
            ),
            "envelope",
        ),
    ],
)
def test_recovery_rejects_event_revision_catalog_envelope_or_ref_tamper(
    event_mutation,
    message: str,
) -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)
    event = event_mutation(_event(envelope, artifact))
    store = _store(envelope=envelope, artifact=artifact, events=(event,))

    with pytest.raises(api.ProviderWireReceiptIntegrityError, match=message):
        _recover(store, envelope, catalog, artifact)


@pytest.mark.parametrize(
    ("artifact_mutation", "message"),
    [
        (lambda artifact: replace(artifact, media_type="text/plain"), "JSON"),
        (lambda artifact: replace(artifact, preview="leak"), "preview"),
        (lambda artifact: replace(artifact, byte_length=1), "byte"),
        (lambda artifact: replace(artifact, sha256="f" * 64), "sha256|digest"),
        (
            lambda artifact: replace(
                artifact,
                ref=ResourceRef("artifact", "provider-wire-1", 1, fragment="page/1"),
            ),
            "whole artifact",
        ),
    ],
)
def test_recovery_rejects_artifact_descriptor_tamper(
    artifact_mutation, message: str
) -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    good_artifact = _artifact(envelope)
    bad_artifact = artifact_mutation(good_artifact)
    event = _event(envelope, bad_artifact)
    store = _store(envelope=envelope, artifact=bad_artifact, events=(event,))

    with pytest.raises(api.ProviderWireReceiptIntegrityError, match=message):
        _recover(
            store,
            envelope,
            catalog,
            good_artifact,
            expected_artifact=bad_artifact,
        )


@pytest.mark.parametrize(
    ("raw_mutation", "message"),
    [
        (lambda content: content + b" ", "bytes|canonical"),
        (lambda content: b"not-json", "bytes|JSON|UTF-8"),
        (
            lambda content: content.replace(b"frontier-model", b"tampered-model", 1),
            "sha256|bytes",
        ),
    ],
)
def test_recovery_rejects_raw_artifact_byte_tamper(raw_mutation, message: str) -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)
    store = _store(
        envelope=envelope,
        artifact=artifact,
        raw_bytes=raw_mutation(envelope.canonical_bytes()),
    )

    with pytest.raises(api.ProviderWireReceiptIntegrityError, match=message):
        _recover(store, envelope, catalog, artifact)


def test_recovery_rejects_oversized_artifact_before_disk_read() -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = replace(
        _artifact(envelope),
        byte_length=api.MAX_PROVIDER_WIRE_BYTES + 1,
    )
    event = _event(envelope, artifact)
    store = _store(envelope=envelope, artifact=artifact, events=(event,))

    with pytest.raises(
        api.ProviderWireReceiptIntegrityError,
        match="64 MiB|size|large",
    ):
        _recover(store, envelope, catalog, artifact)

    assert store.calls == []


def test_recovery_rejects_cursor_lookup_scope_and_catalog_tamper() -> None:
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)

    with pytest.raises(api.ProviderWireReceiptIntegrityError, match="cursor"):
        _recover(
            _store(envelope=envelope, artifact=artifact),
            envelope,
            catalog,
            artifact,
            expected_cursor=EventCursor(99, "provider-wire-event-99"),
        )
    with pytest.raises(api.ProviderWireReceiptIntegrityError, match="execution|scope"):
        _recover(
            _store(
                envelope=envelope,
                artifact=artifact,
                execution_id="execution-foreign",
            ),
            envelope,
            catalog,
            artifact,
        )
    with pytest.raises(
        api.ProviderWireReceiptIntegrityError, match="requested subject"
    ):
        _recover(
            _store(
                envelope=envelope,
                artifact=artifact,
                returned_attempt=FOREIGN_ATTEMPT,
            ),
            envelope,
            catalog,
            artifact,
        )
    foreign_catalog = _catalog(provider="anthropic")
    with pytest.raises(api.ProviderWireReceiptIntegrityError, match="catalog|provider"):
        _recover(
            _store(envelope=envelope, artifact=artifact),
            envelope,
            foreign_catalog,
            artifact,
        )


def test_recovery_rejects_expected_provider_revision_envelope_or_artifact_mismatch() -> (
    None
):
    api = _api()
    catalog = _catalog()
    envelope = _envelope(catalog=catalog)
    artifact = _artifact(envelope)
    variants = (
        ({"expected_provider": "anthropic"}, "provider"),
        ({"expected_adapter_revision": "foreign.adapter.v1"}, "revision|adapter"),
        ({"expected_envelope_sha256": "f" * 64}, "envelope"),
        (
            {
                "expected_artifact": replace(
                    artifact,
                    ref=ResourceRef("artifact", "provider-wire-other", 1),
                )
            },
            "artifact",
        ),
    )
    for overrides, message in variants:
        with pytest.raises(api.ProviderWireReceiptIntegrityError, match=message):
            _recover(
                _store(envelope=envelope, artifact=artifact),
                envelope,
                catalog,
                artifact,
                **overrides,
            )


def test_public_module_has_no_in_memory_persisted_authority_mint_function() -> None:
    api = _api()
    function_names = {
        name for name, value in inspect.getmembers(api) if inspect.isfunction(value)
    }
    assert not {name for name in function_names if "mint" in name and "persist" in name}
