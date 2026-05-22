"""Color helpers for terminal rendering.

We don't know the user's tool names in advance, so we hash them into a small
palette. Hash stability across processes doesn't matter — we just need
*within-run* stability so a tool keeps its color between calls. Users who
want exact control pass an explicit ``tool_colors`` mapping.
"""

from __future__ import annotations

import hashlib

# Ordered for visual contrast, not alphabetical.
_TOOL_COLOR_POOL: tuple[str, ...] = (
    "blue",
    "green",
    "yellow",
    "magenta",
    "cyan",
    "red",
)

DEFAULT_TOOL_COLOR = "white"
DEFAULT_STREAM_COLOR = "white"


def color_for_tool(name: str, *, overrides: dict[str, str] | None = None) -> str:
    """Pick a color for a tool name. Explicit overrides win over the hash pool."""

    if overrides and name in overrides:
        return overrides[name]
    if not name:
        return DEFAULT_TOOL_COLOR
    # md5 keeps the mapping deterministic across Python processes, unlike
    # builtin hash() which is randomized by PYTHONHASHSEED.
    digest = hashlib.md5(name.encode("utf-8")).digest()
    return _TOOL_COLOR_POOL[digest[0] % len(_TOOL_COLOR_POOL)]


__all__ = ["color_for_tool", "DEFAULT_TOOL_COLOR", "DEFAULT_STREAM_COLOR"]
