from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from .models import EventCursor, JournalEvent, ResourceRef, _required_text
from .resource_limits import (
    JsonResourceLimits,
    enforce_item_limit,
    validate_json_resource,
)


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
JOURNAL_SNAPSHOT_LIMITS = JsonResourceLimits(
    max_items=10_000,
    max_bytes=32 * 1024 * 1024,
    max_depth=64,
    max_nodes=1_000_000,
)


class JournalSnapshotError(ValueError):
    """An execution snapshot is incomplete, aliased, or not reproducible."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def journal_event_sha256(event: JournalEvent) -> str:
    if not isinstance(event, JournalEvent):
        raise TypeError("event must be a JournalEvent")
    return hashlib.sha256(_canonical_bytes(event.to_dict())).hexdigest()


def _snapshot_sha256(
    *,
    execution_id: str,
    high_water: EventCursor | None,
    event_sha256s: Sequence[str],
) -> str:
    return hashlib.sha256(
        _canonical_bytes(
            {
                "domain": "unchain.journal_snapshot.v1",
                "execution_id": execution_id,
                "high_water": high_water.to_dict() if high_water else None,
                "event_sha256s": list(event_sha256s),
            }
        )
    ).hexdigest()


def _snapshot_resource_adapter(value: Any) -> Any:
    if isinstance(value, EventCursor):
        return value.to_dict()
    if isinstance(value, ResourceRef):
        return value.to_dict()
    if isinstance(value, JournalEvent):
        return {
            "schema": value.SCHEMA,
            "event_id": value.event_id,
            "event_type": value.event_type,
            "attempt": value.attempt.to_dict(),
            "operation": value.operation.to_dict(),
            "store_seq": value.store_seq,
            "payload": value.payload,
            "resource_refs": value.resource_refs,
        }
    return value


def _validate_snapshot_resource(
    *,
    execution_id: Any,
    high_water: Any,
    events: Sequence[Any],
    snapshot_sha256: Any,
) -> None:
    limits = JOURNAL_SNAPSHOT_LIMITS
    enforce_item_limit(
        events,
        boundary="journal_snapshot.events",
        limits=limits,
    )
    validate_json_resource(
        {
            "schema": JournalSnapshot.SCHEMA,
            "execution_id": execution_id,
            "high_water": high_water,
            "events": events,
            "snapshot_sha256": snapshot_sha256,
            "event_count": len(events),
        },
        boundary="journal_snapshot",
        limits=limits,
        record_adapter=_snapshot_resource_adapter,
    )


@dataclass(frozen=True)
class JournalSnapshot:
    SCHEMA: ClassVar[str] = "unchain.journal_snapshot.v1"

    execution_id: str
    high_water: EventCursor | None
    events: tuple[JournalEvent, ...]
    snapshot_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.events, Sequence) or isinstance(
            self.events,
            (str, bytes, bytearray),
        ):
            raise TypeError("journal snapshot events must be an array")
        _validate_snapshot_resource(
            execution_id=self.execution_id,
            high_water=self.high_water,
            events=self.events,
            snapshot_sha256=self.snapshot_sha256,
        )
        execution_id = _required_text(
            self.execution_id,
            "execution_id",
            identifier=True,
        )
        object.__setattr__(self, "execution_id", execution_id)
        if self.high_water is not None and not isinstance(
            self.high_water,
            EventCursor,
        ):
            object.__setattr__(
                self,
                "high_water",
                EventCursor.from_dict(self.high_water),
            )
        events = tuple(
            event
            if isinstance(event, JournalEvent)
            else JournalEvent.from_dict(event)
            for event in self.events
        )
        object.__setattr__(self, "events", events)
        if any(
            previous.store_seq >= current.store_seq
            for previous, current in zip(events, events[1:])
        ):
            raise JournalSnapshotError(
                "journal snapshot event sequences must be strictly increasing"
            )
        if tuple(event.store_seq for event in events) != tuple(
            range(1, len(events) + 1)
        ):
            raise JournalSnapshotError(
                "journal snapshot must be a complete contiguous prefix"
            )
        if (
            len({event.event_id for event in events}) != len(events)
            or len({event.store_seq for event in events}) != len(events)
        ):
            raise JournalSnapshotError(
                "journal snapshot event cursors must be unique"
            )
        if any(
            event.attempt.generation.execution_id != execution_id
            for event in events
        ):
            raise JournalSnapshotError(
                "journal snapshot contains a foreign execution"
            )
        expected_high_water = (
            EventCursor(
                store_seq=events[-1].store_seq,
                event_id=events[-1].event_id,
            )
            if events
            else None
        )
        if self.high_water != expected_high_water:
            raise JournalSnapshotError(
                "journal snapshot high water is inconsistent"
            )
        if (
            not isinstance(self.snapshot_sha256, str)
            or _SHA256_RE.fullmatch(self.snapshot_sha256) is None
        ):
            raise JournalSnapshotError("journal snapshot digest is invalid")
        expected_digest = _snapshot_sha256(
            execution_id=execution_id,
            high_water=expected_high_water,
            event_sha256s=self.event_sha256s,
        )
        if self.snapshot_sha256 != expected_digest:
            raise JournalSnapshotError("journal snapshot digest is inconsistent")

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def event_sha256s(self) -> tuple[str, ...]:
        return tuple(journal_event_sha256(event) for event in self.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "execution_id": self.execution_id,
            "high_water": self.high_water.to_dict() if self.high_water else None,
            "events": [event.to_dict() for event in self.events],
            "snapshot_sha256": self.snapshot_sha256,
            "event_count": self.event_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> JournalSnapshot:
        expected = {
            "schema",
            "execution_id",
            "high_water",
            "events",
            "snapshot_sha256",
            "event_count",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise JournalSnapshotError("journal snapshot record is invalid")
        if value.get("schema") != cls.SCHEMA:
            raise JournalSnapshotError("journal snapshot schema is invalid")
        raw_events = value.get("events")
        if not isinstance(raw_events, Sequence) or isinstance(
            raw_events,
            (str, bytes, bytearray),
        ):
            raise JournalSnapshotError("journal snapshot events are invalid")
        _validate_snapshot_resource(
            execution_id=value.get("execution_id"),
            high_water=value.get("high_water"),
            events=raw_events,
            snapshot_sha256=value.get("snapshot_sha256"),
        )
        events = tuple(JournalEvent.from_dict(event) for event in raw_events)
        if value.get("event_count") != len(events):
            raise JournalSnapshotError("journal snapshot event count is invalid")
        raw_high_water = value.get("high_water")
        return cls(
            execution_id=value.get("execution_id"),
            high_water=(
                EventCursor.from_dict(raw_high_water)
                if raw_high_water is not None
                else None
            ),
            events=events,
            snapshot_sha256=value.get("snapshot_sha256"),
        )


def capture_journal_snapshot(
    *,
    execution_id: str,
    events: Sequence[JournalEvent],
) -> JournalSnapshot:
    normalized_execution_id = _required_text(
        execution_id,
        "execution_id",
        identifier=True,
    )
    if not isinstance(events, Sequence) or isinstance(
        events,
        (str, bytes, bytearray),
    ):
        raise TypeError("events must be an array")
    enforce_item_limit(
        events,
        boundary="journal_snapshot.events",
        limits=JOURNAL_SNAPSHOT_LIMITS,
    )
    normalized_events = tuple(events)
    high_water = (
        EventCursor(
            store_seq=normalized_events[-1].store_seq,
            event_id=normalized_events[-1].event_id,
        )
        if normalized_events
        else None
    )
    _validate_snapshot_resource(
        execution_id=normalized_execution_id,
        high_water=high_water,
        events=normalized_events,
        snapshot_sha256="0" * 64,
    )
    event_sha256s = tuple(
        journal_event_sha256(event) for event in normalized_events
    )
    return JournalSnapshot(
        execution_id=normalized_execution_id,
        high_water=high_water,
        events=normalized_events,
        snapshot_sha256=_snapshot_sha256(
            execution_id=normalized_execution_id,
            high_water=high_water,
            event_sha256s=event_sha256s,
        ),
    )


__all__ = [
    "JournalSnapshot",
    "JournalSnapshotError",
    "capture_journal_snapshot",
    "journal_event_sha256",
]
