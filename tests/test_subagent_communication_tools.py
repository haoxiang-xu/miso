import json
from types import SimpleNamespace

import pytest

from unchain.agent import Agent, SubagentModule
from unchain.kernel import ModelTurnResult, ToolCall
from unchain.subagents.plugin import SubagentToolPlugin
from unchain.subagents import (
    build_close_agent_thread_tool,
    build_read_agent_board_tool,
    build_return_handoff_to_subagent_tool,
    build_return_to_parent_tool,
    build_send_agent_message_tool,
    build_spawn_agent_thread_tool,
    build_wait_agent_messages_tool,
    build_write_agent_board_tool,
    SubagentExecutor,
    SubagentPolicy,
    SubagentState,
    SubagentTemplate,
)


def _openai_tool_turn(*, call_id: str, name: str, arguments: dict) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[
            {
                "role": "assistant",
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            }
        ],
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        final_text="",
    )


def _text_turn(text: str) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": text}],
        tool_calls=[],
        final_text=text,
    )


class SequenceModelIO:
    def __init__(self, provider: str, steps):
        self.provider = provider
        self.model = f"{provider}-model"
        self._steps = list(steps)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if not self._steps:
            raise AssertionError("unexpected model turn")
        step = self._steps.pop(0)
        if callable(step):
            return step(request)
        return step


class RecordingLoop:
    def __init__(self):
        self.events = []

    def emit_event(self, callback, event_type: str, run_id: str, **payload):
        event = {"type": event_type, "run_id": run_id, **payload}
        self.events.append(event)
        if callable(callback):
            callback(event)


def _plugin_context_with_threads(threads: dict[str, dict]):
    return SimpleNamespace(
        state=SimpleNamespace(
            subagent_state=SubagentState(
                root_agent_id="manager",
                active_agent_id="manager",
                active_lineage=["manager"],
                threads=threads,
            )
        ),
        loop=None,
        callback=None,
        run_id="root-run",
        iteration=1,
    )


def _wait_plugin() -> SubagentToolPlugin:
    return SubagentToolPlugin(
        parent_agent=SimpleNamespace(name="manager"),
        templates=(),
        policy=SubagentPolicy(),
        executor=SubagentExecutor(),
    )


def test_communication_runtime_tool_builders_have_expected_names():
    tools = [
        build_spawn_agent_thread_tool(),
        build_send_agent_message_tool(),
        build_wait_agent_messages_tool(),
        build_close_agent_thread_tool(),
        build_write_agent_board_tool(),
        build_read_agent_board_tool(),
        build_return_handoff_to_subagent_tool(),
        build_return_to_parent_tool(),
    ]

    assert [tool.name for tool in tools] == [
        "spawn_agent_thread",
        "send_agent_message",
        "wait_agent_messages",
        "close_agent_thread",
        "write_agent_board",
        "read_agent_board",
        "return_handoff_to_subagent",
        "return_to_parent",
    ]


def test_write_and_read_agent_board_filters_items():
    events = []

    def _after_first_write(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_board_write"
        assert payload["status"] == "written"
        assert payload["item"]["title"] == "Parser bug"
        assert payload["item"]["confidence"] == 0.8
        return _openai_tool_turn(
            call_id="call_write_second",
            name="write_agent_board",
            arguments={
                "kind": "risk",
                "title": "Missing tests",
                "content": "No regression coverage.",
                "tags": ["tests"],
                "refs": ["tests/test_example.py:1"],
            },
        )

    def _after_second_write(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_board_write"
        assert payload["status"] == "written"
        assert payload["item"]["title"] == "Missing tests"
        return _openai_tool_turn(
            call_id="call_read",
            name="read_agent_board",
            arguments={"kinds": ["finding"], "tags": ["parser"]},
        )

    def _after_read(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_board_read"
        assert payload["status"] == "ok"
        assert payload["count"] == 1
        assert [item["title"] for item in payload["items"]] == ["Parser bug"]
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(SubagentModule(),),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_write_first",
                    name="write_agent_board",
                    arguments={
                        "kind": "finding",
                        "title": "Parser bug",
                        "content": "The parser drops empty chunks.",
                        "tags": ["parser"],
                        "confidence": 0.8,
                        "refs": ["src/parser.py:10"],
                    },
                ),
                _after_first_write,
                _after_second_write,
                _after_read,
            ],
        ),
    )

    result = parent.run("coordinate", max_iterations=4, callback=events.append)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"
    write_event = next(event for event in events if event["type"] == "agent_board_item_written")
    assert write_event["board_id"] == "default"
    assert write_event["item_id"].startswith("item-")
    assert write_event["kind"] == "finding"
    assert write_event["author_agent_id"] == "manager"
    assert write_event["title"] == "Parser bug"
    assert write_event["tags"] == ["parser"]
    assert write_event["supersedes_item_id"] is None
    read_event = next(event for event in events if event["type"] == "agent_board_read")
    assert read_event["board_id"] == "default"
    assert read_event["count"] == 1
    assert read_event["kinds"] == ["finding"]
    assert read_event["tags"] == ["parser"]
    assert read_event["author_agent_id"] == ""
    assert read_event["limit"] == 50


