from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..tool import toolkit
from ..workspace_pins import WorkspacePinExecutionContext


class builtin_toolkit(toolkit):
    """Base class for builtin toolkits that operate within a workspace directory.

    Subclasses should call ``super().__init__(...)`` and then register their
    tools via ``self.register`` / ``self.register_many``.

    Parameters
    ----------
    workspace_root:
        Root directory the toolkit is allowed to access.  Defaults to the
        current working directory when *None*.
    """

    def __init__(self, *, workspace_root: str | Path | None = None):
        super().__init__()
        self.workspace_root: Path = Path(workspace_root or os.getcwd()).resolve()
        self._execution_context_stack: list[WorkspacePinExecutionContext] = []

    # ── shared path helper ─────────────────────────────────────────────────

    def _resolve_workspace_path(self, path: str) -> Path:
        """Resolve *path* relative to ``workspace_root`` and verify it stays inside."""
        path_obj = Path(path)
        if path_obj.is_absolute():
            resolved = path_obj.resolve()
        else:
            resolved = (self.workspace_root / path_obj).resolve()

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise ValueError("path is outside workspace_root")

        return resolved

    def push_execution_context(self, context: WorkspacePinExecutionContext) -> None:
        self._execution_context_stack.append(context)

    def pop_execution_context(self) -> None:
        if self._execution_context_stack:
            self._execution_context_stack.pop()

    @property
    def current_execution_context(self) -> WorkspacePinExecutionContext | None:
        if not self._execution_context_stack:
            return None
        return self._execution_context_stack[-1]


__all__ = ["builtin_toolkit"]
