from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from ...base import BuiltinToolkit
from ....tools.models import ToolHistoryOptimizationContext
from ....workspace.pins import (
    MAX_FULL_FILE_PIN_CHARS,
    MAX_SESSION_PIN_COUNT,
    build_pin_record,
    find_duplicate_pin,
    load_workspace_pins,
    remove_pins,
    save_workspace_pins,
)
from ....workspace.syntax import build_syntax_tree_payload


class WorkspaceToolkit(BuiltinToolkit):
    """Toolkit for accessing workspace files, directories, and line-level edits."""

    def __init__(self, *, workspace_root: str | Path | None = None):
        super().__init__(workspace_root=workspace_root)
        self._register_file_tools()
        self._register_directory_tools()
        self._register_line_tools()
        self._register_pin_tools()

    def _read_lines(self, path: Path) -> list[str]:
        """Read a file and return lines *with* line endings preserved."""
        return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

    def _write_lines(self, path: Path, lines: list[str]) -> int:
        """Write *lines* back and return bytes written."""
        content = "".join(lines)
        path.write_text(content, encoding="utf-8")
        return len(content.encode("utf-8"))

    def _validate_range(self, total: int, start: int, end: int) -> str | None:
        """Return an error string if *start*/*end* (1-based) are invalid, else None."""
        if start < 1:
            return "start must be >= 1"
        if end < start:
            return "end must be >= start"
        if start > total:
            return f"start ({start}) exceeds total lines ({total})"
        return None

    def _preview_text(self, text: str, preview_chars: int) -> str:
        if len(text) <= max(1, preview_chars * 2):
            return text
        head = text[:preview_chars]
        tail = text[-preview_chars:]
        omitted = len(text) - len(head) - len(tail)
        return f"{head}\n... <omitted {omitted} chars> ...\n{tail}"

    def _compact_text_blob(self, text: str, context: ToolHistoryOptimizationContext) -> dict[str, Any]:
        compacted = {
            "compacted": True,
            "chars": len(text),
            "lines": text.count("\n") + (0 if text.endswith("\n") else 1 if text else 0),
            "preview": self._preview_text(text, context.preview_chars),
        }
        if context.include_hash:
            compacted["digest"] = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
        return compacted

    def _require_pin_context(self) -> tuple[str, Any] | tuple[None, None]:
        context = self.current_execution_context
        if context is None or not context.session_id:
            return None, {
                "error": (
                    "pin_file_context and unpin_file_context require an active session_id; "
                    "retry inside a session-scoped run."
                )
            }
        return context.session_id, context.session_store

    def _register_file_tools(self) -> None:
        self.register(
            self.read_file,
            history_arguments_optimizer=self._compact_read_file_history_arguments,
            history_result_optimizer=self._compact_read_file_history_result,
        )
        self.register(
            self.read_file_ast,
            history_arguments_optimizer=self._compact_read_file_ast_history_arguments,
            history_result_optimizer=self._compact_read_file_ast_history_result,
        )
        self.register(
            self.write_file,
            history_arguments_optimizer=self._compact_write_like_history_arguments,
        )
        self.register(
            self.create_file,
            history_arguments_optimizer=self._compact_write_like_history_arguments,
        )
        self.register_many(
            self.delete_file,
            self.copy_file,
            self.move_file,
            self.file_exists,
        )

    def _compact_read_file_history_arguments(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        compacted: dict[str, Any] = {}
        for key in ("path", "max_chars", "start", "end"):
            if key in payload:
                compacted[key] = payload[key]
        if compacted != payload:
            compacted["compacted"] = True
        return compacted or {"compacted": True}

    def _compact_read_file_history_result(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        content = payload.get("content")
        if not isinstance(content, str):
            return payload
        if len(content) <= context.max_chars:
            return payload
        compacted = {
            "path": payload.get("path"),
            "total_lines": payload.get("total_lines"),
            "truncated": payload.get("truncated"),
            "content": self._preview_text(content, context.preview_chars),
            "compacted": True,
        }
        if context.include_hash:
            compacted["digest"] = hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()
        return compacted

    def _compact_write_like_history_arguments(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        content = payload.get("content")
        if not isinstance(content, str) or not content:
            return payload
        compacted = {
            "path": payload.get("path"),
            "compacted": True,
            "content": self._compact_text_blob(content, context),
        }
        if "append" in payload:
            compacted["append"] = payload.get("append")
        return compacted

    def _compact_read_file_ast_history_arguments(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        compacted: dict[str, Any] = {}
        for key in ("path", "language", "max_nodes"):
            if key in payload:
                compacted[key] = payload[key]
        if compacted != payload:
            compacted["compacted"] = True
        return compacted or {"compacted": True}

    def _ast_history_preview(self, node: Any, *, limit: int = 8) -> dict[str, Any]:
        if not isinstance(node, dict):
            return {"type": type(node).__name__}
        preview: dict[str, Any] = {"type": node.get("type")}
        if "text" in node:
            preview["text"] = node.get("text")
        children = node.get("children")
        if isinstance(children, list):
            preview["children"] = [
                {
                    "type": child.get("type"),
                    **({"field_name": child.get("field_name")} if child.get("field_name") else {}),
                }
                for child in children[:limit]
                if isinstance(child, dict)
            ]
            if len(children) > limit:
                preview["children_truncated"] = True
        return preview

    def _compact_read_file_ast_history_result(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict) or "ast" not in payload:
            return payload
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded) <= context.max_chars:
            return payload
        compacted = {
            "path": payload.get("path"),
            "language": payload.get("language"),
            "node_count": payload.get("node_count"),
            "returned_node_count": payload.get("returned_node_count"),
            "truncated": payload.get("truncated"),
            "ast": self._ast_history_preview(payload.get("ast")),
            "compacted": True,
        }
        if "error" in payload:
            compacted["error"] = payload.get("error")
        if context.include_hash:
            compacted["digest"] = hashlib.sha1(encoded.encode("utf-8", errors="replace")).hexdigest()
        return compacted

    def read_file(self, path: str, max_chars: int = 20000) -> dict[str, Any]:
        """Read entire UTF-8 text file from workspace.

        :param path: Relative or absolute path inside workspace.
        :param max_chars: Truncate content after this many characters.
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}
        if not target.is_file():
            return {"error": f"not a file: {target}"}

        content = target.read_text(encoding="utf-8", errors="replace")
        total_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]

        return {
            "path": str(target),
            "content": content,
            "total_lines": total_lines,
            "truncated": truncated,
        }

    def read_file_ast(
        self,
        path: str,
        language: str | None = None,
        max_nodes: int = 400,
    ) -> dict[str, Any]:
        """Parse a source file and return a JSON-safe syntax tree.

        :param path: Relative or absolute path inside workspace.
        :param language: Optional language override.
        :param max_nodes: Maximum AST nodes to serialize in the response.
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}
        if not target.is_file():
            return {"error": f"not a file: {target}"}
        return build_syntax_tree_payload(
            target,
            source_bytes=target.read_bytes(),
            language=language,
            max_nodes=max_nodes,
        )

    def write_file(self, path: str, content: str, append: bool = False) -> dict[str, Any]:
        """Write UTF-8 text file into workspace (overwrite or append).

        :param path: Relative or absolute path inside workspace.
        :param content: Text content to write.
        :param append: If True, append to existing content instead of overwriting.
        """
        target = self._resolve_workspace_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as fp:
            fp.write(content)

        return {
            "path": str(target),
            "bytes_written": len(content.encode("utf-8")),
            "append": append,
        }

    def create_file(self, path: str, content: str = "") -> dict[str, Any]:
        """Create a new file. Fails if the file already exists.

        :param path: Relative or absolute path inside workspace.
        :param content: Optional initial content.
        """
        target = self._resolve_workspace_path(path)
        if target.exists():
            return {"error": f"file already exists: {target}"}

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "path": str(target),
            "created": True,
            "bytes_written": len(content.encode("utf-8")),
        }

    def delete_file(self, path: str) -> dict[str, Any]:
        """Delete a file from workspace.

        :param path: Relative or absolute path inside workspace.
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}
        if not target.is_file():
            return {"error": f"not a file: {target}"}
        target.unlink()
        return {"path": str(target), "deleted": True}

    def copy_file(self, source: str, destination: str) -> dict[str, Any]:
        """Copy a file to another location within workspace.

        :param source: Source file path.
        :param destination: Destination file path.
        """
        src = self._resolve_workspace_path(source)
        dst = self._resolve_workspace_path(destination)
        if not src.exists():
            return {"error": f"source not found: {src}"}
        if not src.is_file():
            return {"error": f"source is not a file: {src}"}
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        return {"source": str(src), "destination": str(dst), "copied": True}

    def move_file(self, source: str, destination: str) -> dict[str, Any]:
        """Move or rename a file within workspace.

        :param source: Source file path.
        :param destination: Destination file path.
        """
        src = self._resolve_workspace_path(source)
        dst = self._resolve_workspace_path(destination)
        if not src.exists():
            return {"error": f"source not found: {src}"}
        if not src.is_file():
            return {"error": f"source is not a file: {src}"}
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {"source": str(src), "destination": str(dst), "moved": True}

    def file_exists(self, path: str) -> dict[str, Any]:
        """Check whether a path exists and its type.

        :param path: Relative or absolute path inside workspace.
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"path": str(target), "exists": False, "type": None}
        kind = "directory" if target.is_dir() else "file"
        return {"path": str(target), "exists": True, "type": kind}

    def _register_directory_tools(self) -> None:
        self.register_many(
            self.list_directory,
            self.create_directory,
        )
        self.register(self.search_text, observe=True)

    def list_directory(self, path: str = ".", recursive: bool = False, max_entries: int = 200) -> dict[str, Any]:
        """List files and folders under a workspace path.

        :param path: Directory path relative to workspace root.
        :param recursive: If True, list all descendants recursively.
        :param max_entries: Maximum entries to return.
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"path not found: {target}"}

        entries: list[str] = []
        iterator = target.rglob("*") if recursive else target.iterdir()

        for entry in iterator:
            rel = entry.relative_to(self.workspace_root)
            suffix = "/" if entry.is_dir() else ""
            entries.append(f"{rel}{suffix}")
            if len(entries) >= max_entries:
                break

        return {
            "path": str(target),
            "entries": entries,
            "truncated": len(entries) >= max_entries,
        }

    def create_directory(self, path: str) -> dict[str, Any]:
        """Create a directory (and parents) inside workspace.

        :param path: Directory path relative to workspace root.
        """
        target = self._resolve_workspace_path(path)
        target.mkdir(parents=True, exist_ok=True)
        return {"path": str(target), "created": True}

    def search_text(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 100,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        """Search text pattern across workspace files.

        :param pattern: Regex pattern to search for.
        :param path: Directory or file path to search within.
        :param max_results: Maximum number of matches to return.
        :param case_sensitive: Whether the search is case-sensitive.
        """
        if not pattern:
            return {"error": "pattern is required"}

        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"path not found: {target}"}

        flags = 0 if case_sensitive else re.IGNORECASE
        compiled = re.compile(pattern, flags)

        root_iter = [target] if target.is_file() else list(target.rglob("*"))
        results: list[dict[str, Any]] = []

        for file_path in root_iter:
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for line_number, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    results.append(
                        {
                            "path": str(file_path.relative_to(self.workspace_root)),
                            "line": line_number,
                            "text": line,
                        }
                    )
                    if len(results) >= max_results:
                        return {
                            "path": str(target),
                            "pattern": pattern,
                            "matches": results,
                            "truncated": True,
                        }

        return {
            "path": str(target),
            "pattern": pattern,
            "matches": results,
            "truncated": False,
        }

    def _register_line_tools(self) -> None:
        self.register_many(
            self.read_lines,
            self.insert_lines,
            self.replace_lines,
            self.delete_lines,
            self.copy_lines,
            self.move_lines,
            self.search_and_replace,
        )

    def _register_pin_tools(self) -> None:
        self.register(
            self.pin_file_context,
            description=(
                "Pin live workspace file context into the current session so future requests can re-read it. "
                "Prefer the smallest necessary line range, avoid pinning whole large files, pass start_with for a "
                "class or function declaration when possible, and unpin as soon as you no longer need it."
            ),
            parameters=[
                {
                    "name": "path",
                    "description": "File path relative to workspace root.",
                    "type_": "string",
                    "required": True,
                },
                {
                    "name": "start",
                    "description": "Optional first line to pin (1-based, inclusive). Provide together with end.",
                    "type_": "integer",
                    "required": False,
                },
                {
                    "name": "end",
                    "description": "Optional last line to pin (1-based, inclusive). Provide together with start.",
                    "type_": "integer",
                    "required": False,
                },
                {
                    "name": "start_with",
                    "description": "Optional anchor text for the start of the pin. Prefer a declaration line.",
                    "type_": "string",
                    "required": False,
                },
                {
                    "name": "end_with",
                    "description": "Optional anchor text for the end of the pin.",
                    "type_": "string",
                    "required": False,
                },
                {
                    "name": "reason",
                    "description": "Optional short reason for keeping the pin.",
                    "type_": "string",
                    "required": False,
                },
            ],
        )
        self.register(
            self.unpin_file_context,
            description=(
                "Remove previously pinned file context from the current session. Prefer pin_id when available, "
                "use path plus start/end only as a fallback, and clear stale pins promptly."
            ),
            parameters=[
                {
                    "name": "pin_id",
                    "description": "Exact pin identifier to remove. This takes priority over the fallback matchers.",
                    "type_": "string",
                    "required": False,
                },
                {
                    "name": "path",
                    "description": "Fallback file path relative to workspace root.",
                    "type_": "string",
                    "required": False,
                },
                {
                    "name": "start",
                    "description": "Fallback first line of the pinned range (1-based, inclusive).",
                    "type_": "integer",
                    "required": False,
                },
                {
                    "name": "end",
                    "description": "Fallback last line of the pinned range (1-based, inclusive).",
                    "type_": "integer",
                    "required": False,
                },
                {
                    "name": "all",
                    "description": "Remove every pin in the current session when no more specific selector is provided.",
                    "type_": "boolean",
                    "required": False,
                },
            ],
        )

    def read_lines(self, path: str, start: int = 1, end: int | None = None) -> dict[str, Any]:
        """Read a range of lines from a file (1-based, inclusive).

        :param path: File path relative to workspace root.
        :param start: First line number to read (1-based).
        :param end: Last line number to read (inclusive). Defaults to end of file.
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}
        if not target.is_file():
            return {"error": f"not a file: {target}"}

        lines = self._read_lines(target)
        total = len(lines)
        if end is None:
            end = total

        err = self._validate_range(total, start, end)
        if err:
            return {"error": err, "total_lines": total}

        end = min(end, total)
        selected = lines[start - 1 : end]
        content = "".join(selected)

        return {
            "path": str(target),
            "start": start,
            "end": end,
            "total_lines": total,
            "content": content,
        }

    def insert_lines(self, path: str, line: int, content: str) -> dict[str, Any]:
        """Insert text before a given line number (1-based).

        :param path: File path relative to workspace root.
        :param line: Line number to insert before (1-based). Use total_lines+1 to append.
        :param content: Text content to insert (will be split into lines).
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}

        lines = self._read_lines(target)
        total = len(lines)

        if line < 1 or line > total + 1:
            return {"error": f"line ({line}) out of range [1, {total + 1}]", "total_lines": total}

        new_lines = content.splitlines(keepends=True)
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        lines[line - 1 : line - 1] = new_lines
        bytes_written = self._write_lines(target, lines)

        return {
            "path": str(target),
            "inserted_at": line,
            "lines_inserted": len(new_lines),
            "total_lines": len(lines),
            "bytes_written": bytes_written,
        }

    def replace_lines(self, path: str, start: int, end: int, content: str) -> dict[str, Any]:
        """Replace a range of lines [start, end] (1-based, inclusive) with new content.

        :param path: File path relative to workspace root.
        :param start: First line to replace (1-based).
        :param end: Last line to replace (inclusive).
        :param content: Replacement text (can be any number of lines).
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}

        lines = self._read_lines(target)
        total = len(lines)
        err = self._validate_range(total, start, end)
        if err:
            return {"error": err, "total_lines": total}

        end = min(end, total)
        new_lines = content.splitlines(keepends=True)
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        lines[start - 1 : end] = new_lines
        bytes_written = self._write_lines(target, lines)

        return {
            "path": str(target),
            "replaced_range": [start, end],
            "new_lines_count": len(new_lines),
            "total_lines": len(lines),
            "bytes_written": bytes_written,
        }

    def delete_lines(self, path: str, start: int, end: int) -> dict[str, Any]:
        """Delete a range of lines [start, end] (1-based, inclusive).

        :param path: File path relative to workspace root.
        :param start: First line to delete (1-based).
        :param end: Last line to delete (inclusive).
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}

        lines = self._read_lines(target)
        total = len(lines)
        err = self._validate_range(total, start, end)
        if err:
            return {"error": err, "total_lines": total}

        end = min(end, total)
        deleted = lines[start - 1 : end]
        del lines[start - 1 : end]
        bytes_written = self._write_lines(target, lines)

        return {
            "path": str(target),
            "deleted_range": [start, end],
            "lines_deleted": len(deleted),
            "total_lines": len(lines),
            "bytes_written": bytes_written,
        }

    def copy_lines(self, path: str, start: int, end: int, to_line: int) -> dict[str, Any]:
        """Copy lines [start, end] and insert them before to_line (same file).

        :param path: File path relative to workspace root.
        :param start: First line to copy (1-based).
        :param end: Last line to copy (inclusive).
        :param to_line: Destination line number to insert before (1-based).
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}

        lines = self._read_lines(target)
        total = len(lines)
        err = self._validate_range(total, start, end)
        if err:
            return {"error": err, "total_lines": total}

        end = min(end, total)
        if to_line < 1 or to_line > total + 1:
            return {"error": f"to_line ({to_line}) out of range [1, {total + 1}]", "total_lines": total}

        copied = list(lines[start - 1 : end])
        insert_idx = to_line - 1
        lines[insert_idx:insert_idx] = copied
        bytes_written = self._write_lines(target, lines)

        return {
            "path": str(target),
            "copied_range": [start, end],
            "inserted_at": to_line,
            "lines_copied": len(copied),
            "total_lines": len(lines),
            "bytes_written": bytes_written,
        }

    def move_lines(self, path: str, start: int, end: int, to_line: int) -> dict[str, Any]:
        """Cut lines [start, end] and paste them before to_line (same file).

        :param path: File path relative to workspace root.
        :param start: First line to move (1-based).
        :param end: Last line to move (inclusive).
        :param to_line: Destination line number to paste before (1-based, in the original numbering).
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}

        lines = self._read_lines(target)
        total = len(lines)
        err = self._validate_range(total, start, end)
        if err:
            return {"error": err, "total_lines": total}

        end = min(end, total)
        if to_line < 1 or to_line > total + 1:
            return {"error": f"to_line ({to_line}) out of range [1, {total + 1}]", "total_lines": total}

        if start <= to_line <= end + 1:
            return {
                "path": str(target),
                "moved_range": [start, end],
                "inserted_at": to_line,
                "lines_moved": 0,
                "total_lines": total,
                "bytes_written": 0,
                "note": "to_line is within the moved range; no change",
            }

        moved = list(lines[start - 1 : end])
        del lines[start - 1 : end]

        if to_line > end:
            insert_idx = to_line - 1 - len(moved)
        else:
            insert_idx = to_line - 1

        lines[insert_idx:insert_idx] = moved
        bytes_written = self._write_lines(target, lines)

        return {
            "path": str(target),
            "moved_range": [start, end],
            "inserted_at": to_line,
            "lines_moved": len(moved),
            "total_lines": len(lines),
            "bytes_written": bytes_written,
        }

    def search_and_replace(
        self,
        path: str,
        search: str,
        replace: str,
        regex: bool = False,
        case_sensitive: bool = True,
        max_count: int = 0,
    ) -> dict[str, Any]:
        """Find and replace text within a single file.

        :param path: File path relative to workspace root.
        :param search: Text or regex pattern to find.
        :param replace: Replacement string (supports backreferences when regex=True).
        :param regex: Treat search as a regular expression.
        :param case_sensitive: Case-sensitive matching.
        :param max_count: Stop after this many replacements (0 = unlimited).
        """
        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}
        if not target.is_file():
            return {"error": f"not a file: {target}"}
        if not search:
            return {"error": "search string is required"}

        content = target.read_text(encoding="utf-8", errors="replace")

        flags = 0 if case_sensitive else re.IGNORECASE
        if regex:
            compiled = re.compile(search, flags)
        else:
            compiled = re.compile(re.escape(search), flags)

        count = max_count if max_count > 0 else 0
        new_content, replacements_made = compiled.subn(replace, content, count=count)
        target.write_text(new_content, encoding="utf-8")

        return {
            "path": str(target),
            "search": search,
            "replace": replace,
            "replacements_made": replacements_made,
            "bytes_written": len(new_content.encode("utf-8")),
        }

    def pin_file_context(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
        start_with: str | None = None,
        end_with: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Pin a file or line range into the current session."""
        session_id, store_or_error = self._require_pin_context()
        if session_id is None:
            return store_or_error
        store = store_or_error

        if (start is None) != (end is None):
            return {"error": "start and end must be provided together when pinning a line range"}

        target = self._resolve_workspace_path(path)
        if not target.exists():
            return {"error": f"file not found: {target}"}
        if not target.is_file():
            return {"error": f"not a file: {target}"}

        lines = self._read_lines(target)
        total_lines = len(lines)

        if start is not None and end is not None:
            err = self._validate_range(total_lines, start, end)
            if err:
                return {"error": err, "total_lines": total_lines}
        else:
            content_chars = len("".join(lines))
            if content_chars > MAX_FULL_FILE_PIN_CHARS:
                return {
                    "error": "file too large to pin as a whole",
                    "path": str(target),
                    "mode": "file",
                    "max_chars": MAX_FULL_FILE_PIN_CHARS,
                    "file_chars": content_chars,
                    "suggestion": (
                        "Pin a smaller line range around the relevant class or function and pass start_with "
                        "for the declaration line."
                    ),
                }

        candidate = build_pin_record(
            path=target,
            lines=lines,
            start=start,
            end=end,
            start_with=start_with,
            end_with=end_with,
            reason=reason,
        )

        state, pins = load_workspace_pins(store, session_id)
        duplicate = find_duplicate_pin(pins, candidate)
        if duplicate is not None:
            return {
                "pin_id": duplicate["pin_id"],
                "path": duplicate["path"],
                "mode": duplicate["mode"],
                "created": False,
                "duplicate": True,
                "start": duplicate.get("start"),
                "end": duplicate.get("end"),
            }

        if len(pins) >= MAX_SESSION_PIN_COUNT:
            return {
                "error": "session pin limit reached",
                "pin_limit": MAX_SESSION_PIN_COUNT,
                "existing_pin_count": len(pins),
                "suggestion": "Unpin stale context before adding another pin.",
            }

        pins.append(candidate)
        save_workspace_pins(store, session_id, state, pins)
        return {
            "pin_id": candidate["pin_id"],
            "path": candidate["path"],
            "mode": candidate["mode"],
            "created": True,
            "duplicate": False,
            "start": candidate.get("start"),
            "end": candidate.get("end"),
            "reason": candidate.get("reason"),
            "pin_count": len(pins),
        }

    def unpin_file_context(
        self,
        pin_id: str | None = None,
        path: str | None = None,
        start: int | None = None,
        end: int | None = None,
        all: bool = False,
    ) -> dict[str, Any]:
        """Remove one or more pinned file contexts from the current session."""
        session_id, store_or_error = self._require_pin_context()
        if session_id is None:
            return store_or_error
        store = store_or_error

        resolved_path: str | None = None
        if path is not None:
            resolved_path = str(self._resolve_workspace_path(path))

        if not pin_id and resolved_path is None and not all:
            return {
                "error": "provide pin_id, path with start/end, or all=True",
            }
        if resolved_path is not None and ((start is None) != (end is None)):
            return {"error": "start and end must be provided together when using the path fallback"}

        state, pins = load_workspace_pins(store, session_id)
        remaining, removed_ids = remove_pins(
            pins,
            pin_id=pin_id,
            path=resolved_path,
            start=start,
            end=end,
            remove_all=all and not pin_id and resolved_path is None,
        )
        if removed_ids:
            save_workspace_pins(store, session_id, state, remaining)
        return {
            "removed": len(removed_ids),
            "pin_ids": removed_ids,
            "remaining_pin_count": len(remaining),
        }


__all__ = ["WorkspaceToolkit"]
