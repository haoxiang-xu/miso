"""Tests for the tool confirmation callback system."""

import json

from miso import (
    broth as Broth,
    tool,
    toolkit,
    ToolConfirmationRequest,
    ToolConfirmationResponse,
)
from miso.broth import ProviderTurnResult, ToolCall


# ── helpers ────────────────────────────────────────────────────────────────


def _make_agent_with_tools(*, confirmed_tool_func=None, plain_tool_func=None):
    """Return a broth agent wired with a confirmed and a plain tool."""
    agent = Broth()
    agent.provider = "ollama"

    if confirmed_tool_func is None:
        confirmed_tool_func = lambda x="default": {"executed": True, "x": x}
    if plain_tool_func is None:
        plain_tool_func = lambda: {"plain": True}

    confirmed = tool(
        name="dangerous_action",
        description="A dangerous action that needs confirmation.",
        func=confirmed_tool_func,
        requires_confirmation=True,
        parameters=[],
    )
    plain = tool(
        name="safe_action",
        func=plain_tool_func,
        parameters=[],
    )

    agent.toolkit = toolkit({
        confirmed.name: confirmed,
        plain.name: plain,
    })
    return agent


def _fake_fetch_factory(tool_calls_first_turn, final_text="done"):
    """Return a _fetch_once replacement that emits tool_calls on turn 1, then final_text."""
    state = {"turn": 0}

    def fake_fetch_once(**kwargs):
        if kwargs.get("run_id") == "observe":
            return ProviderTurnResult(
                assistant_messages=[{"role": "assistant", "content": "obs"}],
                tool_calls=[],
                final_text="obs",
            )
        state["turn"] += 1
        if state["turn"] == 1:
            return ProviderTurnResult(
                assistant_messages=[{"role": "assistant", "content": ""}],
                tool_calls=tool_calls_first_turn,
                final_text="",
            )
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": final_text}],
            tool_calls=[],
            final_text=final_text,
        )

    return fake_fetch_once


# ── unit tests: ToolConfirmationResponse.from_raw ─────────────────────────


def test_confirmation_response_from_bool_true():
    resp = ToolConfirmationResponse.from_raw(True)
    assert resp.approved is True
    assert resp.modified_arguments is None
    assert resp.reason == ""


def test_confirmation_response_from_bool_false():
    resp = ToolConfirmationResponse.from_raw(False)
    assert resp.approved is False


def test_confirmation_response_from_dict():
    resp = ToolConfirmationResponse.from_raw({
        "approved": False,
        "reason": "too dangerous",
    })
    assert resp.approved is False
    assert resp.reason == "too dangerous"
    assert resp.modified_arguments is None


def test_confirmation_response_from_dict_with_modified_args():
    resp = ToolConfirmationResponse.from_raw({
        "approved": True,
        "modified_arguments": {"x": "safe_value"},
    })
    assert resp.approved is True
    assert resp.modified_arguments == {"x": "safe_value"}


def test_confirmation_response_from_response_object():
    original = ToolConfirmationResponse(approved=True, reason="ok")
    resp = ToolConfirmationResponse.from_raw(original)
    assert resp is original


# ── unit tests: tool.requires_confirmation attribute ──────────────────────


def test_tool_requires_confirmation_default_false():
    t = tool(name="t", func=lambda: None, parameters=[])
    assert t.requires_confirmation is False


def test_tool_requires_confirmation_set_true():
    t = tool(name="t", func=lambda: None, parameters=[], requires_confirmation=True)
    assert t.requires_confirmation is True


def test_tool_from_callable_requires_confirmation():
    def fn():
        pass
    t = tool.from_callable(fn, requires_confirmation=True)
    assert t.requires_confirmation is True


def test_toolkit_register_requires_confirmation():
    tk = toolkit()
    def fn():
        pass
    registered = tk.register(fn, requires_confirmation=True)
    assert registered.requires_confirmation is True


