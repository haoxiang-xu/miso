from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Sequence
from zoneinfo import ZoneInfo

_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_ALIASES = {
    "monday": "mon", "mon": "mon", "tuesday": "tue", "tue": "tue",
    "wednesday": "wed", "wed": "wed", "thursday": "thu", "thu": "thu",
    "friday": "fri", "fri": "fri", "saturday": "sat", "sat": "sat",
    "sunday": "sun", "sun": "sun",
    "daily": "daily", "everyday": "daily",
    "weekday": "weekday", "weekdays": "weekday",
    "weekend": "weekend", "weekends": "weekend",
}
_STATUS_DEFAULTS = {
    "free": {"availability": "available", "interruption_tolerance": 0.9},
    "available": {"availability": "available", "interruption_tolerance": 0.9},
    "working": {"availability": "limited", "interruption_tolerance": 0.35},
    "study": {"availability": "limited", "interruption_tolerance": 0.3},
    "studying": {"availability": "limited", "interruption_tolerance": 0.3},
    "commuting": {"availability": "limited", "interruption_tolerance": 0.25},
    "busy": {"availability": "busy", "interruption_tolerance": 0.15},
    "meeting": {"availability": "busy", "interruption_tolerance": 0.08},
    "sleeping": {"availability": "offline", "interruption_tolerance": 0.0},
    "offline": {"availability": "offline", "interruption_tolerance": 0.0},
}
_VALID_REPLY_MODES = {"auto", "reply", "defer", "ignore"}
_VALID_AVAILABILITY = {"available", "limited", "busy", "offline"}


def _trimmed(value: object, fallback: str = "") -> str:
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed:
            return trimmed
    return fallback


def _clamp_unit_interval(value: object, fallback: float) -> float:
    try:
        numeric = float(value)
    except Exception:
        numeric = fallback
    return max(0.0, min(1.0, round(numeric, 4)))


def _coerce_timezone(value: object, fallback: str = "UTC") -> str:
    candidate = _trimmed(value, fallback=fallback)
    try:
        ZoneInfo(candidate)
        return candidate
    except Exception:
        return fallback


def _parse_datetime(value: object, timezone_name: str) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        normalized = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
        try:
            dt = datetime.fromisoformat(normalized)
        except Exception:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(timezone_name))
    return dt.astimezone(ZoneInfo(timezone_name))


def _coerce_now(value: object, timezone_name: str) -> datetime:
    parsed = _parse_datetime(value, timezone_name)
    if parsed is not None:
        return parsed.astimezone(ZoneInfo(timezone_name))
    return datetime.now(ZoneInfo(timezone_name))


def _weekday_token(dt: datetime) -> str:
    return _DAY_NAMES[dt.weekday()]


def _normalize_days(value: object) -> tuple[str, ...]:
    if value is None:
        return ("daily",)
    raw_values: list[str] = []
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                raw_values.append(item)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        alias = _DAY_ALIASES.get(item.strip().lower())
        if not alias or alias in seen:
            continue
        seen.add(alias)
        normalized.append(alias)
    return tuple(normalized or ("daily",))


def _day_matches(day_token: str, configured_days: Sequence[str]) -> bool:
    normalized_days = {item for item in configured_days if item}
    if "daily" in normalized_days:
        return True
    if "weekday" in normalized_days and day_token in {"mon", "tue", "wed", "thu", "fri"}:
        return True
    if "weekend" in normalized_days and day_token in {"sat", "sun"}:
        return True
    return day_token in normalized_days


def _minutes_from_hhmm(value: object, *, fallback: int) -> int:
    if isinstance(value, int):
        return max(0, min(24 * 60 - 1, value))
    text = _trimmed(value)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return fallback
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return fallback
    return hour * 60 + minute


