from __future__ import annotations

from dataclasses import replace

import pytest

from unchain.context.health import (
    ContextV2Admission,
    ContextV2HealthInputs,
    ContextV2HealthService,
    ContextV2PreflightBlocker,
    ContextV2PreflightError,
)
from unchain.journal import ContextBuildStatus
from unchain.providers.durable_turn_runtime import DurableProviderTurnMode


READY = ContextV2HealthInputs(
    capture_status=ContextBuildStatus.COMPLETE,
    journal_available=True,
    object_store_available=True,
    exact_provider_transport_available=True,
)


def _admitted_service() -> ContextV2HealthService:
    return ContextV2HealthService(
        admission=ContextV2Admission(
            mode=DurableProviderTurnMode.ENFORCE_TEST,
            admitted=True,
        )
    )


def test_default_gate_is_explicitly_off_and_routes_only_non_admitted_legacy() -> None:
    service = ContextV2HealthService()

    report = service.preflight(ContextV2HealthInputs())

    assert report.mode is DurableProviderTurnMode.OFF
    assert report.admitted is False
    assert report.capture_status is ContextBuildStatus.LEGACY
    assert report.ready_for_model_tool_work is False
    assert report.ready_for_shadow_write is False
    assert report.fallback_forbidden is False
    assert report.blockers == ()


@pytest.mark.parametrize(
    ("mode", "admitted"),
    [
        (DurableProviderTurnMode.OFF, True),
        (DurableProviderTurnMode.SHADOW, True),
        (DurableProviderTurnMode.ENFORCE_TEST, False),
    ],
)
def test_admission_cannot_disagree_with_the_closed_mode_contract(
    mode: DurableProviderTurnMode,
    admitted: bool,
) -> None:
    with pytest.raises(ValueError, match="admission|mode|enforce_test"):
        ContextV2Admission(mode=mode, admitted=admitted)


def test_shadow_requires_durable_write_prerequisites_but_not_exact_transport() -> None:
    service = ContextV2HealthService(
        admission=ContextV2Admission(mode=DurableProviderTurnMode.SHADOW)
    )
    inputs = replace(READY, exact_provider_transport_available=False)

    report = service.preflight(inputs)

    assert report.admitted is False
    assert report.ready_for_shadow_write is True
    assert report.ready_for_model_tool_work is False
    assert report.fallback_forbidden is False
    assert report.blockers == ()


@pytest.mark.parametrize(
    ("field_name", "blocker"),
    [
        ("journal_available", ContextV2PreflightBlocker.JOURNAL_UNAVAILABLE),
        (
            "object_store_available",
            ContextV2PreflightBlocker.OBJECT_STORE_UNAVAILABLE,
        ),
    ],
)
def test_shadow_fails_its_dry_run_before_writing_when_storage_is_unavailable(
    field_name: str,
    blocker: ContextV2PreflightBlocker,
) -> None:
    service = ContextV2HealthService(
        admission=ContextV2Admission(mode=DurableProviderTurnMode.SHADOW)
    )

    with pytest.raises(ContextV2PreflightError) as raised:
        service.preflight(replace(READY, **{field_name: False}))

    assert raised.value.report.admitted is False
    assert raised.value.report.fallback_forbidden is False
    assert raised.value.report.blockers == (blocker,)


def test_admitted_complete_run_is_ready_only_with_every_required_prerequisite() -> None:
    report = _admitted_service().preflight(READY)

    assert report.admitted is True
    assert report.mode is DurableProviderTurnMode.ENFORCE_TEST
    assert report.ready_for_model_tool_work is True
    assert report.ready_for_shadow_write is False
    assert report.fallback_forbidden is True
    assert report.blockers == ()


@pytest.mark.parametrize(
    ("inputs", "blocker"),
    [
        (
            replace(READY, journal_available=False),
            ContextV2PreflightBlocker.JOURNAL_UNAVAILABLE,
        ),
        (
            replace(READY, object_store_available=False),
            ContextV2PreflightBlocker.OBJECT_STORE_UNAVAILABLE,
        ),
        (
            replace(READY, exact_provider_transport_available=False),
            ContextV2PreflightBlocker.EXACT_PROVIDER_TRANSPORT_UNAVAILABLE,
        ),
        (
            replace(READY, capture_status=ContextBuildStatus.PARTIAL),
            ContextV2PreflightBlocker.PARTIAL_ATTEMPT,
        ),
        (
            replace(READY, capture_status=ContextBuildStatus.UNAVAILABLE),
            ContextV2PreflightBlocker.CONTEXT_UNAVAILABLE,
        ),
        (
            replace(READY, read_only_degraded=True),
            ContextV2PreflightBlocker.READ_ONLY_DEGRADED,
        ),
    ],
)
def test_admitted_v2_fails_closed_without_legacy_fallback(
    inputs: ContextV2HealthInputs,
    blocker: ContextV2PreflightBlocker,
) -> None:
    service = _admitted_service()

    report = service.inspect(inputs)
    assert report.ready_for_model_tool_work is False
    assert report.fallback_forbidden is True
    assert blocker in report.blockers

    with pytest.raises(ContextV2PreflightError) as raised:
        service.preflight(inputs)

    assert raised.value.report == report
    assert raised.value.fallback_forbidden is True
    assert blocker.value in str(raised.value)


def test_legacy_capture_quality_is_provenance_not_a_v1_fallback_for_admitted_v2() -> (
    None
):
    report = _admitted_service().preflight(
        replace(READY, capture_status=ContextBuildStatus.LEGACY)
    )

    assert report.capture_status is ContextBuildStatus.LEGACY
    assert report.ready_for_model_tool_work is True
    assert report.fallback_forbidden is True


def test_health_inputs_require_exact_boolean_prerequisite_states() -> None:
    with pytest.raises(TypeError, match="journal_available"):
        ContextV2HealthInputs(journal_available=1)
