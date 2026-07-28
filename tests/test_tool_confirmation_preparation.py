from __future__ import annotations

from typing import Any

import pytest

from unchain.kernel import ToolCall
from unchain.tools import Toolkit
from unchain.tools.confirmation import (
    execute_confirmable_tool_call,
    prepare_tool_confirmation,
)


class _RecordingLoop:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit_event(
        self,
        callback: Any,
        event_type: str,
        run_id: str,
        **payload: Any,
    ) -> None:
        del callback
        self.events.append({"type": event_type, "run_id": run_id, **payload})


def _build_toolkit(
    activity: dict[str, Any],
    *,
    resolver: Any | None = None,
) -> Toolkit:
    toolkit = Toolkit()

    def write(path: str) -> dict[str, Any]:
        activity.setdefault("tool_arguments", []).append({"path": path})
        return {"path": path}

    toolkit.register(
        write,
        name="write",
        description="Write a file",
        observe=True,
        requires_confirmation=True,
        confirmation_resolver=resolver,
    )
    return toolkit


def _execute(
    *,
    toolkit: Toolkit,
    tool_call: ToolCall,
    on_tool_confirm: Any,
    loop: Any = None,
    **extra: Any,
):
    return execute_confirmable_tool_call(
        toolkit=toolkit,
        tool_call=tool_call,
        on_tool_confirm=on_tool_confirm,
        loop=loop,
        callback=None,
        run_id="run-1",
        iteration=2,
        **extra,
    )


def test_prepare_builds_request_without_executing_callback_or_tool() -> None:
    activity: dict[str, Any] = {"resolver_calls": 0}

    def resolver(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        del context
        activity["resolver_calls"] += 1
        return {
            "requires_confirmation": True,
            "description": f"Write {arguments['path']}",
            "interact_type": "code_diff",
            "interact_config": {"path": arguments["path"]},
        }

    toolkit = _build_toolkit(activity, resolver=resolver)
    arguments = {"path": "original.txt"}
    tool_call = ToolCall(call_id="call-1", name="write", arguments=arguments)

    preparation = prepare_tool_confirmation(toolkit=toolkit, tool_call=tool_call)

    assert activity == {"resolver_calls": 1}
    assert preparation.should_observe is True
    assert preparation.effective_arguments == arguments
    assert preparation.effective_arguments is not arguments
    assert preparation.requires_confirmation is True
    assert preparation.needs_confirmation_response is True
    assert preparation.resolver_error is None
    assert preparation.request is not None
    assert preparation.request.description == "Write original.txt"
    assert preparation.request.interact_type == "code_diff"
    assert preparation.request.interact_config == {"path": "original.txt"}


def test_execute_with_preparation_does_not_run_resolver_twice() -> None:
    activity: dict[str, Any] = {"resolver_calls": 0}

    def resolver(arguments: dict[str, Any], context: Any) -> bool:
        del arguments, context
        activity["resolver_calls"] += 1
        return True

    toolkit = _build_toolkit(activity, resolver=resolver)
    tool_call = ToolCall(
        call_id="call-1",
        name="write",
        arguments={"path": "original.txt"},
    )
    preparation = prepare_tool_confirmation(toolkit=toolkit, tool_call=tool_call)

    outcome = _execute(
        toolkit=toolkit,
        tool_call=tool_call,
        on_tool_confirm=lambda request: {"approved": True},
        prepared_confirmation=preparation,
    )

    assert activity["resolver_calls"] == 1
    assert activity["tool_arguments"] == [{"path": "original.txt"}]
    assert outcome.tool_result == {"path": "original.txt"}


def test_explicit_confirmation_response_skips_callback() -> None:
    activity: dict[str, Any] = {"resolver_calls": 0, "callback_calls": 0}

    def resolver(arguments: dict[str, Any], context: Any) -> bool:
        del arguments, context
        activity["resolver_calls"] += 1
        return True

    def unexpected_callback(request: Any) -> bool:
        del request
        activity["callback_calls"] += 1
        raise AssertionError("explicit response must bypass callback")

    toolkit = _build_toolkit(activity, resolver=resolver)
    tool_call = ToolCall(
        call_id="call-1",
        name="write",
        arguments={"path": "original.txt"},
    )
    preparation = prepare_tool_confirmation(toolkit=toolkit, tool_call=tool_call)

    outcome = _execute(
        toolkit=toolkit,
        tool_call=tool_call,
        on_tool_confirm=unexpected_callback,
        prepared_confirmation=preparation,
        confirmation_response={
            "approved": True,
            "modified_arguments": {"path": "modified.txt"},
        },
    )

    assert activity["resolver_calls"] == 1
    assert activity["callback_calls"] == 0
    assert activity["tool_arguments"] == [{"path": "modified.txt"}]
    assert outcome.effective_arguments == {"path": "modified.txt"}
    assert outcome.tool_result == {"path": "modified.txt"}


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            {"approved": False, "reason": "not allowed"},
            id="denied",
        ),
        pytest.param(
            {
                "approved": True,
                "modified_arguments": {"path": "modified.txt"},
            },
            id="modified-arguments",
        ),
    ],
)
def test_explicit_response_matches_legacy_callback_path(response: dict[str, Any]) -> None:
    legacy_activity: dict[str, Any] = {}
    legacy_toolkit = _build_toolkit(legacy_activity)
    explicit_activity: dict[str, Any] = {}
    explicit_toolkit = _build_toolkit(explicit_activity)
    tool_call = ToolCall(
        call_id="call-1",
        name="write",
        arguments={"path": "original.txt"},
    )
    legacy_loop = _RecordingLoop()
    explicit_loop = _RecordingLoop()

    legacy_outcome = _execute(
        toolkit=legacy_toolkit,
        tool_call=tool_call,
        on_tool_confirm=lambda request: response,
        loop=legacy_loop,
    )
    preparation = prepare_tool_confirmation(
        toolkit=explicit_toolkit,
        tool_call=tool_call,
    )
    explicit_outcome = _execute(
        toolkit=explicit_toolkit,
        tool_call=tool_call,
        on_tool_confirm=lambda request: pytest.fail("callback should not run"),
        loop=explicit_loop,
        prepared_confirmation=preparation,
        confirmation_response=response,
    )

    assert explicit_outcome == legacy_outcome
    assert explicit_activity == legacy_activity
    assert explicit_loop.events == legacy_loop.events


