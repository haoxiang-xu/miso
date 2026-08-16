from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    GenerationRef,
    JournalEvent,
    OperationRef,
    ResourceRef,
)
from unchain.journal.provider_result import (
    PROVIDER_TURN_RESULT_EVENT_TYPE,
    BoundProviderTurnResultStore,
    ProviderTurnResultEnvelope,
    ProviderTurnResultIntegrityError,
    ProviderTurnResultPersistRequest,
    ProviderTurnResultReceipt,
    ProviderTurnResultReceiptLookup,
    PROVIDER_TURN_RESULT_LIMITS,
    build_provider_turn_result_event_payload,
    recover_provider_turn_result,
)
from unchain.kernel.run_ledger import build_model_attempt_receipt
from unchain.kernel.types import ModelTurnResult, ToolCall
from unchain.providers.request_lease import (
    ProviderRequestLease,
    ProviderRequestStatus,
    ProviderRequestSubject,
)
from unchain.run_bundle import RunIdentity


ATTEMPT = AttemptRef(
    GenerationRef("execution-provider-result", "generation-provider-result"),
    "attempt-provider-result",
)
SUBJECT = ProviderRequestSubject(
    attempt=ATTEMPT,
    iteration=4,
    envelope_sha256="a" * 64,
    route="primary",
    retry_ordinal=0,
)
ROUTE_SHA256 = "b" * 64


def _result() -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "done"}],
        tool_calls=[ToolCall("call-1", "lookup", {"query": "stable"})],
        final_text="done",
        response_id="response-1",
        reasoning_items=[{"type": "reasoning", "summary": "bounded"}],
        consumed_tokens=13,
        input_tokens=8,
        output_tokens=5,
        cache_read_input_tokens=2,
        cache_creation_input_tokens=1,
        provider_replay_frame={
            "format": "openai.responses.v1",
            "complete": True,
            "items": [],
        },
    )


def _envelope(*, visible_output: bool = True) -> ProviderTurnResultEnvelope:
    return ProviderTurnResultEnvelope.from_model_turn_result(
        subject=SUBJECT,
        route_sha256=ROUTE_SHA256,
        visible_output=visible_output,
        result=_result(),
    )


def _artifact(envelope: ProviderTurnResultEnvelope) -> ArtifactRef:
    content = envelope.canonical_bytes()
    return ArtifactRef(
        ref=ResourceRef("artifact", "provider-result-artifact", 1),
        media_type="application/json",
        byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        preview="",
    )


def _event(
    envelope: ProviderTurnResultEnvelope,
    artifact: ArtifactRef,
    *,
    store_seq: int = 7,
    operation: OperationRef | None = None,
) -> JournalEvent:
    return JournalEvent(
        event_id="provider-turn-result-event",
        event_type=PROVIDER_TURN_RESULT_EVENT_TYPE,
        attempt=SUBJECT.attempt,
        operation=operation or OperationRef("provider-turn-result-operation", "c" * 64),
        store_seq=store_seq,
        payload=build_provider_turn_result_event_payload(
            envelope=envelope,
            artifact=artifact,
        ),
        resource_refs=(artifact.ref,),
    )


def _started_lease() -> ProviderRequestLease:
    return ProviderRequestLease(
        subject=SUBJECT,
        route_sha256=ROUTE_SHA256,
        status=ProviderRequestStatus.STARTED,
        revision=1,
        visible_output=False,
        retryable=False,
        classification="",
        operation=OperationRef("provider-request-started", "d" * 64),
    )