def test_child_subagent_board_write_is_visible_to_parent():
    def _child_after_write(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_board_write"
        assert payload["status"] == "written"
        assert payload["item"]["title"] == "Child finding"
        return _text_turn("child done")

    child = Agent(
        name="researcher",
        provider="openai",
        modules=(SubagentModule(),),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="child_write_board",
                    name="write_agent_board",
                    arguments={
                        "kind": "finding",
                        "title": "Child finding",
                        "content": "Found by child",
                        "tags": ["child"],
                    },
                ),
                _child_after_write,
            ],
        ),
    )

    def _after_delegate(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "completed"
        assert payload["summary"] == "child done"
        return _openai_tool_turn(
            call_id="parent_read_board",
            name="read_agent_board",
            arguments={"tags": ["child"]},
        )

    def _after_read(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_board_read"
        assert payload["status"] == "ok"
        assert payload["count"] == 1
        assert payload["items"][0]["title"] == "Child finding"
        assert payload["items"][0]["author_agent_id"] == "manager.researcher.1"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        agent=child,
                        allowed_modes=("delegate", "worker"),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="parent_delegate",
                    name="delegate_to_subagent",
                    arguments={"target": "researcher", "task": "Find evidence"},
                ),
                _after_delegate,
                _after_read,
            ],
        ),
    )

    result = parent.run("coordinate", max_iterations=3)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"title": "Parser bug", "content": "Details"}, "write_agent_board requires kind"),
        ({"kind": "   ", "title": "Parser bug", "content": "Details"}, "write_agent_board requires kind"),
        ({"kind": 123, "title": "Parser bug", "content": "Details"}, "write_agent_board requires kind"),
        ({"kind": "finding", "content": "Details"}, "write_agent_board requires title"),
        ({"kind": "finding", "title": "   ", "content": "Details"}, "write_agent_board requires title"),
        ({"kind": "finding", "title": 123, "content": "Details"}, "write_agent_board requires title"),
        ({"kind": "finding", "title": "Parser bug"}, "write_agent_board requires content"),
        ({"kind": "finding", "title": "Parser bug", "content": "   "}, "write_agent_board requires content"),
        ({"kind": "finding", "title": "Parser bug", "content": 123}, "write_agent_board requires content"),
    ],
)
def test_write_agent_board_requires_non_empty_strings(arguments, error):
    plugin = _wait_plugin()
    context = _plugin_context_with_threads({})
    original_blackboards = json.loads(json.dumps(context.state.subagent_state.blackboards))

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_write_invalid_required",
            name="write_agent_board",
            arguments=arguments,
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result == {
        "tool": "write_agent_board",
        "error": error,
    }
    assert outcome.state_updates == {}
    assert context.state.subagent_state.blackboards == original_blackboards


@pytest.mark.parametrize("limit", [0, -1, "1", True])
def test_read_agent_board_rejects_invalid_limit_without_mutation(limit):
    plugin = _wait_plugin()
    context = _plugin_context_with_threads({})
    context.state.subagent_state.blackboards["default"] = [
        {
            "item_id": "item-1",
            "board_id": "default",
            "author_agent_id": "manager",
            "kind": "finding",
            "title": "Parser bug",
            "content": "Details",
            "tags": ["parser"],
        }
    ]
    original_blackboards = json.loads(json.dumps(context.state.subagent_state.blackboards))

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_read_invalid_limit",
            name="read_agent_board",
            arguments={"limit": limit},
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result == {
        "tool": "read_agent_board",
        "mode": "agent_board_read",
        "status": "failed",
        "error": "read_agent_board limit must be a positive integer",
    }
    assert outcome.state_updates == {}
    assert context.state.subagent_state.blackboards == original_blackboards


