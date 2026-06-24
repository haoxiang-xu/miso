from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from ...base import BuiltinExecutionContext, BuiltinToolkit
from ..core import CoreToolkit


class WorkspaceToolkit(BuiltinToolkit):
    """Compatibility workspace toolkit with legacy workspace tool names."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ) -> None:
        super().__init__(workspace_root=workspace_root, workspace_roots=workspace_roots)
        self._inner = CoreToolkit(workspace_roots=self.workspace_roots)
        self._register_workspace_tools()

    def _register_workspace_tools(self) -> None:
        self.register(
            self.read_files,
            name="read_files",
            description="Read one or more files from the workspace.",
        )
        self.register(
            self.read_file,
            name="read_file",
            description="Read a single file from the workspace.",
        )
        self.register(
            self.read_lines,
            name="read_lines",
            description="Read a line range from a workspace file.",
        )
        self.register(
            self.search_text,
            name="search_text",
            description="Search text across files in the workspace.",
        )
        self.register(
            self.list_directories,
            name="list_directories",
            description="List files and directories under a workspace path.",
        )
        self.register(
            self.file_exists,
            name="file_exists",
            description="Check whether a workspace file exists.",
        )
        self.register(
            self.write_file,
            name="write_file",
            description="Create or overwrite a workspace file.",
            requires_confirmation=True,
        )
        self.register(
            self.delete_file,
            name="delete_file",
            description="Delete a workspace file.",
            requires_confirmation=True,
        )
        self.register(
            self.move_file,
            name="move_file",
            description="Move or rename a workspace file.",
            requires_confirmation=True,
        )
        self.register(
            self.terminal_exec,
            name="terminal_exec",
            description="Execute a shell command inside the workspace.",
            requires_confirmation=True,
        )
        self.register(
            self.pin_file_context,
            name="pin_file_context",
            description="Pin file context for the current session.",
        )
        self.register(
            self.unpin_file_context,
            name="unpin_file_context",
            description="Remove pinned file context from the current session.",
        )

    def push_execution_context(self, context: BuiltinExecutionContext) -> None:
        super().push_execution_context(context)
        self._inner.push_execution_context(context)

    def pop_execution_context(self) -> None:
        super().pop_execution_context()
        self._inner.pop_execution_context()

    def shutdown(self) -> None:
        self._inner.shutdown()

    def _resolve_path(self, path: str) -> Path:
        return self._inner._resolve_workspace_path(path)

    def read_file(
        self,
        path: str,
        offset: int = 0,
        limit: int = 2000,
        max_chars: int = 30000,
        ast_mode: str = "auto",
    ) -> dict[str, Any]:
        result = self._inner.read(path=path, offset=offset, limit=limit)
        return self._limit_content(result, max_chars=max_chars)

    def read_files(
        self,
        paths: list[str] | None = None,
        path: str | None = None,
        offset: int = 0,
        limit: int = 2000,
        max_chars: int = 30000,
        ast_mode: str = "auto",
    ) -> dict[str, Any]:
        requested_paths = list(paths or [])
        if path:
            requested_paths.append(path)
        if not requested_paths:
            return {"items": [], "count": 0}

        items = [
            self.read_file(
                path=item_path,
                offset=offset,
                limit=limit,
                max_chars=max_chars,
                ast_mode=ast_mode,
            )
            for item_path in requested_paths
        ]
        return {"items": items, "count": len(items)}

    def read_lines(
        self,
        path: str,
        start: int = 1,
        end: int | None = None,
        max_chars: int = 30000,
    ) -> dict[str, Any]:
        safe_start = max(1, int(start or 1))
        safe_end = max(safe_start, int(end or safe_start))
        return self.read_file(
            path=path,
            offset=safe_start - 1,
            limit=(safe_end - safe_start) + 1,
            max_chars=max_chars,
            ast_mode="never",
        )

    def search_text(
        self,
        query: str,
        path: str = ".",
        file_glob: str | None = None,
        max_results: int = 50,
        offset: int = 0,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        return self._inner.grep(
            pattern=query,
            path=path,
            glob=file_glob,
            output_mode="content",
            head_limit=max_results,
            offset=offset,
            case_sensitive=case_sensitive,
        )

    def list_directories(
        self,
        path: str = ".",
        include_files: bool = True,
        max_results: int = 200,
    ) -> dict[str, Any]:
        try:
            target = self._resolve_path(path)
        except Exception as exc:
            return {"error": str(exc), "path": path}
        if not target.exists():
            return {"error": f"path not found: {path}"}
        if not target.is_dir():
            return {"error": f"not a directory: {path}"}

        items: list[dict[str, Any]] = []
        for child in sorted(target.iterdir(), key=lambda item: item.name):
            if child.is_dir() or include_files:
                items.append(
                    {
                        "path": self._relative_path(child),
                        "name": child.name,
                        "type": "directory" if child.is_dir() else "file",
                    }
                )
            if len(items) >= max_results:
                break

        return {
            "path": path,
            "items": items,
            "truncated": len(items) >= max_results,
        }

    def file_exists(self, path: str) -> dict[str, Any]:
        try:
            resolved = self._resolve_path(path)
        except Exception as exc:
            return {"error": str(exc), "path": path}
        return {"path": path, "exists": resolved.exists(), "is_file": resolved.is_file()}

    def write_file(
        self,
        path: str,
        content: str,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        try:
            target = self._resolve_path(path)
        except Exception as exc:
            return {"error": str(exc), "path": path}
        if target.exists() and not overwrite:
            return {"error": f"destination already exists: {path}", "path": str(target)}
        return self._inner.write(path=str(target), content=content)

    def delete_file(self, path: str, missing_ok: bool = False) -> dict[str, Any]:
        try:
            target = self._resolve_path(path)
        except Exception as exc:
            return {"error": str(exc), "path": path}
        if not target.exists():
            if missing_ok:
                return {"path": path, "deleted": False, "missing": True}
            return {"error": f"file not found: {path}"}
        if not target.is_file():
            return {"error": f"not a file: {path}"}

        old_text = target.read_text(encoding="utf-8", errors="replace")
        target.unlink()
        self._inner._record_workspace_change(
            target=target,
            before_text=old_text,
            after_text=None,
            operation="deleted",
            tool_name="delete_file",
        )
        return {"path": path, "deleted": True}

    def move_file(
        self,
        source_path: str,
        destination_path: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        try:
            source = self._resolve_path(source_path)
            destination = self._resolve_path(destination_path)
        except Exception as exc:
            return {"error": str(exc)}
        if not source.exists():
            return {"error": f"file not found: {source_path}"}
        if not source.is_file():
            return {"error": f"not a file: {source_path}"}
        if destination.exists() and not overwrite:
            return {"error": f"destination already exists: {destination_path}"}

        old_text = source.read_text(encoding="utf-8", errors="replace")
        previous_destination_text = (
            destination.read_text(encoding="utf-8", errors="replace")
            if destination.exists() and destination.is_file()
            else None
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        self._inner._record_workspace_change(
            target=source,
            before_text=old_text,
            after_text=None,
            operation="deleted",
            tool_name="move_file",
        )
        self._inner._record_workspace_change(
            target=destination,
            before_text=previous_destination_text,
            after_text=old_text,
            operation="modified" if previous_destination_text is not None else "created",
            tool_name="move_file",
        )
        return {
            "source_path": source_path,
            "destination_path": destination_path,
            "moved": True,
        }

    def terminal_exec(
        self,
        command: str,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        return self._inner.shell(
            action="run",
            command=command,
            cwd=cwd,
            timeout_ms=max(1, int(timeout_seconds or 30)) * 1000,
            max_output_chars=max_output_chars,
        )

    def pin_file_context(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
        reason: str | None = None,
        start_with: str | None = None,
        end_with: str | None = None,
    ) -> dict[str, Any]:
        context = self.current_execution_context
        if context is None:
            return {"error": "workspace pin context unavailable"}

        try:
            pins_module = importlib.import_module("unchain.workspace.pins")
        except Exception as exc:
            return {"error": f"workspace pin helpers unavailable: {exc}"}

        try:
            target = self._resolve_path(path)
        except Exception as exc:
            return {"error": str(exc), "path": path}
        if not target.exists() or not target.is_file():
            return {"error": f"file not found: {path}"}

        content = target.read_text(encoding="utf-8", errors="replace")
        if start is None and end is None and len(content) > pins_module.MAX_FULL_FILE_PIN_CHARS:
            return {
                "error": "file too large for full-file pin",
                "path": path,
                "max_chars": pins_module.MAX_FULL_FILE_PIN_CHARS,
            }

        state, pins = pins_module.load_workspace_pins(context.session_store, context.session_id)
        if len(pins) >= pins_module.MAX_SESSION_PIN_COUNT:
            return {
                "error": "too many pinned file contexts",
                "max_count": pins_module.MAX_SESSION_PIN_COUNT,
            }

        candidate = pins_module.build_pin_record(
            path=target,
            lines=content.splitlines(keepends=True),
            start=start,
            end=end,
            reason=reason,
            start_with=start_with,
            end_with=end_with,
        )
        duplicate = pins_module.find_duplicate_pin(pins, candidate)
        if duplicate is not None:
            return {"pin_id": duplicate["pin_id"], "duplicate": True, "pin": duplicate}

        pins.append(candidate)
        pins_module.save_workspace_pins(context.session_store, context.session_id, state, pins)
        return {"pin_id": candidate["pin_id"], "duplicate": False, "pin": candidate}

    def unpin_file_context(
        self,
        pin_id: str | None = None,
        path: str | None = None,
        start: int | None = None,
        end: int | None = None,
        all: bool = False,
    ) -> dict[str, Any]:
        context = self.current_execution_context
        if context is None:
            return {"error": "workspace pin context unavailable"}

        try:
            pins_module = importlib.import_module("unchain.workspace.pins")
        except Exception as exc:
            return {"error": f"workspace pin helpers unavailable: {exc}"}

        state, pins = pins_module.load_workspace_pins(context.session_store, context.session_id)
        remaining, removed_pin_ids = pins_module.remove_pins(
            pins,
            pin_id=pin_id,
            path=path,
            start=start,
            end=end,
            remove_all=all,
        )
        pins_module.save_workspace_pins(context.session_store, context.session_id, state, remaining)
        return {
            "removed_pin_ids": removed_pin_ids,
            "removed": len(removed_pin_ids),
            "remaining": len(remaining),
        }

    def _relative_path(self, target: Path) -> str:
        for root in self.workspace_roots:
            try:
                return str(target.relative_to(root))
            except ValueError:
                continue
        return str(target)

    @staticmethod
    def _limit_content(result: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
        content = result.get("content")
        if not isinstance(content, str):
            return result
        try:
            limit = max(1, int(max_chars))
        except (TypeError, ValueError):
            limit = 30000
        if len(content) <= limit:
            return result
        limited = dict(result)
        limited["content"] = content[:limit]
        limited["truncated"] = True
        limited["returned_chars"] = limit
        return limited


class DevToolkit(WorkspaceToolkit):
    """Legacy compatibility name for the workspace toolkit."""


__all__ = ["DevToolkit", "WorkspaceToolkit"]
