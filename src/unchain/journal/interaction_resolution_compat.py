"""Narrow replay compatibility for one historical interaction-resolution defect.

The durable journal is immutable.  A legacy host could append an underscore
``interaction_resolved`` event without the response artifact descriptor, then
later append the official descriptor-bound ``interaction.resolved`` event for
the same interaction.  Consumers may suppress only that exact two-event shape.
Every other duplicate or cross-scope shape remains an integrity error.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class InteractionResolutionCompatibilityError(ValueError):
    """Persisted resolution evidence cannot be uniquely superseded."""


def _compact_artifact_ref(value: Any) -> tuple[str, str, int] | None:
    if isinstance(value, Mapping):
        nested = value.get("ref")
        if isinstance(nested, Mapping):
            return _compact_artifact_ref(nested)
        kind = value.get("kind")
        identifier = value.get("id", value.get("resource_id"))
        revision = value.get("revision")
    else:
        kind = getattr(value, "kind", None)
        identifier = getattr(value, "resource_id", None)
        revision = getattr(value, "revision", None)
    if (
        not isinstance(kind, str)
        or not kind.strip()
        or not isinstance(identifier, str)
        or not identifier.strip()
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision <= 0
    ):
        return None
    return kind.strip(), identifier.strip(), revision


def has_complete_interaction_response_descriptor(
    payload: Mapping[str, Any],
) -> bool:
    """Return whether a resolution carries the compiler-required descriptor."""

    if not isinstance(payload, Mapping):
        return False
    content_ref = _compact_artifact_ref(payload.get("content_ref"))
    content_bytes = payload.get("content_bytes")
    content_sha256 = payload.get("content_sha256")
    return bool(
        content_ref is not None
        and content_ref[0] == "artifact"
        and not isinstance(content_bytes, bool)
        and isinstance(content_bytes, int)
        and content_bytes >= 0
        and isinstance(content_sha256, str)
        and len(content_sha256) == 64
        and all(character in "0123456789abcdef" for character in content_sha256)
        and isinstance(payload.get("preview"), str)
        and type(payload.get("preview_truncated")) is bool
        and isinstance(payload.get("submitted_by"), str)
        and bool(str(payload.get("submitted_by") or "").strip())
    )


@dataclass(frozen=True, slots=True)
class InteractionResolutionCompatibilityRecord:
    ordinal: int
    event_type: str
    interaction_id: str
    execution_id: str
    generation_id: str
    attempt_id: str
    descriptor_authorized: bool

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int):
            raise TypeError("ordinal must be an integer")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")
        if self.event_type not in {
            "interaction_resolved",
            "interaction.resolved",
        }:
            raise ValueError("event_type is not an interaction resolution")
        if not isinstance(self.interaction_id, str) or not self.interaction_id:
            raise ValueError("interaction_id must be non-empty text")
        for field_name in ("execution_id", "generation_id", "attempt_id"):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be text")
        if type(self.descriptor_authorized) is not bool:
            raise TypeError("descriptor_authorized must be an exact boolean")

    @property
    def scope(self) -> tuple[str, str, str, str]:
        return (
            self.execution_id,
            self.generation_id,
            self.attempt_id,
            self.interaction_id,
        )


@dataclass(frozen=True, slots=True)
class InteractionResolutionSupersession:
    legacy_ordinal: int
    canonical_ordinal: int
    scope: tuple[str, str, str, str]


def interaction_resolution_compatibility_record(
    *,
    ordinal: int,
    event_type: str,
    interaction_id: str,
    execution_id: str = "",
    generation_id: str = "",
    attempt_id: str = "",
    payload: Mapping[str, Any],
    resource_refs: Sequence[Any] = (),
) -> InteractionResolutionCompatibilityRecord:
    descriptor = _compact_artifact_ref(payload.get("content_ref"))
    authorized_refs = {
        compact
        for value in resource_refs
        if (compact := _compact_artifact_ref(value)) is not None
    }
    return InteractionResolutionCompatibilityRecord(
        ordinal=ordinal,
        event_type=str(event_type or "").strip(),
        interaction_id=str(interaction_id or "").strip(),
        execution_id=str(execution_id or "").strip(),
        generation_id=str(generation_id or "").strip(),
        attempt_id=str(attempt_id or "").strip(),
        descriptor_authorized=(
            has_complete_interaction_response_descriptor(payload)
            and descriptor is not None
            and descriptor in authorized_refs
        ),
    )


def legacy_interaction_resolution_supersession_pairs(
    records: Sequence[InteractionResolutionCompatibilityRecord],
) -> tuple[InteractionResolutionSupersession, ...]:
    """Return the exact legacy/canonical pair admitted for each scope.

    The only admitted pair is exactly one descriptor-incomplete underscore
    event followed by exactly one descriptor-complete dotted event.
    This does not repair the journal; it gives deterministic consumers a shared
    replay winner while preserving every durable audit record.
    """

    groups: dict[
        tuple[str, str, str, str],
        list[InteractionResolutionCompatibilityRecord],
    ] = defaultdict(list)
    seen_ordinals: set[int] = set()
    for record in records:
        if not isinstance(record, InteractionResolutionCompatibilityRecord):
            raise TypeError("records must contain compatibility records")
        if record.ordinal in seen_ordinals:
            raise InteractionResolutionCompatibilityError(
                "interaction resolution ordinal is duplicated"
            )
        seen_ordinals.add(record.ordinal)
        groups[record.scope].append(record)

    pairs: list[InteractionResolutionSupersession] = []
    for scoped_records in groups.values():
        if len(scoped_records) == 1:
            continue
        canonical = [
            record
            for record in scoped_records
            if record.event_type == "interaction.resolved"
            and record.descriptor_authorized
        ]
        malformed_legacy = [
            record
            for record in scoped_records
            if record.event_type == "interaction_resolved"
            and not record.descriptor_authorized
        ]
        if (
            len(scoped_records) == 2
            and len(canonical) == 1
            and len(malformed_legacy) == 1
            and canonical[0].ordinal > malformed_legacy[0].ordinal
        ):
            pairs.append(
                InteractionResolutionSupersession(
                    legacy_ordinal=malformed_legacy[0].ordinal,
                    canonical_ordinal=canonical[0].ordinal,
                    scope=canonical[0].scope,
                )
            )
            continue
        raise InteractionResolutionCompatibilityError(
            "interaction resolution evidence is ambiguous"
        )
    return tuple(sorted(pairs, key=lambda pair: pair.canonical_ordinal))


def legacy_interaction_resolution_supersessions(
    records: Sequence[InteractionResolutionCompatibilityRecord],
) -> frozenset[int]:
    """Return malformed legacy ordinals superseded by canonical evidence."""

    return frozenset(
        pair.legacy_ordinal
        for pair in legacy_interaction_resolution_supersession_pairs(records)
    )


__all__ = [
    "InteractionResolutionCompatibilityError",
    "InteractionResolutionCompatibilityRecord",
    "InteractionResolutionSupersession",
    "has_complete_interaction_response_descriptor",
    "interaction_resolution_compatibility_record",
    "legacy_interaction_resolution_supersession_pairs",
    "legacy_interaction_resolution_supersessions",
]