@pytest.mark.parametrize("confidence", [2, -0.1, True, float("inf"), float("-inf")])
def test_write_agent_board_rejects_invalid_confidence_without_mutation(confidence):
    plugin = _wait_plugin()
    context = _plugin_context_with_threads({})
    original_blackboards = json.loads(json.dumps(context.state.subagent_state.blackboards))

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_write_invalid_confidence",
            name="write_agent_board",
            arguments={
                "kind": "finding",
                "title": "Parser bug",
                "content": "Details",
                "confidence": confidence,
            },
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result == {
        "tool": "write_agent_board",
        "mode": "agent_board_write",
        "status": "failed",
        "error": "write_agent_board confidence must be between 0 and 1",
    }
    assert outcome.state_updates == {}
    assert context.state.subagent_state.blackboards == original_blackboards


def test_write_agent_board_rejects_nan_confidence_without_mutation():
    plugin = _wait_plugin()
    context = _plugin_context_with_threads({})
    original_blackboards = json.loads(json.dumps(context.state.subagent_state.blackboards))

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_write_nan_confidence",
            name="write_agent_board",
            arguments={
                "kind": "finding",
                "title": "Parser bug",
                "content": "Details",
                "confidence": float("nan"),
            },
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result == {
        "tool": "write_agent_board",
        "mode": "agent_board_write",
        "status": "failed",
        "error": "write_agent_board confidence must be between 0 and 1",
    }
    assert outcome.state_updates == {}
    assert context.state.subagent_state.blackboards == original_blackboards


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"tags": "parser"}, "write_agent_board tags must be an array of strings"),
        ({"tags": ["parser", 3]}, "write_agent_board tags must be an array of strings"),
        ({"refs": "src.py:1"}, "write_agent_board refs must be an array of strings"),
        ({"refs": ["src.py:1", 7]}, "write_agent_board refs must be an array of strings"),
    ],
)
def test_write_agent_board_rejects_invalid_tags_and_refs_without_mutation(arguments, error):
    plugin = _wait_plugin()
    context = _plugin_context_with_threads({})
    original_blackboards = json.loads(json.dumps(context.state.subagent_state.blackboards))

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_write_invalid_metadata",
            name="write_agent_board",
            arguments={
                "kind": "finding",
                "title": "Parser bug",
                "content": "Details",
                **arguments,
            },
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result == {
        "tool": "write_agent_board",
        "mode": "agent_board_write",
        "status": "failed",
        "error": error,
    }
    assert outcome.state_updates == {}
    assert context.state.subagent_state.blackboards == original_blackboards


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"tags": "parser"}, "read_agent_board tags must be an array of strings"),
        ({"kinds": [123]}, "read_agent_board kinds must be an array of strings"),
        ({"tags": ["parser", 3]}, "read_agent_board tags must be an array of strings"),
    ],
)
def test_read_agent_board_rejects_invalid_filters_without_mutation(arguments, error):
    plugin = _wait_plugin()
    context = _plugin_context_with_threads({})
    context.state.subagent_state.blackboards["default"] = [
        {
            "item_id": "item-1",
            "board_id": "default",
            "author_agent_id": "manager",
            "kind": "finding",
            "title": "Parser bug",
            "content": "Details",
            "tags": ["parser"],
        },
        {
            "item_id": "item-2",
            "board_id": "default",
            "author_agent_id": "manager",
            "kind": "risk",
            "title": "Missing tests",
            "content": "No coverage.",
            "tags": ["tests"],
        },
    ]
    original_blackboards = json.loads(json.dumps(context.state.subagent_state.blackboards))

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_read_invalid_filter",
            name="read_agent_board",
            arguments=arguments,
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result == {
        "tool": "read_agent_board",
        "mode": "agent_board_read",
        "status": "failed",
        "error": error,
    }
    assert "items" not in outcome.tool_result
    assert outcome.state_updates == {}
    assert context.state.subagent_state.blackboards == original_blackboards


