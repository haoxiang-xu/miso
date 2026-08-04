from __future__ import annotations

import sqlite3

import pytest

from unchain.journal.models import (
    AttemptRef,
    GenerationRef,
    JournalAppendRequest,
    OperationRef,
)
from unchain.journal.provider_result import (
    ProviderTurnResultEnvelope,
    ProviderTurnResultIntegrityError,
    ProviderTurnResultReceiptLookup,
    recover_provider_turn_result,
)
from unchain.kernel.types import ModelTurnResult
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store
from unchain.providers.request_lease import (
    ProviderRequestLeaseCoordinator,
    ProviderRequestStatus,
    ProviderTurnResultBinding,
)


ATTEMPT = AttemptRef(
    GenerationRef("execution-result-sqlite", "generation-result-sqlite"),
    "attempt-result-sqlite",
)
ITERATION = 5
ENVELOPE_SHA256 = "a" * 64
ROUTE_SHA256 = "b" * 64


def _operation(name: str, digit: str) -> OperationRef:
    return OperationRef(name, digit * 64)


def _store(tmp_path) -> SQLiteContextV2Store:
    return SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )


def _claim(repository):
    coordinator = ProviderRequestLeaseCoordinator(repository)
    lease = coordinator.claim_initial(
        attempt=ATTEMPT,
        iteration=ITERATION,
        envelope_sha256=ENVELOPE_SHA256,
        route="primary",
        route_sha256=ROUTE_SHA256,
        operation=_operation("provider-result-request-start", "1"),
    )
    return coordinator, lease


def _envelope(lease, *, text: str = "durable") -> ProviderTurnResultEnvelope:
    return ProviderTurnResultEnvelope.from_model_turn_result(
        subject=lease.subject,
        route_sha256=lease.route_sha256,
        visible_output=True,
        result=ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": text}],
            tool_calls=[],
            final_text=text,
            response_id="response-durable",
            consumed_tokens=3,
            input_tokens=2,
            output_tokens=1,
        ),
    )


def _persist(repository, lease, envelope):
    return repository.persist_provider_turn_result_cas(
        started_lease=lease,
        envelope=envelope,
        artifact_operation=_operation("provider-result-artifact", "2"),
        event_operation=_operation("provider-result-event-operation", "3"),
        event_id="provider-result-event",
    )


def test_result_artifact_event_and_index_are_atomic_and_recover_after_restart(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    coordinator, started = _claim(repository)
    envelope = _envelope(started)

    receipt = _persist(repository, started, envelope)

    assert receipt.duplicate is False
    assert receipt.envelope == envelope
    assert repository.load(subject=started.subject) == started
    reopened = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    recovered = recover_provider_turn_result(
        reopened,
        subject=started.subject,
        expected_route_sha256=ROUTE_SHA256,
    )
    assert recovered.envelope.to_model_turn_result().final_text == "durable"
    assert recovered.artifact == receipt.artifact
    assert recovered.event == receipt.event

    completed = ProviderRequestLeaseCoordinator(reopened).record_completed_result(
        started,
        result_binding=ProviderTurnResultBinding(
            route_sha256=recovered.envelope.route_sha256,
            result_sha256=recovered.envelope.result_sha256,
            artifact=recovered.artifact,
            cursor=recovered.cursor,
        ),
        visible_output=recovered.envelope.visible_output,
        operation=_operation("provider-result-request-completed", "4"),
    )
    assert completed.status is ProviderRequestStatus.COMPLETED

    final_reopen = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    assert final_reopen.load(subject=started.subject) == completed
    assert (
        recover_provider_turn_result(
            final_reopen,
            subject=started.subject,
            expected_route_sha256=ROUTE_SHA256,
        )
        .envelope.to_model_turn_result()
        .final_text
        == "durable"
    )


def test_exact_replay_is_idempotent_and_changed_result_conflicts(tmp_path) -> None:
    repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    _coordinator, started = _claim(repository)
    envelope = _envelope(started)

    first = _persist(repository, started, envelope)
    replayed = _persist(repository, started, envelope)

    assert replayed.duplicate is True
    assert replayed.artifact == first.artifact
    assert replayed.event == first.event

    with pytest.raises(Exception):
        _persist(repository, started, _envelope(started, text="changed"))


def test_event_operation_conflict_rolls_back_artifact_metadata_and_receipt(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    _coordinator, started = _claim(repository)
    event_operation = _operation("provider-result-event-operation", "3")
    repository.append(
        request=JournalAppendRequest(
            event_id="foreign-event",
            event_type="message.user",
            attempt=ATTEMPT,
            operation=event_operation,
            payload={"content": "already claimed"},
        )
    )

    with pytest.raises(Exception):
        _persist(repository, started, _envelope(started))

    lookup = repository.lookup_provider_turn_result_receipts(subject=started.subject)
    assert lookup == ProviderTurnResultReceiptLookup(started.subject, (), False)
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM artifacts WHERE logical_kind = ?",
                ("provider_turn_result_artifact",),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM provider_turn_result_receipts"
            ).fetchone()[0]
            == 0
        )
    assert repository.load(subject=started.subject) == started


def test_current_lease_must_still_be_exact_started_before_atomic_result_write(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    coordinator, started = _claim(repository)
    coordinator.record_failure(
        started,
        classification="bad_request",
        retryable=False,
        visible_output=False,
        operation=_operation("provider-result-request-failed", "5"),
    )

    with pytest.raises(ProviderTurnResultIntegrityError, match="lease|STARTED"):
        _persist(repository, started, _envelope(started))

    assert (
        repository.lookup_provider_turn_result_receipts(subject=started.subject).events
        == ()
    )
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM artifacts WHERE logical_kind = ?",
                ("provider_turn_result_artifact",),
            ).fetchone()[0]
            == 0
        )
