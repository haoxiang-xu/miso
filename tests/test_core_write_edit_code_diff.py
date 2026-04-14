"""Integration tests that CoreToolkit resolvers produce code_diff policies."""
from __future__ import annotations

from pathlib import Path

import pytest

from unchain.toolkits.builtin.core.core import CoreToolkit
from unchain.tools.models import ToolConfirmationPolicy


@pytest.fixture
def toolkit(tmp_path: Path) -> CoreToolkit:
    return CoreToolkit(workspace_root=str(tmp_path))


def _abspath(tmp_path: Path, rel: str) -> str:
    return str(tmp_path / rel)


def test_write_overwrite_builds_code_diff_policy(toolkit, tmp_path):
    target = tmp_path / "foo.py"
    target.write_text("old\n")
    policy = toolkit._resolve_write_confirmation(
        {"path": _abspath(tmp_path, "foo.py"), "content": "new\n"},
        None,
    )
    assert isinstance(policy, ToolConfirmationPolicy)
    assert policy.requires_confirmation is True
    assert policy.interact_type == "code_diff"
    cfg = policy.interact_config
    assert isinstance(cfg, dict)
    assert cfg["operation"] == "edit"
    assert cfg["path"] == str(target.resolve())
    assert "-old" in cfg["unified_diff"]
    assert "+new" in cfg["unified_diff"]
    assert cfg["truncated"] is False


def test_write_new_path_builds_create_diff(toolkit, tmp_path):
    policy = toolkit._resolve_write_confirmation(
        {"path": _abspath(tmp_path, "brand_new.py"), "content": "hello\n"},
        None,
    )
    assert policy.interact_type == "code_diff"
    assert policy.interact_config["operation"] == "create"


def test_write_binary_existing_falls_back(toolkit, tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    policy = toolkit._resolve_write_confirmation(
        {"path": _abspath(tmp_path, "blob.bin"), "content": "text"},
        None,
    )
    assert policy.requires_confirmation is True
    assert policy.interact_type == "confirmation"
    assert policy.interact_config is None


def test_edit_simple_replace_builds_code_diff(toolkit, tmp_path):
    target = tmp_path / "foo.py"
    target.write_text("hello world\n")
    policy = toolkit._resolve_edit_confirmation(
        {
            "path": _abspath(tmp_path, "foo.py"),
            "old_string": "world",
            "new_string": "there",
        },
        None,
    )
    assert policy.interact_type == "code_diff"
    cfg = policy.interact_config
    assert cfg["operation"] == "edit"
    assert "-hello world" in cfg["unified_diff"]
    assert "+hello there" in cfg["unified_diff"]


def test_edit_target_missing_falls_back(toolkit, tmp_path):
    policy = toolkit._resolve_edit_confirmation(
        {
            "path": _abspath(tmp_path, "nope.py"),
            "old_string": "x",
            "new_string": "y",
        },
        None,
    )
    assert policy.requires_confirmation is True
    assert policy.interact_type == "confirmation"


def test_edit_old_string_not_found_falls_back(toolkit, tmp_path):
    (tmp_path / "foo.py").write_text("alpha\n")
    policy = toolkit._resolve_edit_confirmation(
        {
            "path": _abspath(tmp_path, "foo.py"),
            "old_string": "MISSING",
            "new_string": "y",
        },
        None,
    )
    assert policy.interact_type == "confirmation"


def test_edit_large_diff_is_truncated(toolkit, tmp_path):
    lines = [f"line {i}" for i in range(400)]
    (tmp_path / "big.py").write_text("\n".join(lines) + "\n")
    policy = toolkit._resolve_edit_confirmation(
        {
            "path": _abspath(tmp_path, "big.py"),
            "old_string": "line ",
            "new_string": "LINE ",
            "replace_all": True,
        },
        None,
    )
    assert policy.interact_type == "code_diff"
    cfg = policy.interact_config
    assert cfg["truncated"] is True
    assert cfg["displayed_lines"] == 200


def test_write_resolver_registered_on_tool(toolkit):
    tool = toolkit.tools.get("write")
    assert tool is not None
    assert tool.requires_confirmation is True
    assert callable(tool.confirmation_resolver)


def test_edit_resolver_registered_on_tool(toolkit):
    tool = toolkit.tools.get("edit")
    assert tool is not None
    assert tool.requires_confirmation is True
    assert callable(tool.confirmation_resolver)