def test_read_agent_board_limit_returns_latest_matching_item_and_emits_filters():
    plugin = _wait_plugin()
    events = []
    context = _plugin_context_with_threads({})
    context.loop = RecordingLoop()
    context.callback = events.append
    context.state.subagent_state.blackboards["default"] = [
        {
            "item_id": "item-1",
            "board_id": "default",
            "author_agent_id": "manager",
            "kind": "finding",
            "title": "Older parser bug",
            "content": "Older details",
            "tags": ["parser", "runtime"],
        },
        {
            "item_id": "item-2",
            "board_id": "default",
            "author_agent_id": "manager",
            "kind": "risk",
            "title": "Missing tests",
            "content": "No coverage.",
            "tags": ["parser", "tests"],
        },
        {
            "item_id": "item-3",
            "board_id": "default",
            "author_agent_id": "manager",
            "kind": "finding",
            "title": "Latest parser bug",
            "content": "Latest details",
            "tags": ["parser", "runtime"],
        },
    ]

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_read_latest",
            name="read_agent_board",
            arguments={
                "kinds": ["finding"],
                "tags": ["parser", "runtime"],
                "author_agent_id": "manager",
                "limit": 1,
            },
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result["status"] == "ok"
    assert outcome.tool_result["count"] == 1
    assert [item["title"] for item in outcome.tool_result["items"]] == ["Latest parser bug"]
    read_event = next(event for event in events if event["type"] == "agent_board_read")
    assert read_event["board_id"] == "default"
    assert read_event["count"] == 1
    assert read_event["kinds"] == ["finding"]
    assert read_event["tags"] == ["parser", "runtime"]
    assert read_event["author_agent_id"] == "manager"
    assert read_event["limit"] == 1


def test_spawn_wait_and_close_agent_thread_records_state_and_result():
    child = Agent(
        name="researcher",
        provider="openai",
        model_io_factory=lambda spec, ctx: SequenceModelIO("openai", [_text_turn("thread result")]),
    )
    observed_thread_id = ""

    def _after_spawn(request):
        nonlocal observed_thread_id
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_thread"
        assert payload["status"] == "completed"
        assert payload["summary"] == "thread result"
        assert payload["thread_id"]
        observed_thread_id = payload["thread_id"]
        return _openai_tool_turn(
            call_id="call_wait",
            name="wait_agent_messages",
            arguments={"thread_ids": [observed_thread_id], "condition": "all_done"},
        )

    def _after_wait(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "completed"
        assert payload["threads"][0]["status"] == "completed"
        return _openai_tool_turn(
            call_id="call_close",
            name="close_agent_thread",
            arguments={"thread_id": observed_thread_id, "reason": "inspected"},
        )

    def _after_close(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "closed"
        assert payload["thread"]["close_reason"] == "inspected"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        agent=child,
                        allowed_modes=("delegate", "worker"),
                    ),
                ),
                policy=SubagentPolicy(max_open_threads=2),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_spawn",
                    name="spawn_agent_thread",
                    arguments={"target": "researcher", "task": "Investigate"},
                ),
                _after_spawn,
                _after_wait,
                _after_close,
            ],
        ),
    )
    events = []

    result = parent.run("coordinate", max_iterations=4, callback=events.append)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"
    event_types = [event["type"] for event in events]
    assert "agent_thread_spawned" in event_types
    assert "agent_thread_completed" in event_types
    assert "agent_thread_closed" in event_types


def test_wait_agent_messages_any_done_completes_when_one_thread_is_done():
    plugin = _wait_plugin()
    context = _plugin_context_with_threads(
        {
            "thread-1": {"thread_id": "thread-1", "status": "completed"},
            "thread-2": {"thread_id": "thread-2", "status": "running"},
        }
    )

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_wait",
            name="wait_agent_messages",
            arguments={"thread_ids": ["thread-1", "thread-2"], "condition": "any_done"},
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result["status"] == "completed"
    assert [thread["status"] for thread in outcome.tool_result["threads"]] == ["completed", "running"]


def test_wait_agent_messages_unknown_thread_returns_not_found_status():
    plugin = _wait_plugin()
    context = _plugin_context_with_threads({"thread-1": {"thread_id": "thread-1", "status": "running"}})

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_wait",
            name="wait_agent_messages",
            arguments={"thread_ids": ["thread-1", "missing-thread"], "condition": "all_done"},
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result["status"] == "not_found"
    assert outcome.tool_result["threads"][1] == {"thread_id": "missing-thread", "status": "not_found"}


