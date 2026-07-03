from __future__ import annotations

from pathlib import Path
from typing import Any

from ...base import BuiltinExecutionContext
from ..core import CoreToolkit


class WorkspaceToolkitBackend:
    """Private transition backend for workspace and coding behavior."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ) -> None:
        self._core = CoreToolkit(workspace_root=workspace_root, workspace_roots=workspace_roots)

    @property
    def workspace_roots(self) -> list[Path]:
        return self._core.workspace_roots

    @property
    def workspace_root(self) -> Path:
        return self._core.workspace_root

    def push_execution_context(self, context: BuiltinExecutionContext) -> None:
        self._core.push_execution_context(context)

    def pop_execution_context(self) -> None:
        self._core.pop_execution_context()

    def shutdown(self) -> None:
        self._core.shutdown()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._core, name)


__all__ = ["WorkspaceToolkitBackend"]
