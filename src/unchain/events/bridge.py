from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .normalizer import RuntimeEventDraft, RuntimeEventNormalizerContext, normalize_raw_event
from .types import RuntimeEvent, RuntimeEventLinks


class RuntimeEventBridge:
    def __init__(
        self,
        *,
        session_id: str,
        root_run_id: str | None = None,
        root_agent_id: str = "developer",
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        trace_level: str = "minimal",
    ) -> None:
        self.session_id = str(session_id or "")
        self.root_run_id = str(root_run_id or "")
        self.root_agent_id = str(root_agent_id or "developer")
        self.trace_level = str(trace_level or "minimal")
        self._id_factory = id_factory or (lambda: f"evt_{uuid.uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._dropped_events: list[dict[str, Any]] = []

    def _timestamp(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _event_from_draft(self, draft: RuntimeEventDraft) -> RuntimeEvent:
        return RuntimeEvent(
            event_id=str(self._id_factory()),
            type=draft.type,
            timestamp=self._timestamp(),
            session_id=self.session_id,
            run_id=draft.run_id,
            agent_id=draft.agent_id,
            turn_id=draft.turn_id,
            links=draft.links,
            visibility=draft.visibility,
            payload=copy.deepcopy(draft.payload),
            metadata=copy.deepcopy(draft.metadata),
        )

    def emit_session_started(self, payload: dict[str, Any] | None = None) -> RuntimeEvent:
        return RuntimeEvent(
            event_id=str(self._id_factory()),
            type="session.started",
            timestamp=self._timestamp(),
            session_id=self.session_id,
            run_id=self.root_run_id,
            agent_id=self.root_agent_id,
            turn_id=None,
            links=RuntimeEventLinks(),
            visibility="debug",
            payload=copy.deepcopy(payload or {}),
            metadata={},
        )

    def normalize(self, raw_event: dict[str, Any]) -> list[RuntimeEvent]:
        if isinstance(raw_event, dict) and not self.root_run_id:
            run_id = raw_event.get("run_id")
            if isinstance(run_id, str) and run_id:
                self.root_run_id = run_id
        context = RuntimeEventNormalizerContext(
            session_id=self.session_id,
            root_run_id=self.root_run_id,
            root_agent_id=self.root_agent_id,
        )
        drafts = normalize_raw_event(raw_event, context=context)
        if not drafts:
            self._record_dropped(raw_event)
            return []
        return [self._event_from_draft(draft) for draft in drafts]

    def emit_transport_failure(
        self,
        message: str,
        *,
        code: str = "stream_failed",
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_id=str(self._id_factory()),
            type="run.failed",
            timestamp=self._timestamp(),
            session_id=self.session_id,
            run_id=self.root_run_id,
            agent_id=self.root_agent_id,
            turn_id=None,
            links=RuntimeEventLinks(),
            visibility="user",
            payload={
                "status": "failed",
                "error": {
                    "code": code or "stream_failed",
                    "message": message or "Stream failed",
                },
                "recoverable": False,
            },
            metadata={"source": "transport"},
        )

    def diagnostics(self) -> dict[str, Any]:
        return {
            "dropped_event_count": len(self._dropped_events),
            "dropped_events": copy.deepcopy(self._dropped_events),
        }

    def _record_dropped(self, raw_event: Any) -> None:
        if isinstance(raw_event, dict):
            raw_type = raw_event.get("type")
            self._dropped_events.append(
                {
                    "type": raw_type if isinstance(raw_type, str) else "",
                    "event": copy.deepcopy(raw_event),
                }
            )
        else:
            self._dropped_events.append({"type": "", "event": str(raw_event)})
