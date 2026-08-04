from __future__ import annotations

from abc import ABC, abstractmethod

from .models import (
    AttemptRef,
    EventCursor,
    JournalAppendRequest,
    JournalAppendResult,
    JournalPage,
    ToolExecutionReceiptLookup,
    _required_text,
)
from .snapshot import JournalSnapshot


class JournalRepositoryError(RuntimeError):
    """Base error for an execution-bound journal capability."""


class JournalConflictError(JournalRepositoryError):
    """An idempotency key or optimistic write precondition conflicted."""


class JournalScopeError(JournalRepositoryError):
    """A record did not belong to the capability's bound execution."""


class BoundExecutionJournal(ABC):
    """Journal capability constructed for exactly one execution."""

    def __init__(self, execution_id: str) -> None:
        self._execution_id = _required_text(
            execution_id,
            "execution_id",
            identifier=True,
        )

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @abstractmethod
    def append(self, *, request: JournalAppendRequest) -> JournalAppendResult:
        """Durably append or idempotently replay one semantic event."""

    @abstractmethod
    def read(self, *, after: EventCursor | None = None, limit: int = 100) -> JournalPage:
        """Read integrity-verified persisted events after an optional cursor."""

    @abstractmethod
    def capture_snapshot(
        self,
        *,
        max_events: int = 10_000,
        max_bytes: int = 32 * 1024 * 1024,
    ) -> JournalSnapshot:
        """Atomically capture a bounded execution high-water snapshot."""


class BoundToolReceiptIndex(BoundExecutionJournal):
    """Execution-bound journal with an exact indexed tool receipt lookup."""

    @abstractmethod
    def lookup_tool_execution_receipts(
        self,
        *,
        attempt: AttemptRef,
        call_id: str,
    ) -> ToolExecutionReceiptLookup:
        """Atomically return the exhaustive receipt set for one tool call."""


__all__ = [
    "BoundExecutionJournal",
    "BoundToolReceiptIndex",
    "JournalConflictError",
    "JournalRepositoryError",
    "JournalScopeError",
]
