"""Typed execution identity shared without importing an agent package."""

from __future__ import annotations

from enum import StrEnum


class MemoryV2RunRole(StrEnum):
    """The explicit relationship between one agent call and its root run."""

    ROOT = "root"
    SUBAGENT = "subagent"
    GRAPH_STEP = "graph_step"


__all__ = ["MemoryV2RunRole"]
