from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


SessionConsistency = Literal["compare_and_swap", "best_effort"]


@dataclass(frozen=True)
class SessionSnapshot:
    """A session state read or write paired with its concurrency revision."""

    state: dict[str, Any]
    revision: int | None

    @property
    def revision_supported(self) -> bool:
        return self.revision is not None

    @property
    def consistency(self) -> SessionConsistency:
        return "compare_and_swap" if self.revision_supported else "best_effort"


class SessionRevisionConflictError(RuntimeError):
    """Raised when a stale worker tries to overwrite newer session state."""

    code = "session_revision_conflict"

    def __init__(
        self,
        *,
        session_id: str,
        expected_revision: int,
        actual_revision: int,
    ) -> None:
        self.session_id = session_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            "session state revision conflict: "
            f"session_id={session_id!r}, expected={expected_revision}, actual={actual_revision}"
        )


class SessionStoreCorruptionError(RuntimeError):
    """Raised when persisted session state cannot be decoded safely."""

    code = "session_store_corruption"

    def __init__(self, *, session_id: str, detail: str) -> None:
        self.session_id = session_id
        self.detail = detail
        super().__init__(f"session state is corrupted: session_id={session_id!r}, {detail}")


@runtime_checkable
class RevisionedSessionStore(Protocol):
    """Optional SessionStore extension providing atomic compare-and-swap."""

    def load_with_revision(self, session_id: str) -> SessionSnapshot:
        ...

    def save_if_revision(
        self,
        session_id: str,
        state: dict[str, Any],
        expected_revision: int,
    ) -> int:
        ...


def _validate_revision(revision: object, *, session_id: str) -> int:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise SessionStoreCorruptionError(
            session_id=session_id,
            detail="revision must be a non-negative integer",
        )
    return revision


def _validate_snapshot(snapshot: object, *, session_id: str) -> SessionSnapshot:
    if not isinstance(snapshot, SessionSnapshot):
        raise SessionStoreCorruptionError(
            session_id=session_id,
            detail="load_with_revision() returned an invalid snapshot",
        )
    if not isinstance(snapshot.state, dict):
        raise SessionStoreCorruptionError(
            session_id=session_id,
            detail="session state must be a JSON object",
        )
    revision = _validate_revision(snapshot.revision, session_id=session_id)
    return SessionSnapshot(state=copy.deepcopy(snapshot.state), revision=revision)


def _has_revision_capability(store: object) -> bool:
    has_revision_load = callable(getattr(store, "load_with_revision", None))
    has_revision_save = callable(getattr(store, "save_if_revision", None))
    if has_revision_load != has_revision_save:
        raise TypeError(
            "revisioned session store capability is incomplete: "
            "load_with_revision and save_if_revision must be implemented together"
        )
    return has_revision_load


def load_session_snapshot(store: object, session_id: str) -> SessionSnapshot:
    """Load a revisioned snapshot, falling back to legacy best-effort stores."""

    if _has_revision_capability(store):
        load_with_revision = getattr(store, "load_with_revision")
        return _validate_snapshot(load_with_revision(session_id), session_id=session_id)

    load = getattr(store, "load", None)
    if not callable(load):
        raise TypeError("session store must define load(session_id)")
    state = load(session_id)
    if not isinstance(state, dict):
        raise SessionStoreCorruptionError(
            session_id=session_id,
            detail="session state must be a JSON object",
        )
    return SessionSnapshot(state=copy.deepcopy(state), revision=None)


def save_session_snapshot(
    store: object,
    session_id: str,
    state: dict[str, Any],
    *,
    expected_revision: int | None,
) -> SessionSnapshot:
    """Persist state with CAS when available, otherwise use a legacy save."""

    if not isinstance(state, dict):
        raise TypeError("session state must be a dict")

    saved_state = copy.deepcopy(state)
    if _has_revision_capability(store):
        if expected_revision is None:
            raise ValueError(
                "expected_revision is required for a revisioned session store"
            )
        expected = _validate_revision(expected_revision, session_id=session_id)
        save_if_revision = getattr(store, "save_if_revision")
        next_revision = save_if_revision(session_id, saved_state, expected)
        revision = _validate_revision(next_revision, session_id=session_id)
        if revision <= expected:
            raise SessionStoreCorruptionError(
                session_id=session_id,
                detail="save_if_revision() did not advance the revision",
            )
        return SessionSnapshot(state=copy.deepcopy(saved_state), revision=revision)

    save = getattr(store, "save", None)
    if not callable(save):
        raise TypeError("session store must define save(session_id, state)")
    save(session_id, saved_state)
    return SessionSnapshot(state=copy.deepcopy(saved_state), revision=None)


__all__ = [
    "RevisionedSessionStore",
    "SessionConsistency",
    "SessionRevisionConflictError",
    "SessionSnapshot",
    "SessionStoreCorruptionError",
    "load_session_snapshot",
    "save_session_snapshot",
]
