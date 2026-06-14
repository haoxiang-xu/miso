from __future__ import annotations

import shutil
import subprocess
from typing import Any

from ....tools.toolkit import Toolkit


_AGENT_REACH_INSTALL = "pip install agent-reach or pip install agent-reach[all]"


class AgentReachToolkit(Toolkit):
    """Low-risk Agent-Reach integration toolkit.

    This wrapper exposes status and read-only fetch helpers only. It deliberately
    does not install packages, import cookies, configure credentials, or publish
    to social platforms.
    """

    def __init__(self) -> None:
        super().__init__()
        self.register_many(
            self.agent_reach_status,
            self.agent_reach_read_web,
            self.agent_reach_youtube_metadata,
        )

    def _missing_agent_reach(self, tool_name: str) -> dict[str, Any]:
        return {
            "ok": False,
            "tool": tool_name,
            "missing_dependency": "agent-reach",
            "error": "agent-reach is not installed",
            "install": _AGENT_REACH_INSTALL,
        }

    def _error_result(self, tool_name: str, exc: Exception) -> dict[str, Any]:
        return {
            "ok": False,
            "tool": tool_name,
            "error": f"{type(exc).__name__}: {exc}",
        }

    def _truncate(self, text: str, max_chars: int) -> tuple[str, bool]:
        try:
            limit = max(0, int(max_chars))
        except (TypeError, ValueError):
            limit = 0
        if len(text) <= limit:
            return text, False
        return text[:limit], True

    def agent_reach_status(self) -> dict[str, Any]:
        """Check Agent-Reach channel availability."""
        try:
            from agent_reach import AgentReach
        except ImportError:
            return self._missing_agent_reach("agent_reach_status")

        try:
            return AgentReach().doctor()
        except Exception as exc:
            return self._error_result("agent_reach_status", exc)

    def agent_reach_read_web(
        self,
        url: str,
        max_chars: int = 50000,
    ) -> dict[str, Any]:
        """Read a web page through Agent-Reach's Jina Reader channel.

        :param url: URL to read.
        :param max_chars: Maximum content characters to return.
        """
        clean_url = str(url or "").strip()
        if not clean_url:
            return {
                "ok": False,
                "tool": "agent_reach_read_web",
                "error": "url is required",
            }

        try:
            from agent_reach.channels.web import WebChannel
        except ImportError:
            return self._missing_agent_reach("agent_reach_read_web")

        try:
            content = str(WebChannel().read(clean_url))
        except Exception as exc:
            return self._error_result("agent_reach_read_web", exc)

        truncated_content, truncated = self._truncate(content, max_chars)
        return {
            "ok": True,
            "url": clean_url,
            "content": truncated_content,
            "truncated": truncated,
        }

    def agent_reach_youtube_metadata(
        self,
        url: str,
        max_output_chars: int = 50000,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """Fetch YouTube metadata with yt-dlp without downloading media.

        :param url: YouTube URL to inspect.
        :param max_output_chars: Maximum stdout/stderr characters to return.
        :param timeout_seconds: Maximum seconds to wait for yt-dlp.
        """
        clean_url = str(url or "").strip()
        if not clean_url:
            return {
                "ok": False,
                "tool": "agent_reach_youtube_metadata",
                "error": "url is required",
            }

        ytdlp = shutil.which("yt-dlp")
        if not ytdlp:
            return {
                "ok": False,
                "tool": "agent_reach_youtube_metadata",
                "missing_dependency": "yt-dlp",
                "error": "yt-dlp executable not found",
                "install": _AGENT_REACH_INSTALL,
            }

        try:
            timeout = max(1, int(timeout_seconds))
        except (TypeError, ValueError):
            timeout = 60

        argv = [ytdlp, "--dump-json", "--skip-download", clean_url]
        base: dict[str, Any] = {
            "ok": False,
            "url": clean_url,
            "argv": argv,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }

        try:
            completed = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            stdout, out_truncated = self._truncate(str(stdout), max_output_chars)
            stderr, err_truncated = self._truncate(str(stderr), max_output_chars)
            base.update(
                {
                    "error": f"yt-dlp timed out after {timeout}s",
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": True,
                    "truncated": out_truncated or err_truncated,
                }
            )
            return base
        except Exception as exc:
            base["error"] = f"{type(exc).__name__}: {exc}"
            return base

        stdout, out_truncated = self._truncate(completed.stdout or "", max_output_chars)
        stderr, err_truncated = self._truncate(completed.stderr or "", max_output_chars)
        base.update(
            {
                "ok": completed.returncode == 0,
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": out_truncated or err_truncated,
            }
        )
        return base


__all__ = ["AgentReachToolkit"]
