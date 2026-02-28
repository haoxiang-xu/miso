from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import builtin_toolkit
from .python_workspace_toolkit import python_workspace_toolkit


def build_builtin_toolkit(
    *,
    workspace_root: str | Path | None = None,
    include_python_runtime: bool = True,
    include_terminal_runtime: bool = True,
    terminal_strict_mode: bool = True,
) -> python_workspace_toolkit:
    """Build a python_workspace_toolkit (backward-compatible helper)."""
    return python_workspace_toolkit(
        workspace_root=workspace_root,
        include_python_runtime=include_python_runtime,
        include_terminal_runtime=include_terminal_runtime,
        terminal_strict_mode=terminal_strict_mode,
    )


__all__ = [
    "builtin_toolkit",
    "build_builtin_toolkit",
    "python_workspace_toolkit",
]
