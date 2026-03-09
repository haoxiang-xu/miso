from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..base import builtin_toolkit


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Isolated Python runtime helper
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _PythonRuntime:
    """Manages an isolated Python virtual-environment inside a workspace."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.runtime_dir = self.workspace_root / ".miso_python_runtime"

    @property
    def python_bin(self) -> Path:
        if os.name == "nt":
            return self.runtime_dir / "Scripts" / "python.exe"
        return self.runtime_dir / "bin" / "python"

    def ensure(self, reset: bool = False) -> dict[str, Any]:
        if reset and self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir)

        if not self.python_bin.exists():
            builder = venv.EnvBuilder(with_pip=True, clear=False)
            builder.create(str(self.runtime_dir))

        return {
            "runtime_dir": str(self.runtime_dir),
            "python_bin": str(self.python_bin),
            "created": self.python_bin.exists(),
        }

    def install(self, packages: list[str], timeout_seconds: int = 180) -> dict[str, Any]:
        if not packages:
            return {"error": "packages is required"}

        self.ensure()

        command = [str(self.python_bin), "-m", "pip", "install", *packages]
        completed = subprocess.run(
            command,
            cwd=str(self.workspace_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def run_code(self, code: str, timeout_seconds: int = 30) -> dict[str, Any]:
        if not code:
            return {"error": "code is required"}

        self.ensure()

        command = [str(self.python_bin), "-c", code]
        completed = subprocess.run(
            command,
            cwd=str(self.workspace_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def reset(self) -> dict[str, Any]:
        if self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir)
            return {"reset": True, "runtime_dir": str(self.runtime_dir)}
        return {"reset": False, "runtime_dir": str(self.runtime_dir)}


@dataclass
class _TerminalSession:
    process: subprocess.Popen[bytes]
    cwd: Path
    opened_at: float
    timeout_seconds: int


class _TerminalRuntime:
    """Manages restricted shell command execution inside a workspace."""

    _BLOCKED_EXECUTABLES = {
        "sudo",
        "shutdown",
        "reboot",
        "mkfs",
        "dd",
        "curl",
        "wget",
        "ssh",
    }
    _ALLOWED_SHELLS = {"bash", "sh", "zsh"}
    _BLOCKED_COMMAND_PATTERN = re.compile(
        r"(?:^|[;&|]\s*)(sudo|shutdown|reboot|mkfs(?:\.[^\s;|&]+)?|dd|curl|wget|ssh)\b",
        re.IGNORECASE,
    )
    _BLOCKED_RM_PATTERN = re.compile(
        r"(?:^|[;&|]\s*)rm\s+-[^\n;&|]*[rf][^\n;&|]*[rf][^\n;&|]*\s+(?:--\s+)?/\s*(?:$|[;&|])",
        re.IGNORECASE,
    )

    def __init__(self, workspace_root: Path, strict_mode: bool = True):
        self.workspace_root = workspace_root.resolve()
        self.strict_mode = strict_mode
        self.sessions: dict[str, _TerminalSession] = {}

    def _truncate_text(self, text: str, max_chars: int) -> tuple[str, bool]:
        if max_chars < 0:
            max_chars = 0
        if len(text) <= max_chars:
            return text, False
        return text[:max_chars], True

    def _default_result(self) -> dict[str, Any]:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }

    def _normalize_executable(self, raw: str) -> str:
        return Path(raw).name.lower()

    def _is_command_blocked(self, command: str, argv: list[str] | None = None) -> str | None:
        if not self.strict_mode:
            return None

        command_text = command.strip()
        if self._BLOCKED_RM_PATTERN.search(command_text):
            return "command blocked by strict mode: rm -rf / is not allowed"

        match = self._BLOCKED_COMMAND_PATTERN.search(command_text)
        if match:
            return f"command blocked by strict mode: '{match.group(1)}' is not allowed"

        if argv:
            executable = self._normalize_executable(argv[0])
            if executable in self._BLOCKED_EXECUTABLES:
                return f"command blocked by strict mode: '{executable}' is not allowed"

        return None

    def _parse_command(self, command: str) -> tuple[list[str] | None, str | None]:
        if not command or not command.strip():
            return None, "command is required"
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            return None, f"invalid command: {exc}"
        if not argv:
            return None, "command is required"
        return argv, None

    def _read_pipe(self, pipe: Any, max_output_chars: int) -> tuple[str, bool]:
        if pipe is None:
            return "", False

        collected: list[str] = []
        chars = 0
        truncated = False

        while True:
            try:
                chunk = os.read(pipe.fileno(), 4096)
            except BlockingIOError:
                break
            except OSError:
                break

            if not chunk:
                break

            text = chunk.decode("utf-8", errors="replace")
            if chars >= max_output_chars:
                truncated = True
                continue

            remaining = max_output_chars - chars
            if len(text) > remaining:
                collected.append(text[:remaining])
                chars = max_output_chars
                truncated = True
            else:
                collected.append(text)
                chars += len(text)

        return "".join(collected), truncated

    def _drain_process_output(
        self,
        process: subprocess.Popen[bytes],
        max_output_chars: int,
    ) -> tuple[str, str, bool]:
        stdout, stdout_truncated = self._read_pipe(process.stdout, max_output_chars)
        stderr, stderr_truncated = self._read_pipe(process.stderr, max_output_chars)
        return stdout, stderr, stdout_truncated or stderr_truncated

    def _resolve_cwd(self, cwd: str) -> tuple[Path | None, str | None]:
        target = cwd or "."
        path_obj = Path(target)
        if path_obj.is_absolute():
            resolved = path_obj.resolve()
        else:
            resolved = (self.workspace_root / path_obj).resolve()

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            return None, "cwd is outside workspace_root"
        return resolved, None

    def execute(
        self,
        command: str,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        result = self._default_result()
        result["command"] = command

        parsed, parse_error = self._parse_command(command)
        if parse_error:
            result["error"] = parse_error
            return result

        blocked = self._is_command_blocked(command, parsed)
        if blocked:
            result["error"] = blocked
            return result

        resolved_cwd, cwd_error = self._resolve_cwd(cwd)
        if cwd_error:
            result["error"] = cwd_error
            return result

        timeout_seconds = max(1, int(timeout_seconds))
        max_output_chars = max(0, int(max_output_chars))

        try:
            completed = subprocess.run(
                parsed,
                cwd=str(resolved_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_raw = exc.stdout or ""
            stderr_raw = exc.stderr or ""
            if isinstance(stdout_raw, bytes):
                stdout_raw = stdout_raw.decode("utf-8", errors="replace")
            if isinstance(stderr_raw, bytes):
                stderr_raw = stderr_raw.decode("utf-8", errors="replace")
            stdout, stdout_truncated = self._truncate_text(stdout_raw, max_output_chars)
            stderr, stderr_truncated = self._truncate_text(stderr_raw, max_output_chars)
            result.update(
                {
                    "error": f"command timed out after {timeout_seconds}s",
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": True,
                    "truncated": stdout_truncated or stderr_truncated,
                    "cwd": str(resolved_cwd),
                    "argv": parsed,
                }
            )
            return result
        except Exception as exc:
            result["error"] = str(exc)
            return result

        stdout, stdout_truncated = self._truncate_text(completed.stdout or "", max_output_chars)
        stderr, stderr_truncated = self._truncate_text(completed.stderr or "", max_output_chars)
        result.update(
            {
                "ok": True,
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": stdout_truncated or stderr_truncated,
                "cwd": str(resolved_cwd),
                "argv": parsed,
            }
        )
        return result

    def _get_session(self, session_id: str) -> tuple[_TerminalSession | None, dict[str, Any] | None]:
        session = self.sessions.get(session_id)
        if session is None:
            result = self._default_result()
            result.update(
                {
                    "session_id": session_id,
                    "error": f"session not found: {session_id}",
                }
            )
            return None, result
        return session, None

    def _set_streams_nonblocking(self, session: _TerminalSession) -> None:
        for stream in (session.process.stdout, session.process.stderr):
            if stream is None:
                continue
            try:
                os.set_blocking(stream.fileno(), False)
            except Exception:
                continue

    def _validate_shell(self, shell: str) -> tuple[list[str] | None, str | None]:
        try:
            argv = shlex.split(shell, posix=True)
        except ValueError as exc:
            return None, f"invalid shell: {exc}"
        if not argv:
            return None, "shell is required"

        if self.strict_mode:
            executable = self._normalize_executable(argv[0])
            if executable not in self._ALLOWED_SHELLS:
                allowed = ", ".join(sorted(self._ALLOWED_SHELLS))
                return None, f"strict mode only allows shells: {allowed}"

        return argv, None

    def open_session(
        self,
        shell: str = "/bin/bash",
        cwd: str = ".",
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        result = self._default_result()

        shell_argv, shell_error = self._validate_shell(shell)
        if shell_error:
            result["error"] = shell_error
            return result

        resolved_cwd, cwd_error = self._resolve_cwd(cwd)
        if cwd_error:
            result["error"] = cwd_error
            return result

        timeout_seconds = max(1, int(timeout_seconds))

        try:
            process = subprocess.Popen(
                shell_argv,
                cwd=str(resolved_cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except Exception as exc:
            result["error"] = str(exc)
            return result

        session_id = uuid.uuid4().hex
        session = _TerminalSession(
            process=process,
            cwd=resolved_cwd,
            opened_at=time.time(),
            timeout_seconds=timeout_seconds,
        )
        self._set_streams_nonblocking(session)
        self.sessions[session_id] = session

        result.update(
            {
                "ok": True,
                "session_id": session_id,
                "cwd": str(resolved_cwd),
                "shell": shell_argv,
            }
        )
        return result

    def _terminate_session(
        self,
        session_id: str,
        session: _TerminalSession,
    ) -> tuple[int | None, str, str, bool, bool]:
        process = session.process
        timed_out = False

        if process.poll() is None:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

        stdout, stderr, truncated = self._drain_process_output(process, 20000)
        self.sessions.pop(session_id, None)
        return process.poll(), stdout, stderr, truncated, timed_out

    def write_session(
        self,
        session_id: str,
        input: str = "",
        yield_time_ms: int = 300,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        session, error_result = self._get_session(session_id)
        if error_result is not None:
            return error_result
        assert session is not None

        process = session.process
        result = self._default_result()
        result["session_id"] = session_id
        max_output_chars = max(0, int(max_output_chars))
        yield_time_ms = max(0, int(yield_time_ms))

        if time.time() - session.opened_at > session.timeout_seconds:
            returncode, stdout, stderr, truncated, timed_out = self._terminate_session(session_id, session)
            result.update(
                {
                    "error": f"session timed out after {session.timeout_seconds}s",
                    "returncode": returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": timed_out,
                    "truncated": truncated,
                }
            )
            return result

        if input:
            blocked = self._is_command_blocked(input)
            if blocked:
                result["error"] = blocked
                return result

            if process.poll() is None and process.stdin is not None:
                try:
                    process.stdin.write(input.encode("utf-8"))
                    process.stdin.flush()
                except Exception as exc:
                    result["error"] = str(exc)
                    return result

        if yield_time_ms:
            time.sleep(yield_time_ms / 1000.0)

        stdout, stderr, truncated = self._drain_process_output(process, max_output_chars)
        returncode = process.poll()
        if returncode is not None:
            self.sessions.pop(session_id, None)

        result.update(
            {
                "ok": True,
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False,
                "truncated": truncated,
            }
        )
        return result

    def close_session(self, session_id: str) -> dict[str, Any]:
        session, error_result = self._get_session(session_id)
        if error_result is not None:
            return error_result
        assert session is not None

        process = session.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

        stdout, stderr, truncated = self._drain_process_output(process, 20000)
        self.sessions.pop(session_id, None)

        result = self._default_result()
        result.update(
            {
                "ok": True,
                "session_id": session_id,
                "returncode": process.poll(),
                "stdout": stdout,
                "stderr": stderr,
                "truncated": truncated,
            }
        )
        return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  python_workspace_toolkit
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class python_workspace_toolkit(builtin_toolkit):
    """All-in-one workspace toolkit: filesystem + line-level editing + runtimes.

    Parameters
    ----------
    workspace_root:
        Root directory the toolkit is allowed to access and where the
        ``.miso_python_runtime`` venv will live.  Defaults to cwd.
    include_python_runtime:
        When *False*, only filesystem / editing tools are registered.
    include_terminal_runtime:
        When *False*, terminal execution tools are not registered.
    terminal_strict_mode:
        Enable strict safety checks for terminal commands.
    """

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        include_python_runtime: bool = True,
        include_terminal_runtime: bool = True,
        terminal_strict_mode: bool = True,
    ):
        super().__init__(workspace_root=workspace_root)
        self.python_runtime = _PythonRuntime(self.workspace_root)
        self.terminal_runtime = _TerminalRuntime(
            self.workspace_root,
            strict_mode=terminal_strict_mode,
        )

        self._register_file_tools()
        self._register_directory_tools()
        self._register_line_tools()
        self._register_api_tools()
        if include_python_runtime:
            self._register_python_runtime_tools()
        if include_terminal_runtime:
            self._register_terminal_runtime_tools()

    # ── internal line helpers ──────────────────────────────────────────────

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

    # ════════════════════════════════════════════════════════════════════════
    #  File-level tools
    # ════════════════════════════════════════════════════════════════════════

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

    # ════════════════════════════════════════════════════════════════════════
    #  Directory tools
    # ════════════════════════════════════════════════════════════════════════

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

    # ════════════════════════════════════════════════════════════════════════
    #  Line-level editing tools  (all 1-based line numbers)
    # ════════════════════════════════════════════════════════════════════════

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
        # Adjust insertion index when pasting after the copied block
        insert_idx = to_line - 1
        if to_line > end:
            # No adjustment needed — indices haven't shifted yet since we're only inserting
            pass

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
        :param to_line: Destination line number to paste before (1-based, in the *original* numbering).
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

        # Moving into the range itself is a no-op
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

        # Adjust destination after removal
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

    # ════════════════════════════════════════════════════════════════════════
    #  Python runtime tools
    # ════════════════════════════════════════════════════════════════════════

    def _register_python_runtime_tools(self) -> None:
        self.register(self.python_runtime_init)
        self.register(self.python_runtime_reset)
        self.register(self.python_runtime_install, observe=True)
        self.register(self.python_runtime_run, observe=True)

    def python_runtime_init(self, reset: bool = False) -> dict[str, Any]:
        """Create isolated Python runtime (venv) in workspace.

        :param reset: If True, delete existing runtime and recreate.
        """
        return self.python_runtime.ensure(reset=reset)

    def python_runtime_install(self, packages: list[str], timeout_seconds: int = 180) -> dict[str, Any]:
        """Install Python packages into isolated runtime via pip.

        :param packages: List of package names to install.
        :param timeout_seconds: Maximum seconds to wait for pip.
        """
        return self.python_runtime.install(packages=packages, timeout_seconds=timeout_seconds)

    def python_runtime_run(self, code: str, timeout_seconds: int = 30) -> dict[str, Any]:
        """Run Python code inside isolated runtime.

        :param code: Python code string to execute.
        :param timeout_seconds: Maximum seconds to wait for execution.
        """
        return self.python_runtime.run_code(code=code, timeout_seconds=timeout_seconds)

    def python_runtime_reset(self) -> dict[str, Any]:
        """Delete and reset isolated Python runtime."""
        return self.python_runtime.reset()

    # ════════════════════════════════════════════════════════════════════════
    #  Terminal runtime tools
    # ════════════════════════════════════════════════════════════════════════

    def _register_terminal_runtime_tools(self) -> None:
        self.register_many(
            self.terminal_exec,
            self.terminal_session_open,
            self.terminal_session_write,
            self.terminal_session_close,
        )

    def terminal_exec(
        self,
        command: str,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Execute one shell command with ``shell=False`` using shlex parsing.

        :param command: Command string to parse and execute.
        :param cwd: Working directory within workspace.
        :param timeout_seconds: Maximum seconds to wait.
        :param max_output_chars: Maximum stdout/stderr chars to return.
        """
        return self.terminal_runtime.execute(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def terminal_session_open(
        self,
        shell: str = "/bin/bash",
        cwd: str = ".",
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        """Open a persistent shell session and return a session id.

        :param shell: Shell executable, e.g. ``/bin/bash``.
        :param cwd: Working directory within workspace.
        :param timeout_seconds: Session lifetime before forced timeout.
        """
        return self.terminal_runtime.open_session(
            shell=shell,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )

    def terminal_session_write(
        self,
        session_id: str,
        input: str = "",
        yield_time_ms: int = 300,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Write to a session stdin and collect available output.

        :param session_id: Session id returned by ``terminal_session_open``.
        :param input: Text to write into session stdin.
        :param yield_time_ms: Milliseconds to wait before collecting output.
        :param max_output_chars: Maximum stdout/stderr chars to return.
        """
        return self.terminal_runtime.write_session(
            session_id=session_id,
            input=input,
            yield_time_ms=yield_time_ms,
            max_output_chars=max_output_chars,
        )

    def terminal_session_close(self, session_id: str) -> dict[str, Any]:
        """Close a persistent shell session and return final output.

        :param session_id: Session id returned by ``terminal_session_open``.
        """
        return self.terminal_runtime.close_session(session_id=session_id)

    # ════════════════════════════════════════════════════════════════════════
    #  External API tools
    # ════════════════════════════════════════════════════════════════════════

    def _register_api_tools(self) -> None:
        self.register_many(
            self.api_get,
            self.api_post,
        )

    def api_get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        max_response_chars: int = 50000,
    ) -> dict[str, Any]:
        """Send a GET request to an external API endpoint.

        :param url: Full URL to send the GET request to.
        :param headers: Optional dictionary of HTTP headers to include.
        :param timeout_seconds: Maximum seconds to wait for response.
        :param max_response_chars: Maximum response body chars to return.
        """
        try:
            headers = headers or {}
            req = urllib.request.Request(url, headers=headers, method="GET")

            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                status_code = response.status
                response_headers = dict(response.headers)

                # Read response body
                body_bytes = response.read()
                body = body_bytes.decode("utf-8", errors="replace")

                truncated = len(body) > max_response_chars
                if truncated:
                    body = body[:max_response_chars]

                return {
                    "ok": True,
                    "url": url,
                    "status_code": status_code,
                    "headers": response_headers,
                    "body": body,
                    "truncated": truncated,
                }

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            return {
                "ok": False,
                "error": f"HTTP {e.code}: {e.reason}",
                "url": url,
                "status_code": e.code,
                "body": error_body[:max_response_chars],
            }
        except urllib.error.URLError as e:
            return {
                "ok": False,
                "error": f"URL error: {str(e.reason)}",
                "url": url,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": f"Request failed: {type(e).__name__}: {str(e)}",
                "url": url,
            }

    def api_post(
        self,
        url: str,
        body: str | dict[str, Any],
        headers: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        max_response_chars: int = 50000,
    ) -> dict[str, Any]:
        """Send a POST request to an external API endpoint.

        :param url: Full URL to send the POST request to.
        :param body: Request body as string or dict (dict will be JSON-encoded).
        :param headers: Optional dictionary of HTTP headers to include.
        :param timeout_seconds: Maximum seconds to wait for response.
        :param max_response_chars: Maximum response body chars to return.
        """
        try:
            headers = headers or {}

            # Handle body encoding
            if isinstance(body, dict):
                body_str = json.dumps(body)
                data = body_str.encode("utf-8")
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "application/json"
            else:
                data = body.encode("utf-8")
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "application/json"

            req = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST"
            )

            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                status_code = response.status
                response_headers = dict(response.headers)

                # Read response body
                body_bytes = response.read()
                response_body = body_bytes.decode("utf-8", errors="replace")

                truncated = len(response_body) > max_response_chars
                if truncated:
                    response_body = response_body[:max_response_chars]

                return {
                    "ok": True,
                    "url": url,
                    "status_code": status_code,
                    "headers": response_headers,
                    "body": response_body,
                    "truncated": truncated,
                }

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            return {
                "ok": False,
                "error": f"HTTP {e.code}: {e.reason}",
                "url": url,
                "status_code": e.code,
                "body": error_body[:max_response_chars],
            }
        except urllib.error.URLError as e:
            return {
                "ok": False,
                "error": f"URL error: {str(e.reason)}",
                "url": url,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": f"Request failed: {type(e).__name__}: {str(e)}",
                "url": url,
            }


__all__ = ["python_workspace_toolkit"]
