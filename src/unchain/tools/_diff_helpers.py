"""Diff payload builder for the code_diff interact UI.

Dependency-free (stdlib only). Returns None on any condition where a diff
should NOT be shown (binary, oversized, internal error); callers fall back
to the legacy confirmation UI in that case.
"""
from __future__ import annotations

import difflib
import logging
from typing import Any

log = logging.getLogger(__name__)

_MAX_LINES_DEFAULT = 200
_MAX_BYTES_DEFAULT = 1_000_000


def _coerce_text(value: str | bytes | None) -> str | None:
    """Return a UTF-8 string, or None for binary / undecodable input."""
    if value is None:
        return ""
    if isinstance(value, str):
        if "\x00" in value:
            return None
        return value
    if isinstance(value, (bytes, bytearray)):
        if b"\x00" in value:
            return None
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def build_code_diff_payload(
    path: str,
    old_content: str | bytes | None,
    new_content: str | bytes | None,
    operation: str,
    *,
    max_lines: int = _MAX_LINES_DEFAULT,
    max_bytes: int = _MAX_BYTES_DEFAULT,
) -> dict[str, Any] | None:
    """Build a single file entry for a code_diff interact_config.

    Returns None when code_diff is NOT appropriate — caller must fall back
    to the legacy confirmation path (binary content, oversized, or any
    unexpected exception, which is logged at WARNING).
    """
    try:
        old_text = _coerce_text(old_content)
        new_text = _coerce_text(new_content)
        if old_text is None or new_text is None:
            return None

        old_bytes_len = len(old_text.encode("utf-8", errors="replace"))
        new_bytes_len = len(new_text.encode("utf-8", errors="replace"))
        if old_bytes_len + new_bytes_len > max_bytes:
            return None

        old_norm = _normalize_newlines(old_text)
        new_norm = _normalize_newlines(new_text)

        if operation == "create":
            sub_operation = "create"
        elif operation == "delete":
            sub_operation = "delete"
        else:
            sub_operation = "edit"

        if old_norm == new_norm:
            return {
                "path": path,
                "sub_operation": sub_operation,
                "unified_diff": "",
                "truncated": False,
                "total_lines": 0,
                "displayed_lines": 0,
            }

        old_lines = old_norm.splitlines(keepends=False)
        new_lines = new_norm.splitlines(keepends=False)

        diff_iter = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=3,
        )
        all_lines = list(diff_iter)
        total_lines = len(all_lines)

        truncated = total_lines > max_lines
        displayed = all_lines[:max_lines] if truncated else all_lines

        unified_diff = "\n".join(displayed)
        if unified_diff and not unified_diff.endswith("\n"):
            unified_diff += "\n"

        return {
            "path": path,
            "sub_operation": sub_operation,
            "unified_diff": unified_diff,
            "truncated": truncated,
            "total_lines": total_lines,
            "displayed_lines": min(total_lines, max_lines),
        }
    except Exception:
        log.warning("build_code_diff_payload failed for %s", path, exc_info=True)
        return None
