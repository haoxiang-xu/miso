from __future__ import annotations

import copy
import uuid
from typing import Any


class ProviderReplayHandle(dict[str, str]):
    """JSON-safe process-local capability carrying private replay state."""

    __slots__ = ("_frame",)

    def __init__(
        self,
        values: Any = (),
        *,
        replay_frame: dict[str, Any] | None = None,
        handle_id: str | None = None,
    ) -> None:
        if isinstance(replay_frame, dict):
            super().__init__(
                id=handle_id or "provider_replay_" + uuid.uuid4().hex,
                scope="process",
            )
            self._frame: dict[str, Any] | None = copy.deepcopy(replay_frame)
            return
        super().__init__(values)
        self._frame = None

    def __deepcopy__(self, memo: dict[int, Any]) -> ProviderReplayHandle:
        existing = memo.get(id(self))
        if isinstance(existing, ProviderReplayHandle):
            return existing
        clone = (
            ProviderReplayHandle(
                replay_frame=self._frame,
                handle_id=self["id"],
            )
            if isinstance(self._frame, dict)
            else ProviderReplayHandle(self.items())
        )
        memo[id(self)] = clone
        return clone

    def resolve(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._frame) if isinstance(self._frame, dict) else None

    def __reduce_ex__(self, protocol: int) -> tuple[Any, tuple[dict[str, str]]]:
        del protocol
        return (ProviderReplayHandle, (dict(self),))


def store_provider_replay_handle(
    frame: dict[str, Any] | None,
) -> ProviderReplayHandle | None:
    if not isinstance(frame, dict):
        return None
    return ProviderReplayHandle(replay_frame=frame)


def load_provider_replay_handle(handle: Any) -> dict[str, Any] | None:
    if not isinstance(handle, ProviderReplayHandle):
        return None
    return handle.resolve()


__all__ = [
    "ProviderReplayHandle",
    "load_provider_replay_handle",
    "store_provider_replay_handle",
]
