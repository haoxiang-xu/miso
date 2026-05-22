"""Tests for unchain.render.TerminalRenderer.

We drive the renderer with a synthetic event stream so the tests don't depend on
a live model provider. Output is captured via a ``rich.console.Console`` writing
to an in-memory buffer.
"""

from __future__ import annotations

import copy
import io

import pytest

rich = pytest.importorskip("rich")
from rich.console import Console

from unchain.render import RenderTotals, TerminalRenderer
from unchain.render._colors import color_for_tool
from unchain.render._format import (
    arg_keys,
    fallback_tool_result_sketch,
    truncate,
)


def _make_renderer(**overrides):
    buf = io.StringIO()
    console = Console(
        file=buf,
        width=200,
        force_terminal=False,
        color_system=None,
        highlight=False,
        no_color=True,
    )
    renderer = TerminalRenderer(console=console, **overrides)
    return renderer, buf


# ---------------------------------------------------------------------------
# Helpers


def test_truncate_short_text_unchanged():
    assert truncate("hi", 10) == "hi"


def test_truncate_long_text_gets_ellipsis():
    assert truncate("abcdefghij", 5) == "ab..."


def test_arg_keys_handles_non_dict():
    assert arg_keys("not-a-dict") == []
    assert arg_keys({"a": 1, "b": 2}) == ["a", "b"]


def test_fallback_tool_result_sketch_surfaces_error():
    sketch = fallback_tool_result_sketch({"error": "boom"})
    assert "error" in sketch and "boom" in sketch


def test_fallback_tool_result_sketch_lists_keys():
    sketch = fallback_tool_result_sketch({"chunks": [], "total": 0})
    assert "chunks" in sketch and "total" in sketch


def test_color_for_tool_stable_and_overridable():
    a = color_for_tool("split_into_chunks")
    b = color_for_tool("split_into_chunks")
    assert a == b
    assert color_for_tool("foo", overrides={"foo": "red"}) == "red"


# ---------------------------------------------------------------------------
# Lifecycle / kernel events


def test_run_started_prints_provider_and_model():
    r, buf = _make_renderer()
    r({"type": "run_started", "provider": "anthropic", "model": "claude-x"})
    out = buf.getvalue()
    assert "anthropic" in out
    assert "claude-x" in out


def test_iteration_started_increments_totals():
    r, buf = _make_renderer()
    r({"type": "iteration_started", "iteration": 0})
    r({"type": "iteration_started", "iteration": 1})
    assert r.totals.iterations == 2


def test_response_received_updates_token_totals():
    r, _ = _make_renderer()
    r(
        {
            "type": "response_received",
            "bundle": {
                "input_tokens": 100,
                "output_tokens": 50,
                "consumed_tokens": 150,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
                "last_turn_input_tokens": 20,
                "last_turn_output_tokens": 30,
            },
            "has_tool_calls": True,
        }
    )
    assert r.totals.input_tokens == 100
    assert r.totals.output_tokens == 50
    assert r.totals.consumed_tokens == 150
    assert r.totals.cache_read_input_tokens == 10
    assert r.totals.cache_creation_input_tokens == 5


def test_final_message_truncates_preview():
    r, buf = _make_renderer(truncate_text=20)
    r({"type": "final_message", "content": "x" * 100})
    out = buf.getvalue()
    assert "..." in out


def test_run_completed_renders_status():
    r, buf = _make_renderer()
    r({"type": "run_completed", "status": "max_iterations"})
    assert "max_iterations" in buf.getvalue()


# ---------------------------------------------------------------------------
# Streaming events


def test_token_delta_writes_text():
    r, buf = _make_renderer()
    r({"type": "token_delta", "delta": "hello "})
    r({"type": "token_delta", "delta": "world"})
    assert "hello world" in buf.getvalue()


def test_token_delta_disabled_writes_nothing():
    r, buf = _make_renderer(show_token_deltas=False)
    r({"type": "token_delta", "delta": "should not appear"})
    assert "should not appear" not in buf.getvalue()


def test_reasoning_disabled_writes_nothing():
    r, buf = _make_renderer(show_reasoning=False)
    r({"type": "reasoning", "delta": "internal thought"})
    assert "internal thought" not in buf.getvalue()


# ---------------------------------------------------------------------------
# Default-hidden noisy events


def test_request_messages_default_hidden():
    r, buf = _make_renderer()
    r({"type": "request_messages", "messages": [1, 2, 3]})
    assert buf.getvalue() == ""


def test_request_messages_visible_when_opted_in():
    r, buf = _make_renderer(show_request_messages=True)
    r({"type": "request_messages", "messages": [1, 2, 3]})
    assert "messages_len=3" in buf.getvalue()


def test_memory_events_default_hidden():
    r, buf = _make_renderer()
    r({"type": "memory_prepare"})
    r({"type": "memory_commit"})
    assert buf.getvalue() == ""


