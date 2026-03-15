from __future__ import annotations

from pathlib import Path

from .base import builtin_toolkit
from .terminal_toolkit import terminal_toolkit
from .external_api_toolkit import external_api_toolkit
from .workspace_toolkit import workspace_toolkit


def build_builtin_toolkit(
    *,
    workspace_root: str | Path | None = None,
) -> workspace_toolkit:
    """Build a workspace_toolkit."""
    return workspace_toolkit(workspace_root=workspace_root)


__all__ = [
    "builtin_toolkit",
    "build_builtin_toolkit",
    "workspace_toolkit",
    "terminal_toolkit",
    "external_api_toolkit",
]
