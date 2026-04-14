"""Tests for ToolConfirmationPolicy interact_type/interact_config fields."""
from __future__ import annotations

from unchain.tools.models import ToolConfirmationPolicy


def test_policy_defaults():
    p = ToolConfirmationPolicy()
    assert p.requires_confirmation is True
    assert p.description == ""
    assert p.render_component is None
    assert p.interact_type == "confirmation"
    assert p.interact_config is None


def test_policy_with_code_diff_interact():
    cfg = {
        "title": "Edit foo.py",
        "operation": "edit",
        "path": "foo.py",
        "unified_diff": "--- a/foo.py\n+++ b/foo.py\n",
        "truncated": False,
        "total_lines": 2,
        "displayed_lines": 2,
        "fallback_description": "edit foo.py (+1 -0)",
    }
    p = ToolConfirmationPolicy(
        requires_confirmation=True,
        description="Edit foo.py",
        interact_type="code_diff",
        interact_config=cfg,
    )
    assert p.interact_type == "code_diff"
    assert p.interact_config == cfg


def test_from_raw_bool_true():
    p = ToolConfirmationPolicy.from_raw(True)
    assert p.requires_confirmation is True
    assert p.interact_type == "confirmation"
    assert p.interact_config is None


def test_from_raw_bool_false():
    p = ToolConfirmationPolicy.from_raw(False)
    assert p.requires_confirmation is False
    assert p.interact_type == "confirmation"


def test_from_raw_dict_with_interact_fields():
    raw = {
        "requires_confirmation": True,
        "description": "Edit",
        "interact_type": "code_diff",
        "interact_config": {"operation": "edit", "path": "foo.py"},
    }
    p = ToolConfirmationPolicy.from_raw(raw)
    assert p.interact_type == "code_diff"
    assert p.interact_config == {"operation": "edit", "path": "foo.py"}


def test_from_raw_dict_without_interact_fields():
    raw = {"requires_confirmation": True, "description": "ok"}
    p = ToolConfirmationPolicy.from_raw(raw)
    assert p.interact_type == "confirmation"
    assert p.interact_config is None


def test_from_raw_passes_existing_policy_through():
    original = ToolConfirmationPolicy(
        requires_confirmation=True,
        interact_type="code_diff",
        interact_config={"foo": "bar"},
    )
    p = ToolConfirmationPolicy.from_raw(original)
    assert p is original