def test_toolkit_register_overrides_requires_confirmation():
    t = tool(name="t", func=lambda: None, parameters=[], requires_confirmation=False)
    tk = toolkit()
    tk.register(t, requires_confirmation=True)
    assert t.requires_confirmation is True


def test_decorator_style_requires_confirmation():
    @tool(requires_confirmation=True)
    def delete_everything():
        """Dangerous operation."""
        return {"deleted": True}

    assert delete_everything.requires_confirmation is True
    assert delete_everything.name == "delete_everything"


# ── integration tests: confirmation gate in _execute_tool_calls ───────────


def test_tool_confirmation_approved():
    """When callback returns True, tool executes normally."""
    agent = _make_agent_with_tools()
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="dangerous_action", arguments={}),
    ])

    events = []
    messages, _ = agent.run(
        messages=[{"role": "user", "content": "go"}],
        callback=events.append,
        on_tool_confirm=lambda req: True,
        max_iterations=3,
    )

    event_types = [e["type"] for e in events]
    assert "tool_confirmed" in event_types
    assert "tool_denied" not in event_types

    # Tool actually executed
    tool_result_events = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_result_events) == 1
    result = json.loads(tool_result_events[0]["result"]) if isinstance(tool_result_events[0]["result"], str) else tool_result_events[0]["result"]
    assert result.get("executed") is True


def test_tool_confirmation_denied():
    """When callback returns False, tool does NOT execute; denied result returned."""
    call_log = []

    def guarded_func(x="default"):
        call_log.append("called")
        return {"executed": True, "x": x}

    agent = _make_agent_with_tools(confirmed_tool_func=guarded_func)
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="dangerous_action", arguments={}),
    ])

    events = []
    messages, _ = agent.run(
        messages=[{"role": "user", "content": "go"}],
        callback=events.append,
        on_tool_confirm=lambda req: False,
        max_iterations=3,
    )

    # Function was never called
    assert call_log == []

    event_types = [e["type"] for e in events]
    assert "tool_denied" in event_types
    assert "tool_confirmed" not in event_types

    # The tool result message conveys the denial
    tool_result_events = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_result_events) == 1
    result = tool_result_events[0]["result"]
    if isinstance(result, str):
        result = json.loads(result)
    assert result.get("denied") is True


def test_tool_confirmation_denied_with_reason():
    """Callback can provide a structured denial with a reason."""
    agent = _make_agent_with_tools()
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="dangerous_action", arguments={}),
    ])

    def confirm_cb(req):
        return {"approved": False, "reason": "Sorry, not allowed."}

    events = []
    agent.run(
        messages=[{"role": "user", "content": "go"}],
        callback=events.append,
        on_tool_confirm=confirm_cb,
        max_iterations=3,
    )

    denied_events = [e for e in events if e["type"] == "tool_denied"]
    assert len(denied_events) == 1
    assert denied_events[0]["reason"] == "Sorry, not allowed."


def test_tool_confirmation_modified_arguments():
    """Callback can approve with modified arguments."""
    agent = _make_agent_with_tools(
        confirmed_tool_func=lambda x="default": {"executed": True, "x": x},
    )
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="dangerous_action", arguments={"x": "original"}),
    ])

    def confirm_cb(req):
        assert req.arguments == {"x": "original"}
        return {"approved": True, "modified_arguments": {"x": "sanitized"}}

    events = []
    agent.run(
        messages=[{"role": "user", "content": "go"}],
        callback=events.append,
        on_tool_confirm=confirm_cb,
        max_iterations=3,
    )

    event_types = [e["type"] for e in events]
    assert "tool_confirmed" in event_types

    tool_result_events = [e for e in events if e["type"] == "tool_result"]
    result = tool_result_events[0]["result"]
    if isinstance(result, str):
        result = json.loads(result)
    assert result["x"] == "sanitized"


