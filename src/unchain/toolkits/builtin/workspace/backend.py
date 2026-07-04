from __future__ import annotations

from pathlib import Path
from typing import Any

from ...base import BuiltinExecutionContext
from ..core import CoreToolkit
from ..core.coding_backend import CoreCodingBackend


class WorkspaceToolkitBackend(CoreCodingBackend):
    """Legacy workspace backend adapter over the core coding backend."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ) -> None:
        self._core = CoreToolkit(workspace_root=workspace_root, workspace_roots=workspace_roots)
        super().__init__(workspace_roots=self._core.workspace_roots)

    def push_execution_context(self, context: BuiltinExecutionContext) -> None:
        super().push_execution_context(context)
        self._core.push_execution_context(context)

    def pop_execution_context(self) -> None:
        super().pop_execution_context()
        self._core.pop_execution_context()

    def shutdown(self) -> None:
        super().shutdown()
        self._core.shutdown()

    def lsp(
        self,
        operation: str,
        file_path: str,
        line: int | None = None,
        character: int | None = None,
        query: str = "",
    ) -> dict[str, Any]:
        return self._core.lsp(
            operation=operation,
            file_path=file_path,
            line=line,
            character=character,
            query=query,
        )

    def _resolve_write_confirmation(self, arguments: dict[str, Any], execution_context: Any) -> Any:
        return self._core._resolve_write_confirmation(arguments, execution_context)

    def _resolve_edit_confirmation(self, arguments: dict[str, Any], execution_context: Any) -> Any:
        return self._core._resolve_edit_confirmation(arguments, execution_context)

    def _compact_read_args(self, payload: Any, context: Any) -> Any:
        return self._core._compact_read_args(payload, context)

    def _compact_read_result(self, payload: Any, context: Any) -> Any:
        return self._core._compact_read_result(payload, context)

    def _compact_write_args(self, payload: Any, context: Any) -> Any:
        return self._core._compact_write_args(payload, context)

    def _compact_edit_args(self, payload: Any, context: Any) -> Any:
        return self._core._compact_edit_args(payload, context)

    def _compact_mutation_result(self, payload: Any, context: Any) -> Any:
        return self._core._compact_mutation_result(payload, context)

    def _compact_glob_result(self, payload: Any, context: Any) -> Any:
        return self._core._compact_glob_result(payload, context)

    def _compact_grep_result(self, payload: Any, context: Any) -> Any:
        return self._core._compact_grep_result(payload, context)

    def _compact_shell_args(self, payload: Any, context: Any) -> Any:
        return self._core._compact_shell_args(payload, context)

    def _compact_shell_result(self, payload: Any, context: Any) -> Any:
        return self._core._compact_shell_result(payload, context)

    def _compact_lsp_args(self, payload: Any, context: Any) -> Any:
        return self._core._compact_lsp_args(payload, context)

    def _compact_lsp_result(self, payload: Any, context: Any) -> Any:
        return self._core._compact_lsp_result(payload, context)


__all__ = ["WorkspaceToolkitBackend"]