def _hhmm_from_minutes(value: int) -> str:
    minutes = max(0, min(24 * 60 - 1, int(value)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _coerce_reply_mode(value: object, fallback: str = "auto") -> str:
    candidate = _trimmed(value, fallback=fallback).lower()
    return candidate if candidate in _VALID_REPLY_MODES else fallback


def _coerce_availability(value: object, fallback: str = "available") -> str:
    candidate = _trimmed(value, fallback=fallback).lower()
    return candidate if candidate in _VALID_AVAILABILITY else fallback


def _normalize_metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return copy.deepcopy(value)


# ---------------------------------------------------------------------------
# Schedule data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CharacterScheduleBlock:
    days: tuple[str, ...] = ("daily",)
    start_time: str = "00:00"
    end_time: str = "23:59"
    status: str = "free"
    label: str = ""
    availability: str = ""
    interruption_tolerance: float | None = None
    reply_mode: str = "auto"
    courtesy_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def coerce(cls, value: object) -> CharacterScheduleBlock:
        if isinstance(value, cls):
            return copy.deepcopy(value)
        raw = value if isinstance(value, dict) else {}
        status = _trimmed(raw.get("status"), fallback="free").lower()
        defaults = _STATUS_DEFAULTS.get(status, _STATUS_DEFAULTS["free"])
        start_minutes = _minutes_from_hhmm(raw.get("start_time"), fallback=0)
        end_minutes = _minutes_from_hhmm(raw.get("end_time"), fallback=(24 * 60) - 1)
        return cls(
            days=_normalize_days(raw.get("days")),
            start_time=_hhmm_from_minutes(start_minutes),
            end_time=_hhmm_from_minutes(end_minutes),
            status=status,
            label=_trimmed(raw.get("label")),
            availability=_coerce_availability(raw.get("availability"), fallback=defaults["availability"]),
            interruption_tolerance=_clamp_unit_interval(
                raw.get("interruption_tolerance"), defaults["interruption_tolerance"],
            ),
            reply_mode=_coerce_reply_mode(raw.get("reply_mode"), fallback="auto"),
            courtesy_message=_trimmed(raw.get("courtesy_message")),
            metadata=_normalize_metadata(raw.get("metadata")),
        )

    def start_minutes(self) -> int:
        return _minutes_from_hhmm(self.start_time, fallback=0)

    def end_minutes(self) -> int:
        return _minutes_from_hhmm(self.end_time, fallback=(24 * 60) - 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": list(self.days), "start_time": self.start_time, "end_time": self.end_time,
            "status": self.status, "label": self.label, "availability": self.availability,
            "interruption_tolerance": self.interruption_tolerance, "reply_mode": self.reply_mode,
            "courtesy_message": self.courtesy_message, "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(slots=True)
class CharacterSchedule:
    timezone: str = "UTC"
    blocks: tuple[CharacterScheduleBlock, ...] = ()
    default_status: str = "free"
    default_availability: str = "available"
    default_interruption_tolerance: float = 0.7
    default_reply_mode: str = "auto"
    default_courtesy_message: str = ""

    @classmethod
    def coerce(cls, value: object, *, fallback_timezone: str = "UTC") -> CharacterSchedule:
        if isinstance(value, cls):
            return copy.deepcopy(value)
        raw = value if isinstance(value, dict) else {}
        default_status = _trimmed(raw.get("default_status"), fallback="free").lower()
        defaults = _STATUS_DEFAULTS.get(default_status, _STATUS_DEFAULTS["free"])
        raw_blocks = raw.get("blocks") if isinstance(raw.get("blocks"), list) else []
        return cls(
            timezone=_coerce_timezone(raw.get("timezone"), fallback=fallback_timezone),
            blocks=tuple(CharacterScheduleBlock.coerce(item) for item in raw_blocks),
            default_status=default_status,
            default_availability=_coerce_availability(raw.get("default_availability"), fallback=defaults["availability"]),
            default_interruption_tolerance=_clamp_unit_interval(raw.get("default_interruption_tolerance"), defaults["interruption_tolerance"]),
            default_reply_mode=_coerce_reply_mode(raw.get("default_reply_mode"), fallback="auto"),
            default_courtesy_message=_trimmed(raw.get("default_courtesy_message")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "blocks": [b.to_dict() for b in self.blocks],
            "default_status": self.default_status,
            "default_availability": self.default_availability,
            "default_interruption_tolerance": self.default_interruption_tolerance,
            "default_reply_mode": self.default_reply_mode,
            "default_courtesy_message": self.default_courtesy_message,
        }


# ---------------------------------------------------------------------------
# Character identity spec (old-style structured fields)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CharacterIdentitySpec:
    """Old-style structured character identity used by PuPu's character system."""
    id: str
    name: str
    gender: str = ""
    role: str = ""
    persona: str = ""
    speaking_style: tuple[str, ...] = ()
    talkativeness: float = 0.5
    politeness: float = 0.5
    autonomy: float = 0.5
    avatar_ref: str | None = None
    timezone: str = "UTC"
    schedule: CharacterSchedule = field(default_factory=CharacterSchedule)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def coerce(cls, value: object) -> CharacterIdentitySpec:
        if isinstance(value, cls):
            return copy.deepcopy(value)
        raw = value if isinstance(value, dict) else {}
        from .naming import sanitize_character_key_component
        timezone_name = _coerce_timezone(raw.get("timezone"), fallback="UTC")
        name = _trimmed(raw.get("name"), fallback="Character")
        character_id = sanitize_character_key_component(
            raw.get("id"), fallback=sanitize_character_key_component(name, fallback="character"),
        )
        schedule = CharacterSchedule.coerce(raw.get("schedule"), fallback_timezone=timezone_name)
        return cls(
            id=character_id, name=name,
            gender=_trimmed(raw.get("gender")),
            role=_trimmed(raw.get("role")),
            persona=_trimmed(raw.get("persona")),
            speaking_style=tuple(
                s.strip() for s in (raw.get("speaking_style") or [])
                if isinstance(s, str) and s.strip()
            ),
            talkativeness=_clamp_unit_interval(raw.get("talkativeness"), 0.5),
            politeness=_clamp_unit_interval(raw.get("politeness"), 0.5),
            autonomy=_clamp_unit_interval(raw.get("autonomy"), 0.5),
            avatar_ref=_trimmed(raw.get("avatar_ref")) or None,
            timezone=schedule.timezone,
            schedule=schedule,
            metadata=_normalize_metadata(raw.get("metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "gender": self.gender, "role": self.role,
            "persona": self.persona, "speaking_style": list(self.speaking_style),
            "talkativeness": self.talkativeness, "politeness": self.politeness,
            "autonomy": self.autonomy, "avatar_ref": self.avatar_ref,
            "timezone": self.timezone, "schedule": self.schedule.to_dict(),
            "metadata": copy.deepcopy(self.metadata),
        }


# ---------------------------------------------------------------------------
# Obligations
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CharacterObligation:
    label: str = ""
    status: str = "busy"
    availability: str = "busy"
    interruption_tolerance: float = 0.1
    reply_mode: str = "auto"
    courtesy_message: str = ""
    start_at: str | None = None
    end_at: str | None = None
    priority: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def coerce(cls, value: object, *, timezone_name: str) -> CharacterObligation:
        if isinstance(value, cls):
            return copy.deepcopy(value)
        raw = value if isinstance(value, dict) else {}
        status = _trimmed(raw.get("status"), fallback="busy").lower()
        defaults = _STATUS_DEFAULTS.get(status, _STATUS_DEFAULTS["busy"])
        start_dt = _parse_datetime(raw.get("start_at"), timezone_name)
        end_dt = _parse_datetime(raw.get("end_at"), timezone_name)
        return cls(
            label=_trimmed(raw.get("label")),
            status=status,
            availability=_coerce_availability(raw.get("availability"), fallback=defaults["availability"]),
            interruption_tolerance=_clamp_unit_interval(raw.get("interruption_tolerance"), defaults["interruption_tolerance"]),
            reply_mode=_coerce_reply_mode(raw.get("reply_mode"), fallback="auto"),
            courtesy_message=_trimmed(raw.get("courtesy_message")),
            start_at=start_dt.isoformat() if start_dt is not None else None,
            end_at=end_dt.isoformat() if end_dt is not None else None,
            priority=_clamp_unit_interval(raw.get("priority"), 0.5),
            metadata=_normalize_metadata(raw.get("metadata")),
        )

    def is_active(self, now: datetime, timezone_name: str) -> bool:
        start_dt = _parse_datetime(self.start_at, timezone_name)
        end_dt = _parse_datetime(self.end_at, timezone_name)
        if start_dt is not None and now < start_dt:
            return False
        if end_dt is not None and now >= end_dt:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Evaluation and decision results
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CharacterEvaluation:
    at: str
    timezone: str
    status: str
    availability: str
    interruption_tolerance: float
    reply_mode: str
    courtesy_message: str | None = None
    available_at: str | None = None
    reasons: tuple[str, ...] = ()
    active_schedule_block: dict[str, Any] | None = None
    active_obligation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at, "timezone": self.timezone, "status": self.status,
            "availability": self.availability, "interruption_tolerance": self.interruption_tolerance,
            "reply_mode": self.reply_mode, "courtesy_message": self.courtesy_message,
            "available_at": self.available_at, "reasons": list(self.reasons),
            "active_schedule_block": copy.deepcopy(self.active_schedule_block),
            "active_obligation": copy.deepcopy(self.active_obligation),
        }


@dataclass(slots=True)
class CharacterDecision:
    action: str
    should_reply: bool
    courtesy_message: str | None
    reason: str
    evaluation: CharacterEvaluation

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action, "should_reply": self.should_reply,
            "courtesy_message": self.courtesy_message, "reason": self.reason,
            "evaluation": self.evaluation.to_dict(),
        }


# ---------------------------------------------------------------------------
# Schedule matching
# ---------------------------------------------------------------------------

def _block_is_active(block: CharacterScheduleBlock, now: datetime) -> bool:
    start_minutes = block.start_minutes()
    end_minutes = block.end_minutes()
    current_minutes = now.hour * 60 + now.minute
    current_day = _weekday_token(now)
    if start_minutes <= end_minutes:
        return _day_matches(current_day, block.days) and start_minutes <= current_minutes < end_minutes
    previous_day = _DAY_NAMES[(now.weekday() - 1) % 7]
    if _day_matches(current_day, block.days) and current_minutes >= start_minutes:
        return True
    return _day_matches(previous_day, block.days) and current_minutes < end_minutes


def _active_schedule_block(schedule: CharacterSchedule, now: datetime) -> CharacterScheduleBlock | None:
    for block in schedule.blocks:
        if _block_is_active(block, now):
            return block
    return None


def _select_active_obligation(obligations: Sequence[CharacterObligation], *, now: datetime, timezone_name: str) -> CharacterObligation | None:
    active = [item for item in obligations if item.is_active(now, timezone_name)]
    if not active:
        return None
    return sorted(active, key=lambda item: (item.priority, 1.0 - item.interruption_tolerance, item.status), reverse=True)[0]


def _base_schedule_state(schedule: CharacterSchedule, now: datetime):
    active_block = _active_schedule_block(schedule, now)
    if active_block is None:
        return (schedule.default_status, schedule.default_availability, schedule.default_interruption_tolerance,
                schedule.default_reply_mode, schedule.default_courtesy_message, None)
    return (active_block.status, active_block.availability,
            active_block.interruption_tolerance if active_block.interruption_tolerance is not None else schedule.default_interruption_tolerance,
            active_block.reply_mode or schedule.default_reply_mode,
            active_block.courtesy_message or schedule.default_courtesy_message, active_block)


def _compose_courtesy_message(spec: CharacterIdentitySpec, *, availability: str, status: str, available_at: str | None, fallback_message: str) -> str:
    if fallback_message:
        return fallback_message
    if availability == "offline":
        return f"{spec.name} is unavailable right now."
    if available_at:
        local_dt = _parse_datetime(available_at, spec.timezone)
        when_text = local_dt.strftime("%a %H:%M") if local_dt is not None else available_at
        return f"{spec.name} is busy with {status} right now and may respond after {when_text}."
    return f"{spec.name} is busy with {status} right now."


def _next_available_at(spec: CharacterIdentitySpec, *, now: datetime, obligations: Sequence[CharacterObligation]) -> str | None:
    probe = now
    for _ in range(1, 7 * 24 * 4 + 1):
        probe = probe + timedelta(minutes=15)
        ev = evaluate_character(spec, now=probe, obligations=obligations, include_next_available=False)
        if ev.availability in {"available", "limited"}:
            return ev.at
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_character(
    character: CharacterIdentitySpec | dict[str, Any],
    *,
    now: datetime | str | None = None,
    obligations: Sequence[CharacterObligation | dict[str, Any]] | None = None,
    include_next_available: bool = True,
) -> CharacterEvaluation:
    spec = CharacterIdentitySpec.coerce(character)
    local_now = _coerce_now(now, spec.timezone)
    normalized_obligations = tuple(
        item if isinstance(item, CharacterObligation) else CharacterObligation.coerce(item, timezone_name=spec.timezone)
        for item in (obligations or [])
    )
    status, availability, interruption_tolerance, reply_mode, courtesy_message, active_block = _base_schedule_state(spec.schedule, local_now)
    reasons = [f"schedule:{status}"]
    active_obligation = _select_active_obligation(normalized_obligations, now=local_now, timezone_name=spec.timezone)
    if active_obligation is not None:
        status = active_obligation.status or status
        availability = active_obligation.availability or availability
        interruption_tolerance = active_obligation.interruption_tolerance
        if active_obligation.reply_mode and active_obligation.reply_mode != "auto":
            reply_mode = active_obligation.reply_mode
        if active_obligation.courtesy_message:
            courtesy_message = active_obligation.courtesy_message
        reasons.append(f"obligation:{active_obligation.label or active_obligation.status}")
    available_at = None
    if include_next_available and availability in {"busy", "offline"}:
        available_at = _next_available_at(spec, now=local_now, obligations=normalized_obligations)
    return CharacterEvaluation(
        at=local_now.isoformat(), timezone=spec.timezone, status=status, availability=availability,
        interruption_tolerance=_clamp_unit_interval(interruption_tolerance, 0.0),
        reply_mode=_coerce_reply_mode(reply_mode, fallback="auto"),
        courtesy_message=courtesy_message or None, available_at=available_at, reasons=tuple(reasons),
        active_schedule_block=active_block.to_dict() if active_block is not None else None,
        active_obligation=active_obligation.to_dict() if active_obligation is not None else None,
    )


def decide_character_response(
    character: CharacterIdentitySpec | dict[str, Any],
    *,
    evaluation: CharacterEvaluation | None = None,
    now: datetime | str | None = None,
    obligations: Sequence[CharacterObligation | dict[str, Any]] | None = None,
) -> CharacterDecision:
    spec = CharacterIdentitySpec.coerce(character)
    current_evaluation = evaluation or evaluate_character(spec, now=now, obligations=obligations)
    explicit_mode = current_evaluation.reply_mode
    social_drive = (spec.talkativeness * 0.6) + (spec.politeness * 0.25) + ((1.0 - spec.autonomy) * 0.15)

    if explicit_mode != "auto":
        action, reason = explicit_mode, f"explicit:{explicit_mode}"
    elif current_evaluation.availability == "offline":
        action = "defer" if spec.politeness >= 0.65 else "ignore"
        reason = "offline_state"
    elif current_evaluation.availability == "busy":
        if current_evaluation.interruption_tolerance >= 0.75 and social_drive >= 0.5:
            action, reason = "reply", "interruptible_busy_state"
        elif spec.politeness >= 0.5:
            action, reason = "defer", "busy_but_polite"
        else:
            action, reason = "ignore", "busy_and_unavailable"
    elif current_evaluation.availability == "limited":
        if social_drive >= 0.45 or current_evaluation.interruption_tolerance >= 0.55 or spec.politeness >= 0.6:
            action, reason = "reply", "limited_but_responsive"
        else:
            action, reason = "defer", "limited_attention"
    else:
        if social_drive >= 0.3 or spec.politeness >= 0.4:
            action, reason = "reply", "available_and_willing"
        elif spec.autonomy >= 0.85 and spec.talkativeness <= 0.2:
            action, reason = "ignore", "autonomous_and_reserved"
        else:
            action, reason = "defer", "available_but_disinclined"

    courtesy_message = None
    if action == "defer":
        courtesy_message = _compose_courtesy_message(
            spec, availability=current_evaluation.availability, status=current_evaluation.status,
            available_at=current_evaluation.available_at, fallback_message=current_evaluation.courtesy_message or "",
        )
    return CharacterDecision(
        action=action, should_reply=action == "reply", courtesy_message=courtesy_message,
        reason=reason, evaluation=current_evaluation,
    )
