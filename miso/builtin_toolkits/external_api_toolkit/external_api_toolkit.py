from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..base import builtin_toolkit


class external_api_toolkit(builtin_toolkit):
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


__all__ = ["external_api_toolkit"]
