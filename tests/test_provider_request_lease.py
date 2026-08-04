from __future__ import annotations

import importlib
from dataclasses import replace

import pytest

from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    GenerationRef,
    OperationRef,
    ResourceRef,
)


ATTEMPT = AttemptRef(
    GenerationRef("execution-request-lease", "generation-request-lease"),
    "attempt-request-lease",
)
ITERATION = 7
ENVELOPE_SHA256 = "a" * 64
PRIMARY_ROUTE_SHA256 = "b" * 64
FALLBACK_ROUTE_SHA256 = "c" * 64


def _api():
    try:
        return importlib.import_module("unchain.providers.request_lease")
    except ModuleNotFoundError:
        pytest.fail("provider request lease API is not implemented")


def _operation(index: int) -> OperationRef:
    return OperationRef(f"provider-request-operation-{index}", f"{index:x}" * 64)


def _result_binding(api, *, route_sha256: str = PRIMARY_ROUTE_SHA256):
    return api.ProviderTurnResultBinding(
        route_sha256=route_sha256,
        result_sha256="d" * 64,
        artifact=ArtifactRef(
            ResourceRef("artifact", "provider-request-result", 1),
            "application/json",
            128,
            "e" * 64,
            "",
        ),
        cursor=EventCursor(9, "provider-request-result-event"),
    )


def _port(*, cas_conflict: bool = False, returned_mutator=None):
    api = _api()

    class Port(api.ProviderRequestLeasePort):
        def __init__(self) -> None:
            self.records = {}
            self.calls = []

        def load(self, *, subject):
            self.calls.append(("load", subject))
            return self.records.get(subject.key)

        def compare_and_swap(
            self,
            *,
            subject,
            expected_revision,
            replacement,
        ):
            self.calls.append(("cas", subject, expected_revision, replacement))
            if cas_conflict:
                raise api.ProviderRequestLeaseConflict(
                    "concurrent request claim conflict"
                )
            current = self.records.get(subject.key)
            current_revision = 0 if current is None else current.revision
            if current_revision != expected_revision:
                raise api.ProviderRequestLeaseConflict("request lease CAS conflict")
            self.records[subject.key] = replacement
            if returned_mutator is not None:
                return returned_mutator(replacement)
            return replacement

    return Port()


def _coordinator(port=None):
    api = _api()
    resolved = port or _port()
    return api.ProviderRequestLeaseCoordinator(resolved), resolved


def _claim_primary(coordinator, *, operation_index: int = 1):
    return coordinator.claim_initial(
        attempt=ATTEMPT,
        iteration=ITERATION,
        envelope_sha256=ENVELOPE_SHA256,
        route="primary",
        route_sha256=PRIMARY_ROUTE_SHA256,
        operation=_operation(operation_index),
    )


def _retryable_failure(
    coordinator,
    lease,
    *,
    classification="transient",
    operation_index: int = 2,
):
    return coordinator.record_failure(
        lease,
        classification=classification,
        retryable=True,
        visible_output=False,
        operation=_operation(operation_index),
    )


def test_subject_key_is_exact_and_round_trips() -> None:
    api = _api()
    subject = api.ProviderRequestSubject(
        attempt=ATTEMPT,
        iteration=ITERATION,
        envelope_sha256=ENVELOPE_SHA256,
        route="primary",
        retry_ordinal=0,
    )

    assert subject.key == (
        ATTEMPT,
        ITERATION,
        ENVELOPE_SHA256,
        "primary",
        0,
    )
    assert api.ProviderRequestSubject.from_dict(subject.to_dict()) == subject


def test_atomic_first_claim_succeeds_and_same_subject_never_claims_twice() -> None:
    api = _api()
    coordinator, port = _coordinator()

    claimed = _claim_primary(coordinator)

    assert claimed.status is api.ProviderRequestStatus.STARTED
    assert claimed.revision == 1
    assert claimed.visible_output is False
    assert claimed.retryable is False
    assert [call[0] for call in port.calls] == ["load", "cas"]
    assert port.calls[1][2] == 0
    with pytest.raises(api.ProviderRequestLeaseConflict, match="already|claimed"):
        _claim_primary(coordinator, operation_index=2)


