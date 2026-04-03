from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...base import BuiltinToolkit
from ....tools.models import ToolHistoryOptimizationContext
from ..terminal_runtime import _TerminalRuntime


class TerminalToolkit(BuiltinToolkit):
    """User-facing terminal toolkit backed by the internal terminal runtime."""

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
            workspace_roots=self.workspace_roots,
            strict_mode=terminal_strict_mode,
        )
        self._register_tools()

    def _register_tools(self) -> None:
        self.register(
            self.terminal_exec,
            history_result_optimizer=self._compact_terminal_result,
        )
        self.register(self.terminal_session_open)
        self.register(self.terminal_session_write)
        self.register(self.terminal_session_close)

    def _preview_text(self, text: str, chars: int = 160) -> str:
        if len(text) <= chars * 2:
            return text
        return f"{text[:chars]}\n... <omitted {len(text) - chars * 2} chars> ...\n{text[-chars:]}"

    def terminal_exec(
        self,
        command: str,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
        shell: str = "/bin/bash",
    ) -> dict[str, Any]:
        """Execute a copy/paste-ready shell command string inside the workspace."""
        return self._terminal_runtime.execute(
            command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            shell=shell,
        )

    def terminal_session_open(
        self,
        shell: str = "/bin/bash",
        cwd: str = ".",
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        """Open a persistent shell session and return a session id."""
        return self._terminal_runtime.open_session(
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
        """Write to a shell session and collect available output."""
        return self._terminal_runtime.write_session(
            session_id,
            input=input,
            yield_time_ms=yield_time_ms,
            max_output_chars=max_output_chars,
        )

    def terminal_session_close(self, session_id: str) -> dict[str, Any]:
        """Close a persistent shell session and return final output."""
        return self._terminal_runtime.close_session(session_id)

    def shutdown(self) -> None:
        self._terminal_runtime.close_all_sessions()

    def _compact_terminal_result(
        self,
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        if not isinstance(payload, dict):
            return payload

        stdout = payload.get("stdout")
        stderr = payload.get("stderr")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded) <= context.max_chars:
            return payload

        compacted = dict(payload)
        if isinstance(stdout, str):
            compacted["stdout"] = self._preview_text(stdout, context.preview_chars)
        if isinstance(stderr, str):
            compacted["stderr"] = self._preview_text(stderr, context.preview_chars)
        compacted["compacted"] = True
        if context.include_hash:
            compacted["digest"] = hashlib.sha1(encoded.encode("utf-8", errors="replace")).hexdigest()
        return compacted


__all__ = ["TerminalToolkit"]
