from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ...base import BuiltinToolkit
from ....tools.models import ToolHistoryOptimizationContext
from ....workspace.pins import (
    MAX_FULL_FILE_PIN_CHARS,
    MAX_SESSION_PIN_COUNT,
    WorkspacePinExecutionContext,
    build_pin_record,
    find_duplicate_pin,
    load_workspace_pins,
    remove_pins,
    save_workspace_pins,
)
from ....workspace.syntax import (
    build_syntax_tree_payload,
    detect_language,
    is_language_supported,
)


class WorkspaceToolkit(BuiltinToolkit):
    """Lean workspace toolkit for structured reads, search, edits, and pins."""

    _SKIP_DIR_NAMES: set[str] = {
        ".git",
        "node_modules",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
        ".nuxt",
        "dist",
        "build",
        ".venv",
        "venv",
        ".egg-info",
    }

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ) -> None:
        super().__init__(workspace_root=workspace_root, workspace_roots=workspace_roots)
        self._register_tools()

    def _register_tools(self) -> None:
        self.register(
            self.read_files,
            description="Read one or more UTF-8 text files from the workspace. Returns structured file results and may auto-upgrade large source files to AST output.",
            history_arguments_optimizer=self._compact_read_files_args,
            history_result_optimizer=self._compact_read_files_result,
        )
        self.register(
            self.list_directories,
            description="List workspace directories as structured entries. This is local workspace inspection only and does not search the web.",
            history_arguments_optimizer=self._compact_list_directories_args,
            history_result_optimizer=self._compact_list_directories_result,
        )
        self.register(
            self.search_text,
            description="Search text across workspace files with regex. This searches only the local workspace and does not search the web.",
            history_result_optimizer=self._compact_search_text_result,
        )
        self.register(
            self.write_file,
            requires_confirmation=True,
            description="Create or overwrite a UTF-8 workspace file, or append when requested.",
            history_arguments_optimizer=self._compact_write_file_args,
        )
        self.register(self.read_lines)
        self.register(
            self.insert_lines,
            requires_confirmation=True,
            history_arguments_optimizer=self._compact_text_edit_args,
        )
        self.register(
            self.replace_lines,
            requires_confirmation=True,
            history_arguments_optimizer=self._compact_text_edit_args,
        )
        self.register(
            self.delete_lines,
            requires_confirmation=True,
            history_arguments_optimizer=self._compact_text_edit_args,
        )
        self.register(
            self.pin_file_context,
            description=(
                "Pin a file or line range into the current session context. "
                "Prefer the smallest necessary line range and unpin as soon as you no longer need it."
            ),
        )
        self.register(
            self.unpin_file_context,
            description=(
                "Remove one or more pinned file contexts from the current session by pin_id, "
                "matching path and line range, or all=True."
            ),
        )

    def _resolve_safe(self, path: str) -> tuple[Path | None, str | None]:
        try:
            return self._resolve_workspace_path(path), None
        except Exception as exc:
            return None, str(exc)

    def _relative_path(self, resolved: Path) -> str:
        for root in self.workspace_roots:
            try:
                return str(resolved.relative_to(root))
            except ValueError:
                continue
        return str(resolved)

    def _should_skip(self, path: Path) -> bool:
        return any(part in self._SKIP_DIR_NAMES for part in path.parts)

    def _iter_workspace_files(
        self,
        base: Path,
        *,
        file_glob: str | None = None,
    ):
        if base.is_file():
            if not self._should_skip(base) and (file_glob is None or fnmatch.fnmatch(base.name, file_glob)):
                yield base
            return

        for fp in base.rglob("*"):
            if not fp.is_file():
                continue
            if self._should_skip(fp):
                continue
            if file_glob is not None and not fnmatch.fnmatch(fp.name, file_glob):
                continue
            yield fp

    def _preview_text(self, text: str, chars: int = 160) -> str:
        if len(text) <= chars * 2:
            return text
        return f"{text[:chars]}\n... <omitted {len(text) - chars * 2} chars> ...\n{text[-chars:]}"

    def _coerce_positive_int(self, value: Any, default: int, *, minimum: int = 0) -> int:
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, coerced)

    def _load_file_text(self, target: Path) -> tuple[str | None, dict[str, Any] | None]:
        if not target.exists():
            return None, {"error": f"file not found: {target}"}
        if not target.is_file():
            return None, {"error": f"not a file: {target}"}
        return target.read_text(encoding="utf-8", errors="replace"), None

    def _total_lines(self, raw: str) -> int:
        if not raw:
            return 0
        return raw.count("\n") + (0 if raw.endswith("\n") else 1)

    def _split_lines(self, raw: str) -> list[str]:
        return raw.splitlines(keepends=True)

    def _validate_line_range(
        self,
        *,
        total_lines: int,
        start: int,
        end: int,
        allow_append: bool = False,
    ) -> str | None:
        if start < 1 or end < start:
            return "invalid line range"
        max_line = total_lines + (1 if allow_append else 0)
        if start > max_line or end > max_line:
            return f"line range out of bounds: total_lines={total_lines}"
        return None

    def _require_pin_context(self) -> tuple[WorkspacePinExecutionContext | None, dict[str, Any] | None]:
        context = self.current_execution_context
        if context is None or not context.session_id:
            return None, {"error": "pin tools require an active session_id execution context"}
        return context, None

    def read_files(
        self,
        paths: list[str],
        max_chars_per_file: int = 20000,
        max_total_chars: int = 50000,
        ast_threshold: int = 256,
    ) -> dict[str, Any]:
        """Read multiple UTF-8 text files from the workspace."""
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            return {"error": "paths must be a list of strings"}

        max_chars_per_file = self._coerce_positive_int(max_chars_per_file, 20000)
        max_total_chars = self._coerce_positive_int(max_total_chars, 50000)
        ast_threshold = self._coerce_positive_int(ast_threshold, 256)
        remaining_chars = max_total_chars

        files: list[dict[str, Any]] = []
        skipped_paths: list[str] = []
        any_truncated = False

        for requested_path in paths:
            target, err = self._resolve_safe(requested_path)
            if target is None:
                files.append({"requested_path": requested_path, "error": err})
                continue
            if not target.exists():
                files.append({"requested_path": requested_path, "error": f"file not found: {requested_path}"})
                continue
            if not target.is_file():
                files.append({"requested_path": requested_path, "error": f"not a file: {target.resolve()}"})
                continue

            raw = target.read_text(encoding="utf-8", errors="replace")
            if ast_threshold > 0 and len(raw) > ast_threshold:
                source_bytes = target.read_bytes()
                language = detect_language(target, source_bytes=source_bytes)
                language_supported = False
                if language is not None:
                    try:
                        language_supported = is_language_supported(language)
                    except Exception:
                        language_supported = False
                if language_supported:
                    ast_payload = build_syntax_tree_payload(
                        target,
                        source_bytes=source_bytes,
                        language=language,
                        max_nodes=400,
                    )
                    if "error" not in ast_payload:
                        files.append(
                            {
                                "requested_path": requested_path,
                                "path": self._relative_path(target),
                                "ast": ast_payload.get("ast"),
                                "language": language,
                                "node_count": ast_payload.get("node_count"),
                                "returned_node_count": ast_payload.get("returned_node_count"),
                                "total_lines": self._total_lines(raw),
                                "ast_upgraded": True,
                                "truncated": bool(ast_payload.get("truncated")),
                            }
                        )
                        any_truncated = any_truncated or bool(ast_payload.get("truncated"))
                        continue

            if remaining_chars <= 0:
                skipped_paths.append(requested_path)
                any_truncated = True
                continue

            char_budget = min(max_chars_per_file, remaining_chars)
            content = raw[:char_budget]
            truncated = len(raw) > char_budget
            files.append(
                {
                    "requested_path": requested_path,
                    "path": self._relative_path(target),
                    "content": content,
                    "total_lines": self._total_lines(raw),
                    "truncated": truncated,
                }
            )
            remaining_chars -= len(content)
            any_truncated = any_truncated or truncated

        return {
            "requested_paths": len(paths),
            "returned_files": len(files),
            "files": files,
            "truncated": any_truncated,
            "skipped_paths": skipped_paths,
        }

    def _build_directory_entries(
        self,
        directory: Path,
        *,
        recursive: bool,
        per_directory_remaining: list[int],
        total_remaining: list[int],
    ) -> tuple[list[dict[str, Any]], bool]:
        items: list[dict[str, Any]] = []
        truncated = False

        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except Exception:
            return items, True

        for child in children:
            if self._should_skip(child):
                continue
            if per_directory_remaining[0] <= 0 or total_remaining[0] <= 0:
                truncated = True
                break

            entry = {
                "name": f"{child.name}/" if child.is_dir() else child.name,
                "path": self._relative_path(child),
                "type": "directory" if child.is_dir() else "file",
            }
            per_directory_remaining[0] -= 1
            total_remaining[0] -= 1

            if child.is_dir() and recursive:
                children_items, child_truncated = self._build_directory_entries(
                    child,
                    recursive=recursive,
                    per_directory_remaining=per_directory_remaining,
                    total_remaining=total_remaining,
                )
                entry["children"] = children_items
                truncated = truncated or child_truncated

            items.append(entry)

        return items, truncated

    def list_directories(
        self,
        paths: list[str],
        recursive: bool = False,
        max_entries_per_directory: int = 200,
        max_total_entries: int = 500,
    ) -> dict[str, Any]:
        """List multiple workspace directories in one call."""
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            return {"error": "paths must be a list of strings"}

        max_entries_per_directory = self._coerce_positive_int(max_entries_per_directory, 200)
        max_total_entries = self._coerce_positive_int(max_total_entries, 500)
        total_remaining = [max_total_entries]

        directories: list[dict[str, Any]] = []
        skipped_paths: list[str] = []
        any_truncated = False

        for requested_path in paths:
            if total_remaining[0] <= 0:
                skipped_paths.append(requested_path)
                any_truncated = True
                continue

            target, err = self._resolve_safe(requested_path)
            if target is None:
                directories.append({"requested_path": requested_path, "error": err})
                continue
            if not target.exists():
                directories.append({"requested_path": requested_path, "error": f"path not found: {requested_path}"})
                continue
            if not target.is_dir():
                directories.append({"requested_path": requested_path, "error": f"not a directory: {target.resolve()}"})
                continue

            per_directory_remaining = [max_entries_per_directory]
            entries, truncated = self._build_directory_entries(
                target,
                recursive=bool(recursive),
                per_directory_remaining=per_directory_remaining,
                total_remaining=total_remaining,
            )
            directory_result = {
                "requested_path": requested_path,
                "path": self._relative_path(target),
                "entry_count": max_entries_per_directory - per_directory_remaining[0],
                "truncated": truncated,
            }
            if recursive:
                directory_result["tree"] = entries
            else:
                directory_result["entries"] = entries
            directories.append(directory_result)
            any_truncated = any_truncated or truncated

        return {
            "requested_paths": len(paths),
            "returned_directories": len(directories),
            "directories": directories,
            "truncated": any_truncated,
            "skipped_paths": skipped_paths,
        }

    def search_text(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 100,
        case_sensitive: bool = False,
        file_glob: str | None = None,
        context_lines: int = 0,
    ) -> dict[str, Any]:
        """Search text across workspace files."""
        if not pattern:
            return {"error": "pattern is required"}

        base, err = self._resolve_safe(path)
        if base is None:
            return {"error": err}
        if not base.exists():
            return {"error": f"path not found: {path}"}

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            return {"error": f"invalid regex: {exc}"}

        max_results = self._coerce_positive_int(max_results, 100)
        context_lines = self._coerce_positive_int(context_lines, 0)

        matches: list[dict[str, Any]] = []
        truncated = False
        for fp in self._iter_workspace_files(base, file_glob=file_glob):
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            lines = text.splitlines()
            for index, line in enumerate(lines, start=1):
                if not compiled.search(line):
                    continue
                entry: dict[str, Any] = {
                    "path": self._relative_path(fp),
                    "line": index,
                    "text": line,
                }
                if context_lines > 0:
                    entry["context_before"] = lines[max(0, index - context_lines - 1) : index - 1]
                    entry["context_after"] = lines[index : index + context_lines]
                matches.append(entry)
                if len(matches) >= max_results:
                    truncated = True
                    break
            if truncated:
                break

        return {
            "pattern": pattern,
            "path": self._relative_path(base),
            "matches": matches,
            "num_matches": len(matches),
            "truncated": truncated,
        }

    def write_file(
        self,
        path: str,
        content: str,
        append: bool = False,
    ) -> dict[str, Any]:
        """Write UTF-8 text into a workspace file."""
        target, err = self._resolve_safe(path)
        if target is None:
            return {"error": err}

        created = not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            target.write_text(content, encoding="utf-8")

        return {
            "path": self._relative_path(target),
            "bytes_written": len(content.encode("utf-8")),
            "append": bool(append),
            "created": created,
        }

    def read_lines(
        self,
        path: str,
        start: int = 1,
        end: int | None = None,
    ) -> dict[str, Any]:
        """Read a 1-based inclusive line range from a workspace file."""
        target, err = self._resolve_safe(path)
        if target is None:
            return {"error": err}
        raw, load_error = self._load_file_text(target)
        if load_error is not None:
            return load_error
        assert raw is not None

        lines = self._split_lines(raw)
        total_lines = len(lines)
        start = self._coerce_positive_int(start, 1, minimum=1)
        end = total_lines if end is None else self._coerce_positive_int(end, total_lines, minimum=1)
        range_error = self._validate_line_range(total_lines=total_lines, start=start, end=end)
        if range_error:
            return {"error": range_error, "path": self._relative_path(target), "total_lines": total_lines}

        content = "".join(lines[start - 1 : end])
        return {
            "path": self._relative_path(target),
            "content": content,
            "start": start,
            "end": end,
            "total_lines": total_lines,
        }

    def insert_lines(
        self,
        path: str,
        line: int,
        content: str,
    ) -> dict[str, Any]:
        """Insert content before a 1-based line number."""
        target, err = self._resolve_safe(path)
        if target is None:
            return {"error": err}
        raw, load_error = self._load_file_text(target)
        if load_error is not None:
            return load_error
        assert raw is not None

        lines = self._split_lines(raw)
        total_lines = len(lines)
        line = self._coerce_positive_int(line, 1, minimum=1)
        range_error = self._validate_line_range(total_lines=total_lines, start=line, end=line, allow_append=True)
        if range_error:
            return {"error": range_error, "path": self._relative_path(target), "total_lines": total_lines}

        insert_lines = self._split_lines(content)
        updated_lines = lines[: line - 1] + insert_lines + lines[line - 1 :]
        target.write_text("".join(updated_lines), encoding="utf-8")
        return {
            "path": self._relative_path(target),
            "line": line,
            "inserted_lines": len(insert_lines),
            "total_lines": len(updated_lines),
        }

    def replace_lines(
        self,
        path: str,
        start: int,
        end: int,
        content: str,
    ) -> dict[str, Any]:
        """Replace a 1-based inclusive line range with new content."""
        target, err = self._resolve_safe(path)
        if target is None:
            return {"error": err}
        raw, load_error = self._load_file_text(target)
        if load_error is not None:
            return load_error
        assert raw is not None

        lines = self._split_lines(raw)
        total_lines = len(lines)
        start = self._coerce_positive_int(start, 1, minimum=1)
        end = self._coerce_positive_int(end, total_lines, minimum=1)
        range_error = self._validate_line_range(total_lines=total_lines, start=start, end=end)
        if range_error:
            return {"error": range_error, "path": self._relative_path(target), "total_lines": total_lines}

        replacement_lines = self._split_lines(content)
        updated_lines = lines[: start - 1] + replacement_lines + lines[end:]
        target.write_text("".join(updated_lines), encoding="utf-8")
        return {
            "path": self._relative_path(target),
            "start": start,
            "end": end,
            "replacement_lines": len(replacement_lines),
            "total_lines": len(updated_lines),
        }

    def delete_lines(
        self,
        path: str,
        start: int,
        end: int,
    ) -> dict[str, Any]:
        """Delete a 1-based inclusive line range."""
        target, err = self._resolve_safe(path)
        if target is None:
            return {"error": err}
        raw, load_error = self._load_file_text(target)
        if load_error is not None:
            return load_error
        assert raw is not None

        lines = self._split_lines(raw)
        total_lines = len(lines)
        start = self._coerce_positive_int(start, 1, minimum=1)
        end = self._coerce_positive_int(end, total_lines, minimum=1)
        range_error = self._validate_line_range(total_lines=total_lines, start=start, end=end)
        if range_error:
            return {"error": range_error, "path": self._relative_path(target), "total_lines": total_lines}

        updated_lines = lines[: start - 1] + lines[end:]
        target.write_text("".join(updated_lines), encoding="utf-8")
        return {
            "path": self._relative_path(target),
            "start": start,
            "end": end,
            "deleted_lines": end - start + 1,
            "total_lines": len(updated_lines),
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
        """Pin a file or line range into the current session context."""
        context, error = self._require_pin_context()
        if error is not None:
            return error
        assert context is not None

        target, err = self._resolve_safe(path)
        if target is None:
            return {"error": err}
        raw, load_error = self._load_file_text(target)
        if load_error is not None:
            return load_error
        assert raw is not None

        lines = self._split_lines(raw)
        total_lines = len(lines)

        if (start is None) ^ (end is None):
            return {"error": "start and end must both be provided for line-range pins"}

        if start is None and end is None and len(raw) > MAX_FULL_FILE_PIN_CHARS:
            return {
                "error": "file too large to pin as a whole",
                "max_chars": MAX_FULL_FILE_PIN_CHARS,
                "suggestion": "Pin a smaller line range instead.",
            }

        if start is not None and end is not None:
            start = self._coerce_positive_int(start, 1, minimum=1)
            end = self._coerce_positive_int(end, total_lines, minimum=1)
            range_error = self._validate_line_range(total_lines=total_lines, start=start, end=end)
            if range_error:
                return {"error": range_error, "path": self._relative_path(target), "total_lines": total_lines}

        state, pins = load_workspace_pins(context.session_store, context.session_id)
        candidate = build_pin_record(
            path=target.resolve(),
            lines=lines,
            start=start,
            end=end,
            start_with=start_with,
            end_with=end_with,
            reason=reason,
        )
        duplicate = find_duplicate_pin(pins, candidate)
        if duplicate is not None:
            return {
                "created": False,
                "duplicate": True,
                "pin_id": duplicate.get("pin_id"),
                "path": self._relative_path(target),
                "start": duplicate.get("start"),
                "end": duplicate.get("end"),
            }

        if len(pins) >= MAX_SESSION_PIN_COUNT:
            return {
                "error": "maximum session pin count reached",
                "max_pins": MAX_SESSION_PIN_COUNT,
            }

        pins.append(candidate)
        save_workspace_pins(context.session_store, context.session_id, state, pins)
        return {
            "created": True,
            "duplicate": False,
            "pin_id": candidate.get("pin_id"),
            "path": self._relative_path(target),
            "start": candidate.get("start"),
            "end": candidate.get("end"),
        }

    def unpin_file_context(
        self,
        pin_id: str | None = None,
        path: str | None = None,
        start: int | None = None,
        end: int | None = None,
        all: bool = False,
    ) -> dict[str, Any]:
        """Remove pinned file context records from the current session."""
        context, error = self._require_pin_context()
        if error is not None:
            return error
        assert context is not None

        if not all and pin_id is None and not (path is not None and start is not None and end is not None):
            return {"error": "provide pin_id, path with start/end, or all=True"}

        resolved_path = None
        if path is not None:
            target, err = self._resolve_safe(path)
            if target is None:
                return {"error": err}
            resolved_path = str(target.resolve())

        state, pins = load_workspace_pins(context.session_store, context.session_id)
        remaining, removed_ids = remove_pins(
            pins,
            pin_id=pin_id,
            path=resolved_path,
            start=start,
            end=end,
            remove_all=bool(all),
        )
        save_workspace_pins(context.session_store, context.session_id, state, remaining)
        return {
            "removed": len(removed_ids),
            "removed_pin_ids": removed_ids,
        }

    def _compact_read_files_args(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        compacted = {
            "paths": payload.get("paths"),
            "max_chars_per_file": payload.get("max_chars_per_file"),
            "max_total_chars": payload.get("max_total_chars"),
            "compacted": True,
        }
        if "ast_threshold" in payload:
            compacted["ast_threshold"] = payload.get("ast_threshold")
        return compacted

    def _compact_list_directories_args(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        return {
            "paths": payload.get("paths"),
            "recursive": payload.get("recursive"),
            "max_entries_per_directory": payload.get("max_entries_per_directory"),
            "max_total_entries": payload.get("max_total_entries"),
            "compacted": True,
        }

    def _compact_read_files_result(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload

        compacted_files: list[dict[str, Any]] = []
        for item in payload.get("files", []):
            if not isinstance(item, dict):
                continue
            compacted_item = {k: v for k, v in item.items() if k not in {"content", "ast"}}
            if isinstance(item.get("content"), str):
                compacted_item["content"] = self._preview_text(item["content"], context.preview_chars)
            elif item.get("ast_upgraded"):
                compacted_item["ast_upgraded"] = True
                compacted_item["language"] = item.get("language")
                compacted_item["node_count"] = item.get("node_count")
                compacted_item["returned_node_count"] = item.get("returned_node_count")
            compacted_files.append(compacted_item)

        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return {
            "requested_paths": payload.get("requested_paths"),
            "returned_files": payload.get("returned_files"),
            "files": compacted_files,
            "truncated": payload.get("truncated"),
            "skipped_paths": payload.get("skipped_paths"),
            "compacted": True,
            **({"digest": hashlib.sha1(encoded.encode("utf-8", errors="replace")).hexdigest()} if context.include_hash else {}),
        }

    def _flatten_directory_names(self, items: list[dict[str, Any]], output: list[str]) -> None:
        for item in items:
            if isinstance(item, str):
                output.append(item)
                continue
            if not isinstance(item, dict):
                continue
            name = str(item.get("path") or item.get("name") or "")
            if name:
                output.append(name)
            children = item.get("children")
            if isinstance(children, list):
                self._flatten_directory_names(children, output)

    def _compact_list_directories_result(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload

        compacted_directories: list[dict[str, Any]] = []
        for item in payload.get("directories", []):
            if not isinstance(item, dict):
                continue
            preview_names: list[str] = []
            if isinstance(item.get("entries"), list):
                for entry in item["entries"]:
                    if isinstance(entry, dict):
                        preview_names.append(str(entry.get("path") or entry.get("name") or ""))
                    elif isinstance(entry, str):
                        preview_names.append(entry)
            elif isinstance(item.get("tree"), list):
                self._flatten_directory_names(item["tree"], preview_names)
            compacted_directories.append(
                {
                    "requested_path": item.get("requested_path"),
                    "path": item.get("path"),
                    "entry_count": item.get("entry_count", len(preview_names)),
                    "entries": [self._preview_text(name, context.preview_chars) for name in preview_names[:12]],
                    "truncated": item.get("truncated"),
                }
            )

        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return {
            "requested_paths": payload.get("requested_paths"),
            "returned_directories": payload.get("returned_directories"),
            "directories": compacted_directories,
            "truncated": payload.get("truncated"),
            "skipped_paths": payload.get("skipped_paths"),
            "compacted": True,
            **({"digest": hashlib.sha1(encoded.encode("utf-8", errors="replace")).hexdigest()} if context.include_hash else {}),
        }

    def _compact_search_text_result(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded) <= context.max_chars:
            return payload
        matches = payload.get("matches")
        if isinstance(matches, list):
            return {
                "pattern": payload.get("pattern"),
                "path": payload.get("path"),
                "matches": matches[:8],
                "num_matches": payload.get("num_matches"),
                "truncated": payload.get("truncated"),
                "compacted": True,
                **({"digest": hashlib.sha1(encoded.encode("utf-8", errors="replace")).hexdigest()} if context.include_hash else {}),
            }
        return payload

    def _compact_write_file_args(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        compacted: dict[str, Any] = {
            "path": payload.get("path"),
            "append": payload.get("append", False),
            "compacted": True,
        }
        content = payload.get("content")
        if isinstance(content, str) and len(content) > context.max_chars:
            compacted["content"] = {
                "compacted": True,
                "chars": len(content),
                **({"digest": hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()} if context.include_hash else {}),
            }
        else:
            compacted["content"] = content
        return compacted

    def _compact_text_edit_args(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        compacted = dict(payload)
        content = compacted.get("content")
        if isinstance(content, str) and len(content) > context.max_chars:
            compacted["content"] = self._preview_text(content, context.preview_chars)
            compacted["compacted"] = True
        return compacted


__all__ = ["WorkspaceToolkit"]
