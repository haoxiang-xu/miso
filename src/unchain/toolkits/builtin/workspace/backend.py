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

    def _resolve_workspace_path(self, path: str) -> Path:
        return self._core._resolve_workspace_path(path)

    def read(self, path: str, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        return self._core.read(path=path, offset=offset, limit=limit)

    def write(self, path: str, content: str) -> dict[str, Any]:
        return self._core.write(path=path, content=content)

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        return self._core.edit(
            path=path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    def glob(self, pattern: str, path: str | None = None) -> dict[str, Any]:
        return self._core.glob(pattern=pattern, path=path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        output_mode: str = "content",
        context: int = 0,
        head_limit: int = 50,
        offset: int = 0,
        case_sensitive: bool = True,
        multiline: bool = False,
    ) -> dict[str, Any]:
        return self._core.grep(
            pattern=pattern,
            path=path,
            glob=glob,
            output_mode=output_mode,
            context=context,
            head_limit=head_limit,
            offset=offset,
            case_sensitive=case_sensitive,
            multiline=multiline,
        )

    def shell(
        self,
        action: str,
        command: str = "",
        cwd: str | None = None,
        timeout_ms: int = 120000,
        run_in_background: bool = False,
        max_output_chars: int = 20000,
        yield_time_ms: int = 300,
        task_id: str = "",
    ) -> dict[str, Any]:
        return self._core.shell(
            action=action,
            command=command,
            cwd=cwd,
            timeout_ms=timeout_ms,
            run_in_background=run_in_background,
            max_output_chars=max_output_chars,
            yield_time_ms=yield_time_ms,
            task_id=task_id,
        )

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

    def _record_workspace_change(
        self,
        *,
        target: Path,
        before_text: str | None,
        after_text: str | None,
        operation: str,
        tool_name: str,
    ) -> None:
        self._core._record_workspace_change(
            target=target,
            before_text=before_text,
            after_text=after_text,
            operation=operation,
            tool_name=tool_name,
        )

    def _resolve_write_confirmation(self, arguments: dict[str, Any], execution_context: Any) -> Any:
        return self._core._resolve_write_confirmation(arguments, execution_context)

    def _resolve_edit_confirmation(self, arguments: dict[str, Any], execution_context: Any) -> Any:
        return self._core._resolve_edit_confirmation(arguments, execution_context)

    def _resolve_shell_confirmation(self, arguments: dict[str, Any], execution_context: Any) -> Any:
        return self._core._resolve_shell_confirmation(arguments, execution_context)

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