def test_prepared_resolver_error_converts_to_legacy_tool_error() -> None:
    activity: dict[str, Any] = {"callback_calls": 0}

    def resolver(arguments: dict[str, Any], context: Any) -> bool:
        del arguments, context
        raise ValueError("bad policy")

    def unexpected_callback(request: Any) -> bool:
        del request
        activity["callback_calls"] += 1
        return True

    toolkit = _build_toolkit(activity, resolver=resolver)
    tool_call = ToolCall(
        call_id="call-1",
        name="write",
        arguments={"path": "original.txt"},
    )
    preparation = prepare_tool_confirmation(toolkit=toolkit, tool_call=tool_call)

    assert preparation.resolver_error == "ValueError: bad policy"
    assert preparation.needs_confirmation_response is False

    outcome = _execute(
        toolkit=toolkit,
        tool_call=tool_call,
        on_tool_confirm=unexpected_callback,
        prepared_confirmation=preparation,
        confirmation_response={"approved": True},
    )

    assert outcome.tool_result == {
        "error": "tool confirmation resolver failed: ValueError: bad policy",
        "tool": "write",
    }
    assert activity == {"callback_calls": 0}


def test_legacy_no_callback_still_executes_confirmable_tool() -> None:
    activity: dict[str, Any] = {}
    toolkit = _build_toolkit(activity)
    tool_call = ToolCall(
        call_id="call-1",
        name="write",
        arguments={"path": "original.txt"},
    )

    outcome = _execute(
        toolkit=toolkit,
        tool_call=tool_call,
        on_tool_confirm=None,
    )

    assert activity["tool_arguments"] == [{"path": "original.txt"}]
    assert outcome.tool_result == {"path": "original.txt"}
