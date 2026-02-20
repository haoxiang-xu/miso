from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..tool import toolkit


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


__all__ = ["builtin_toolkit"]
