from __future__ import annotations

import copy

import pytest

from unchain.interaction.durable import (
    INTERACTION_JOURNAL_KEY,
    INTERACTION_KIND_HUMAN_INPUT,
    INTERACTION_KIND_MAX_BUDGET,
    InteractionAlreadyAppliedError,
    InteractionIntegrityError,
    InteractionNotPendingError,
    InteractionReceipt,
    InteractionReceiptConflictError,
    InteractionRequest,
    build_interaction_receipt,
    build_interaction_request,
    get_active_interaction,
    mark_interaction_applied,
    new_interaction_journal,
    record_interaction_receipt,
    register_interaction_request,
    validate_interaction_journal,
)


def _request(*, occurrence: str = "call-1") -> InteractionRequest:
    return build_interaction_request(
        session_id="session-1",
        kind=INTERACTION_KIND_HUMAN_INPUT,
        source_run_id="run-1",
        occurrence=occurrence,
        payload={"question": "Choose", "options": ["a", "b"]},
        response_contract={
            "type": "object",
            "properties": {"selected": {"type": "string"}},
            "required": ["selected"],
        },
        created_revision=7,
        subject={"call_id": occurrence},
    )


def test_constants_and_request_digest_are_deterministic() -> None:
    assert INTERACTION_JOURNAL_KEY == "interaction_journal"
    first = _request()
    second = build_interaction_request(
        session_id="session-1",
        kind=INTERACTION_KIND_HUMAN_INPUT,
        source_run_id="run-1",
        occurrence="call-1",
        payload={"options": ["a", "b"], "question": "Choose"},
        response_contract={
            "required": ["selected"],
            "properties": {"selected": {"type": "string"}},
            "type": "object",
        },
        created_revision=7,
        subject={"call_id": "call-1"},
    )

    assert second == first
    assert first.interaction_id == f"interaction_{first.request_digest[:32]}"
    assert len(first.schema_digest) == 64
    assert _request(occurrence="call-2").request_digest != first.request_digest


def test_request_round_trip_isolated_from_input_and_rejects_tampering() -> None:
    payload = {"question": "Choose", "nested": {"value": 1}}
    request = build_interaction_request(
        session_id="session-1",
        kind=INTERACTION_KIND_MAX_BUDGET,
        source_run_id="run-1",
        occurrence="iteration-6",
        payload=payload,
        response_contract={"type": "boolean"},
        created_revision=2,
    )
    payload["nested"]["value"] = 9
    assert request.payload["nested"]["value"] == 1
    assert InteractionRequest.from_dict(request.to_dict()) == request

    tampered = request.to_dict()
    tampered["payload"]["nested"]["value"] = 2
    with pytest.raises(InteractionIntegrityError, match="digest"):
        InteractionRequest.from_dict(tampered)

    tampered = request.to_dict()
    tampered["response_contract"] = {"type": "string"}
    with pytest.raises(InteractionIntegrityError, match="schema digest"):
        InteractionRequest.from_dict(tampered)


@pytest.mark.parametrize(
    "invalid",
    [
        {"bad": float("nan")},
        {"bad": float("inf")},
        {1: "non-string-key"},
        {"bad": ("tuple",)},
        {"bad": object()},
    ],
)
def test_request_rejects_non_strict_json(invalid: object) -> None:
    with pytest.raises(InteractionIntegrityError):
        build_interaction_request(
            session_id="session",
            kind=INTERACTION_KIND_HUMAN_INPUT,
            source_run_id="run",
            occurrence="one",
            payload=invalid,
            response_contract={"type": "object"},
            created_revision=0,
        )


def test_receipt_identity_ignores_submission_time_and_detects_tampering() -> None:
    request = _request()
    first = build_interaction_receipt(
        request,
        {"selected": "a"},
        submitted_at_ms=100,
    )
    retry = build_interaction_receipt(
        request,
        {"selected": "a"},
        submitted_at_ms=900,
    )

    assert first.receipt_id == retry.receipt_id
    assert first.receipt_digest == retry.receipt_digest
    assert first.submitted_at_ms != retry.submitted_at_ms
    assert InteractionReceipt.from_dict(first.to_dict(), request=request) == first

    tampered = first.to_dict()
    tampered["response"]["selected"] = "b"
    with pytest.raises(InteractionIntegrityError, match="response digest"):
        InteractionReceipt.from_dict(tampered, request=request)