def test_envelope_round_trips_detached_complete_model_turn_result() -> None:
    result = _result()
    envelope = ProviderTurnResultEnvelope.from_model_turn_result(
        subject=SUBJECT,
        route_sha256=ROUTE_SHA256,
        visible_output=True,
        result=result,
    )
    original_bytes = envelope.canonical_bytes()

    result.assistant_messages[0]["content"] = "mutated"
    assert envelope.canonical_bytes() == original_bytes
    assert ProviderTurnResultEnvelope.from_dict(envelope.to_dict()) == envelope
    assert envelope.subject == SUBJECT
    assert (
        envelope.subject_sha256
        == hashlib.sha256(
            json.dumps(
                SUBJECT.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    assert envelope.to_model_turn_result() == _result()


@pytest.mark.parametrize(
    "replacement",
    [
        {"consumed_tokens": -1},
        {"input_tokens": True},
        {"assistant_messages": ["not-an-object"]},
        {"tool_calls": [{"call_id": "", "name": "lookup", "arguments": {}}]},
        {"provider_replay_frame": {"invalid": object()}},
    ],
)
def test_envelope_rejects_noncanonical_provider_result(replacement) -> None:
    payload = ProviderTurnResultEnvelope.from_model_turn_result(
        subject=SUBJECT,
        route_sha256=ROUTE_SHA256,
        visible_output=True,
        result=_result(),
    ).to_dict()
    payload["result"].update(replacement)

    with pytest.raises((TypeError, ValueError)):
        ProviderTurnResultEnvelope.from_dict(payload)


def test_receipt_binds_subject_result_artifact_event_and_cursor() -> None:
    envelope = _envelope()
    artifact = _artifact(envelope)
    event = _event(envelope, artifact)

    receipt = ProviderTurnResultReceipt(
        envelope=envelope,
        artifact=artifact,
        event=event,
        cursor=EventCursor(event.store_seq, event.event_id),
        duplicate=False,
    )

    assert receipt.envelope.to_model_turn_result() == _result()
    assert receipt.subject == SUBJECT

    changed = event.to_dict()
    changed["payload"]["route_sha256"] = "d" * 64
    with pytest.raises(ProviderTurnResultIntegrityError, match="route|event"):
        ProviderTurnResultReceipt(
            envelope=envelope,
            artifact=artifact,
            event=JournalEvent.from_dict(changed),
            cursor=EventCursor(event.store_seq, event.event_id),
        )


def test_lookup_is_exact_bounded_and_rejects_foreign_subject() -> None:
    envelope = _envelope()
    artifact = _artifact(envelope)
    event = _event(envelope, artifact)

    lookup = ProviderTurnResultReceiptLookup(
        subject=SUBJECT,
        events=(event,),
        overflow=False,
    )
    assert ProviderTurnResultReceiptLookup.from_dict(lookup.to_dict()) == lookup

    foreign_subject = ProviderRequestSubject(
        attempt=SUBJECT.attempt,
        iteration=SUBJECT.iteration,
        envelope_sha256="e" * 64,
        route=SUBJECT.route,
        retry_ordinal=SUBJECT.retry_ordinal,
    )
    with pytest.raises(ValueError, match="subject|envelope"):
        ProviderTurnResultReceiptLookup(
            subject=foreign_subject,
            events=(event,),
        )


def test_atomic_store_entry_validates_started_lease_subject_route_and_scope() -> None:
    envelope = _envelope()
    artifact = _artifact(envelope)
    event_operation = OperationRef("provider-turn-result-operation", "c" * 64)
    event = _event(envelope, artifact, operation=event_operation)
    receipt = ProviderTurnResultReceipt(
        envelope=envelope,
        artifact=artifact,
        event=event,
        cursor=EventCursor(event.store_seq, event.event_id),
    )

    class Store(BoundProviderTurnResultStore):
        def __init__(self) -> None:
            super().__init__(SUBJECT.attempt.generation.execution_id)
            self.requests = []

        def _persist_provider_turn_result_cas(self, *, request):
            assert type(request) is ProviderTurnResultPersistRequest
            self.requests.append(request)
            return receipt

        def read_provider_turn_result_full_verified(self, *, artifact):
            raise AssertionError

        def lookup_provider_turn_result_receipts(self, *, subject):
            raise AssertionError

    store = Store()
    kwargs = {
        "started_lease": _started_lease(),
        "envelope": envelope,
        "artifact_operation": OperationRef("provider-result-artifact-op", "e" * 64),
        "event_operation": event_operation,
        "event_id": event.event_id,
    }

    assert store.persist_provider_turn_result_cas(**kwargs) == receipt
    assert len(store.requests) == 1

    foreign_subject = replace(SUBJECT, envelope_sha256="1" * 64)
    invalid_leases = (
        replace(
            _started_lease(),
            status=ProviderRequestStatus.FAILED,
            retryable=False,
            classification="bad_request",
        ),
        replace(_started_lease(), subject=foreign_subject),
        replace(_started_lease(), route_sha256="2" * 64),
    )
    for invalid in invalid_leases:
        with pytest.raises((TypeError, ValueError, ProviderTurnResultIntegrityError)):
            store.persist_provider_turn_result_cas(
                **{**kwargs, "started_lease": invalid}
            )
    assert len(store.requests) == 1

    foreign_store = Store()
    foreign_store._execution_id = "execution-foreign"
    with pytest.raises(ProviderTurnResultIntegrityError, match="scope|execution"):
        foreign_store.persist_provider_turn_result_cas(**kwargs)
    assert not foreign_store.requests


def test_fallback_result_receipt_uses_the_physical_send_ordinal() -> None:
    fallback_subject = replace(
        SUBJECT,
        route="openai_previous_response_fallback",
        retry_ordinal=0,
    )
    started_lease = replace(_started_lease(), subject=fallback_subject)
    envelope = ProviderTurnResultEnvelope.from_model_turn_result(
        subject=fallback_subject,
        route_sha256=ROUTE_SHA256,
        visible_output=True,
        result=_result(),
    )
    identity = RunIdentity(
        execution_id=ATTEMPT.generation.execution_id,
        attempt_id=ATTEMPT.attempt_id,
        root_run_id=ATTEMPT.attempt_id,
        run_id=ATTEMPT.attempt_id,
        parent_run_id=None,
        relation="root",
    )

    def accounting_receipt(physical_ordinal: int):
        return build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            iteration=fallback_subject.iteration,
            retry_ordinal=physical_ordinal,
            purpose="agent_turn",
            request_digest="f" * 64,
            route="openai.responses.create",
            started_at="2026-08-15T00:00:00Z",
            completed_at="2026-08-15T00:00:01Z",
            turn=_result(),
        )

    common = {
        "started_lease": started_lease,
        "envelope": envelope,
        "artifact_operation": OperationRef("fallback-result-artifact", "1" * 64),
        "event_operation": OperationRef("fallback-result-event", "2" * 64),
        "event_id": "fallback-provider-turn-result",
    }

    request = ProviderTurnResultPersistRequest(
        **common,
        provider_call_receipt=accounting_receipt(1),
    )
    assert request.provider_call_receipt.identity.retry_ordinal == 1

    with pytest.raises(
        ProviderTurnResultIntegrityError,
        match="durable send subject",
    ):
        ProviderTurnResultPersistRequest(
            **common,
            provider_call_receipt=accounting_receipt(0),
        )


def test_oversized_artifact_descriptor_is_rejected_before_any_read() -> None:
    envelope = _envelope()
    artifact = _artifact(envelope)
    event = _event(envelope, artifact)
    raw = event.to_dict()
    raw_artifact = raw["payload"]["result_artifact"]
    raw_artifact["byte_length"] = PROVIDER_TURN_RESULT_LIMITS.max_bytes + 1

    with pytest.raises(ProviderTurnResultIntegrityError, match="size|limit|artifact"):
        ProviderTurnResultReceiptLookup(
            SUBJECT,
            (JournalEvent.from_dict(raw),),
            False,
        )
    with pytest.raises(ValueError, match="at most two"):
        ProviderTurnResultReceiptLookup(
            subject=SUBJECT,
            events=(event, _event(envelope, artifact, store_seq=8), event),
        )


def test_recovery_uses_bounded_index_and_verified_full_bytes() -> None:
    envelope = _envelope(visible_output=False)
    artifact = _artifact(envelope)
    expected_artifact = artifact
    event = _event(envelope, artifact)

    class Store(BoundProviderTurnResultStore):
        def __init__(self) -> None:
            super().__init__(SUBJECT.attempt.generation.execution_id)
            self.reads = 0

        def _persist_provider_turn_result_cas(self, **_kwargs):
            raise AssertionError("recovery must not write")

        def read_provider_turn_result_full_verified(self, *, artifact):
            assert artifact == expected_artifact
            self.reads += 1
            return envelope.canonical_bytes()

        def lookup_provider_turn_result_receipts(self, *, subject):
            assert subject == SUBJECT
            return ProviderTurnResultReceiptLookup(subject, (event,), False)

        def append(self, *, request):
            raise AssertionError("recovery must not append")

        def read(self, *, after=None, limit=100):
            raise AssertionError("recovery must not scan journal pages")

    store = Store()
    receipt = recover_provider_turn_result(
        store,
        subject=SUBJECT,
        expected_route_sha256=ROUTE_SHA256,
    )

    assert store.reads == 1
    assert receipt.envelope == envelope
    assert receipt.artifact == artifact
    assert receipt.cursor == EventCursor(event.store_seq, event.event_id)


def test_recovery_rejects_missing_overflow_or_changed_bytes() -> None:
    envelope = _envelope()
    artifact = _artifact(envelope)
    event = _event(envelope, artifact)

    class Store(BoundProviderTurnResultStore):
        def __init__(self, lookup, content) -> None:
            super().__init__(SUBJECT.attempt.generation.execution_id)
            self.lookup = lookup
            self.content = content

        def _persist_provider_turn_result_cas(self, **_kwargs):
            raise AssertionError

        def read_provider_turn_result_full_verified(self, *, artifact):
            return self.content

        def lookup_provider_turn_result_receipts(self, *, subject):
            return self.lookup

        def append(self, *, request):
            raise AssertionError

        def read(self, *, after=None, limit=100):
            raise AssertionError

    for lookup in (
        ProviderTurnResultReceiptLookup(SUBJECT, (), False),
        ProviderTurnResultReceiptLookup(SUBJECT, (event,), True),
    ):
        with pytest.raises(ProviderTurnResultIntegrityError):
            recover_provider_turn_result(
                Store(lookup, envelope.canonical_bytes()),
                subject=SUBJECT,
                expected_route_sha256=ROUTE_SHA256,
            )

    with pytest.raises(ProviderTurnResultIntegrityError, match="artifact|bytes|digest"):
        recover_provider_turn_result(
            Store(
                ProviderTurnResultReceiptLookup(SUBJECT, (event,), False),
                envelope.canonical_bytes() + b"changed",
            ),
            subject=SUBJECT,
            expected_route_sha256=ROUTE_SHA256,
        )
