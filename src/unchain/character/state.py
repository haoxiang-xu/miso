from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .spec import CharacterSpec, NarrativeEvent, OutcomeCondition


@dataclass
class EventBookmark:
    """Tracks a single event's progression."""
    status: Literal["pending", "in_progress", "completed"]
    reached_at: float | None = None
    completed_at: float | None = None
    outcome: str | None = None
    bookmark: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reached_at": self.reached_at,
            "completed_at": self.completed_at,
            "outcome": self.outcome,
            "bookmark": self.bookmark,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventBookmark:
        return cls(
            status=data.get("status", "pending"),
            reached_at=data.get("reached_at"),
            completed_at=data.get("completed_at"),
            outcome=data.get("outcome"),
            bookmark=data.get("bookmark"),
        )


@dataclass
class CharacterState:
    """Mutable runtime state for a character, stored in memory."""
    bookmarks: dict[str, EventBookmark] = field(default_factory=dict)
    traits: dict[str, Any] = field(default_factory=dict)
    schedule: list[dict[str, Any]] = field(default_factory=list)
    activated_at: float = 0.0

    def is_event_completed(self, event_id: str) -> bool:
        bm = self.bookmarks.get(event_id)
        return bm is not None and bm.status == "completed"

    def is_event_pending(self, event_id: str) -> bool:
        bm = self.bookmarks.get(event_id)
        return bm is not None and bm.status in ("pending", "in_progress")

    def evaluate_condition(self, condition: OutcomeCondition) -> bool:
        return condition.evaluate(self.traits)

    def is_event_eligible(self, event: NarrativeEvent, now: float | None = None) -> bool:
        """Check if an event is eligible to be active (all preconditions met)."""
        if now is None:
            now = time.time()

        bm = self.bookmarks.get(event.id)
        if bm is None:
            return False
        if bm.status == "completed":
            return False
        if bm.status not in ("pending", "in_progress"):
            return False

        # All required events must be completed
        for req_id in event.requires:
            if not self.is_event_completed(req_id):
                return False

        # Check time delay
        delay = event.delay_seconds()
        if delay is not None and event.requires:
            latest_req_time = max(
                (self.bookmarks[r].completed_at or 0.0)
                for r in event.requires
                if r in self.bookmarks
            )
            if now < latest_req_time + delay:
                return False

        # Check optional condition
        if event.condition is not None:
            if not self.evaluate_condition(event.condition):
                return False

        return True

    def active_events(self, spec: CharacterSpec, now: float | None = None) -> list[NarrativeEvent]:
        """Return all narrative events that are currently eligible."""
        if now is None:
            now = time.time()
        return [
            event
            for event in spec.narrative.values()
            if self.is_event_eligible(event, now)
        ]

    def resolve_outcome(self, event: NarrativeEvent) -> str | None:
        """Evaluate outcomes for an event and return the matching outcome ID."""
        for outcome in event.outcomes:
            if outcome.matches(self.traits):
                return outcome.id
        return None

    def apply_outcome(self, event: NarrativeEvent, outcome_id: str, now: float | None = None) -> None:
        """Mark event as completed with the given outcome and apply its effects."""
        if now is None:
            now = time.time()

        outcome = next((o for o in event.outcomes if o.id == outcome_id), None)
        if outcome is None:
            return

        # Mark completed
        bm = self.bookmarks.get(event.id)
        if bm is None:
            bm = EventBookmark(status="completed", reached_at=now, completed_at=now, outcome=outcome_id)
        else:
            bm.status = "completed"
            bm.completed_at = now
            bm.outcome = outcome_id
            bm.bookmark = None
        self.bookmarks[event.id] = bm

        # Apply trait updates
        for key, value in outcome.trait_updates.items():
            if isinstance(value, (int, float)) and isinstance(self.traits.get(key), (int, float)):
                if value < 0 or str(value).startswith("-"):
                    self.traits[key] = self.traits[key] + value
                else:
                    self.traits[key] = value
            else:
                self.traits[key] = value

        # Apply schedule updates
        for update in outcome.schedule_updates:
            action = update.get("action", "add")
            if action == "add":
                self.schedule.append(copy.deepcopy(update.get("block", update)))
            elif action == "remove":
                target_id = update.get("id")
                if target_id:
                    self.schedule = [b for b in self.schedule if b.get("id") != target_id]

        # Unlock new events
        for unlocked_id in outcome.unlocks:
            if unlocked_id not in self.bookmarks:
                self.bookmarks[unlocked_id] = EventBookmark(status="pending", reached_at=now)

    def check_time_delayed_events(self, spec: CharacterSpec, now: float | None = None) -> list[str]:
        """Check for events whose time delay has elapsed and mark them as pending."""
        if now is None:
            now = time.time()
        newly_pending: list[str] = []
        for event in spec.narrative.values():
            if event.id in self.bookmarks:
                continue
            if not event.requires:
                continue
            if not all(self.is_event_completed(r) for r in event.requires):
                continue
            delay = event.delay_seconds()
            if delay is None:
                # No time delay — unlock immediately if all requires met
                self.bookmarks[event.id] = EventBookmark(status="pending", reached_at=now)
                newly_pending.append(event.id)
                continue
            latest_req_time = max(
                (self.bookmarks[r].completed_at or 0.0)
                for r in event.requires
                if r in self.bookmarks
            )
            if now >= latest_req_time + delay:
                self.bookmarks[event.id] = EventBookmark(status="pending", reached_at=now)
                newly_pending.append(event.id)
        return newly_pending

    def to_dict(self) -> dict[str, Any]:
        return {
            "bookmarks": {k: v.to_dict() for k, v in self.bookmarks.items()},
            "traits": copy.deepcopy(self.traits),
            "schedule": copy.deepcopy(self.schedule),
            "activated_at": self.activated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterState:
        raw_bookmarks = data.get("bookmarks") or {}
        return cls(
            bookmarks={k: EventBookmark.from_dict(v) for k, v in raw_bookmarks.items()},
            traits=copy.deepcopy(data.get("traits") or {}),
            schedule=copy.deepcopy(data.get("schedule") or []),
            activated_at=float(data.get("activated_at") or 0.0),
        )

    @classmethod
    def initial(cls, spec: CharacterSpec) -> CharacterState:
        """Create initial state from spec, marking entry events as pending."""
        now = time.time()
        bookmarks: dict[str, EventBookmark] = {}
        for event_id in spec.entry_events():
            bookmarks[event_id] = EventBookmark(status="pending", reached_at=now)
        return cls(
            bookmarks=bookmarks,
            traits=copy.deepcopy(dict(spec.initial_traits)),
            schedule=[b.__dict__ for b in spec.initial_schedule] if spec.initial_schedule else [],
            activated_at=now,
        )