def test_wait_agent_messages_treats_terminal_thread_statuses_as_done():
    plugin = _wait_plugin()
    context = _plugin_context_with_threads(
        {
            "thread-1": {"thread_id": "thread-1", "status": "max_iterations"},
            "thread-2": {"thread_id": "thread-2", "status": "needs_clarification"},
        }
    )

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_wait",
            name="wait_agent_messages",
            arguments={"thread_ids": ["thread-1", "thread-2"], "condition": "all_done"},
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result["status"] == "completed"
    assert [thread["status"] for thread in outcome.tool_result["threads"]] == [
        "max_iterations",
        "needs_clarification",
    ]


def test_wait_agent_messages_idle_completes_for_idle_and_terminal_threads():
    plugin = _wait_plugin()
    context = _plugin_context_with_threads(
        {
            "thread-1": {"thread_id": "thread-1", "status": "idle"},
            "thread-2": {"thread_id": "thread-2", "status": "needs_clarification"},
        }
    )

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_wait",
            name="wait_agent_messages",
            arguments={"thread_ids": ["thread-1", "thread-2"], "condition": "idle"},
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result["status"] == "completed"
    assert [thread["status"] for thread in outcome.tool_result["threads"]] == ["idle", "needs_clarification"]


def test_send_agent_message_runs_followup_on_existing_thread_session():
    child_observations = []
    events = []

    def _child_factory(spec, ctx):
        del spec

        def _fetch_child_turn(request):
            content = request.messages[-1]["content"]
            child_observations.append(
                {
                    "session_id": ctx.session_id,
                    "memory_namespace": ctx.memory_namespace,
                    "content": content,
                }
            )
            if content == "Initial task":
                return _text_turn("initial done")
            if content == "Follow up":
                return _text_turn("followup done")
            raise AssertionError(f"unexpected child content: {content}")

        return SequenceModelIO("openai", [_fetch_child_turn])

    child = Agent(
        name="researcher",
        provider="openai",
        model_io_factory=_child_factory,
    )
    observed_thread_id = ""

    def _after_spawn(request):
        nonlocal observed_thread_id
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_thread"
        assert payload["status"] == "completed"
        observed_thread_id = payload["thread_id"]
        return _openai_tool_turn(
            call_id="call_send",
            name="send_agent_message",
            arguments={
                "recipient": observed_thread_id,
                "content": "Follow up",
                "kind": "followup",
            },
        )

    def _after_send(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_message"
        assert payload["status"] == "completed"
        assert payload["thread_id"] == observed_thread_id
        assert payload["reply"]["summary"] == "followup done"
        assert payload["message"]["kind"] == "followup"
        assert payload["message"]["content"] == "Follow up"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        agent=child,
                        allowed_modes=("delegate", "worker"),
                        memory_policy="scoped_persistent",
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_spawn",
                    name="spawn_agent_thread",
                    arguments={"target": "researcher", "task": "Initial task"},
                ),
                _after_spawn,
                _after_send,
            ],
        ),
    )

    result = parent.run(
        "coordinate",
        max_iterations=3,
        session_id="root-session",
        memory_namespace="root-ns",
        callback=events.append,
    )

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"
    assert [item["content"] for item in child_observations] == ["Initial task", "Follow up"]
    assert child_observations[0]["session_id"] == child_observations[1]["session_id"]
    assert child_observations[0]["memory_namespace"] == child_observations[1]["memory_namespace"]
    assert child_observations[0]["session_id"] == f"root-session:{observed_thread_id}"
    assert child_observations[0]["memory_namespace"] == f"root-ns:{observed_thread_id}"
    event_types = [event["type"] for event in events]
    assert "agent_message_sent" in event_types
    assert "agent_message_completed" in event_types
    completed_event = next(event for event in events if event["type"] == "agent_message_completed")
    assert completed_event["thread_id"] == observed_thread_id
    assert completed_event["status"] == "completed"
    assert completed_event["subagent_id"] == observed_thread_id
    assert completed_event["parent_id"] == "manager"


