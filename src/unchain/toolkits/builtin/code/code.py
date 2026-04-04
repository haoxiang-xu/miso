from __future__ import annotations

import fnmatch
import hashlib
import json
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...base import BuiltinToolkit
from ....tools.models import ToolHistoryOptimizationContext
from .web_fetch import WebFetchService, run_extract_model


@dataclass
class _ReadSnapshot:
    path: str
    content_sha1: str
    size: int
    mtime_ns: int
    fully_read: bool


class CodeToolkit(BuiltinToolkit):
    """Claude-style coding toolkit with guarded reads, writes, edits, glob, and grep."""

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

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ) -> None:
        super().__init__(workspace_root=workspace_root, workspace_roots=workspace_roots)
        self._read_snapshots: dict[str, dict[str, _ReadSnapshot]] = {}
        self._web_fetch_service = WebFetchService()
        self._register_tools()

    def _register_tools(self) -> None:
        self.register(
            self.read,
            description="Read a UTF-8 text file by absolute path with line-numbered output and optional line slicing.",
            history_arguments_optimizer=self._compact_read_args,
            history_result_optimizer=self._compact_read_result,
        )
        self.register(
            self.write,
            description="Create or fully overwrite a UTF-8 text file by absolute path. Existing files must be fully read first.",
            requires_confirmation=True,
            history_arguments_optimizer=self._compact_write_args,
            history_result_optimizer=self._compact_mutation_result,
        )
        self.register(
            self.edit,
            description="Replace one unique string match, or all matches when requested, in an existing UTF-8 text file by absolute path.",
            requires_confirmation=True,
            history_arguments_optimizer=self._compact_edit_args,
            history_result_optimizer=self._compact_mutation_result,
        )
        self.register(
            self.glob,
            description="List files matching a glob pattern inside the workspace, sorted by most recently modified first.",
            history_result_optimizer=self._compact_glob_result,
        )
        self.register(
            self.grep,
            description="Search UTF-8 text files inside the workspace with regex, optional glob filters, and paginated result modes.",
            history_result_optimizer=self._compact_grep_result,
        )
        self.register(
            self.web_fetch,
            description="Fetch a public web page over HTTP(S), return raw page content or run a runtime-configured extraction model.",
            requires_confirmation=True,
            history_arguments_optimizer=self._compact_web_fetch_args,
            history_result_optimizer=self._compact_web_fetch_result,
        )

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

    def _tool_runtime_config_for(self, tool_name: str) -> dict[str, Any]:
        context = self.current_execution_context
        config = getattr(context, "tool_runtime_config", None)
        if not isinstance(config, dict):
            return {}
        tool_config = config.get(tool_name)
        return dict(tool_config) if isinstance(tool_config, dict) else {}

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

    def _preview_text(self, text: str, chars: int = 160) -> str:
        if len(text) <= chars * 2:
            return text
        return f"{text[:chars]}\n... <omitted {len(text) - chars * 2} chars> ...\n{text[-chars:]}"

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

    def _build_result_digest(self, payload: Any) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(encoded.encode("utf-8", errors="replace")).hexdigest()

    def read(self, path: str, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        """Read a UTF-8 text file by absolute path with optional line slicing.

        Args:
            path: Absolute path to the file inside the workspace roots.
            offset: Zero-based line offset to start reading from.
            limit: Maximum number of lines to return. Omit for the full file.
        """
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

    def write(self, path: str, content: str) -> dict[str, Any]:
        """Create or fully overwrite a UTF-8 text file by absolute path.

        Args:
            path: Absolute path to the file inside the workspace roots.
            content: Full replacement content for the file.
        """
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

        before_bytes = len(old_raw.encode("utf-8", errors="replace"))
        after_bytes = len(content.encode("utf-8", errors="replace"))
        return {
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

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """Replace one unique string match, or all matches, in a UTF-8 text file.

        Args:
            path: Absolute path to the file inside the workspace roots.
            old_string: Existing string to replace.
            new_string: Replacement string.
            replace_all: Replace every occurrence instead of requiring a unique match.
        """
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

        return {
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

    def glob(self, pattern: str, path: str | None = None) -> dict[str, Any]:
        """List files matching a glob pattern inside the workspace.

        Args:
            pattern: Glob pattern relative to the base path, for example `**/*.py`.
            path: Optional absolute base directory or file path. Defaults to the first workspace root.
        """
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
        """Search UTF-8 text files inside the workspace with regex.

        Args:
            pattern: Regex pattern to search for.
            path: Optional absolute base directory or file path. Defaults to the first workspace root.
            glob: Optional glob filter applied to relative file paths.
            output_mode: One of `content`, `files_with_matches`, or `count`.
            context: Number of surrounding lines to include for `content` mode.
            head_limit: Maximum number of results to return.
            offset: Pagination offset for `content` and `files_with_matches` modes.
            case_sensitive: When false, search using case-insensitive regex.
            multiline: When true, allow regex matches to span newlines.
        """
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

    def web_fetch(
        self,
        url: str,
        mode: str = "raw",
        prompt: str | None = None,
        offset: int = 0,
        max_chars: int = 20000,
    ) -> dict[str, Any]:
        """Fetch a public web page and return raw content or extracted content.

        Args:
            url: Public HTTP(S) URL to fetch.
            mode: Either `raw` or `extract`.
            prompt: Extraction prompt used only when `mode="extract"`.
            offset: Zero-based character offset for `raw` mode pagination.
            max_chars: Maximum characters to return in `raw` mode. Capped at 50,000.
        """
        resolved_mode = str(mode or "raw").strip().lower()
        if resolved_mode not in {"raw", "extract"}:
            return {"ok": False, "url": url, "error": "mode must be one of: raw, extract"}

        page_result, page_content = self._web_fetch_service.fetch(url)
        result = dict(page_result)
        result["mode"] = resolved_mode
        if not result.get("ok"):
            return result
        if not isinstance(page_content, str):
            result["ok"] = False
            result["error"] = "web page content could not be processed"
            return result

        if resolved_mode == "extract":
            if not isinstance(prompt, str) or not prompt.strip():
                result["ok"] = False
                result["error"] = "prompt is required when mode=extract"
                return result
            tool_config = self._tool_runtime_config_for("web_fetch")
            extract_model = tool_config.get("extract_model")
            if not isinstance(extract_model, dict):
                result["ok"] = False
                result["error"] = (
                    "web_fetch extract mode requires runtime config at "
                    "tool_runtime_config['web_fetch']['extract_model']"
                )
                return result
            try:
                extract_output = run_extract_model(
                    url=str(result.get("final_url") or result.get("url") or url),
                    content=page_content,
                    prompt=prompt,
                    extract_model_config=extract_model,
                )
            except Exception as exc:
                result["ok"] = False
                result["error"] = f"extract failed: {type(exc).__name__}: {exc}"
                return result
            result["result"] = extract_output
            result["returned_chars"] = len(extract_output)
            result["truncated"] = False
            result["next_offset"] = None
            return result

        offset_value = self._coerce_nonnegative_int(offset, 0)
        try:
            limit_value = max(1, min(50_000, int(max_chars)))
        except (TypeError, ValueError):
            limit_value = 20_000
        chunk = page_content[offset_value : offset_value + limit_value]
        next_offset = offset_value + len(chunk) if offset_value + len(chunk) < len(page_content) else None
        result["result"] = chunk
        result["returned_chars"] = len(chunk)
        result["truncated"] = next_offset is not None
        result["next_offset"] = next_offset
        return result

    def _compact_read_args(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        return {
            "path": payload.get("path"),
            "offset": payload.get("offset"),
            "limit": payload.get("limit"),
            "compacted": True,
        }

    def _compact_read_result(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        content = payload.get("content")
        if not isinstance(content, str) or len(content) <= context.max_chars:
            return payload
        compacted = dict(payload)
        compacted["content"] = self._preview_text(content, context.preview_chars)
        compacted["compacted"] = True
        if context.include_hash:
            compacted["digest"] = hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()
        return compacted

    def _compact_write_args(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        content = payload.get("content")
        compacted = {"path": payload.get("path"), "compacted": True}
        if isinstance(content, str) and len(content) > context.max_chars:
            compacted["content"] = {
                "chars": len(content),
                "preview": self._preview_text(content, context.preview_chars),
                "digest": hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()
                if context.include_hash
                else "",
            }
        else:
            compacted["content"] = content
        return compacted

    def _compact_edit_args(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload

        def _compact_string(value: Any) -> Any:
            if not isinstance(value, str) or len(value) <= context.max_chars:
                return value
            compacted_value = {
                "chars": len(value),
                "preview": self._preview_text(value, context.preview_chars),
            }
            if context.include_hash:
                compacted_value["digest"] = hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()
            return compacted_value

        return {
            "path": payload.get("path"),
            "old_string": _compact_string(payload.get("old_string")),
            "new_string": _compact_string(payload.get("new_string")),
            "replace_all": payload.get("replace_all", False),
            "compacted": True,
        }

    def _compact_mutation_result(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded) <= context.max_chars:
            return payload
        compacted = {
            key: value
            for key, value in payload.items()
            if key not in {"old_string", "new_string", "original_file"}
        }
        compacted["compacted"] = True
        if context.include_hash:
            compacted["digest"] = hashlib.sha1(encoded.encode("utf-8", errors="replace")).hexdigest()
        return compacted

    def _compact_glob_result(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        matches = payload.get("matches")
        if not isinstance(matches, list) or len(matches) <= 20:
            return payload
        compacted = dict(payload)
        compacted["matches"] = matches[:20]
        compacted["compacted"] = True
        if context.include_hash:
            compacted["digest"] = self._build_result_digest(payload)
        return compacted

    def _compact_grep_result(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        compacted = dict(payload)
        if isinstance(compacted.get("matches"), list) and len(compacted["matches"]) > 8:
            compacted["matches"] = compacted["matches"][:8]
            compacted["compacted"] = True
        if isinstance(compacted.get("files"), list) and len(compacted["files"]) > 20:
            compacted["files"] = compacted["files"][:20]
            compacted["compacted"] = True
        if compacted.get("compacted") and context.include_hash:
            compacted["digest"] = self._build_result_digest(payload)
        return compacted

    def _compact_web_fetch_args(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        prompt = payload.get("prompt")
        compacted = {
            "url": payload.get("url"),
            "mode": payload.get("mode", "raw"),
            "offset": payload.get("offset"),
            "max_chars": payload.get("max_chars"),
            "compacted": True,
        }
        if isinstance(prompt, str) and prompt:
            compacted["prompt"] = (
                self._preview_text(prompt, context.preview_chars)
                if len(prompt) > context.max_chars
                else prompt
            )
            if len(prompt) > context.max_chars and context.include_hash:
                compacted["prompt_digest"] = hashlib.sha1(prompt.encode("utf-8", errors="replace")).hexdigest()
        return compacted

    def _compact_web_fetch_result(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        result_text = payload.get("result")
        if not isinstance(result_text, str):
            return payload
        if len(result_text) <= context.max_chars:
            return payload
        compacted = dict(payload)
        compacted["result"] = self._preview_text(result_text, context.preview_chars)
        compacted["compacted"] = True
        if context.include_hash:
            compacted["digest"] = hashlib.sha1(result_text.encode("utf-8", errors="replace")).hexdigest()
        return compacted


__all__ = ["CodeToolkit"]
