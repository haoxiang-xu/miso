from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ...base import BuiltinToolkit
from ....tools.models import ToolConfirmationPolicy

_MAX_DIFF_LINES_PER_FILE = 200
_COMMIT_DIFF_MAX_CHARS = 50_000


class GitToolkit(BuiltinToolkit):
    """Local Git workflow toolkit with fixed argv templates and workspace-scoped path validation."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ) -> None:
        super().__init__(workspace_root=workspace_root, workspace_roots=workspace_roots)
        self._register_tools()

    def _register_tools(self) -> None:
        self.register_many(self.git_status, self.git_diff)
        self.register(self.git_stage, requires_confirmation=True)
        self.register(self.git_unstage, requires_confirmation=True)
        self.register(
            self.git_commit,
            requires_confirmation=True,
            confirmation_resolver=self._resolve_commit_confirmation,
        )

    # ════════════════════════════════════════════════════════════════════════
    #  Internal helpers
    # ════════════════════════════════════════════════════════════════════════

    def _base_result(self, action: str) -> dict[str, Any]:
        return {
            "ok": False,
            "action": action,
            "cwd": None,
            "repo_root": None,
            "argv": [],
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }

    def _truncate(self, text: str, limit: int) -> tuple[str, bool]:
        if limit < 0:
            limit = 0
        if len(text) <= limit:
            return text, False
        return text[:limit], True

    def _resolve_cwd(self, cwd: str) -> tuple[Path | None, str | None]:
        try:
            return self._resolve_workspace_path(cwd), None
        except ValueError as exc:
            return None, str(exc)

    def _get_repo_root(self, cwd: Path) -> tuple[Path | None, str | None]:
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
                shell=False,
            )
        except FileNotFoundError:
            return None, "git executable not found"
        except subprocess.TimeoutExpired:
            return None, "git rev-parse timed out"
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

        if proc.returncode != 0:
            msg = proc.stderr.strip() or "not a git repository"
            return None, msg

        repo_root = Path(proc.stdout.strip()).resolve()
        for root in self.workspace_roots:
            try:
                repo_root.relative_to(root)
                return repo_root, None
            except ValueError:
                continue
        return None, f"repo root {repo_root} is outside all workspace roots"

    def _validate_path_list(
        self,
        paths: list[str],
        repo_root: Path,
    ) -> tuple[list[Path] | None, str | None]:
        if not paths:
            return None, "paths must be a non-empty list"
        resolved: list[Path] = []
        for p in paths:
            if not isinstance(p, str) or not p.strip():
                return None, "each path must be a non-empty string"
            if p.startswith("-"):
                return None, f"path must not start with '-': {p!r}"
            if p.startswith(":"):
                return None, f"path must not use pathspec magic: {p!r}"
            if p.strip() in (".", ".."):
                return None, f"path {p!r} is not allowed; use specific file paths"
            candidate = (repo_root / p).resolve()
            try:
                candidate.relative_to(repo_root)
            except ValueError:
                return None, f"path is outside repo root: {p!r}"
            resolved.append(candidate)
        return resolved, None

    def _relative_paths(self, resolved: list[Path], repo_root: Path) -> list[str]:
        return [str(p.relative_to(repo_root)) for p in resolved]

    def _run_argv(
        self,
        argv: list[str],
        cwd: Path,
        max_output_chars: int,
        timeout: int = 30,
    ) -> tuple[bool, int | None, str, str, bool, bool]:
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except FileNotFoundError:
            return False, None, "", "git executable not found", False, False
        except subprocess.TimeoutExpired as exc:
            raw_out = exc.stdout or ""
            raw_err = exc.stderr or ""
            if isinstance(raw_out, bytes):
                raw_out = raw_out.decode("utf-8", errors="replace")
            if isinstance(raw_err, bytes):
                raw_err = raw_err.decode("utf-8", errors="replace")
            out, _ = self._truncate(raw_out, max_output_chars)
            err, _ = self._truncate(raw_err, max_output_chars)
            return False, None, out, err, True, False
        except Exception as exc:
            return False, None, "", f"{type(exc).__name__}: {exc}", False, False

        out, out_trunc = self._truncate(completed.stdout or "", max_output_chars)
        err, err_trunc = self._truncate(completed.stderr or "", max_output_chars)
        ok = completed.returncode == 0
        return ok, completed.returncode, out, err, False, out_trunc or err_trunc

    # ════════════════════════════════════════════════════════════════════════
    #  Output parsers
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _parse_status_summary(output: str) -> list[dict[str, str]]:
        """Parse `git status --short --branch` output into structured file entries."""
        entries: list[dict[str, str]] = []
        for line in output.splitlines():
            if line.startswith("##") or not line:
                continue
            if len(line) < 3:
                continue
            index_status = line[0]
            worktree_status = line[1]
            path = line[3:]
            entries.append(
                {
                    "path": path,
                    "index_status": index_status,
                    "worktree_status": worktree_status,
                }
            )
        return entries

    @staticmethod
    def _parse_diff_file_summary(output: str) -> list[dict[str, Any]]:
        """Parse unified diff output to count additions/deletions per file."""
        file_stats: dict[str, dict[str, int]] = {}
        current_file: str | None = None
        for line in output.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
                if current_file not in file_stats:
                    file_stats[current_file] = {"additions": 0, "deletions": 0}
            elif current_file:
                if line.startswith("+") and not line.startswith("+++"):
                    file_stats[current_file]["additions"] += 1
                elif line.startswith("-") and not line.startswith("---"):
                    file_stats[current_file]["deletions"] += 1
        return [
            {"path": path, "additions": stats["additions"], "deletions": stats["deletions"]}
            for path, stats in file_stats.items()
        ]

    # ════════════════════════════════════════════════════════════════════════
    #  Tools
    # ════════════════════════════════════════════════════════════════════════

    def git_status(
        self,
        cwd: str = ".",
        include_untracked: bool = True,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Show the working tree status with branch, staged, and unstaged changes.

        :param cwd: Working directory path (must be within workspace roots).
        :param include_untracked: Whether to include untracked files in output.
        :param max_output_chars: Maximum characters to return in stdout/stderr.
        """
        result = self._base_result("status")

        cwd_path, error = self._resolve_cwd(cwd)
        if error:
            result["error"] = error
            return result
        result["cwd"] = str(cwd_path)

        repo_root, error = self._get_repo_root(cwd_path)
        if error:
            result["error"] = error
            return result
        result["repo_root"] = str(repo_root)

        untracked = "all" if include_untracked else "no"
        argv = ["git", "status", "--short", "--branch", f"--untracked-files={untracked}"]
        result["argv"] = argv

        ok, returncode, stdout, stderr, timed_out, truncated = self._run_argv(
            argv, cwd_path, max_output_chars
        )
        result.update(
            ok=ok,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=truncated,
        )
        if ok:
            result["file_summary"] = self._parse_status_summary(stdout)
        return result

    def git_diff(
        self,
        cwd: str = ".",
        staged: bool = False,
        paths: list[str] | None = None,
        context_lines: int = 3,
        max_output_chars: int = 50000,
    ) -> dict[str, Any]:
        """Show changes as a unified diff for worktree or staged content, with optional path filter.

        :param cwd: Working directory path (must be within workspace roots).
        :param staged: Show staged (cached) diff instead of worktree diff.
        :param paths: Optional list of file paths to restrict the diff.
        :param context_lines: Lines of context around each change.
        :param max_output_chars: Maximum characters to return in stdout/stderr.
        """
        result = self._base_result("diff")

        cwd_path, error = self._resolve_cwd(cwd)
        if error:
            result["error"] = error
            return result
        result["cwd"] = str(cwd_path)

        repo_root, error = self._get_repo_root(cwd_path)
        if error:
            result["error"] = error
            return result
        result["repo_root"] = str(repo_root)

        try:
            context_lines = max(0, int(context_lines))
        except (TypeError, ValueError):
            result["error"] = "context_lines must be an integer >= 0"
            return result

        argv: list[str] = ["git", "diff", "--no-ext-diff", f"--unified={context_lines}"]
        if staged:
            argv.append("--cached")

        if paths is not None:
            if not isinstance(paths, list):
                result["error"] = "paths must be a list of strings"
                return result
            resolved_paths, error = self._validate_path_list(paths, repo_root)
            if error:
                result["error"] = error
                return result
            rel_paths = self._relative_paths(resolved_paths, repo_root)
            argv += ["--", *rel_paths]

        result["argv"] = argv

        ok, returncode, stdout, stderr, timed_out, truncated = self._run_argv(
            argv, cwd_path, max_output_chars
        )
        result.update(
            ok=ok,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=truncated,
        )
        if ok:
            result["file_summary"] = self._parse_diff_file_summary(stdout)
        return result

    def git_stage(
        self,
        paths: list[str],
        cwd: str = ".",
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Stage specified files for the next commit.

        :param paths: List of file paths to stage (relative to repo root).
        :param cwd: Working directory path (must be within workspace roots).
        :param max_output_chars: Maximum characters to return in stdout/stderr.
        """
        result = self._base_result("stage")

        if not isinstance(paths, list):
            result["error"] = "paths must be a list of strings"
            return result

        cwd_path, error = self._resolve_cwd(cwd)
        if error:
            result["error"] = error
            return result
        result["cwd"] = str(cwd_path)

        repo_root, error = self._get_repo_root(cwd_path)
        if error:
            result["error"] = error
            return result
        result["repo_root"] = str(repo_root)

        resolved_paths, error = self._validate_path_list(paths, repo_root)
        if error:
            result["error"] = error
            return result

        rel_paths = self._relative_paths(resolved_paths, repo_root)
        argv = ["git", "add", "--", *rel_paths]
        result["argv"] = argv

        ok, returncode, stdout, stderr, timed_out, truncated = self._run_argv(
            argv, cwd_path, max_output_chars
        )
        result.update(
            ok=ok,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=truncated,
        )
        return result

    def git_unstage(
        self,
        paths: list[str],
        cwd: str = ".",
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Remove specified files from the staging area.

        :param paths: List of file paths to unstage (relative to repo root).
        :param cwd: Working directory path (must be within workspace roots).
        :param max_output_chars: Maximum characters to return in stdout/stderr.
        """
        result = self._base_result("unstage")

        if not isinstance(paths, list):
            result["error"] = "paths must be a list of strings"
            return result

        cwd_path, error = self._resolve_cwd(cwd)
        if error:
            result["error"] = error
            return result
        result["cwd"] = str(cwd_path)

        repo_root, error = self._get_repo_root(cwd_path)
        if error:
            result["error"] = error
            return result
        result["repo_root"] = str(repo_root)

        resolved_paths, error = self._validate_path_list(paths, repo_root)
        if error:
            result["error"] = error
            return result

        rel_paths = self._relative_paths(resolved_paths, repo_root)
        argv = ["git", "restore", "--staged", "--", *rel_paths]
        result["argv"] = argv

        ok, returncode, stdout, stderr, timed_out, truncated = self._run_argv(
            argv, cwd_path, max_output_chars
        )
        result.update(
            ok=ok,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=truncated,
        )
        return result

    def git_commit(
        self,
        message: str,
        cwd: str = ".",
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Commit currently staged changes with the given message.

        Only commits already-staged content. Does not auto-stage files.
        Fails immediately if nothing is staged.

        :param message: Commit message (required, non-empty).
        :param cwd: Working directory path (must be within workspace roots).
        :param max_output_chars: Maximum characters to return in stdout/stderr.
        """
        result = self._base_result("commit")

        if not isinstance(message, str) or not message.strip():
            result["error"] = "message is required"
            return result

        cwd_path, error = self._resolve_cwd(cwd)
        if error:
            result["error"] = error
            return result
        result["cwd"] = str(cwd_path)

        repo_root, error = self._get_repo_root(cwd_path)
        if error:
            result["error"] = error
            return result
        result["repo_root"] = str(repo_root)

        # Verify there is something staged before attempting the commit
        check = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=str(cwd_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
        if check.returncode == 0:
            result["error"] = "nothing to commit (no staged changes)"
            return result

        argv = ["git", "commit", "-m", message]
        result["argv"] = argv

        ok, returncode, stdout, stderr, timed_out, truncated = self._run_argv(
            argv, cwd_path, max_output_chars
        )
        result.update(
            ok=ok,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=truncated,
        )
        return result

    # ════════════════════════════════════════════════════════════════════════
    #  Confirmation resolver
    # ════════════════════════════════════════════════════════════════════════

    def _resolve_commit_confirmation(
        self,
        args: dict[str, Any],
        ctx: Any,
    ) -> ToolConfirmationPolicy:
        message = args.get("message", "")
        description = f"Commit staged changes with message: {message!r}"

        try:
            cwd_path, cwd_error = self._resolve_cwd(str(args.get("cwd", ".")))
            if cwd_error or cwd_path is None:
                return ToolConfirmationPolicy(requires_confirmation=True, description=description)

            repo_root, repo_error = self._get_repo_root(cwd_path)
            if repo_error or repo_root is None:
                return ToolConfirmationPolicy(requires_confirmation=True, description=description)

            proc = subprocess.run(
                ["git", "diff", "--staged", "--no-ext-diff", "--unified=5"],
                cwd=str(cwd_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                check=False,
                shell=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return ToolConfirmationPolicy(requires_confirmation=True, description=description)

            diff_text = proc.stdout
            if len(diff_text) > _COMMIT_DIFF_MAX_CHARS:
                return ToolConfirmationPolicy(requires_confirmation=True, description=description)

            file_entries = _parse_staged_diff_files(diff_text)
            if not file_entries:
                return ToolConfirmationPolicy(requires_confirmation=True, description=description)

            return ToolConfirmationPolicy(
                requires_confirmation=True,
                description=description,
                interact_type="code_diff",
                interact_config=file_entries,
            )
        except Exception:
            return ToolConfirmationPolicy(requires_confirmation=True, description=description)


# ════════════════════════════════════════════════════════════════════════════
#  Diff parser (module-level, no I/O)
# ════════════════════════════════════════════════════════════════════════════

def _parse_staged_diff_files(diff_text: str) -> list[dict[str, Any]]:
    """Split a unified diff into per-file entries matching the code_diff interact_config schema."""
    sections: list[tuple[str, list[str]]] = []
    current_path: str | None = None
    current_lines: list[str] = []

    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_path is not None:
                sections.append((current_path, current_lines))
            # Extract b/<path> from "diff --git a/<path> b/<path>"
            parts = line.rstrip("\n").split(" b/", 1)
            current_path = parts[1] if len(parts) == 2 else None
            current_lines = [line]
        elif current_path is not None:
            current_lines.append(line)

    if current_path is not None:
        sections.append((current_path, current_lines))

    entries: list[dict[str, Any]] = []
    for path, lines in sections:
        header_text = "".join(lines[:10])
        if "new file mode" in header_text:
            sub_operation = "create"
        elif "deleted file mode" in header_text:
            sub_operation = "delete"
        else:
            sub_operation = "edit"

        total_lines = len(lines)
        truncated = total_lines > _MAX_DIFF_LINES_PER_FILE
        displayed = lines[:_MAX_DIFF_LINES_PER_FILE] if truncated else lines
        unified_diff = "".join(displayed)
        if unified_diff and not unified_diff.endswith("\n"):
            unified_diff += "\n"

        entries.append(
            {
                "path": path,
                "sub_operation": sub_operation,
                "unified_diff": unified_diff,
                "truncated": truncated,
                "total_lines": total_lines,
                "displayed_lines": min(total_lines, _MAX_DIFF_LINES_PER_FILE),
            }
        )
    return entries


__all__ = ["GitToolkit"]
