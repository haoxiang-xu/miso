from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...base import BuiltinExecutionContext
from ..core import CoreToolkit


@dataclass
class _ReadSnapshot:
    path: str
    content_sha1: str
    size: int
    mtime_ns: int
    fully_read: bool


class WorkspaceToolkitBackend:
    """Private transition backend for workspace and coding behavior."""

    _IMAGE_SUFFIXES: set[str] = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".ico",
        ".tiff",
        ".avif",
    }
    _PDF_SUFFIXES: set[str] = {".pdf"}

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ) -> None:
        self._core = CoreToolkit(workspace_root=workspace_root, workspace_roots=workspace_roots)
        self.workspace_roots: list[Path] = list(self._core.workspace_roots)
        self.workspace_root: Path = self._core.workspace_root
        self._read_snapshots: dict[str, dict[str, _ReadSnapshot]] = {}

    @property
    def current_execution_context(self) -> BuiltinExecutionContext | None:
        return self._core.current_execution_context

    def push_execution_context(self, context: BuiltinExecutionContext) -> None:
        self._core.push_execution_context(context)

    def pop_execution_context(self) -> None:
        self._core.pop_execution_context()

    def shutdown(self) -> None:
        self._core.shutdown()

    def _session_key(self) -> str:
        context = self.current_execution_context
        session_id = str(getattr(context, "session_id", "") or "").strip()
        if session_id:
            return session_id
        run_id = str(getattr(context, "run_id", "") or "").strip()
        if run_id:
            return f"run:{run_id}"
        return "__default__"

    def _session_snapshots(self) -> dict[str, _ReadSnapshot]:
        return self._read_snapshots.setdefault(self._session_key(), {})

    def _resolve_workspace_path(self, path: str) -> Path:
        path_obj = Path(path)
        resolved = path_obj.resolve() if path_obj.is_absolute() else (self.workspace_root / path_obj).resolve()

        for root in self.workspace_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue

        raise ValueError("path is outside all workspace roots")

    def _resolve_absolute_path(self, path: str) -> tuple[Path | None, str | None]:
        if not isinstance(path, str) or not path.strip():
            return None, "path is required"
        raw_path = Path(path)
        if not raw_path.is_absolute():
            return None, "path must be an absolute path"
        try:
            return self._resolve_workspace_path(path), None
        except Exception as exc:
            return None, str(exc)

    def _read_text_file(self, target: Path) -> tuple[str | None, dict[str, Any] | None]:
        if not target.exists():
            return None, {"error": f"file not found: {target}"}
        if not target.is_file():
            return None, {"error": f"not a file: {target}"}
        raw_bytes = target.read_bytes()
        file_kind = self._detect_file_kind(target, raw_bytes)
        if file_kind != "text":
            return None, {
                "error": f"{file_kind} files are not supported by this tool",
                "path": str(target),
                "file_kind": file_kind,
                "skipped": True,
            }
        return raw_bytes.decode("utf-8", errors="replace"), None

    def _detect_file_kind(self, target: Path, raw_bytes: bytes) -> str:
        suffix = target.suffix.lower()
        if suffix in self._PDF_SUFFIXES:
            return "pdf"
        if suffix in self._IMAGE_SUFFIXES:
            return "image"
        mime_type, _ = mimetypes.guess_type(str(target))
        if isinstance(mime_type, str) and mime_type.startswith("image/") and suffix != ".svg":
            return "image"
        if b"\x00" in raw_bytes[:8192]:
            return "binary"
        return "text"

    def _split_lines(self, raw: str) -> list[str]:
        return raw.splitlines()

    def _total_lines(self, raw: str) -> int:
        return len(self._split_lines(raw))

    def _coerce_nonnegative_int(self, value: Any, default: int) -> int:
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, coerced)

    def _number_lines(self, lines: list[str], *, start_line: int) -> str:
        if not lines:
            return ""
        return "\n".join(f"{line_no}\t{line}" for line_no, line in enumerate(lines, start=start_line))

    def _snapshot_for(self, target: Path) -> _ReadSnapshot | None:
        return self._session_snapshots().get(str(target))

    def _record_read_snapshot(self, target: Path, raw: str, *, fully_read: bool) -> None:
        encoded = raw.encode("utf-8", errors="replace")
        stat_result = target.stat()
        content_sha1 = hashlib.sha1(encoded).hexdigest()
        existing = self._snapshot_for(target)
        resolved_fully_read = fully_read or bool(
            existing is not None
            and existing.fully_read
            and existing.content_sha1 == content_sha1
        )
        self._session_snapshots()[str(target)] = _ReadSnapshot(
            path=str(target),
            content_sha1=content_sha1,
            size=len(encoded),
            mtime_ns=int(stat_result.st_mtime_ns),
            fully_read=resolved_fully_read,
        )

    def read(self, path: str, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        target, err = self._resolve_absolute_path(path)
        if target is None:
            return {"error": err, "path": path}

        raw, load_error = self._read_text_file(target)
        if load_error is not None:
            return load_error
        assert isinstance(raw, str)

        lines = self._split_lines(raw)
        total_lines = len(lines)
        resolved_offset = self._coerce_nonnegative_int(offset, 0)
        resolved_limit = None if limit is None else self._coerce_nonnegative_int(limit, 0)
        start_index = min(resolved_offset, total_lines)
        end_index = total_lines if resolved_limit is None else min(total_lines, start_index + resolved_limit)
        selected_lines = lines[start_index:end_index]
        truncated = start_index > 0 or end_index < total_lines

        fully_read = not truncated
        self._record_read_snapshot(target, raw, fully_read=fully_read)
        self._core._record_read_snapshot(target, raw, fully_read=fully_read)

        start_line = start_index + 1 if selected_lines else 0
        end_line = end_index if selected_lines else 0
        return {
            "path": str(target),
            "content": self._number_lines(selected_lines, start_line=max(1, start_line)),
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "truncated": truncated,
            "file_kind": "text",
        }

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