def test_concurrent_or_conflicting_initial_claim_fails_closed() -> None:
    api = _api()
    coordinator, port = _coordinator(_port(cas_conflict=True))

    with pytest.raises(api.ProviderRequestLeaseConflict, match="conflict"):
        _claim_primary(coordinator)
    assert [call[0] for call in port.calls] == ["load", "cas"]


def test_durable_started_without_terminal_is_uncertain_after_restart_and_not_resent() -> (
    None
):
    api = _api()
    coordinator, port = _coordinator()
    started = _claim_primary(coordinator)

    restarted = api.ProviderRequestLeaseCoordinator(port)
    recovery = restarted.recover(started.subject)

    assert recovery.disposition is api.ProviderRequestDisposition.UNCERTAIN
    assert recovery.lease == started
    assert recovery.auto_resend_allowed is False
    with pytest.raises(api.ProviderRequestLeaseConflict, match="already|claimed"):
        _claim_primary(restarted, operation_index=2)


@pytest.mark.parametrize("terminal_kind", ("completed", "visible_failure"))
def test_completed_or_visible_output_request_cannot_retry(terminal_kind: str) -> None:
    api = _api()
    coordinator, _port_value = _coordinator()
    started = _claim_primary(coordinator)
    if terminal_kind == "completed":
        terminal = coordinator.record_completed_result(
            started,
            result_binding=_result_binding(api),
            visible_output=True,
            operation=_operation(2),
        )
    else:
        terminal = coordinator.record_failure(
            started,
            classification="transient",
            retryable=True,
            visible_output=True,
            operation=_operation(2),
        )

    with pytest.raises(
        api.ProviderRequestTransitionError, match="retry|visible|completed"
    ):
        coordinator.claim_retry(terminal, operation=_operation(3))


def test_transient_retry_is_exact_previous_plus_one_and_reuses_wire_identity() -> None:
    api = _api()
    coordinator, _port_value = _coordinator()
    first = _claim_primary(coordinator)
    failed = _retryable_failure(coordinator, first)

    retried = coordinator.claim_retry(failed, operation=_operation(3))

    assert retried.status is api.ProviderRequestStatus.STARTED
    assert retried.subject.retry_ordinal == failed.subject.retry_ordinal + 1
    assert retried.subject.attempt == failed.subject.attempt
    assert retried.subject.iteration == failed.subject.iteration
    assert retried.subject.envelope_sha256 == failed.subject.envelope_sha256
    assert retried.subject.route == failed.subject.route
    assert retried.route_sha256 == failed.route_sha256


@pytest.mark.parametrize(
    ("terminal_factory", "message"),
    [
        (lambda coordinator, started: started, "terminal|failure"),
        (
            lambda coordinator, started: coordinator.record_failure(
                started,
                classification="bad_request",
                retryable=False,
                visible_output=False,
                operation=_operation(2),
            ),
            "retryable",
        ),
        (
            lambda coordinator, started: coordinator.record_failure(
                started,
                classification="previous_response_fallback",
                retryable=True,
                visible_output=False,
                operation=_operation(2),
            ),
            "transient|route transition",
        ),
    ],
)
def test_retry_requires_durable_invisible_transient_failure(
    terminal_factory, message: str
) -> None:
    api = _api()
    coordinator, _port_value = _coordinator()
    started = _claim_primary(coordinator)
    previous = terminal_factory(coordinator, started)

    with pytest.raises(api.ProviderRequestTransitionError, match=message):
        coordinator.claim_retry(previous, operation=_operation(3))


def test_each_transient_retry_requires_the_immediately_previous_ordinal() -> None:
    api = _api()
    coordinator, port = _coordinator()
    first = _claim_primary(coordinator)
    failed_zero = _retryable_failure(coordinator, first)
    started_one = coordinator.claim_retry(failed_zero, operation=_operation(3))
    failed_one = _retryable_failure(
        coordinator,
        started_one,
        operation_index=4,
    )
    tampered = replace(
        failed_one,
        subject=replace(failed_one.subject, retry_ordinal=3),
    )

    with pytest.raises(api.ProviderRequestLeaseIntegrityError, match="subject|current"):
        coordinator.claim_retry(tampered, operation=_operation(5))
    assert all(record.subject.retry_ordinal != 4 for record in port.records.values())


