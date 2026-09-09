from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping

from unchain.journal.models import (
    ContextBuildStatus,
    ModelValidationError,
    ResourceRef,
    _record_data,
)

from .models import (
    MAX_PINNED_TASK_STATE_BYTES,
    MAX_PINNED_TASK_STATE_ITEMS,
    ContextTaskStateUnavailable,
    PinnedTaskState,
)


MAX_CONTEXT_TASK_STATE_ITEMS = MAX_PINNED_TASK_STATE_ITEMS
MAX_CONTEXT_TASK_STATE_BYTES = MAX_PINNED_TASK_STATE_BYTES
TASK_STATE_LIMITS_EXCEEDED = "task_state_limits_exceeded"


def _measure(state: PinnedTaskState) -> tuple[int, int]:
    item_count = sum(
        len(getattr(state, field_name))
        for field_name in (
            "success_criteria",
            "constraints",
            "confirmed_decisions",
            "open_questions",
            "active_plan",
            "artifact_refs",
            "memory_refs",
            "source_event_refs",
        )
    )
    content_bytes = len(
        json.dumps(
            state.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    return item_count, content_bytes


@dataclass(frozen=True)
class ContextTaskStateReadOutcome:
    """Provider-neutral result of reading task state for compilation."""

    SCHEMA: ClassVar[str] = "unchain.context_task_state_read_outcome.v1"

    capture_quality: ContextBuildStatus
    state: PinnedTaskState | None = None
    unavailable: ContextTaskStateUnavailable | None = None

    def __post_init__(self) -> None:
        try:
            quality = ContextBuildStatus(self.capture_quality)
        except ValueError as exc:
            raise ModelValidationError("invalid task-state capture quality") from exc
        object.__setattr__(self, "capture_quality", quality)
        if self.state is not None and not isinstance(self.state, PinnedTaskState):
            object.__setattr__(self, "state", PinnedTaskState.from_dict(self.state))
        if self.unavailable is not None and not isinstance(
            self.unavailable,
            ContextTaskStateUnavailable,
        ):
            object.__setattr__(
                self,
                "unavailable",
                ContextTaskStateUnavailable.from_dict(self.unavailable),
            )
        if self.state is not None and self.unavailable is not None:
            raise ModelValidationError(
                "task-state read cannot contain content and an unavailable marker"
            )
        if self.unavailable is not None:
            if quality is not ContextBuildStatus.UNAVAILABLE:
                raise ModelValidationError(
                    "unavailable task state requires unavailable capture quality"
                )
        elif quality is not ContextBuildStatus.COMPLETE:
            raise ModelValidationError(
                "available or absent task state requires complete capture quality"
            )

    @classmethod
    def from_state(
        cls,
        state: PinnedTaskState | None,
    ) -> ContextTaskStateReadOutcome:
        if state is None:
            return cls(capture_quality=ContextBuildStatus.COMPLETE)
        if not isinstance(state, PinnedTaskState):
            state = PinnedTaskState.from_dict(state)
        item_count, content_bytes = _measure(state)
        if (
            item_count > MAX_CONTEXT_TASK_STATE_ITEMS
            or content_bytes > MAX_CONTEXT_TASK_STATE_BYTES
        ):
            return cls(
                capture_quality=ContextBuildStatus.UNAVAILABLE,
                unavailable=ContextTaskStateUnavailable(
                    state_ref=ResourceRef(
                        "task_state",
                        state.state_id,
                        state.revision,
                    ),
                    item_count=item_count,
                    content_bytes=content_bytes,
                    reason=TASK_STATE_LIMITS_EXCEEDED,
                ),
            )
        return cls(
            capture_quality=ContextBuildStatus.COMPLETE,
            state=state,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "capture_quality": self.capture_quality.value,
            "state": self.state.to_dict() if self.state is not None else None,
            "unavailable": (
                self.unavailable.to_dict()
                if self.unavailable is not None
                else None
            ),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> ContextTaskStateReadOutcome:
        raw = _record_data(
            value,
            schema=cls.SCHEMA,
            required=frozenset({"capture_quality", "state", "unavailable"}),
        )
        return cls(
            capture_quality=raw["capture_quality"],
            state=(
                PinnedTaskState.from_dict(raw["state"])
                if raw["state"] is not None
                else None
            ),
            unavailable=(
                ContextTaskStateUnavailable.from_dict(raw["unavailable"])
                if raw["unavailable"] is not None
                else None
            ),
        )


__all__ = [
    "ContextTaskStateReadOutcome",
    "ContextTaskStateUnavailable",
    "MAX_CONTEXT_TASK_STATE_BYTES",
    "MAX_CONTEXT_TASK_STATE_ITEMS",
    "TASK_STATE_LIMITS_EXCEEDED",
]
