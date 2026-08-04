from __future__ import annotations

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
from unchain.providers.request_lease import (
    ProviderRequestLease,
    ProviderRequestLeaseCoordinator,
    ProviderRequestLeaseIntegrityError,
    ProviderRequestLeasePort,
    ProviderRequestStatus,
    ProviderRequestSubject,
    ProviderRequestTransitionError,
    ProviderTurnResultBinding,
)


ATTEMPT = AttemptRef(
    GenerationRef("execution-result-binding", "generation-result-binding"),
    "attempt-result-binding",
)
SUBJECT = ProviderRequestSubject(
    attempt=ATTEMPT,
    iteration=3,
    envelope_sha256="a" * 64,
    route="primary",
    retry_ordinal=0,
)
ROUTE_SHA256 = "b" * 64


def _operation(name: str, digest: str) -> OperationRef:
    return OperationRef(name, digest * 64)


def _binding(*, route_sha256: str = ROUTE_SHA256) -> ProviderTurnResultBinding:
    return ProviderTurnResultBinding(
        route_sha256=route_sha256,
        result_sha256="c" * 64,
        artifact=ArtifactRef(
            ResourceRef("artifact", "provider-result-binding", 1),
            "application/json",
            128,
            "d" * 64,
            "",
        ),
        cursor=EventCursor(9, "provider-result-event"),
    )


class _Port(ProviderRequestLeasePort):
    def __init__(self) -> None:
        self.records = {}
        self.cas_calls = 0

    def load(self, *, subject):
        return self.records.get(subject.key)

    def compare_and_swap(self, *, subject, expected_revision, replacement):
        current = self.records.get(subject.key)
        revision = 0 if current is None else current.revision
        if revision != expected_revision:
            raise AssertionError("unexpected CAS revision")
        self.cas_calls += 1
        self.records[subject.key] = replacement
        return replacement


def _started(coordinator: ProviderRequestLeaseCoordinator) -> ProviderRequestLease:
    return coordinator.claim_initial(
        attempt=ATTEMPT,
        iteration=SUBJECT.iteration,
        envelope_sha256=SUBJECT.envelope_sha256,
        route=SUBJECT.route,
        route_sha256=ROUTE_SHA256,
        operation=_operation("provider-request-start", "1"),
    )


def test_result_binding_is_canonical_and_only_completed_lease_may_hold_it() -> None:
    binding = _binding()
    assert ProviderTurnResultBinding.from_dict(binding.to_dict()) == binding

    with pytest.raises(ValueError, match="result|binding"):
        ProviderRequestLease(
            subject=SUBJECT,
            route_sha256=ROUTE_SHA256,
            status=ProviderRequestStatus.COMPLETED,
            revision=2,
            visible_output=True,
            retryable=False,
            classification="",
            operation=_operation("provider-request-completed", "2"),
        )
    with pytest.raises(ValueError, match="result|binding"):
        ProviderRequestLease(
            subject=SUBJECT,
            route_sha256=ROUTE_SHA256,
            status=ProviderRequestStatus.STARTED,
            revision=1,
            visible_output=False,
            retryable=False,
            classification="",
            operation=_operation("provider-request-started", "3"),
            result_binding=binding,
        )


def test_completion_binds_result_and_ack_loss_replay_is_idempotent() -> None:
    port = _Port()
    coordinator = ProviderRequestLeaseCoordinator(port)
    started = _started(coordinator)
    binding = _binding()
    operation = _operation("provider-request-completed", "4")

    completed = coordinator.record_completed_result(
        started,
        result_binding=binding,
        visible_output=True,
        operation=operation,
    )
    replayed = coordinator.record_completed_result(
        started,
        result_binding=binding,
        visible_output=True,
        operation=operation,
    )

    assert completed.status is ProviderRequestStatus.COMPLETED
    assert completed.result_binding == binding
    assert completed.revision == started.revision + 1
    assert replayed == completed
    assert port.cas_calls == 2
    assert ProviderRequestLease.from_dict(completed.to_dict()) == completed


def test_completion_rejects_missing_or_mismatched_result_binding() -> None:
    port = _Port()
    coordinator = ProviderRequestLeaseCoordinator(port)
    started = _started(coordinator)

    with pytest.raises(ProviderRequestTransitionError, match="result|binding"):
        coordinator.record_completed(
            started,
            visible_output=False,
            operation=_operation("legacy-completion", "5"),
        )
    with pytest.raises(
        (ProviderRequestLeaseIntegrityError, ProviderRequestTransitionError),
        match="route|result|binding",
    ):
        coordinator.record_completed_result(
            started,
            result_binding=_binding(route_sha256="e" * 64),
            visible_output=False,
            operation=_operation("mismatched-completion", "6"),
        )


def test_ack_loss_replay_rejects_changed_predecessor_binding_or_operation() -> None:
    port = _Port()
    coordinator = ProviderRequestLeaseCoordinator(port)
    started = _started(coordinator)
    operation = _operation("provider-request-completed", "7")
    completed = coordinator.record_completed_result(
        started,
        result_binding=_binding(),
        visible_output=False,
        operation=operation,
    )
    assert completed.status is ProviderRequestStatus.COMPLETED

    for changed_started, changed_binding, changed_operation in (
        (
            replace(
                started,
                operation=_operation("provider-request-start-changed", "9"),
            ),
            _binding(),
            operation,
        ),
        (
            started,
            replace(_binding(), result_sha256="f" * 64),
            operation,
        ),
        (
            started,
            _binding(),
            _operation("provider-request-completed-changed", "8"),
        ),
    ):
        with pytest.raises(
            ProviderRequestLeaseIntegrityError, match="completed|changed"
        ):
            coordinator.record_completed_result(
                changed_started,
                result_binding=changed_binding,
                visible_output=False,
                operation=changed_operation,
            )