def test_send_agent_message_rejects_unknown_or_closed_thread():
    def _after_send(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["tool"] == "send_agent_message"
        assert payload["error"] == "unknown agent thread: missing-thread"
        return _text_turn("unknown agent thread")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(SubagentModule(),),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_send",
                    name="send_agent_message",
                    arguments={"recipient": "missing-thread", "content": "hello"},
                ),
                _after_send,
            ],
        ),
    )

    result = parent.run("coordinate", max_iterations=2)

    assert result.status == "completed"
    assert "unknown agent thread" in result.messages[-1]["content"]

    plugin = _wait_plugin()
    context = _plugin_context_with_threads(
        {
            "closed-thread": {
                "thread_id": "closed-thread",
                "agent_id": "manager.researcher.1",
                "status": "closed",
            }
        }
    )

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_send_closed",
            name="send_agent_message",
            arguments={"recipient": "closed-thread", "content": "hello"},
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result["tool"] == "send_agent_message"
    assert outcome.tool_result["error"] == "agent thread is closed: closed-thread"


def test_send_agent_message_rejects_mismatched_recipient_and_explicit_thread_id():
    plugin = _wait_plugin()
    context = _plugin_context_with_threads(
        {
            "thread-a": {
                "thread_id": "thread-a",
                "agent_id": "manager.researcher.1",
                "status": "completed",
            },
            "thread-b": {
                "thread_id": "thread-b",
                "agent_id": "manager.writer.1",
                "status": "completed",
            },
        }
    )

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_send_mismatch",
            name="send_agent_message",
            arguments={
                "recipient": "manager.researcher.1",
                "thread_id": "thread-b",
                "content": "hello",
            },
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result["tool"] == "send_agent_message"
    assert outcome.tool_result["status"] == "failed"
    assert outcome.tool_result["error"] == "recipient does not match agent thread: manager.researcher.1"
    assert outcome.state_updates == {}
    assert context.state.subagent_state.mailboxes == {}


def test_send_agent_message_rejects_missing_explicit_thread_without_agent_fallback():
    plugin = _wait_plugin()
    context = _plugin_context_with_threads(
        {
            "thread-a": {
                "thread_id": "thread-a",
                "agent_id": "manager.researcher.1",
                "status": "completed",
            },
        }
    )

    outcome = plugin.execute(
        tool_call=ToolCall(
            call_id="call_send_missing_explicit",
            name="send_agent_message",
            arguments={
                "recipient": "manager.researcher.1",
                "thread_id": "missing-thread",
                "content": "hello",
            },
        ),
        context=context,
    )

    assert outcome.handled is True
    assert outcome.tool_result["tool"] == "send_agent_message"
    assert outcome.tool_result["status"] == "failed"
    assert outcome.tool_result["error"] == "unknown agent thread: missing-thread"
    assert outcome.state_updates == {}
    assert context.state.subagent_state.mailboxes == {}


def test_send_agent_message_child_failure_persists_failed_thread_for_wait_and_emits_event():
    def _child_factory(spec, ctx):
        del spec, ctx

        def _fetch_child_turn(request):
            content = request.messages[-1]["content"]
            if content == "Initial task":
                return _text_turn("initial done")
            if content == "Follow up":
                raise RuntimeError("followup exploded")
            raise AssertionError(f"unexpected child content: {content}")

        return SequenceModelIO("openai", [_fetch_child_turn])

    child = Agent(
        name="researcher",
        provider="openai",
        model_io_factory=_child_factory,
    )
    observed_thread_id = ""

    def _after_spawn(request):
        nonlocal observed_thread_id
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "completed"
        observed_thread_id = payload["thread_id"]
        return _openai_tool_turn(
            call_id="call_send",
            name="send_agent_message",
            arguments={
                "recipient": observed_thread_id,
                "content": "Follow up",
                "kind": "followup",
            },
        )

    def _after_send_failure(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["tool"] == "send_agent_message"
        assert payload["mode"] == "agent_message"
        assert payload["status"] == "failed"
        assert payload["thread_id"] == observed_thread_id
        assert payload["message"]["content"] == "Follow up"
        assert payload["error"] == "followup exploded"
        return _openai_tool_turn(
            call_id="call_wait",
            name="wait_agent_messages",
            arguments={"thread_ids": [observed_thread_id], "condition": "all_done"},
        )

    def _after_wait(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "completed"
        assert payload["threads"][0]["thread_id"] == observed_thread_id
        assert payload["threads"][0]["status"] == "failed"
        assert payload["threads"][0]["close_reason"] == "followup exploded"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        agent=child,
                        allowed_modes=("delegate", "worker"),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_spawn",
                    name="spawn_agent_thread",
                    arguments={"target": "researcher", "task": "Initial task"},
                ),
                _after_spawn,
                _after_send_failure,
                _after_wait,
            ],
        ),
    )
    events = []

    result = parent.run("coordinate", max_iterations=4, callback=events.append)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"
    event_types = [event["type"] for event in events]
    assert "agent_message_sent" in event_types
    assert "agent_message_failed" in event_types
    failed_event = next(event for event in events if event["type"] == "agent_message_failed")
    assert failed_event["thread_id"] == observed_thread_id
    assert failed_event["status"] == "failed"
    assert failed_event["error"] == "followup exploded"
    assert failed_event["subagent_id"] == observed_thread_id
    assert failed_event["parent_id"] == "manager"


