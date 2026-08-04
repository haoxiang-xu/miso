from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from unchain.context.tool_catalog import ToolCatalogEnvelope
from unchain.journal.models import (
    AttemptRef,
    GenerationRef,
    JournalAppendRequest,
    OperationRef,
)
from unchain.journal.ports import JournalConflictError, JournalScopeError
from unchain.journal.provider_wire import (
    persist_provider_wire_snapshot,
    recover_provider_wire_authority,
)
from unchain.persistence.sqlite_v2 import (
    SQLiteContextV2Store,
    SQLiteContextV2StoreIntegrityError,
)
from unchain.providers.request_lease import (
    ProviderRequestDisposition,
    ProviderRequestLeaseConflict,
    ProviderRequestLeaseCoordinator,
    ProviderRequestLeasePort,
)
from unchain.providers.wire_envelope import ProviderWireEnvelope, ProviderWireRoute


ATTEMPT = AttemptRef(
    GenerationRef("execution-sqlite-v2", "generation-sqlite-v2"),
    "attempt-sqlite-v2",
)
ITERATION = 3


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _operation(index: int, *, digest: str | None = None) -> OperationRef:
    return OperationRef(
        f"sqlite-v2-operation-{index}",
        digest if digest is not None else f"{index:x}" * 64,
    )


def _request(index: int, *, execution_id: str = ATTEMPT.generation.execution_id):
    attempt = AttemptRef(
        GenerationRef(execution_id, ATTEMPT.generation.generation_id),
        ATTEMPT.attempt_id,
    )
    return JournalAppendRequest(
        event_id=f"sqlite-v2-event-{index}",
        event_type="message.user",
        attempt=attempt,
        operation=_operation(index),
        payload={"content": f"message-{index}"},
    )


def _store(tmp_path):
    return SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )


def _catalog() -> ToolCatalogEnvelope:
    return ToolCatalogEnvelope(
        attempt=ATTEMPT,
        iteration=ITERATION,
        provider="openai",
        model="frontier-model",
        semantic_schemas=[],
        entries=[],
        required_betas_sha256=_sha256_json([]),
        prompt_sha256="0" * 64,
        exposure_plan_sha256="1" * 64,
    )


def _wire_envelope(catalog: ToolCatalogEnvelope) -> ProviderWireEnvelope:
    return ProviderWireEnvelope(
        attempt=ATTEMPT,
        iteration=ITERATION,
        provider="openai",
        configured_model="frontier-model",
        request_model="frontier-model",
        adapter_revision="unchain.openai.responses.request.v1",
        transport_kind="openai.responses.create",
        transport_target_sha256="2" * 64,
        source_request_sha256="3" * 64,
        source_payload_sha256="4" * 64,
        catalog_sha256=catalog.catalog_sha256,
        prompt_sha256=catalog.prompt_sha256,
        tool_schema_sha256=catalog.tool_schema_sha256,
        required_betas=(),
        base_anthropic_betas=(),
        routes=(
            ProviderWireRoute(
                name="primary",
                request={
                    "model": "frontier-model",
                    "input": [{"role": "user", "content": "hello"}],
                    "stream": True,
                    "store": False,
                },
            ),
        ),
    )


def test_store_enables_wal_and_journal_survives_reopen(tmp_path) -> None:
    store = _store(tmp_path)
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    appended = repository.append(request=_request(1))

    reopened = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    page = reopened.read()

    assert page.events == (appended.event,)
    assert page.next_cursor == appended.cursor
    assert page.has_more is False
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_journal_scope_cursor_and_operation_idempotency_are_durable(tmp_path) -> None:
    repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    first = repository.append(request=_request(1))
    duplicate = repository.append(request=_request(1))

    assert duplicate.duplicate is True
    assert duplicate.event == first.event
    assert duplicate.cursor == first.cursor

    changed = JournalAppendRequest(
        event_id="sqlite-v2-event-1",
        event_type="message.user",
        attempt=ATTEMPT,
        operation=OperationRef(first.event.operation.operation_id, "f" * 64),
        payload={"content": "changed"},
    )
    with pytest.raises(JournalConflictError, match="operation|payload"):
        repository.append(request=changed)
    with pytest.raises(JournalScopeError, match="execution|scope"):
        repository.append(request=_request(2, execution_id="execution-foreign"))
    with pytest.raises(JournalScopeError, match="cursor"):
        repository.read(after=type(first.cursor)(1, "forged-event"))


