from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Literal


RuntimeEventType = Literal[
    "session.started",
    "run.started",
    "run.completed",
    "run.failed",
    "turn.started",
    "turn.completed",
    "model.started",
    "model.delta",
    "model.completed",
    "tool.started",
    "tool.delta",
    "tool.completed",
    "input.requested",
    "input.resolved",
]

Visibility = Literal["user", "debug", "internal"]

RUNTIME_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "session.started",
        "run.started",
        "run.completed",
        "run.failed",
        "turn.started",
        "turn.completed",
        "model.started",
        "model.delta",
        "model.completed",
        "tool.started",
        "tool.delta",
        "tool.completed",
        "input.requested",
        "input.resolved",
    }
)

VISIBILITIES: frozenset[str] = frozenset({"user", "debug", "internal"})


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_safe(inner)
            for key, inner in value.items()
            if isinstance(key, (str, int, float, bool))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


@dataclass(frozen=True)
class RuntimeEventLinks:
    parent_run_id: str | None = None
    parent_event_id: str | None = None
    caused_by_event_id: str | None = None
    tool_call_id: str | None = None
    input_request_id: str | None = None
    channel_id: str | None = None
    team_id: str | None = None
    plan_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "parent_run_id": self.parent_run_id,
            "parent_event_id": self.parent_event_id,
            "caused_by_event_id": self.caused_by_event_id,
            "tool_call_id": self.tool_call_id,
            "input_request_id": self.input_request_id,
            "channel_id": self.channel_id,
            "team_id": self.team_id,
            "plan_id": self.plan_id,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RuntimeEventLinks":
        if not isinstance(raw, dict):
            return cls()
        values: dict[str, str | None] = {}
        for key in cls().to_dict():
            value = raw.get(key)
            values[key] = value if isinstance(value, str) and value else None
        return cls(**values)


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    type: str
    timestamp: str
    session_id: str
    run_id: str
    agent_id: str
    turn_id: str | None = None
    links: RuntimeEventLinks = RuntimeEventLinks()
    visibility: Visibility = "user"
    payload: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    schema_version: str = "v3"

    def __post_init__(self) -> None:
        if self.schema_version != "v3":
            raise ValueError("schema_version must be 'v3'")
        if self.visibility not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {sorted(VISIBILITIES)}")
        if self.payload is None:
            object.__setattr__(self, "payload", {})
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "turn_id": self.turn_id,
            "links": self.links.to_dict(),
            "visibility": self.visibility,
            "payload": _json_safe(copy.deepcopy(self.payload or {})),
            "metadata": _json_safe(copy.deepcopy(self.metadata or {})),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, strict: bool = True) -> "RuntimeEvent":
        if not isinstance(raw, dict):
            raise TypeError("runtime event must be a dict")
        schema_version = raw.get("schema_version")
        if schema_version != "v3":
            raise ValueError("schema_version must be 'v3'")
        event_type = raw.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("type is required")
        if strict and event_type not in RUNTIME_EVENT_TYPES:
            raise ValueError(f"unknown event type: {event_type}")
        visibility = raw.get("visibility", "user")
        if visibility not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {sorted(VISIBILITIES)}")

        def _string_field(key: str, *, required: bool = True) -> str:
            value = raw.get(key)
            if isinstance(value, str):
                return value
            if required:
                raise ValueError(f"{key} is required")
            return ""

        turn_id = raw.get("turn_id")
        return cls(
            schema_version="v3",
            event_id=_string_field("event_id"),
            type=event_type,
            timestamp=_string_field("timestamp"),
            session_id=_string_field("session_id"),
            run_id=_string_field("run_id"),
            agent_id=_string_field("agent_id"),
            turn_id=turn_id if isinstance(turn_id, str) and turn_id else None,
            links=RuntimeEventLinks.from_dict(raw.get("links")),
            visibility=visibility,  # type: ignore[arg-type]
            payload=copy.deepcopy(raw.get("payload")) if isinstance(raw.get("payload"), dict) else {},
            metadata=copy.deepcopy(raw.get("metadata")) if isinstance(raw.get("metadata"), dict) else {},
        )