def test_tool_no_confirmation_needed():
    """Tools without requires_confirmation skip the gate entirely."""
    confirm_calls = []

    def confirm_cb(req):
        confirm_calls.append(req)
        return True

    agent = _make_agent_with_tools()
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="safe_action", arguments={}),
    ])

    events = []
    agent.run(
        messages=[{"role": "user", "content": "go"}],
        callback=events.append,
        on_tool_confirm=confirm_cb,
        max_iterations=3,
    )

    # Callback was never invoked — tool does not require confirmation
    assert confirm_calls == []

    event_types = [e["type"] for e in events]
    assert "tool_confirmed" not in event_types
    assert "tool_denied" not in event_types

    # But the tool still executed
    tool_result_events = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_result_events) == 1


def test_tool_no_callback_provided_auto_executes():
    """If requires_confirmation is True but no callback given, tool auto-executes (backward compat)."""
    call_log = []

    def guarded_func(x="default"):
        call_log.append("called")
        return {"executed": True, "x": x}

    agent = _make_agent_with_tools(confirmed_tool_func=guarded_func)
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="dangerous_action", arguments={}),
    ])

    events = []
    messages, _ = agent.run(
        messages=[{"role": "user", "content": "go"}],
        callback=events.append,
        # No on_tool_confirm provided
        max_iterations=3,
    )

    # Tool still executed even though requires_confirmation is True
    assert call_log == ["called"]

    event_types = [e["type"] for e in events]
    assert "tool_confirmed" not in event_types
    assert "tool_denied" not in event_types


def test_instance_level_on_tool_confirm():
    """on_tool_confirm set on the broth instance is used as default."""
    agent = _make_agent_with_tools()
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="dangerous_action", arguments={}),
    ])
    agent.on_tool_confirm = lambda req: False

    events = []
    messages, _ = agent.run(
        messages=[{"role": "user", "content": "go"}],
        callback=events.append,
        max_iterations=3,
    )

    event_types = [e["type"] for e in events]
    assert "tool_denied" in event_types


def test_run_level_on_tool_confirm_overrides_instance():
    """on_tool_confirm passed to run() overrides the instance-level callback."""
    agent = _make_agent_with_tools()
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="dangerous_action", arguments={}),
    ])
    # Instance-level denies
    agent.on_tool_confirm = lambda req: False
    # But run-level approves
    events = []
    messages, _ = agent.run(
        messages=[{"role": "user", "content": "go"}],
        callback=events.append,
        on_tool_confirm=lambda req: True,
        max_iterations=3,
    )

    event_types = [e["type"] for e in events]
    assert "tool_confirmed" in event_types
    assert "tool_denied" not in event_types


def test_confirmation_request_object_has_correct_fields():
    """Verify the ToolConfirmationRequest passed to the callback has the expected shape."""
    captured = []

    def confirm_cb(req):
        captured.append(req)
        return True

    agent = _make_agent_with_tools()
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="dangerous_action", arguments={"x": "val"}),
    ])

    agent.run(
        messages=[{"role": "user", "content": "go"}],
        on_tool_confirm=confirm_cb,
        max_iterations=3,
    )

    assert len(captured) == 1
    req = captured[0]
    assert isinstance(req, ToolConfirmationRequest)
    assert req.tool_name == "dangerous_action"
    assert req.call_id == "c1"
    assert req.arguments == {"x": "val"}
    assert req.description != ""  # should have the tool's description


def test_mixed_confirmed_and_plain_tools_in_same_batch():
    """When a batch has both confirmed and plain tools, only the confirmed one triggers callback."""
    confirm_calls = []

    def confirm_cb(req):
        confirm_calls.append(req.tool_name)
        return True

    agent = _make_agent_with_tools()
    agent._fetch_once = _fake_fetch_factory([
        ToolCall(call_id="c1", name="dangerous_action", arguments={}),
        ToolCall(call_id="c2", name="safe_action", arguments={}),
    ])

    events = []
    agent.run(
        messages=[{"role": "user", "content": "go"}],
        callback=events.append,
        on_tool_confirm=confirm_cb,
        max_iterations=3,
    )

    # Only the confirmed tool triggered callback
    assert confirm_calls == ["dangerous_action"]

    # Both tools produced results
    tool_result_events = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_result_events) == 2