def test_journal_read_rejects_operation_metadata_corruption(tmp_path) -> None:
    store = _store(tmp_path)
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    repository.append(request=_request(1))

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            UPDATE operations SET payload_sha256 = ?
            WHERE execution_id = ? AND operation_id = ?
            """,
            (
                "f" * 64,
                ATTEMPT.generation.execution_id,
                _operation(1).operation_id,
            ),
        )

    with pytest.raises(
        SQLiteContextV2StoreIntegrityError,
        match="journal|operation|payload|digest",
    ):
        repository.read()


def test_artifact_bytes_are_content_addressed_verified_and_restart_safe(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    content = b'{"complete":"artifact"}'
    artifact = repository.put(
        content=content,
        media_type="application/json",
        preview="",
        operation=_operation(1),
    )

    reopened = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    assert reopened.read_full_verified(artifact=artifact) == content
    assert reopened.read_verified(artifact=artifact, offset=2, limit=8) == content[2:10]

    object_path = store.object_directory / artifact.sha256
    object_path.write_bytes(b"tampered")
    with pytest.raises(
        SQLiteContextV2StoreIntegrityError, match="artifact|object|digest"
    ):
        reopened.read_full_verified(artifact=artifact)


def test_provider_wire_receipt_and_bytes_recover_after_restart(tmp_path) -> None:
    repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    catalog = _catalog()
    envelope = _wire_envelope(catalog)
    receipt = persist_provider_wire_snapshot(
        repository,
        envelope=envelope,
        catalog=catalog,
        artifact_operation=_operation(1),
        event_operation=_operation(2),
        event_id="provider-wire-snapshot-1",
        expected_artifact_revision=0,
    )

    reopened = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    recovered = recover_provider_wire_authority(
        reopened,
        attempt=ATTEMPT,
        iteration=ITERATION,
        catalog=catalog,
        expected_provider=envelope.provider,
        expected_adapter_revision=envelope.adapter_revision,
        expected_envelope_sha256=envelope.envelope_sha256,
        expected_artifact=receipt.artifact,
        expected_cursor=receipt.cursor,
    )

    assert recovered.envelope == envelope
    assert recovered.artifact == receipt.artifact
    assert recovered.event == receipt.event


def test_provider_wire_artifact_claim_is_idempotent_across_interrupted_sequence(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    catalog = _catalog()
    content = _wire_envelope(catalog).canonical_bytes()
    operation = _operation(1)

    first = repository.write_provider_wire_cas(
        content=content,
        media_type="application/json",
        preview="",
        operation=operation,
        expected_revision=0,
    )
    reopened = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    replay = reopened.write_provider_wire_cas(
        content=content,
        media_type="application/json",
        preview="",
        operation=operation,
        expected_revision=0,
    )

    assert replay == first
    assert reopened.read_provider_wire_full_verified(artifact=replay) == content


def test_receipt_indexes_are_exact_bounded_and_report_overflow(tmp_path) -> None:
    repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    catalog = _catalog()
    content = catalog.canonical_bytes()
    artifact = repository.put(
        content=content,
        media_type="application/json",
        preview="",
        operation=_operation(1),
    )
    for index in range(2, 5):
        repository.append(
            request=JournalAppendRequest(
                event_id=f"catalog-snapshot-{index}",
                event_type="tool.catalog_snapshot",
                attempt=ATTEMPT,
                operation=_operation(index),
                payload={
                    "iteration": ITERATION,
                    "catalog_sha256": catalog.catalog_sha256,
                    "catalog_artifact": artifact.to_dict(),
                },
                resource_refs=(artifact.ref,),
            )
        )

    lookup = repository.lookup_tool_catalog_receipts(
        attempt=ATTEMPT,
        iteration=ITERATION,
    )

    assert len(lookup.events) == 2
    assert lookup.overflow is True
    assert [event.store_seq for event in lookup.events] == [1, 2]


def test_tool_receipt_index_and_snapshot_share_the_durable_event_prefix(
    tmp_path,
) -> None:
    repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    for index in range(1, 6):
        repository.append(
            request=JournalAppendRequest(
                event_id=f"tool-started-{index}",
                event_type="tool.started",
                attempt=ATTEMPT,
                operation=_operation(index),
                payload={"call_id": "call-sqlite-v2", "tool_name": "lookup"},
            )
        )

    lookup = repository.lookup_tool_execution_receipts(
        attempt=ATTEMPT,
        call_id="call-sqlite-v2",
    )
    snapshot = repository.capture_snapshot()

    assert len(lookup.events) == 4
    assert lookup.overflow is True
    assert snapshot.events[-1].store_seq == 5
    assert snapshot.high_water.event_id == "tool-started-5"


def test_started_provider_request_recovers_uncertain_and_never_reclaims(
    tmp_path,
) -> None:
    repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    coordinator = ProviderRequestLeaseCoordinator(repository)
    started = coordinator.claim_initial(
        attempt=ATTEMPT,
        iteration=ITERATION,
        envelope_sha256="a" * 64,
        route="primary",
        route_sha256="b" * 64,
        operation=_operation(1),
    )

    restarted_repository = _store(tmp_path).bind_execution(
        ATTEMPT.generation.execution_id
    )
    restarted = ProviderRequestLeaseCoordinator(restarted_repository)
    recovery = restarted.recover(started.subject)

    assert recovery.disposition is ProviderRequestDisposition.UNCERTAIN
    assert recovery.auto_resend_allowed is False
    with pytest.raises(ProviderRequestLeaseConflict, match="already|claimed"):
        restarted.claim_initial(
            attempt=ATTEMPT,
            iteration=ITERATION,
            envelope_sha256="a" * 64,
            route="primary",
            route_sha256="b" * 64,
            operation=_operation(2),
        )


def test_provider_request_terminal_history_survives_restart_and_gates_retry(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    coordinator = ProviderRequestLeaseCoordinator(repository)
    started = coordinator.claim_initial(
        attempt=ATTEMPT,
        iteration=ITERATION,
        envelope_sha256="a" * 64,
        route="primary",
        route_sha256="b" * 64,
        operation=_operation(1),
    )
    failed = coordinator.record_failure(
        started,
        classification="transient",
        retryable=True,
        visible_output=False,
        operation=_operation(2),
    )

    restarted = ProviderRequestLeaseCoordinator(
        _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    )
    recovery = restarted.recover(failed.subject)
    retry = restarted.claim_retry(failed, operation=_operation(3))

    assert recovery.disposition is ProviderRequestDisposition.RETRYABLE_FAILURE
    assert retry.subject.retry_ordinal == 1
    with sqlite3.connect(store.database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM provider_request_lease_revisions"
        ).fetchone()[0]
    assert count == 3


def test_concurrent_provider_request_claim_has_one_durable_winner(tmp_path) -> None:
    _store(tmp_path)

    def claim(index: int):
        repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
        return ProviderRequestLeaseCoordinator(repository).claim_initial(
            attempt=ATTEMPT,
            iteration=ITERATION,
            envelope_sha256="a" * 64,
            route="primary",
            route_sha256="b" * 64,
            operation=_operation(index),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim, index) for index in (1, 2)]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except ProviderRequestLeaseConflict:
            outcomes.append("conflict")

    assert sum(item == "conflict" for item in outcomes) == 1
    assert sum(item != "conflict" for item in outcomes) == 1


def test_concurrent_same_operation_claim_returns_one_send_authority(tmp_path) -> None:
    repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    both_observed_unclaimed = Barrier(2)

    class SynchronizedPort(ProviderRequestLeasePort):
        def load(self, *, subject):
            current = repository.load(subject=subject)
            if current is None:
                both_observed_unclaimed.wait(timeout=5)
            return current

        def compare_and_swap(
            self,
            *,
            subject,
            expected_revision,
            replacement,
        ):
            return repository.compare_and_swap(
                subject=subject,
                expected_revision=expected_revision,
                replacement=replacement,
            )

    def claim():
        return ProviderRequestLeaseCoordinator(SynchronizedPort()).claim_initial(
            attempt=ATTEMPT,
            iteration=ITERATION,
            envelope_sha256="a" * 64,
            route="primary",
            route_sha256="b" * 64,
            operation=_operation(1),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except ProviderRequestLeaseConflict:
            outcomes.append("conflict")

    assert sum(item == "conflict" for item in outcomes) == 1
    assert sum(item != "conflict" for item in outcomes) == 1