def test_spawn_agent_thread_child_failure_persists_failed_thread_for_wait():
    def _raise_child_failure(request):
        del request
        raise RuntimeError("child exploded")

    child = Agent(
        name="researcher",
        provider="openai",
        model_io_factory=lambda spec, ctx: SequenceModelIO("openai", [_raise_child_failure]),
    )
    observed_thread_id = ""

    def _after_spawn_failure(request):
        nonlocal observed_thread_id
        payload = json.loads(request.messages[-1]["output"])
        assert payload["tool"] == "spawn_agent_thread"
        assert payload["mode"] == "agent_thread"
        assert payload["status"] == "failed"
        assert payload["thread_id"]
        assert payload["error"] == "child exploded"
        observed_thread_id = payload["thread_id"]
        return _openai_tool_turn(
            call_id="call_wait",
            name="wait_agent_messages",
            arguments={"thread_ids": [observed_thread_id], "condition": "all_done"},
        )

    def _after_wait(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "completed"
        assert payload["threads"][0]["thread_id"] == observed_thread_id
        assert payload["threads"][0]["status"] == "failed"
        assert payload["threads"][0]["close_reason"] == "child exploded"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        agent=child,
                        allowed_modes=("delegate", "worker"),
                    ),
                ),
                policy=SubagentPolicy(max_open_threads=2),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_spawn",
                    name="spawn_agent_thread",
                    arguments={"target": "researcher", "task": "Investigate"},
                ),
                _after_spawn_failure,
                _after_wait,
            ],
        ),
    )
    events = []

    result = parent.run("coordinate", max_iterations=3, callback=events.append)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"
    event_types = [event["type"] for event in events]
    assert "agent_thread_spawned" in event_types
    assert "agent_thread_failed" in event_types


def test_spawn_agent_thread_preserves_child_clarification_request_for_parent():
    clarification_args = {
        "title": "Need more detail",
        "question": "Which environment?",
        "selection_mode": "single",
        "options": [{"label": "Prod", "value": "prod"}, {"label": "Staging", "value": "staging"}],
    }
    child = Agent(
        name="clarifier",
        provider="openai",
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [_openai_tool_turn(call_id="child_call", name="ask_user_question", arguments=clarification_args)],
        ),
    )
    observed_thread_id = ""

    def _after_spawn(request):
        nonlocal observed_thread_id
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "needs_clarification"
        assert payload["clarification_request"]["question"] == "Which environment?"
        observed_thread_id = payload["thread_id"]
        return _openai_tool_turn(
            call_id="call_wait",
            name="wait_agent_messages",
            arguments={"thread_ids": [observed_thread_id], "condition": "all_done"},
        )

    def _after_wait(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "completed"
        assert payload["threads"][0]["thread_id"] == observed_thread_id
        assert payload["threads"][0]["status"] == "needs_clarification"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="clarifier",
                        description="Clarification specialist",
                        agent=child,
                        allowed_modes=("delegate", "worker"),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_spawn",
                    name="spawn_agent_thread",
                    arguments={"target": "clarifier", "task": "Ask for missing context"},
                ),
                _after_spawn,
                _after_wait,
            ],
        ),
    )
    events = []

    result = parent.run("coordinate", max_iterations=3, callback=events.append)

    assert result.status == "completed"
    assert result.human_input_request is None
    assert result.messages[-1]["content"] == "done"
    clarification_event = next(event for event in events if event["type"] == "agent_thread_clarification_requested")
    assert clarification_event["thread_id"] == observed_thread_id
    assert clarification_event["request_id"]
    assert clarification_event["clarification_request"]["question"] == "Which environment?"
    assert all(event["type"] != "human_input_requested" for event in events)
