from __future__ import annotations

import os
from pathlib import Path

from ..tools.models import ToolExecutionContext
from ..tools.toolkit import Toolkit
from ..workspace.pins import WorkspacePinExecutionContext


BuiltinExecutionContext = ToolExecutionContext | WorkspacePinExecutionContext


class BuiltinToolkit(Toolkit):
    """Base class for builtin toolkits that operate within a workspace directory.

    Subclasses should call ``super().__init__(...)`` and then register their
    tools via ``self.register`` / ``self.register_many``.

    Parameters
    ----------
    workspace_root:
        Root directory the toolkit is allowed to access.  Defaults to the
        current working directory when *None*.
    workspace_roots:
        Multiple root directories.  When provided, takes precedence over
        *workspace_root*.  Paths are validated against **any** listed root.
    """

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ):
        super().__init__()
        if workspace_roots:
            self.workspace_roots: list[Path] = [Path(r).resolve() for r in workspace_roots]
        elif workspace_root:
            self.workspace_roots = [Path(workspace_root).resolve()]
        else:
            self.workspace_roots = [Path(os.getcwd()).resolve()]
        # Backward compat: workspace_root always points to the first root.
        self.workspace_root: Path = self.workspace_roots[0]
        self._execution_context_stack: list[BuiltinExecutionContext] = []

    # ── shared path helper ─────────────────────────────────────────────────

    def _resolve_workspace_path(self, path: str) -> Path:
        """Resolve *path* relative to the first workspace root and verify it
        stays inside **any** of the registered roots."""
        path_obj = Path(path)
        if path_obj.is_absolute():
            resolved = path_obj.resolve()
        else:
            resolved = (self.workspace_root / path_obj).resolve()

        for root in self.workspace_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue

        raise ValueError("path is outside all workspace roots")

    def push_execution_context(self, context: BuiltinExecutionContext) -> None:
        self._execution_context_stack.append(context)

    def pop_execution_context(self) -> None:
        if self._execution_context_stack:
            self._execution_context_stack.pop()

    @property
    def current_execution_context(self) -> BuiltinExecutionContext | None:
        if not self._execution_context_stack:
            return None
        return self._execution_context_stack[-1]


__all__ = ["BuiltinExecutionContext", "BuiltinToolkit"]
