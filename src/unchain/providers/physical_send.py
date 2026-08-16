"""Deterministic identity for one exact physical provider send."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .request_lease import ProviderRequestSubject

if TYPE_CHECKING:
    from .wire_envelope import ProviderWireRoute


def provider_physical_ordinal(subject: ProviderRequestSubject) -> int:
    """Project a route-local durable subject onto the receipt ordinal."""

    if type(subject) is not ProviderRequestSubject:
        raise TypeError("subject must be an exact ProviderRequestSubject")
    if subject.route == "primary":
        return subject.retry_ordinal
    if subject.route == "openai_previous_response_fallback":
        return subject.retry_ordinal + 1
    raise ValueError("provider request subject route is unsupported")


@dataclass(frozen=True, slots=True)
class ProviderPhysicalSendContext:
    """Immutable subject, authoritative route, and physical receipt ordinal."""

    subject: ProviderRequestSubject
    route: ProviderWireRoute
    physical_ordinal: int

    def __post_init__(self) -> None:
        from .wire_envelope import ProviderWireRoute

        if type(self.subject) is not ProviderRequestSubject:
            raise TypeError("subject must be an exact ProviderRequestSubject")
        if type(self.route) is not ProviderWireRoute:
            raise TypeError("route must be an exact ProviderWireRoute")
        if self.route.name != self.subject.route:
            raise ValueError("provider physical send route changed")
        if type(self.physical_ordinal) is not int:
            raise TypeError("physical_ordinal must be an exact integer")
        expected = provider_physical_ordinal(self.subject)
        if self.physical_ordinal != expected:
            raise ValueError("provider physical ordinal changed")


__all__ = [
    "ProviderPhysicalSendContext",
    "provider_physical_ordinal",
]
