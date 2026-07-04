from __future__ import annotations

import fnmatch
import hashlib
import mimetypes
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...base import BuiltinExecutionContext
from ....tools.models import ToolConfirmationPolicy
from .lsp_runtime import LSPRuntime, LSPRuntimeError
from .shell_runtime import ShellRuntime


_ARTIFACT_DIFF_MAX_LINES = 1_000_000
_ARTIFACT_DIFF_MAX_BYTES = 50_000_000


@dataclass
class _ReadSnapshot:
    path: str
    content_sha1: str
    size: int
    mtime_ns: int
    fully_read: bool


class CoreCodingBackend:
    """Private implementation backend for core coding file tools."""

    _SKIP_DIR_NAMES: set[str] = {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        ".nuxt",
        ".venv",
        "venv",
        "dist",
        "build",
        "coverage",
    }
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
    _MAX_GLOB_RESULTS = 200
    _LSP_RESULT_CHAR_LIMIT = 100_000

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
        execution_context_provider: Callable[[], BuiltinExecutionContext | None] | None = None,
    ) -> None:
        if workspace_roots:
            self.workspace_roots: list[Path] = [Path(root).resolve() for root in workspace_roots]
        elif workspace_root:
            self.workspace_roots = [Path(workspace_root).resolve()]
        else:
            self.workspace_roots = [Path(os.getcwd()).resolve()]
        self.workspace_root: Path = self.workspace_roots[0]
        self._execution_context_provider = execution_context_provider
        self._execution_context_stack: list[BuiltinExecutionContext] = []
        self._read_snapshots: dict[str, dict[str, _ReadSnapshot]] = {}
        self._lsp_runtime = LSPRuntime(self.workspace_roots)
        self._shell_runtime = ShellRuntime(self.workspace_roots)

    @property
    def current_execution_context(self) -> BuiltinExecutionContext | None:
        if self._execution_context_provider is not None:
            return self._execution_context_provider()
        if not self._execution_context_stack:
            return None
        return self._execution_context_stack[-1]

    def push_execution_context(self, context: BuiltinExecutionContext) -> None:
        self._execution_context_stack.append(context)

    def pop_execution_context(self) -> None:
        if self._execution_context_stack:
            self._execution_context_stack.pop()

    def shutdown(self) -> None:
        self._lsp_runtime.shutdown()
        self._shell_runtime.shutdown()

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

        self._record_read_snapshot(target, raw, fully_read=not truncated)

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

    def _check_snapshot_freshness(self, target: Path) -> tuple[str | None, _ReadSnapshot | None]:
        snapshot = self._snapshot_for(target)
        if snapshot is None:
            return "existing files must be fully read before write or edit", None
        if not snapshot.fully_read:
            return "file was only partially read; reread the full file before write or edit", snapshot

        stat_result = target.stat()
        raw_bytes = target.read_bytes()
        current_sha1 = hashlib.sha1(raw_bytes).hexdigest()
        if (
            snapshot.mtime_ns != int(stat_result.st_mtime_ns)
            or snapshot.size != len(raw_bytes)
            or snapshot.content_sha1 != current_sha1
        ):
            return "file changed since it was last read; reread the full file before write or edit", snapshot
        return None, snapshot

    def _iter_candidate_files(self, base: Path) -> list[Path]:
        if base.is_file():
            return [base]

        results: list[Path] = []
        for current_root, dirnames, filenames in os.walk(base):
            dirnames[:] = [name for name in dirnames if name not in self._SKIP_DIR_NAMES]
            root_path = Path(current_root)
            for filename in filenames:
                results.append(root_path / filename)
        return results

    def _relative_path(self, target: Path) -> str:
        for root in self.workspace_roots:
            try:
                return str(target.relative_to(root))
            except ValueError:
                continue
        return str(target)

    def _file_diff_artifact_descriptor(
        self,
        *,
        title: str,
        path: str,
        old_content: str,
        new_content: str,
        operation: str,
    ) -> dict[str, Any] | None:
        from ....tools._diff_helpers import build_code_diff_payload

        file_payload = build_code_diff_payload(
            path,
            old_content,
            new_content,
            operation,
            max_lines=_ARTIFACT_DIFF_MAX_LINES,
            max_bytes=_ARTIFACT_DIFF_MAX_BYTES,
        )
        if file_payload is None or file_payload.get("truncated"):
            return None
        unified_diff = file_payload.get("unified_diff")
        if not isinstance(unified_diff, str) or not unified_diff:
            return None
        file_entry = {
            "path": path,
            "operation": operation,
            "sub_operation": file_payload.get("sub_operation", operation),
            "unified_diff_full": unified_diff,
            "unified_diff": unified_diff,
            "truncated": False,
            "total_lines": file_payload.get("total_lines", 0),
            "displayed_lines": file_payload.get("displayed_lines", 0),
        }
        return {
            "kind": "file_diff",
            "title": title,
            "files": [file_entry],
        }

    def write(self, path: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str):
            return {"error": "content must be a string", "path": path}

        target, err = self._resolve_absolute_path(path)
        if target is None:
            return {"error": err, "path": path}

        parent = target.parent
        if not parent.exists():
            return {"error": f"parent directory does not exist: {parent}", "path": str(target)}
        if not parent.is_dir():
            return {"error": f"parent path is not a directory: {parent}", "path": str(target)}

        existed = target.exists()
        old_raw = ""
        if existed:
            old_raw, load_error = self._read_text_file(target)
            if load_error is not None:
                return load_error
            assert isinstance(old_raw, str)
            freshness_error, _ = self._check_snapshot_freshness(target)
            if freshness_error is not None:
                return {"error": freshness_error, "path": str(target)}

        target.write_text(content, encoding="utf-8")
        self._record_read_snapshot(target, content, fully_read=True)
        self._record_workspace_change(
            target=target,
            before_text=old_raw if existed else None,
            after_text=content,
            operation="modified" if existed else "created",
            tool_name="write",
        )

        before_bytes = len(old_raw.encode("utf-8", errors="replace"))
        after_bytes = len(content.encode("utf-8", errors="replace"))
        operation = "edit" if existed else "create"
        result = {
            "path": str(target),
            "operation": "update" if existed else "create",
            "bytes_written": after_bytes,
            "structured_patch": {
                "type": "replace_file" if existed else "create_file",
                "before_lines": self._total_lines(old_raw),
                "after_lines": self._total_lines(content),
                "before_bytes": before_bytes,
                "after_bytes": after_bytes,
            },
            "original_file": {
                "path": str(target),
                "exists": existed,
                "sha1": hashlib.sha1(old_raw.encode("utf-8", errors="replace")).hexdigest() if existed else "",
                "total_lines": self._total_lines(old_raw),
            },
        }
        artifact = self._file_diff_artifact_descriptor(
            title=f"{'Edit' if existed else 'Create'} {target}",
            path=str(target),
            old_content=old_raw,
            new_content=content,
            operation=operation,
        )
        if artifact is not None:
            result["_artifacts"] = [artifact]
        return result

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(old_string, str) or not old_string:
            return {"error": "old_string must be a non-empty string", "path": path}
        if not isinstance(new_string, str):
            return {"error": "new_string must be a string", "path": path}

        target, err = self._resolve_absolute_path(path)
        if target is None:
            return {"error": err, "path": path}

        raw, load_error = self._read_text_file(target)
        if load_error is not None:
            return load_error
        assert isinstance(raw, str)

        freshness_error, snapshot = self._check_snapshot_freshness(target)
        if freshness_error is not None:
            return {"error": freshness_error, "path": str(target)}
        match_count = raw.count(old_string)
        if match_count == 0:
            return {"error": "old_string was not found in the file", "path": str(target)}
        if match_count > 1 and not replace_all:
            return {
                "error": "old_string matched more than once; set replace_all=true or provide a unique match",
                "path": str(target),
                "match_count": match_count,
            }

        replacement_count = match_count if replace_all else 1
        updated = raw.replace(old_string, new_string, replacement_count)
        first_match_index = raw.find(old_string)
        first_match_line = raw.count("\n", 0, first_match_index) + 1 if first_match_index >= 0 else 0
        target.write_text(updated, encoding="utf-8")
        self._record_read_snapshot(target, updated, fully_read=True)
        self._record_workspace_change(
            target=target,
            before_text=raw,
            after_text=updated,
            operation="modified",
            tool_name="edit",
        )

        result = {
            "path": str(target),
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": bool(replace_all),
            "replacement_count": replacement_count,
            "structured_patch": {
                "type": "string_replace",
                "first_match_line": first_match_line,
                "before_lines": self._total_lines(raw),
                "after_lines": self._total_lines(updated),
            },
            "original_file": {
                "path": str(target),
                "sha1": snapshot.content_sha1 if snapshot is not None else "",
                "total_lines": self._total_lines(raw),
            },
        }
        artifact = self._file_diff_artifact_descriptor(
            title=f"Edit {target}",
            path=str(target),
            old_content=raw,
            new_content=updated,
            operation="edit",
        )
        if artifact is not None:
            result["_artifacts"] = [artifact]
        return result

    def glob(self, pattern: str, path: str | None = None) -> dict[str, Any]:
        if not isinstance(pattern, str) or not pattern.strip():
            return {"error": "pattern is required"}

        base = self.workspace_root if path is None else self._resolve_absolute_path(path)[0]
        if base is None:
            _, err = self._resolve_absolute_path(path or "")
            return {"error": err or "invalid path", "path": path or ""}

        if not base.exists():
            return {"error": f"path not found: {base}", "path": str(base)}

        matches: list[Path] = []
        if base.is_file():
            relative = self._relative_path(base)
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(base.name, pattern):
                matches.append(base)
        else:
            for candidate in base.glob(pattern):
                if candidate.is_file() and not any(part in self._SKIP_DIR_NAMES for part in candidate.parts):
                    matches.append(candidate.resolve())

        unique_matches = sorted(
            {match.resolve() for match in matches},
            key=lambda item: (-item.stat().st_mtime_ns, str(item)),
        )
        truncated = len(unique_matches) > self._MAX_GLOB_RESULTS
        limited_matches = unique_matches[: self._MAX_GLOB_RESULTS]
        return {
            "pattern": pattern,
            "path": str(base.resolve()),
            "matches": [str(match) for match in limited_matches],
            "match_count": len(unique_matches),
            "truncated": truncated,
        }

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
        if not isinstance(pattern, str) or not pattern:
            return {"error": "pattern is required"}
        if output_mode not in {"content", "files_with_matches", "count"}:
            return {"error": "output_mode must be one of: content, files_with_matches, count"}

        base = self.workspace_root if path is None else self._resolve_absolute_path(path)[0]
        if base is None:
            _, err = self._resolve_absolute_path(path or "")
            return {"error": err or "invalid path", "path": path or ""}
        if not base.exists():
            return {"error": f"path not found: {base}", "path": str(base)}

        context_lines = self._coerce_nonnegative_int(context, 0)
        limit_value = max(1, self._coerce_nonnegative_int(head_limit, 50))
        offset_value = self._coerce_nonnegative_int(offset, 0)

        flags = re.MULTILINE
        if not case_sensitive:
            flags |= re.IGNORECASE
        if multiline:
            flags |= re.DOTALL
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            return {"error": f"invalid regex: {exc}"}

        matches: list[dict[str, Any]] = []
        files_with_matches: list[str] = []
        scanned_files = 0

        for candidate in self._iter_candidate_files(base.resolve()):
            if glob and not fnmatch.fnmatch(self._relative_path(candidate), glob):
                continue
            raw, load_error = self._read_text_file(candidate)
            if load_error is not None:
                continue
            assert isinstance(raw, str)
            scanned_files += 1
            relative_path = self._relative_path(candidate)
            lines = raw.splitlines()
            file_match_count = 0

            for match in compiled.finditer(raw):
                file_match_count += 1
                line_number = raw.count("\n", 0, match.start()) + 1 if raw else 0
                line_text = lines[line_number - 1] if 0 < line_number <= len(lines) else ""
                before_start = max(0, line_number - 1 - context_lines)
                after_end = min(len(lines), line_number + context_lines)
                matches.append(
                    {
                        "path": str(candidate),
                        "relative_path": relative_path,
                        "line": line_number,
                        "match": match.group(0),
                        "line_text": line_text,
                        "context_before": lines[before_start : max(0, line_number - 1)],
                        "context_after": lines[line_number:after_end],
                    }
                )

            if file_match_count > 0:
                files_with_matches.append(str(candidate))

        unique_files = sorted(set(files_with_matches))
        if output_mode == "count":
            return {
                "pattern": pattern,
                "path": str(base.resolve()),
                "output_mode": output_mode,
                "match_count": len(matches),
                "files_with_matches": len(unique_files),
                "scanned_files": scanned_files,
                "applied_offset": 0,
                "applied_limit": 0,
                "truncated": False,
            }

        if output_mode == "files_with_matches":
            paged_files = unique_files[offset_value : offset_value + limit_value]
            return {
                "pattern": pattern,
                "path": str(base.resolve()),
                "output_mode": output_mode,
                "files": paged_files,
                "total_files": len(unique_files),
                "scanned_files": scanned_files,
                "applied_offset": offset_value,
                "applied_limit": limit_value,
                "truncated": offset_value + len(paged_files) < len(unique_files),
            }

        paged_matches = matches[offset_value : offset_value + limit_value]
        return {
            "pattern": pattern,
            "path": str(base.resolve()),
            "output_mode": output_mode,
            "matches": paged_matches,
            "match_count": len(matches),
            "files_with_matches": len(unique_files),
            "scanned_files": scanned_files,
            "applied_offset": offset_value,
            "applied_limit": limit_value,
            "truncated": offset_value + len(paged_matches) < len(matches),
        }

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
        resolved_action = str(action or "").strip().lower()
        if resolved_action not in {"run", "poll", "kill"}:
            return {
                "ok": False,
                "action": resolved_action or str(action or ""),
                "status": "error",
                "shell_family": "",
                "platform": sys.platform,
                "cwd": "",
                "task_id": str(task_id or ""),
                "error": "action must be one of: run, poll, kill",
            }

        session_key = self._session_key()
        if resolved_action == "run":
            return self._shell_runtime.run(
                session_key=session_key,
                command=command,
                cwd=cwd,
                timeout_ms=timeout_ms,
                run_in_background=run_in_background,
                max_output_chars=max_output_chars,
                yield_time_ms=yield_time_ms,
            )
        if not isinstance(task_id, str) or not task_id.strip():
            return {
                "ok": False,
                "action": resolved_action,
                "status": "error",
                "shell_family": "",
                "platform": sys.platform,
                "cwd": "",
                "task_id": str(task_id or ""),
                "error": "task_id is required",
            }
        if resolved_action == "poll":
            return self._shell_runtime.poll(task_id=task_id, max_output_chars=max_output_chars)
        return self._shell_runtime.kill(task_id=task_id, max_output_chars=max_output_chars)

    def _resolve_shell_confirmation(
        self,
        arguments: dict[str, Any],
        execution_context: BuiltinExecutionContext | None,
    ) -> ToolConfirmationPolicy:
        action = str(arguments.get("action") or "").strip().lower()
        if action in {"poll", "kill"}:
            return ToolConfirmationPolicy(requires_confirmation=False)
        if action != "run":
            return ToolConfirmationPolicy(requires_confirmation=False)

        if bool(arguments.get("run_in_background")):
            return ToolConfirmationPolicy(requires_confirmation=True)

        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolConfirmationPolicy(requires_confirmation=False)

        shell_family = self._shell_runtime.detect_executor().family
        if self._shell_runtime.is_low_risk_command(command, shell_family):
            return ToolConfirmationPolicy(requires_confirmation=False)
        return ToolConfirmationPolicy(requires_confirmation=True)

    def lsp(
        self,
        operation: str,
        file_path: str,
        line: int | None = None,
        character: int | None = None,
        query: str = "",
    ) -> dict[str, Any]:
        resolved_operation = str(operation or "").strip()
        allowed_operations = {
            "goToDefinition",
            "findReferences",
            "hover",
            "documentSymbol",
            "workspaceSymbol",
        }
        if resolved_operation not in allowed_operations:
            return {
                "ok": False,
                "operation": resolved_operation,
                "file_path": str(file_path or ""),
                "result": "",
                "result_count": 0,
                "file_count": 0,
                "language": "",
                "server": "",
                "error": (
                    "operation must be one of: goToDefinition, findReferences, hover, "
                    "documentSymbol, workspaceSymbol"
                ),
            }

        target, err = self._resolve_absolute_path(file_path)
        if target is None:
            return {
                "ok": False,
                "operation": resolved_operation,
                "file_path": str(file_path or ""),
                "result": "",
                "result_count": 0,
                "file_count": 0,
                "language": "",
                "server": "",
                "error": err or "invalid file path",
            }
        if not target.exists():
            return {
                "ok": False,
                "operation": resolved_operation,
                "file_path": str(target),
                "result": "",
                "result_count": 0,
                "file_count": 0,
                "language": "",
                "server": "",
                "error": f"file not found: {target}",
            }
        if not target.is_file():
            return {
                "ok": False,
                "operation": resolved_operation,
                "file_path": str(target),
                "result": "",
                "result_count": 0,
                "file_count": 0,
                "language": "",
                "server": "",
                "error": f"not a file: {target}",
            }

        if resolved_operation in {"goToDefinition", "findReferences", "hover"}:
            line_value = self._coerce_nonnegative_int(line, 0)
            character_value = self._coerce_nonnegative_int(character, 0)
            if line_value <= 0 or character_value <= 0:
                return {
                    "ok": False,
                    "operation": resolved_operation,
                    "file_path": str(target),
                    "result": "",
                    "result_count": 0,
                    "file_count": 0,
                    "language": "",
                    "server": "",
                    "error": "line and character are required positive integers for this operation",
                }
        else:
            line_value = None
            character_value = None

        try:
            result = self._lsp_runtime.execute(
                file_path=target,
                operation=resolved_operation,
                line=line_value,
                character=character_value,
                query=str(query or ""),
            )
        except LSPRuntimeError as exc:
            return {
                "ok": False,
                "operation": resolved_operation,
                "file_path": str(target),
                "result": "",
                "result_count": 0,
                "file_count": 0,
                "language": "",
                "server": "",
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "ok": False,
                "operation": resolved_operation,
                "file_path": str(target),
                "result": "",
                "result_count": 0,
                "file_count": 0,
                "language": "",
                "server": "",
                "error": f"{type(exc).__name__}: {exc}",
            }

        result_text = result.get("result")
        if isinstance(result_text, str) and len(result_text) > self._LSP_RESULT_CHAR_LIMIT:
            result["result"] = result_text[: self._LSP_RESULT_CHAR_LIMIT]
            result["truncated"] = True
        else:
            result["truncated"] = False
        return result

    def _record_workspace_change(
        self,
        *,
        target: Path,
        before_text: str | None,
        after_text: str | None,
        operation: str,
        tool_name: str,
    ) -> None:
        context = self.current_execution_context
        tracker = getattr(context, "workspace_changes", None) if context is not None else None
        if tracker is None or not hasattr(tracker, "record_text_file_change"):
            return
        call_id = str(getattr(context, "call_id", "") or "")
        turn_id = str(getattr(context, "turn_id", "") or "")
        tracker.record_text_file_change(
            str(target),
            before_text,
            after_text,
            operation=operation,
            tool_name=tool_name,
            call_id=call_id,
            turn_id=turn_id,
        )


__all__ = ["CoreCodingBackend"]
