from __future__ import annotations

from pathlib import Path
from typing import Any

from .._terminal_runtime import _TerminalRuntime
from ..base import builtin_toolkit


class terminal_toolkit(builtin_toolkit):
    """Workspace-scoped toolkit that only exposes restricted terminal tools."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        terminal_strict_mode: bool = True,
    ):
        super().__init__(workspace_root=workspace_root)
        self.terminal_runtime = _TerminalRuntime(
            self.workspace_root,
            strict_mode=terminal_strict_mode,
        )
        self._register_terminal_runtime_tools()

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
        """Execute one shell command with ``shell=False`` using shlex parsing."""
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
        """Open a persistent shell session and return a session id."""
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
        """Write to a session stdin and collect available output."""
        return self.terminal_runtime.write_session(
            session_id=session_id,
            input=input,
            yield_time_ms=yield_time_ms,
            max_output_chars=max_output_chars,
        )

    def terminal_session_close(self, session_id: str) -> dict[str, Any]:
        """Close a persistent shell session and return final output."""
        return self.terminal_runtime.close_session(session_id=session_id)


__all__ = ["terminal_toolkit"]