def test_observation_default_hidden():
    r, buf = _make_renderer()
    r({"type": "observation", "text": "noisy obs"})
    assert "noisy obs" not in buf.getvalue()


# ---------------------------------------------------------------------------
# Tool execution events


def test_tool_call_and_result_roundtrip_increment_counter():
    r, buf = _make_renderer()
    r(
        {
            "type": "tool_call",
            "tool_name": "split_into_chunks",
            "call_id": "c1",
            "arguments": {"text": "..."},
        }
    )
    r(
        {
            "type": "tool_result",
            "tool_name": "split_into_chunks",
            "call_id": "c1",
            "result": {"chunks": []},
        }
    )
    assert r.totals.tool_calls["split_into_chunks"] == 1
    out = buf.getvalue()
    assert "split_into_chunks" in out
    assert "c1" in out


def test_tool_denied_renders_lock_emoji():
    r, buf = _make_renderer()
    r({"type": "tool_denied", "tool_name": "shell"})
    assert "shell" in buf.getvalue()


def test_human_input_requested_renders_prompt():
    r, buf = _make_renderer()
    r(
        {
            "type": "human_input_requested",
            "prompt": "Confirm destructive change?",
        }
    )
    assert "Confirm destructive change?" in buf.getvalue()


# ---------------------------------------------------------------------------
# Unknown events / robustness


def test_unknown_event_renders_dim_oneliner_with_keys():
    r, buf = _make_renderer()
    r({"type": "future_unknown_event", "foo": 1, "bar": 2})
    out = buf.getvalue()
    assert "future_unknown_event" in out
    assert "foo" in out and "bar" in out


def test_handler_exception_does_not_propagate():
    r, buf = _make_renderer()

    class BoomDict(dict):
        def get(self, *args, **kwargs):  # type: ignore[override]
            # Let "type" lookup succeed so dispatch finds the handler;
            # blow up on every other field access so the handler dies.
            if args and args[0] == "type":
                return "tool_call"
            raise RuntimeError("boom")

    r(BoomDict())
    out = buf.getvalue()
    # The wrapper must convert the handler exception into a printed line —
    # the run must not crash.
    assert "renderer error" in out


# ---------------------------------------------------------------------------
# Deepcopy safety


def test_renderer_deepcopy_returns_self():
    r, _ = _make_renderer()
    assert copy.deepcopy(r) is r


def test_event_with_renderer_callback_can_be_deepcopied():
    r, _ = _make_renderer()
    event = {"type": "tool_call", "callback": r}
    # If the renderer were deep-copied, rich Console RLocks would blow up.
    # __deepcopy__ returning self defends against that.
    cloned = copy.deepcopy(event)
    assert cloned["callback"] is r


# ---------------------------------------------------------------------------
# Extension hook


class _CustomRenderer(TerminalRenderer):
    def render_tool_result_sketch(self, tool_name, result):
        if tool_name == "translate_chunk" and isinstance(result, dict):
            return f"id={result.get('id')!r}"
        return super().render_tool_result_sketch(tool_name, result)


def test_render_tool_result_sketch_subclass_override():
    buf = io.StringIO()
    console = Console(file=buf, width=200, force_terminal=False, color_system=None, no_color=True)
    r = _CustomRenderer(console=console)
    r(
        {
            "type": "tool_call",
            "tool_name": "translate_chunk",
            "call_id": "c2",
            "arguments": {},
        }
    )
    r(
        {
            "type": "tool_result",
            "tool_name": "translate_chunk",
            "call_id": "c2",
            "result": {"id": "chunk-7"},
        }
    )
    assert "id='chunk-7'" in buf.getvalue()


# ---------------------------------------------------------------------------
# print_summary


def test_print_summary_after_full_run():
    r, buf = _make_renderer()
    r({"type": "run_started", "provider": "anthropic", "model": "claude-x"})
    r({"type": "iteration_started", "iteration": 0})
    r(
        {
            "type": "tool_call",
            "tool_name": "x",
            "call_id": "c",
            "arguments": {},
        }
    )
    r(
        {
            "type": "tool_result",
            "tool_name": "x",
            "call_id": "c",
            "result": {},
        }
    )
    r(
        {
            "type": "response_received",
            "bundle": {"input_tokens": 1, "output_tokens": 2, "consumed_tokens": 3},
            "has_tool_calls": False,
        }
    )
    r({"type": "run_completed", "status": "ok"})
    r.print_summary()
    out = buf.getvalue()
    assert "run summary" in out
    assert "tool calls: x=1" in out
    assert "iterations" in out


# ---------------------------------------------------------------------------
# RenderTotals dataclass


def test_render_totals_defaults_zero():
    t = RenderTotals()
    assert t.iterations == 0
    assert t.input_tokens == 0
    assert t.output_tokens == 0
    assert t.consumed_tokens == 0
    assert t.cache_read_input_tokens == 0
    assert t.cache_creation_input_tokens == 0
    assert t.tool_calls == {}
