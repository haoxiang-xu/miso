"""Unit tests for build_code_diff_payload."""
from __future__ import annotations

import pytest

from unchain.tools._diff_helpers import build_code_diff_payload


def test_normal_edit_small_file():
    old = "line 1\nline 2\nline 3\n"
    new = "line 1\nline TWO\nline 3\n"
    result = build_code_diff_payload("foo.py", old, new, "edit")
    assert result is not None
    assert result["path"] == "foo.py"
    assert result["sub_operation"] == "edit"
    assert result["truncated"] is False
    assert "line TWO" in result["unified_diff"]
    assert "-line 2" in result["unified_diff"]
    assert "+line TWO" in result["unified_diff"]
    assert result["total_lines"] > 0
    assert result["displayed_lines"] == result["total_lines"]


def test_create_mode():
    result = build_code_diff_payload("new.py", "", "hello\nworld\n", "create")
    assert result is not None
    assert result["sub_operation"] == "create"
    assert "+hello" in result["unified_diff"]
    assert "+world" in result["unified_diff"]


def test_delete_mode_sanity():
    result = build_code_diff_payload("gone.py", "bye\nbye\n", "", "delete")
    assert result is not None
    assert result["sub_operation"] == "delete"
    assert "-bye" in result["unified_diff"]


def test_truncation_over_200_lines():
    old = "".join(f"line {i}\n" for i in range(300))
    new = "".join(f"LINE {i}\n" for i in range(300))
    result = build_code_diff_payload("big.py", old, new, "edit", max_lines=200)
    assert result is not None
    assert result["truncated"] is True
    assert result["displayed_lines"] == 200
    assert result["total_lines"] > 200


def test_binary_bytes_with_nul_returns_none():
    result = build_code_diff_payload(
        "img.png",
        b"\x89PNG\r\n\x1a\n\x00\x00",
        b"\x89PNG\r\n\x1a\n\x00\x01",
        "edit",
    )
    assert result is None


def test_invalid_utf8_bytes_returns_none():
    result = build_code_diff_payload(
        "weird.bin", b"\xff\xfe\xfd", b"\xff\xfe\xfc", "edit"
    )
    assert result is None


def test_oversized_returns_none():
    big = "x" * 600_000
    result = build_code_diff_payload(
        "huge.txt", big, big + "y", "edit", max_bytes=1_000_000
    )
    assert result is None


def test_identical_returns_empty_diff_payload():
    result = build_code_diff_payload("same.py", "a\n", "a\n", "edit")
    assert result is not None
    assert result["unified_diff"] == ""
    assert result["total_lines"] == 0
    assert result["displayed_lines"] == 0


def test_crlf_lf_normalized():
    old = "line1\r\nline2\r\n"
    new = "line1\nline2\n"
    result = build_code_diff_payload("mixed.py", old, new, "edit")
    assert result is not None
    assert result["unified_diff"] == ""


def test_bytes_input_decoded_as_utf8():
    old = "héllo\n".encode("utf-8")
    new = "héllo world\n".encode("utf-8")
    result = build_code_diff_payload("u.py", old, new, "edit")
    assert result is not None
    assert "héllo world" in result["unified_diff"]


def test_exception_in_difflib_returns_none(monkeypatch):
    import unchain.tools._diff_helpers as mod

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(mod.difflib, "unified_diff", boom)
    result = build_code_diff_payload("x.py", "a\n", "b\n", "edit")
    assert result is None
