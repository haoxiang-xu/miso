"""Read-only Context V2 health reporting and fail-closed admission checks.

The service deliberately does not activate Context V2 or select a legacy
runtime.  A host supplies the already-probed durable prerequisite states and
receives one immutable report before any V2 model or tool work begins.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from unchain.journal import ContextBuildStatus
from unchain.providers.durable_turn_runtime import DurableProviderTurnMode


class ContextV2PreflightBlocker(StrEnum):
    JOURNAL_UNAVAILABLE = "journal_unavailable"
    OBJECT_STORE_UNAVAILABLE = "object_store_unavailable"
    EXACT_PROVIDER_TRANSPORT_UNAVAILABLE = "exact_provider_transport_unavailable"
    PARTIAL_ATTEMPT = "partial_attempt"
    CONTEXT_UNAVAILABLE = "context_unavailable"
    READ_ONLY_DEGRADED = "read_only_degraded"


@dataclass(frozen=True, slots=True)
class ContextV2Admission:
    """Explicit gate state for shadow, test, or production enforcement."""

    mode: DurableProviderTurnMode = DurableProviderTurnMode.OFF
    admitted: bool = False

    def __post_init__(self) -> None:
        try:
            mode = DurableProviderTurnMode(self.mode)
        except (TypeError, ValueError) as exc:
            raise ValueError("Context V2 admission mode is unsupported") from exc
        object.__setattr__(self, "mode", mode)
        if type(self.admitted) is not bool:
            raise TypeError("admitted must be an exact boolean")
        enforced = mode in {
            DurableProviderTurnMode.ENFORCE,
            DurableProviderTurnMode.ENFORCE_TEST,
        }
        if self.admitted is not enforced:
            raise ValueError(
                "Context V2 admission must match an explicit enforce mode"
            )


@dataclass(frozen=True, slots=True)
class ContextV2HealthInputs:
    """Sanitized results of host-owned durable prerequisite probes."""

    capture_status: ContextBuildStatus = ContextBuildStatus.LEGACY
    journal_available: bool = False
    object_store_available: bool = False
    exact_provider_transport_available: bool = False
    read_only_degraded: bool = False

    def __post_init__(self) -> None:
        try:
            status = ContextBuildStatus(self.capture_status)
        except (TypeError, ValueError) as exc:
            raise ValueError("capture_status is unsupported") from exc
        object.__setattr__(self, "capture_status", status)
        for field_name in (
            "journal_available",
            "object_store_available",
            "exact_provider_transport_available",
            "read_only_degraded",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be an exact boolean")


@dataclass(frozen=True, slots=True)
class ContextV2HealthReport:
    mode: DurableProviderTurnMode
    admitted: bool
    capture_status: ContextBuildStatus
    read_only_degraded: bool
    ready_for_shadow_write: bool
    ready_for_model_tool_work: bool
    fallback_forbidden: bool
    blockers: tuple[ContextV2PreflightBlocker, ...]


class ContextV2PreflightError(RuntimeError):
    """Required Context V2 work cannot begin; the report is safe to expose."""

    def __init__(self, report: ContextV2HealthReport) -> None:
        if type(report) is not ContextV2HealthReport:
            raise TypeError("report must be an exact ContextV2HealthReport")
        self.report = report
        blockers = ",".join(blocker.value for blocker in report.blockers)
        super().__init__(f"context_v2_preflight_failed:{blockers}")

    @property
    def fallback_forbidden(self) -> bool:
        return self.report.fallback_forbidden


class ContextV2HealthService:
    """Compute health without changing admission or durable state."""

    def __init__(self, *, admission: ContextV2Admission | None = None) -> None:
        resolved = admission if admission is not None else ContextV2Admission()
        if type(resolved) is not ContextV2Admission:
            raise TypeError("admission must be an exact ContextV2Admission or null")
        self._admission = resolved

    @property
    def admission(self) -> ContextV2Admission:
        return self._admission

    def inspect(self, inputs: ContextV2HealthInputs) -> ContextV2HealthReport:
        if type(inputs) is not ContextV2HealthInputs:
            raise TypeError("inputs must be exact ContextV2HealthInputs")

        mode = self._admission.mode
        owns_v2_work = mode is not DurableProviderTurnMode.OFF
        blockers: list[ContextV2PreflightBlocker] = []
        if owns_v2_work:
            if not inputs.journal_available:
                blockers.append(ContextV2PreflightBlocker.JOURNAL_UNAVAILABLE)
            if not inputs.object_store_available:
                blockers.append(ContextV2PreflightBlocker.OBJECT_STORE_UNAVAILABLE)
            if inputs.capture_status is ContextBuildStatus.PARTIAL:
                blockers.append(ContextV2PreflightBlocker.PARTIAL_ATTEMPT)
            elif inputs.capture_status is ContextBuildStatus.UNAVAILABLE:
                blockers.append(ContextV2PreflightBlocker.CONTEXT_UNAVAILABLE)
            if inputs.read_only_degraded:
                blockers.append(ContextV2PreflightBlocker.READ_ONLY_DEGRADED)
        if self._admission.admitted and not inputs.exact_provider_transport_available:
            blockers.append(
                ContextV2PreflightBlocker.EXACT_PROVIDER_TRANSPORT_UNAVAILABLE
            )

        resolved_blockers = tuple(blockers)
        shadow = mode is DurableProviderTurnMode.SHADOW
        return ContextV2HealthReport(
            mode=mode,
            admitted=self._admission.admitted,
            capture_status=inputs.capture_status,
            read_only_degraded=inputs.read_only_degraded,
            ready_for_shadow_write=shadow and not resolved_blockers,
            ready_for_model_tool_work=(
                self._admission.admitted and not resolved_blockers
            ),
            fallback_forbidden=self._admission.admitted,
            blockers=resolved_blockers,
        )

    def preflight(self, inputs: ContextV2HealthInputs) -> ContextV2HealthReport:
        report = self.inspect(inputs)
        if report.mode is not DurableProviderTurnMode.OFF and report.blockers:
            raise ContextV2PreflightError(report)
        return report


__all__ = [
    "ContextV2Admission",
    "ContextV2HealthInputs",
    "ContextV2HealthReport",
    "ContextV2HealthService",
    "ContextV2PreflightBlocker",
    "ContextV2PreflightError",
]
