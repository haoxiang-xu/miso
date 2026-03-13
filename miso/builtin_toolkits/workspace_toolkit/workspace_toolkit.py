from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from ..base import builtin_toolkit


class workspace_toolkit(builtin_toolkit):
    """Workspace toolkit for files, directories, and line-level editing."""

    def __init__(self, *, workspace_root: str | Path | None = None):
        super().__init__(workspace_root=workspace_root)
        self._register_file_tools()
        self._register_directory_tools()
        self._register_line_tools()

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

    def _register_file_tools(self) -> None:
        self.register_many(
            self.read_file,
            self.write_file,
            self.create_file,
            self.delete_file,
            self.copy_file,
            self.move_file,
            self.file_exists,
        )

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


__all__ = ["workspace_toolkit"]
