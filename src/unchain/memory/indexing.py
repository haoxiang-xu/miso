from __future__ import annotations

from typing import Any

from .manager import _collect_complete_turns_for_vector_index


def collect_complete_turns_for_vector_index(
    messages: list[dict[str, Any]],
    *,
    start_index: int = 0,
) -> tuple[list[str], list[dict[str, Any]], int, int]:
    return _collect_complete_turns_for_vector_index(
        messages,
        start_index=start_index,
    )


__all__ = ["collect_complete_turns_for_vector_index"]
