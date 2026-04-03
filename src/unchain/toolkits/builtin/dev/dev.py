from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ...base import BuiltinToolkit
from ....tools.models import ToolHistoryOptimizationContext
from ....workspace.syntax import (
    build_syntax_tree_payload,
    detect_language,
    is_language_supported,
)
from ..terminal_runtime import _TerminalRuntime


class DevToolkit(BuiltinToolkit):
    """Unified developer toolkit: bash, read, edit, write, glob, grep.

    Modeled after Claude Code's tool design — 6 focused tools, each with a
    single clear responsibility.  The system prompt steers the model to use
    dedicated tools (read/edit/write/glob/grep) instead of bash for file ops.
    """

    _SKIP_DIR_NAMES: set[str] = {
        ".git", "node_modules", "__pycache__", ".tox", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", ".next", ".nuxt", "dist",
        "build", ".venv", "venv", ".egg-info",
    }

    _AST_AUTO_THRESHOLD = 256

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
        terminal_strict_mode: bool = True,
    ) -> None:
        super().__init__(workspace_root=workspace_root, workspace_roots=workspace_roots)
        self._terminal_runtime = _TerminalRuntime(
            self.workspace_root,
            strict_mode=terminal_strict_mode,
        )
        self._register_tools()

    def _register_tools(self) -> None:
        self.register(self.bash)
        self.register(
            self.read,
            history_result_optimizer=self._compact_read_result,
        )
        self.register(
            self.edit,
            requires_confirmation=True,
            history_arguments_optimizer=self._compact_edit_args,
        )
        self.register(
            self.write,
            requires_confirmation=True,
            history_arguments_optimizer=self._compact_write_args,
        )
        self.register(self.glob)
        self.register(
            self.grep,
            observe=True,
            history_result_optimizer=self._compact_grep_result,
        )

    # ── helpers ────────────────────────────────────────────────────────────

    def _resolve_safe(self, path: str) -> tuple[Path | None, str | None]:
        try:
            return self._resolve_workspace_path(path), None
        except Exception as exc:
            return None, str(exc)

    def _relative_path(self, resolved: Path) -> str:
        """Return path relative to whichever workspace root contains it."""
        for root in self.workspace_roots:
            try:
                return str(resolved.relative_to(root))
            except ValueError:
                continue
        return str(resolved)

    def _should_skip(self, path: Path) -> bool:
        return any(part in self._SKIP_DIR_NAMES for part in path.parts)

    def _format_numbered_lines(
        self,
        content: str,
        offset: int,
        limit: int,
    ) -> tuple[str, int, int, int]:
        """Return (formatted, start_line, end_line, total_lines)."""
        lines = content.splitlines(keepends=True)
        total = len(lines)
        start = min(offset, total)
        end = min(start + limit, total)
        selected = lines[start:end]
        width = len(str(end))
        formatted = "".join(
            f"{(start + i + 1):>{width}}\t{line}"
            for i, line in enumerate(selected)
        )
        return formatted, start + 1, start + len(selected), total

    def _iter_workspace_files(
        self,
        base: Path,
        file_glob: str | None = None,
    ):
        """Yield file Paths under *base*, skipping noise dirs and binaries."""
        for fp in base.rglob("*"):
            if not fp.is_file():
                continue
            if self._should_skip(fp):
                continue
            if file_glob and not fnmatch.fnmatch(fp.name, file_glob):
                continue
            yield fp

    def _preview_text(self, text: str, chars: int = 160) -> str:
        if len(text) <= chars * 2:
            return text
        return f"{text[:chars]}\n... <omitted {len(text) - chars * 2} chars> ...\n{text[-chars:]}"

    # ── Tool 1: bash ──────────────────────────────────────────────────────

    def bash(
        self,
        command: str,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Execute a shell command and return its output.

        :param command: The shell command to execute.
        :param cwd: Working directory relative to workspace root.
        :param timeout_seconds: Max execution time before killing the process.
        :param max_output_chars: Truncate stdout/stderr after this many characters.
        """
        return self._terminal_runtime.execute(
            command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    # ── Tool 2: read ──────────────────────────────────────────────────────

    def read(
        self,
        path: str,
        offset: int = 0,
        limit: int = 2000,
        max_chars: int = 30000,
        ast_mode: str = "auto",
    ) -> dict[str, Any]:
        """Read a file and return content with line numbers.

        :param path: File path relative to workspace root.
        :param offset: Skip this many lines from the start (0-based).
        :param limit: Maximum number of lines to return.
        :param max_chars: Character budget for the returned content.
        :param ast_mode: "auto" (AST for large files), "always", or "never".
        """
        target, err = self._resolve_safe(path)
        if target is None:
            return {"error": err}
        if not target.exists():
            return {"error": f"file not found: {path}"}
        if not target.is_file():
            return {"error": f"not a file: {path}"}

        raw = target.read_text(encoding="utf-8", errors="replace")

        # AST upgrade check
        if ast_mode != "never":
            should_ast = (
                ast_mode == "always"
                or (ast_mode == "auto" and len(raw) > self._AST_AUTO_THRESHOLD)
            )
            if should_ast:
                source_bytes = target.read_bytes()
                lang = detect_language(target, source_bytes=source_bytes)
                lang_ok = False
                if lang is not None:
                    try:
                        lang_ok = is_language_supported(lang)
                    except Exception:
                        pass
                if lang_ok:
                    ast_payload = build_syntax_tree_payload(
                        target,
                        source_bytes=source_bytes,
                        language=lang,
                        max_nodes=400,
                    )
                    if "error" not in ast_payload:
                        return {
                            "path": str(self._relative_path(target)),
                            "ast": ast_payload.get("ast"),
                            "language": lang,
                            "node_count": ast_payload.get("node_count"),
                            "total_lines": raw.count("\n") + (1 if raw and not raw.endswith("\n") else 0),
                            "ast_upgraded": True,
                        }

        # Numbered-line output
        formatted, start_line, end_line, total_lines = self._format_numbered_lines(
            raw, offset, limit,
        )
        truncated = len(formatted) > max_chars
        if truncated:
            formatted = formatted[:max_chars]

        return {
            "path": str(self._relative_path(target)),
            "content": formatted,
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "truncated": truncated or (end_line < total_lines),
        }

    # ── Tool 3: edit ──────────────────────────────────────────────────────

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """Perform exact string replacement in a file.

        :param path: File path relative to workspace root.
        :param old_string: The exact text to find (must exist in the file).
        :param new_string: The replacement text.
        :param replace_all: Replace all occurrences, or just the first.
        """
        target, err = self._resolve_safe(path)
        if target is None:
            return {"error": err}
        if not target.exists():
            return {"error": f"file not found: {path}"}
        if not target.is_file():
            return {"error": f"not a file: {path}"}

        content = target.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_string)

        if count == 0:
            # Provide a hint using closest line match
            hint = self._find_closest_match(content, old_string)
            result: dict[str, Any] = {
                "error": "old_string not found in file",
                "path": str(self._relative_path(target)),
            }
            if hint:
                result["hint"] = f"Did you mean: {hint!r}?"
            return result

        if count > 1 and not replace_all:
            return {
                "error": (
                    f"old_string found {count} times. "
                    "Set replace_all=True or provide more surrounding context to make it unique."
                ),
                "path": str(self._relative_path(target)),
                "occurrences": count,
            }

        max_replacements = -1 if replace_all else 1
        new_content = content.replace(old_string, new_string, max_replacements)
        target.write_text(new_content, encoding="utf-8")

        replacements = count if replace_all else 1
        return {
            "path": str(self._relative_path(target)),
            "replacements": replacements,
            "bytes_written": len(new_content.encode("utf-8")),
        }

    def _find_closest_match(self, content: str, old_string: str) -> str | None:
        """Find the closest matching line(s) in *content* for *old_string*."""
        file_lines = content.splitlines()
        search_lines = old_string.splitlines()
        if not search_lines:
            return None
        # Match against the first line of old_string
        matches = difflib.get_close_matches(
            search_lines[0].strip(),
            [line.strip() for line in file_lines],
            n=1,
            cutoff=0.6,
        )
        return matches[0] if matches else None

    # ── Tool 4: write ─────────────────────────────────────────────────────

    def write(
        self,
        path: str,
        content: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create a new file or completely overwrite an existing file.

        :param path: File path relative to workspace root.
        :param content: Full file content to write.
        :param overwrite: If False (default), fail when file already exists.
        """
        target, err = self._resolve_safe(path)
        if target is None:
            return {"error": err}

        created = not target.exists()
        if not created and not overwrite:
            return {
                "error": "file already exists, set overwrite=True to replace",
                "path": str(self._relative_path(target)),
            }

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        return {
            "path": str(self._relative_path(target)),
            "bytes_written": len(content.encode("utf-8")),
            "created": created,
        }

    # ── Tool 5: glob ──────────────────────────────────────────────────────

    def glob(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Find files matching a glob pattern, sorted by modification time.

        :param pattern: Glob pattern (e.g. "**/*.py", "src/**/*.ts").
        :param path: Base directory for the search.
        :param max_results: Maximum number of file paths to return.
        """
        base, err = self._resolve_safe(path)
        if base is None:
            return {"error": err}
        if not base.exists():
            return {"error": f"path not found: {path}"}
        if not base.is_dir():
            return {"error": f"not a directory: {path}"}

        matches: list[str] = []
        try:
            candidates = sorted(
                (p for p in base.glob(pattern) if p.is_file() and not self._should_skip(p)),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception as exc:
            return {"error": f"glob failed: {exc}"}

        for p in candidates:
            matches.append(self._relative_path(p))
            if len(matches) >= max_results:
                break

        return {
            "pattern": pattern,
            "path": self._relative_path(base),
            "matches": matches,
            "num_matches": len(matches),
            "truncated": len(candidates) > max_results,
        }

    # ── Tool 6: grep ──────────────────────────────────────────────────────

    def grep(
        self,
        pattern: str,
        path: str = ".",
        output_mode: str = "files_with_matches",
        context_lines: int = 0,
        case_sensitive: bool = False,
        file_glob: str | None = None,
        max_results: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search file contents with regex.

        :param pattern: Regex pattern to search for.
        :param path: Directory or file to search within.
        :param output_mode: "files_with_matches", "content", or "count".
        :param context_lines: Lines of context before/after each match (content mode).
        :param case_sensitive: Case-sensitive matching.
        :param file_glob: Glob filter for files (e.g. "*.py").
        :param max_results: Maximum results to return.
        :param offset: Skip this many results (pagination).
        """
        if not pattern:
            return {"error": "pattern is required"}
        if output_mode not in ("files_with_matches", "content", "count"):
            return {"error": f"invalid output_mode: {output_mode!r}"}

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

        if output_mode == "files_with_matches":
            return self._grep_files(compiled, base, file_glob, max_results, offset)
        elif output_mode == "content":
            return self._grep_content(compiled, base, file_glob, context_lines, max_results, offset, pattern)
        else:
            return self._grep_count(compiled, base, file_glob, max_results, offset, pattern)

    def _grep_files(
        self,
        compiled: re.Pattern[str],
        base: Path,
        file_glob: str | None,
        max_results: int,
        offset: int,
    ) -> dict[str, Any]:
        matches: list[str] = []
        skipped = 0
        files = [base] if base.is_file() else list(self._iter_workspace_files(base, file_glob))
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if compiled.search(text):
                if skipped < offset:
                    skipped += 1
                    continue
                matches.append(str(self._relative_path(fp)))
                if len(matches) >= max_results:
                    return {
                        "pattern": compiled.pattern,
                        "matches": matches,
                        "num_files": len(matches),
                        "truncated": True,
                    }
        return {
            "pattern": compiled.pattern,
            "matches": matches,
            "num_files": len(matches),
            "truncated": False,
        }

    def _grep_content(
        self,
        compiled: re.Pattern[str],
        base: Path,
        file_glob: str | None,
        context_lines: int,
        max_results: int,
        offset: int,
        pattern: str,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        skipped = 0
        files = [base] if base.is_file() else list(self._iter_workspace_files(base, file_glob))
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            lines = text.splitlines()
            rel = str(self._relative_path(fp))
            for i, line in enumerate(lines):
                if compiled.search(line):
                    if skipped < offset:
                        skipped += 1
                        continue
                    entry: dict[str, Any] = {
                        "path": rel,
                        "line": i + 1,
                        "text": line,
                    }
                    if context_lines > 0:
                        entry["context_before"] = lines[max(0, i - context_lines):i]
                        entry["context_after"] = lines[i + 1:i + 1 + context_lines]
                    results.append(entry)
                    if len(results) >= max_results:
                        return {
                            "pattern": pattern,
                            "matches": results,
                            "num_matches": len(results),
                            "truncated": True,
                        }
        return {
            "pattern": pattern,
            "matches": results,
            "num_matches": len(results),
            "truncated": False,
        }

    def _grep_count(
        self,
        compiled: re.Pattern[str],
        base: Path,
        file_glob: str | None,
        max_results: int,
        offset: int,
        pattern: str,
    ) -> dict[str, Any]:
        counts: list[dict[str, Any]] = []
        total = 0
        skipped = 0
        files = [base] if base.is_file() else list(self._iter_workspace_files(base, file_glob))
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            n = len(compiled.findall(text))
            if n > 0:
                if skipped < offset:
                    skipped += 1
                    continue
                counts.append({
                    "path": str(self._relative_path(fp)),
                    "count": n,
                })
                total += n
                if len(counts) >= max_results:
                    return {
                        "pattern": pattern,
                        "counts": counts,
                        "total": total,
                        "truncated": True,
                    }
        return {
            "pattern": pattern,
            "counts": counts,
            "total": total,
            "truncated": False,
        }

    # ── history optimizers ─────────────────────────────────────────────────

    def _compact_read_result(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        content = payload.get("content")
        if not isinstance(content, str) or len(content) <= context.max_chars:
            return payload
        return {
            "path": payload.get("path"),
            "start_line": payload.get("start_line"),
            "end_line": payload.get("end_line"),
            "total_lines": payload.get("total_lines"),
            "content": self._preview_text(content, context.preview_chars),
            "compacted": True,
            **({"digest": hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()} if context.include_hash else {}),
        }

    def _compact_edit_args(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        compacted: dict[str, Any] = {"path": payload.get("path"), "compacted": True}
        for key in ("old_string", "new_string"):
            val = payload.get(key)
            if isinstance(val, str) and len(val) > context.max_chars:
                compacted[key] = self._preview_text(val, context.preview_chars)
            else:
                compacted[key] = val
        if "replace_all" in payload:
            compacted["replace_all"] = payload["replace_all"]
        return compacted

    def _compact_write_args(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        content = payload.get("content")
        if not isinstance(content, str) or len(content) <= context.max_chars:
            return payload
        return {
            "path": payload.get("path"),
            "content": self._preview_text(content, context.preview_chars),
            "compacted": True,
            **({"digest": hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()} if context.include_hash else {}),
        }

    def _compact_grep_result(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded) <= context.max_chars:
            return payload
        # Keep first 8 matches as preview
        for key in ("matches", "counts"):
            items = payload.get(key)
            if isinstance(items, list) and len(items) > 8:
                return {
                    **{k: v for k, v in payload.items() if k != key},
                    key: items[:8],
                    "compacted": True,
                    f"total_{key}": len(items),
                    **({"digest": hashlib.sha1(encoded.encode("utf-8", errors="replace")).hexdigest()} if context.include_hash else {}),
                }
        return payload


__all__ = ["DevToolkit"]
