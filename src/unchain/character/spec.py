from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Literal


def _parse_duration(raw: str) -> float:
    """Parse a duration string like '30m', '2h', '1d' into seconds."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd])", str(raw).strip().lower())
    if not match:
        raise ValueError(f"invalid duration format: {raw!r} (expected e.g. '30m', '2h', '1d')")
    value = float(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


@dataclass(frozen=True)
class OutcomeCondition:
    """A single trait-based condition for outcome evaluation."""
    trait: str
    op: str      # ">=", ">", "<=", "<", "==", "!="
    value: Any

    _OPS = {
        ">=": lambda a, b: a >= b,
        ">": lambda a, b: a > b,
        "<=": lambda a, b: a <= b,
        "<": lambda a, b: a < b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }

    def evaluate(self, traits: dict[str, Any]) -> bool:
        trait_value = traits.get(self.trait)
        if trait_value is None:
            return False
        fn = self._OPS.get(self.op)
        if fn is None:
            return False
        try:
            return bool(fn(trait_value, self.value))
        except (TypeError, ValueError):
            return False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutcomeCondition:
        return cls(
            trait=str(data["trait"]),
            op=str(data["op"]),
            value=data["value"],
        )


@dataclass(frozen=True)
class Outcome:
    """One possible result of experiencing a narrative event."""
    id: str
    condition: OutcomeCondition | Literal["fallback"]
    trait_updates: dict[str, Any] = field(default_factory=dict)
    schedule_updates: list[dict[str, Any]] = field(default_factory=list)
    unlocks: tuple[str, ...] = ()

    def matches(self, traits: dict[str, Any]) -> bool:
        if self.condition == "fallback":
            return True
        return self.condition.evaluate(traits)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Outcome:
        raw_condition = data.get("condition", "fallback")
        if isinstance(raw_condition, dict):
            condition = OutcomeCondition.from_dict(raw_condition)
        else:
            condition = "fallback"
        return cls(
            id=str(data["id"]),
            condition=condition,
            trait_updates=dict(data.get("trait_updates") or {}),
            schedule_updates=list(data.get("schedule_updates") or []),
            unlocks=tuple(str(u) for u in (data.get("unlocks") or [])),
        )


@dataclass(frozen=True)
class NarrativeEvent:
    """A single event node in the narrative graph."""
    id: str
    name: str
    context: str
    trigger: Literal["auto", "interaction"] = "auto"
    requires: tuple[str, ...] = ()
    delay_after: str | None = None
    condition: OutcomeCondition | None = None
    outcomes: tuple[Outcome, ...] = ()

    def delay_seconds(self) -> float | None:
        if self.delay_after is None:
            return None
        return _parse_duration(self.delay_after)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NarrativeEvent:
        raw_condition = data.get("condition")
        condition = OutcomeCondition.from_dict(raw_condition) if isinstance(raw_condition, dict) else None
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", data["id"])),
            context=str(data.get("context", "")),
            trigger=data.get("trigger", "auto"),
            requires=tuple(str(r) for r in (data.get("requires") or [])),
            delay_after=data.get("delay_after"),
            condition=condition,
            outcomes=tuple(Outcome.from_dict(o) for o in (data.get("outcomes") or [])),
        )


@dataclass(frozen=True)
class ScheduleBlock:
    """A time-based behavior block in the character's schedule."""
    id: str
    label: str
    time_range: str
    behavior: str
    priority: int = 0
    active: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleBlock:
        return cls(
            id=str(data["id"]),
            label=str(data.get("label", "")),
            time_range=str(data.get("time_range", "")),
            behavior=str(data.get("behavior", "")),
            priority=int(data.get("priority", 0)),
            active=bool(data.get("active", True)),
        )


@dataclass(frozen=True)
class CharacterSpec:
    """Static character definition loaded from JSON."""
    identity: str
    narrative: dict[str, NarrativeEvent]
    initial_schedule: tuple[ScheduleBlock, ...] = ()
    initial_traits: dict[str, Any] = field(default_factory=dict)
    payload_defaults: dict[str, Any] = field(default_factory=dict)

    def entry_events(self) -> list[str]:
        """Return IDs of events with no requires and no delay_after (narrative roots)."""
        return [
            event.id
            for event in self.narrative.values()
            if not event.requires and not event.delay_after
        ]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CharacterSpec:
        raw_narrative = data.get("narrative") or {}
        narrative = {
            event_id: NarrativeEvent.from_dict({**event_data, "id": event_id})
            for event_id, event_data in raw_narrative.items()
        }
        raw_schedule = data.get("initial_schedule") or []
        schedule = tuple(ScheduleBlock.from_dict(s) for s in raw_schedule)
        return cls(
            identity=str(data.get("identity", "")),
            narrative=narrative,
            initial_schedule=schedule,
            initial_traits=copy.deepcopy(data.get("initial_traits") or {}),
            payload_defaults=copy.deepcopy(data.get("payload_defaults") or {}),
        )
