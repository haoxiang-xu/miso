from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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

    def close_all_sessions(self) -> None:
        for session_id in list(self.sessions):
            self.close_session(session_id)


__all__ = ["_TerminalRuntime"]
