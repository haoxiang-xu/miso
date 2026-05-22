"""Small text formatting helpers used by the terminal renderer."""

from __future__ import annotations

from typing import Any


def truncate(text: str, limit: int) -> str:
    """Cut text at ``limit`` chars, replacing the tail with ``...`` when needed."""

    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def arg_keys(arguments: Any) -> list[str]:
    """Best-effort: list top-level keys of a tool-call ``arguments`` dict."""

    return list(arguments.keys()) if isinstance(arguments, dict) else []


def fallback_tool_result_sketch(result: Any) -> str:
    """Default one-line summary when the user hasn't supplied a custom sketch.

    Surfaces an ``error`` field when present, otherwise reports the top-level
    key list so unknown tools still produce useful output.
    """

    if not isinstance(result, dict):
        return ""
    if "error" in result:
        return f"error={result['error']!r}"
    keys = list(result.keys())
    if not keys:
        return "{}"
    return f"keys={keys}"


__all__ = ["truncate", "arg_keys", "fallback_tool_result_sketch"]
