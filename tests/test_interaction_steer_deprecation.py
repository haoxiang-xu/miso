"""Deprecation-shim tests for the steer -> queued-turns rename.

``unchain.interaction.steer`` and the old package-level names must keep
working for one release, but only through a DeprecationWarning, and they
must be the *same objects* as the new ``queue_turns`` implementations.
"""

import importlib
import sys

import pytest

from unchain.interaction import queue_turns


def _fresh_import_steer_shim():
    # The module-body warning only fires on actual import, so drop any
    # cached module first to make the test order-independent.
    sys.modules.pop("unchain.interaction.steer", None)
    return importlib.import_module("unchain.interaction.steer")


def test_importing_steer_module_emits_deprecation_warning():
    with pytest.warns(DeprecationWarning, match="queue_turns"):
        _fresh_import_steer_shim()


def test_shim_names_are_the_new_objects():
    with pytest.warns(DeprecationWarning):
        shim = _fresh_import_steer_shim()
    assert shim.SteerBuffer is queue_turns.QueuedTurnBuffer
    assert shim.merge_steered_texts is queue_turns.merge_queued_turn_texts


def test_shim_buffer_is_functionally_identical():
    with pytest.warns(DeprecationWarning):
        shim = _fresh_import_steer_shim()
    buf = shim.SteerBuffer()
    assert buf.drain_merged() is None
    buf.post("a")
    buf.post("b")
    merged = buf.drain_merged()
    assert "1. a" in merged and "2. b" in merged
    assert shim.merge_steered_texts(["only one"]) == "only one"


def test_package_level_old_names_warn_and_alias_to_new():
    import unchain.interaction as interaction

    with pytest.warns(DeprecationWarning, match="QueuedTurnBuffer"):
        assert interaction.SteerBuffer is queue_turns.QueuedTurnBuffer
    with pytest.warns(DeprecationWarning, match="merge_queued_turn_texts"):
        assert interaction.merge_steered_texts is queue_turns.merge_queued_turn_texts


def test_from_import_of_old_package_name_warns():
    # `from unchain.interaction import SteerBuffer` is the documented old
    # spelling — it must still work, with a warning.
    import unchain.interaction as interaction

    with pytest.warns(DeprecationWarning):
        old_cls = getattr(interaction, "SteerBuffer")
    assert old_cls is queue_turns.QueuedTurnBuffer
