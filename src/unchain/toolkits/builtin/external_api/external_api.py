from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ...base import BuiltinToolkit


class ExternalAPIToolkit(BuiltinToolkit):
    """Toolkit for calling external HTTP APIs."""

    def __init__(self, *, workspace_root: str | Path | None = None):
        super().__init__(workspace_root=workspace_root)
        self._register_api_tools()

    # ════════════════════════════════════════════════════════════════════════
    #  External API tools
    # ════════════════════════════════════════════════════════════════════════

    def _register_api_tools(self) -> None:
        self.register_many(
            self.http_get,
            self.http_post,
        )
        # git_* methods kept for backwards compatibility but no longer
        # registered as agent tools — use GitToolkit instead.

    def _truncate_text(self, text: str, max_output_chars: int) -> tuple[str, bool]:
        if max_output_chars < 0:
            max_output_chars = 0
        if len(text) <= max_output_chars:
            return text, False
        return text[:max_output_chars], True

    def _default_git_result(self) -> dict[str, Any]:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }

    def _run_git_command(
        self,
        subcommand: str,
        *,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        result = self._default_git_result()

        if not isinstance(subcommand, str) or not subcommand:
            result["error"] = "subcommand is required"
            return result

        if args is None:
            args_list: list[str] = []
        elif isinstance(args, list):
            if not all(isinstance(item, str) for item in args):
                result["error"] = "args must be a list of strings"
                return result
            args_list = args
        else:
            result["error"] = "args must be a list of strings"
            return result

        try:
            timeout_seconds = max(1, int(timeout_seconds))
        except (TypeError, ValueError):
            result["error"] = "timeout_seconds must be an integer >= 1"
            return result

        try:
            max_output_chars = max(0, int(max_output_chars))
        except (TypeError, ValueError):
            result["error"] = "max_output_chars must be an integer >= 0"
            return result

        try:
            resolved_cwd = self._resolve_workspace_path(cwd)
        except ValueError as exc:
            result["error"] = str(exc)
            result["cwd"] = cwd
            return result

        argv = ["git", "--no-pager", subcommand, *args_list]
        result["argv"] = argv
        result["cwd"] = str(resolved_cwd)

        try:
            completed = subprocess.run(
                argv,
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
                }
            )
            return result
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
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
            }
        )
        return result

    def http_get(
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

    def git_status(
        self,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Run ``git status`` in the workspace and return output."""
        return self._run_git_command(
            "status",
            args=None,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def git_log(
        self,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Run ``git log`` with optional arguments and return output."""
        return self._run_git_command(
            "log",
            args=args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def git_diff(
        self,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Run ``git diff`` with optional arguments and return output."""
        return self._run_git_command(
            "diff",
            args=args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def git_add(
        self,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Run ``git add`` with optional arguments and return output."""
        return self._run_git_command(
            "add",
            args=args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def git_commit(
        self,
        message: str,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Run ``git commit -m <message>`` with optional arguments and return output."""
        if not isinstance(message, str) or not message.strip():
            return {
                "ok": False,
                "error": "message is required",
            }
        commit_args = ["-m", message]
        if args:
            commit_args.extend(args)
        return self._run_git_command(
            "commit",
            args=commit_args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def git_checkout(
        self,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Run ``git checkout`` with optional arguments and return output."""
        return self._run_git_command(
            "checkout",
            args=args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def git_branch(
        self,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int = 30,
        max_output_chars: int = 20000,
    ) -> dict[str, Any]:
        """Run ``git branch`` with optional arguments and return output."""
        return self._run_git_command(
            "branch",
            args=args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def http_post(
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
                method="POST",
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


__all__ = ["ExternalAPIToolkit"]