def test_journal_lifecycle_and_retries_are_idempotent_without_mutating_input() -> None:
    request = _request()
    empty = new_interaction_journal()
    registered = register_interaction_request(
        empty,
        request,
        checkpoint_id="checkpoint-pending",
    )
    assert empty == new_interaction_journal()
    assert register_interaction_request(
        registered,
        request,
        checkpoint_id="checkpoint-pending",
    ) == registered
    assert get_active_interaction(registered)["request"] == request.to_dict()

    receipt = build_interaction_receipt(
        request,
        {"selected": "a"},
        submitted_at_ms=100,
    )
    answered = record_interaction_receipt(registered, receipt)
    assert registered["entries"][request.interaction_id]["receipt"] is None

    later_retry = build_interaction_receipt(
        request,
        {"selected": "a"},
        submitted_at_ms=999,
    )
    assert record_interaction_receipt(answered, later_retry) == answered
    assert (
        answered["entries"][request.interaction_id]["receipt"]["submitted_at_ms"]
        == 100
    )

    applied = mark_interaction_applied(
        answered,
        interaction_id=request.interaction_id,
        receipt_id=receipt.receipt_id,
        applied_checkpoint_id="checkpoint-resume-ready",
    )
    assert applied["active_id"] is None
    assert get_active_interaction(applied) is None
    assert mark_interaction_applied(
        applied,
        interaction_id=request.interaction_id,
        receipt_id=receipt.receipt_id,
        applied_checkpoint_id="checkpoint-resume-ready",
    ) == applied
    tombstone = applied["entries"][request.interaction_id]
    assert tombstone["receipt"]["receipt_id"] == receipt.receipt_id
    assert tombstone["application"] == {
        "schema_version": 1,
        "receipt_id": receipt.receipt_id,
        "applied_checkpoint_id": "checkpoint-resume-ready",
    }


def test_different_receipt_for_same_request_conflicts() -> None:
    request = _request()
    journal = register_interaction_request(
        new_interaction_journal(),
        request,
        checkpoint_id="checkpoint-pending",
    )
    first = build_interaction_receipt(
        request,
        {"selected": "a"},
        submitted_at_ms=1,
    )
    second = build_interaction_receipt(
        request,
        {"selected": "b"},
        submitted_at_ms=2,
    )
    answered = record_interaction_receipt(journal, first)

    with pytest.raises(InteractionReceiptConflictError):
        record_interaction_receipt(answered, second)


def test_stale_ids_and_parallel_pending_request_are_rejected() -> None:
    request = _request()
    active = register_interaction_request(
        new_interaction_journal(),
        request,
        checkpoint_id="checkpoint-pending",
    )
    stale_request = _request(occurrence="call-2")
    stale_receipt = build_interaction_receipt(
        stale_request,
        {"selected": "a"},
        submitted_at_ms=1,
    )

    with pytest.raises(InteractionNotPendingError):
        register_interaction_request(
            active,
            stale_request,
            checkpoint_id="checkpoint-other",
        )
    with pytest.raises(InteractionNotPendingError):
        record_interaction_receipt(active, stale_receipt)


def test_mark_applied_requires_recorded_receipt_and_rejects_replacement() -> None:
    request = _request()
    active = register_interaction_request(
        new_interaction_journal(),
        request,
        checkpoint_id="checkpoint-pending",
    )
    with pytest.raises(InteractionNotPendingError, match="before a receipt"):
        mark_interaction_applied(
            active,
            interaction_id=request.interaction_id,
            receipt_id="receipt_missing",
            applied_checkpoint_id="checkpoint-applied",
        )

    receipt = build_interaction_receipt(
        request,
        {"selected": "a"},
        submitted_at_ms=1,
    )
    answered = record_interaction_receipt(active, receipt)
    applied = mark_interaction_applied(
        answered,
        interaction_id=request.interaction_id,
        receipt_id=receipt.receipt_id,
        applied_checkpoint_id="checkpoint-applied",
    )
    with pytest.raises(InteractionAlreadyAppliedError):
        mark_interaction_applied(
            applied,
            interaction_id=request.interaction_id,
            receipt_id=receipt.receipt_id,
            applied_checkpoint_id="checkpoint-different",
        )


def test_journal_validation_rejects_tampered_links_and_unknown_fields() -> None:
    request = _request()
    journal = register_interaction_request(
        new_interaction_journal(),
        request,
        checkpoint_id="checkpoint-pending",
    )

    missing_order = copy.deepcopy(journal)
    missing_order["order"] = []
    with pytest.raises(InteractionIntegrityError, match="order"):
        validate_interaction_journal(missing_order)

    wrong_active = copy.deepcopy(journal)
    wrong_active["active_id"] = "interaction_missing"
    with pytest.raises(InteractionIntegrityError, match="active_id"):
        validate_interaction_journal(wrong_active)

    unknown_field = copy.deepcopy(journal)
    unknown_field["extra"] = True
    with pytest.raises(InteractionIntegrityError, match="unknown"):
        validate_interaction_journal(unknown_field)