@pytest.mark.parametrize(
    "classification",
    ("transient", "bad_request"),
)
def test_openai_fallback_requires_durable_primary_fallback_classification(
    classification: str,
) -> None:
    api = _api()
    coordinator, _port_value = _coordinator()
    first = _claim_primary(coordinator)
    failed = coordinator.record_failure(
        first,
        classification=classification,
        retryable=True,
        visible_output=False,
        operation=_operation(2),
    )

    with pytest.raises(
        api.ProviderRequestTransitionError, match="fallback|classification"
    ):
        coordinator.claim_openai_fallback(
            failed,
            fallback_route_sha256=FALLBACK_ROUTE_SHA256,
            operation=_operation(3),
        )


def test_openai_fallback_is_a_distinct_route_at_ordinal_zero_not_a_retry() -> None:
    api = _api()
    coordinator, _port_value = _coordinator()
    primary = _claim_primary(coordinator)
    classified = _retryable_failure(
        coordinator,
        primary,
        classification="previous_response_fallback",
    )

    fallback = coordinator.claim_openai_fallback(
        classified,
        fallback_route_sha256=FALLBACK_ROUTE_SHA256,
        operation=_operation(3),
    )

    assert fallback.subject.route == "openai_previous_response_fallback"
    assert fallback.subject.retry_ordinal == 0
    assert fallback.subject.envelope_sha256 == primary.subject.envelope_sha256
    assert fallback.route_sha256 == FALLBACK_ROUTE_SHA256
    with pytest.raises(
        api.ProviderRequestTransitionError, match="transient|route transition"
    ):
        coordinator.claim_retry(classified, operation=_operation(4))


@pytest.mark.parametrize(
    "mutator",
    (
        lambda lease: replace(lease, revision=lease.revision + 1),
        lambda lease: replace(
            lease,
            subject=replace(lease.subject, envelope_sha256="f" * 64),
        ),
        lambda lease: replace(
            lease,
            status="completed",
            result_binding=_result_binding(_api()),
            predecessor_sha256=lease.lease_sha256,
        ),
    ),
)
def test_cas_return_status_revision_or_subject_tamper_is_rejected(mutator) -> None:
    api = _api()
    coordinator, _port_value = _coordinator(_port(returned_mutator=mutator))

    with pytest.raises(
        api.ProviderRequestLeaseIntegrityError, match="CAS|returned|changed"
    ):
        _claim_primary(coordinator)


def test_loaded_status_revision_or_subject_tamper_is_rejected() -> None:
    api = _api()
    coordinator, port = _coordinator()
    started = _claim_primary(coordinator)
    original = port.records[started.subject.key]
    variants = (
        replace(original, revision=2),
        replace(
            original,
            status=api.ProviderRequestStatus.COMPLETED,
            result_binding=_result_binding(api),
            predecessor_sha256=original.lease_sha256,
        ),
        replace(
            original,
            subject=replace(original.subject, retry_ordinal=1),
        ),
    )
    for forged in variants:
        port.records[started.subject.key] = forged
        with pytest.raises(
            api.ProviderRequestLeaseIntegrityError, match="current|subject|revision"
        ):
            coordinator.record_completed_result(
                started,
                result_binding=_result_binding(api),
                visible_output=False,
                operation=_operation(2),
            )
    port.records[started.subject.key] = original


def test_lease_round_trip_rejects_invalid_status_revision_and_subject_shape() -> None:
    api = _api()
    coordinator, _port_value = _coordinator()
    lease = _claim_primary(coordinator)
    assert api.ProviderRequestLease.from_dict(lease.to_dict()) == lease

    raw = lease.to_dict()
    raw["revision"] = 0
    with pytest.raises(ValueError, match="revision"):
        api.ProviderRequestLease.from_dict(raw)
    raw = lease.to_dict()
    raw["subject"]["retry_ordinal"] = -1
    with pytest.raises(ValueError, match="retry_ordinal"):
        api.ProviderRequestLease.from_dict(raw)
    raw = lease.to_dict()
    raw["status"] = "foreign"
    with pytest.raises(ValueError, match="status"):
        api.ProviderRequestLease.from_dict(raw)
