import json
import threading
import time

from unchain.agent import Agent, MemoryModule, PoliciesModule, SubagentModule, ToolsModule
from unchain.kernel import ModelTurnResult, ToolCall
from unchain.subagents import SubagentPolicy, SubagentTemplate
from unchain.memory import MemoryManager


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


def _text_turn(text: str, *, response_id: str | None = None) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": text}],
        tool_calls=[],
        final_text=text,
        response_id=response_id,
    )


def _anthropic_tool_turn(*, call_id: str, name: str, arguments: dict, text: str = "") -> ModelTurnResult:
    content: list[dict[str, object]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.append({"type": "tool_use", "id": call_id, "name": name, "input": arguments})
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": content}],
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
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


def test_subagent_delegate_template_returns_structured_result_and_parent_keeps_control():
    child = Agent(
        name="researcher",
        provider="openai",
        model_io_factory=lambda spec, ctx: SequenceModelIO("openai", [_text_turn("child summary")]),
    )
    events = []

    def _second_turn(request):
        assert request.messages[-1]["type"] == "function_call_output"
        payload = json.loads(request.messages[-1]["output"])
        assert payload["template_name"] == "researcher"
        assert payload["status"] == "completed"
        assert payload["summary"] == "child summary"
        return _text_turn("manager final")

    parent_io = SequenceModelIO(
        "openai",
        [
            _openai_tool_turn(
                call_id="call_1",
                name="delegate_to_subagent",
                arguments={
                    "target": "researcher",
                    "task": "Investigate the bug",
                    "output_mode": "summary",
                },
            ),
            _second_turn,
        ],
    )
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
                        allowed_modes=("delegate",),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: parent_io,
    )

    result = parent.run("handle it", max_iterations=2, run_id="root-run")

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "manager final"
    event_types = [event["type"] for event in events]
    assert "subagent_spawned" not in event_types

    # Rerun with callback to assert event payloads without affecting core flow.
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
                        allowed_modes=("delegate",),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="delegate_to_subagent",
                    arguments={"target": "researcher", "task": "Investigate the bug"},
                ),
                _text_turn("manager final"),
            ],
        ),
    )
    events = []
    result = parent.run("handle it", max_iterations=2, run_id="root-run", callback=events.append)
    assert result.status == "completed"
    spawned = next(event for event in events if event["type"] == "subagent_spawned")
    completed = next(event for event in events if event["type"] == "subagent_completed")
    assert spawned["root_run_id"] == "root-run"
    assert spawned["mode"] == "delegate"
    assert completed["template"] == "researcher"


def test_subagent_delegate_dynamic_child_respects_policy():
    def factory(spec, ctx):
        if spec.name == "manager":
            return SequenceModelIO(
                "openai",
                [
                    _openai_tool_turn(
                        call_id="call_1",
                        name="delegate_to_subagent",
                        arguments={"target": "scratch_worker", "task": "Draft a short answer"},
                    ),
                    lambda request: _text_turn(
                        json.loads(request.messages[-1]["output"])["summary"]
                    ),
                ],
            )
        return SequenceModelIO("openai", [_text_turn("dynamic delegate ok")])

    agent = Agent(
        name="manager",
        provider="openai",
        modules=(SubagentModule(policy=SubagentPolicy(allow_dynamic_delegate=True)),),
        model_io_factory=factory,
    )

    result = agent.run("go", max_iterations=2)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "dynamic delegate ok"


def test_subagent_handoff_completes_root_run_and_uses_scoped_persistent_memory():
    memory = MemoryManager()
    seen_child_context = {}

    def _child_factory(spec, ctx):
        seen_child_context["session_id"] = ctx.session_id
        seen_child_context["memory_namespace"] = ctx.memory_namespace
        seen_child_context["module_names"] = [type(module).__name__ for module in spec.modules]
        return SequenceModelIO("openai", [_text_turn("specialist final")])

    specialist = Agent(
        name="specialist",
        provider="openai",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=_child_factory,
    )
    parent_io = SequenceModelIO(
        "openai",
        [
            _openai_tool_turn(
                call_id="call_1",
                name="handoff_to_subagent",
                arguments={"target": "specialist", "reason": "Needs deep expertise"},
            ),
        ],
    )
    events = []
    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            PoliciesModule(max_iterations=2),
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="specialist",
                        description="Specialist handoff target",
                        agent=specialist,
                        allowed_modes=("handoff",),
                        memory_policy="scoped_persistent",
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: parent_io,
    )

    result = parent.run(
        "Need the specialist",
        session_id="root-session",
        memory_namespace="root-ns",
        callback=events.append,
    )

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "specialist final"
    assert len(parent_io.requests) == 1
    assert any(event["type"] == "subagent_handoff" for event in events)
    assert seen_child_context["session_id"] == "root-session:manager.specialist.1"
    assert seen_child_context["memory_namespace"] == "root-ns:manager.specialist.1"
    assert "MemoryModule" in seen_child_context["module_names"]


def test_subagent_handoff_strips_runtime_tool_call_from_child_context_and_final_transcript():
    seen_child_messages = {}

    def _child_factory(spec, ctx):
        def _fetch(request):
            seen_child_messages["messages"] = request.messages
            return _text_turn("specialist final")

        return SequenceModelIO("anthropic", [_fetch])

    specialist = Agent(
        name="specialist",
        provider="anthropic",
        model_io_factory=_child_factory,
    )
    parent = Agent(
        name="manager",
        provider="anthropic",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="specialist",
                        description="Specialist handoff target",
                        agent=specialist,
                        allowed_modes=("handoff",),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "anthropic",
            [
                _anthropic_tool_turn(
                    call_id="toolu_handoff_1",
                    name="handoff_to_subagent",
                    arguments={"target": "specialist", "reason": "Needs deep expertise"},
                    text="Routing to the specialist.",
                ),
            ],
        ),
    )

    result = parent.run("Need the specialist", max_iterations=2)

    assert result.status == "completed"
    assert result.messages == [
        {"role": "user", "content": "Need the specialist"},
        {"role": "assistant", "content": "specialist final"},
    ]

    child_messages = seen_child_messages["messages"]
    non_system_messages = [
        message
        for message in child_messages
        if isinstance(message, dict) and message.get("role") != "system"
    ]
    assert non_system_messages == [{"role": "user", "content": "Need the specialist"}]
    assert all(message.get("type") != "function_call" for message in child_messages if isinstance(message, dict))
    assert all(
        not any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in (message.get("content") if isinstance(message.get("content"), list) else [])
        )
        for message in child_messages
        if isinstance(message, dict)
    )


def test_subagent_worker_batch_runs_in_parallel_and_preserves_input_order():
    tracker = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    def worker_factory(spec, ctx):
        task_text = ctx.input_messages[-1]["content"]

        class WorkerIO:
            provider = "openai"
            model = "gpt-5"

            def fetch_turn(self, request):
                del request
                with lock:
                    tracker["active"] += 1
                    tracker["max_active"] = max(tracker["max_active"], tracker["active"])
                time.sleep(0.05)
                with lock:
                    tracker["active"] -= 1
                return _text_turn(f"done {task_text}")

        return WorkerIO()

    worker_agent = Agent(name="worker", provider="openai", model_io_factory=worker_factory)

    def _join_turn(request):
        payload = json.loads(request.messages[-1]["output"])
        assert [item["output"] for item in payload["results"]] == ["done one", "done two", "done three"]
        return _text_turn("joined")

    parent_io = SequenceModelIO(
        "openai",
        [
            _openai_tool_turn(
                call_id="call_1",
                name="spawn_worker_batch",
                arguments={
                    "target": "worker",
                    "tasks": [{"task": "one"}, {"task": "two"}, {"task": "three"}],
                },
            ),
            _join_turn,
        ],
    )
    events = []
    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="worker",
                        description="Parallel worker",
                        agent=worker_agent,
                        allowed_modes=("worker",),
                        parallel_safe=True,
                    ),
                ),
                policy=SubagentPolicy(max_parallel_workers=3),
            ),
        ),
        model_io_factory=lambda spec, ctx: parent_io,
    )

    result = parent.run("fan out", max_iterations=2, callback=events.append)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "joined"
    assert tracker["max_active"] > 1
    assert any(event["type"] == "subagent_batch_started" for event in events)
    assert any(event["type"] == "subagent_batch_joined" for event in events)


def test_subagent_worker_batch_rejects_non_parallel_safe_template():
    serial_worker = Agent(
        name="serial_worker",
        provider="openai",
        model_io_factory=lambda spec, ctx: SequenceModelIO("openai", [_text_turn("should not run")]),
    )

    def _join_turn(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "partial_failure"
        assert "not parallel_safe" in payload["results"][0]["error"]
        return _text_turn("handled worker failure")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="serial_worker",
                        description="Serial worker",
                        agent=serial_worker,
                        allowed_modes=("worker",),
                        parallel_safe=False,
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="spawn_worker_batch",
                    arguments={"target": "serial_worker", "tasks": [{"task": "only"}]},
                ),
                _join_turn,
            ],
        ),
    )

    result = parent.run("fan out", max_iterations=2)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "handled worker failure"


def test_subagent_child_clarification_is_escalated_without_suspending_root_run():
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

    def _followup(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "needs_clarification"
        assert payload["clarification_request"]["question"] == "Which environment?"
        return _text_turn("parent handled clarification")

    events = []
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
                        allowed_modes=("delegate", "handoff"),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="delegate_to_subagent",
                    arguments={"target": "clarifier", "task": "Ask for missing context"},
                ),
                _followup,
            ],
        ),
    )

    result = parent.run("start", max_iterations=2, callback=events.append)

    assert result.status == "completed"
    assert result.human_input_request is None
    assert result.messages[-1]["content"] == "parent handled clarification"
    assert any(event["type"] == "subagent_clarification_requested" for event in events)
    assert all(event["type"] != "human_input_requested" for event in events)


def test_subagent_policy_limits_are_enforced_as_tool_errors():
    def _after_error(request):
        payload = json.loads(request.messages[-1]["output"])
        assert "max_total_subagents exceeded" in payload["error"]
        return _text_turn("quota handled")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(SubagentModule(policy=SubagentPolicy(max_total_subagents=0, allow_dynamic_delegate=True)),),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="delegate_to_subagent",
                    arguments={"target": "dyn", "task": "Try to spawn"},
                ),
                _after_error,
            ],
        ),
    )

    result = parent.run("go", max_iterations=2)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "quota handled"


def test_subagent_template_without_agent_inherits_parent_and_overrides_model():
    seen_child = {}

    def factory(spec, ctx):
        if spec.name == "manager":
            return SequenceModelIO(
                "openai",
                [
                    _openai_tool_turn(
                        call_id="call_1",
                        name="delegate_to_subagent",
                        arguments={"target": "researcher", "task": "Investigate the bug"},
                    ),
                    lambda request: _text_turn(json.loads(request.messages[-1]["output"])["summary"]),
                ],
            )
        seen_child["name"] = spec.name
        seen_child["provider"] = spec.provider
        seen_child["model"] = spec.model
        seen_child["allowed_tools"] = spec.allowed_tools
        return SequenceModelIO("openai", [_text_turn("child model override ok")])

    parent = Agent(
        name="manager",
        provider="openai",
        model="gpt-parent",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        allowed_modes=("delegate",),
                        model="gpt-child",
                    ),
                ),
            ),
        ),
        model_io_factory=factory,
    )

    result = parent.run("handle it", max_iterations=2)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "child model override ok"
    assert seen_child["name"].startswith("manager.researcher.")
    assert seen_child["provider"] == "openai"
    assert seen_child["model"] == "gpt-child"
    assert seen_child["allowed_tools"] is None


def test_subagent_template_allowed_tools_limits_normal_tools():
    calls = {"allowed": 0, "denied": 0}

    def allowed_tool():
        calls["allowed"] += 1
        return {"value": "allowed"}

    def denied_tool():
        calls["denied"] += 1
        return {"value": "denied"}

    def _after_allowed(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["template_name"] == "scoped_child"
        assert payload["summary"] == "allowed ok"
        return _openai_tool_turn(
            call_id="call_2",
            name="delegate_to_subagent",
            arguments={"target": "scoped_child", "task": "use denied"},
        )

    def _after_denied(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["template_name"] == "scoped_child"
        assert payload["summary"] == "denied blocked"
        return _text_turn("done")

    def factory(spec, ctx):
        if spec.name == "manager":
            return SequenceModelIO(
                "openai",
                [
                    _openai_tool_turn(
                        call_id="call_1",
                        name="delegate_to_subagent",
                        arguments={"target": "scoped_child", "task": "use allowed"},
                    ),
                    _after_allowed,
                    _after_denied,
                ],
            )

        task_text = ctx.input_messages[-1]["content"]
        if task_text == "use allowed":
            return SequenceModelIO(
                "openai",
                [
                    _openai_tool_turn(call_id="child_allowed", name="allowed_tool", arguments={}),
                    lambda request: (
                        _text_turn(
                            "allowed ok"
                            if json.loads(request.messages[-1]["output"])["value"] == "allowed"
                            else "unexpected"
                        )
                    ),
                ],
            )
        if task_text == "use denied":
            return SequenceModelIO(
                "openai",
                [
                    _openai_tool_turn(call_id="child_denied", name="denied_tool", arguments={}),
                    lambda request: (
                        _text_turn(
                            "denied blocked"
                            if json.loads(request.messages[-1]["output"])["error"] == "tool not found: denied_tool"
                            else "unexpected"
                        )
                    ),
                ],
            )
        raise AssertionError(f"unexpected child task: {task_text}")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            ToolsModule(tools=(allowed_tool, denied_tool)),
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="scoped_child",
                        description="Scoped child",
                        allowed_modes=("delegate",),
                        allowed_tools=("allowed_tool",),
                    ),
                ),
            ),
        ),
        model_io_factory=factory,
    )

    result = parent.run("handle it", max_iterations=3)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"
    assert calls["allowed"] == 1
    assert calls["denied"] == 0


def test_subagent_template_allowed_tools_rejects_unknown_tool_names():
    def _after_error(request):
        payload = json.loads(request.messages[-1]["output"])
        assert "allowed_tools contains unknown tool names: missing_tool" in payload["error"]
        return _text_turn("invalid allowlist handled")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="scoped_child",
                        description="Scoped child",
                        allowed_modes=("delegate",),
                        allowed_tools=("missing_tool",),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="delegate_to_subagent",
                    arguments={"target": "scoped_child", "task": "use missing tool"},
                ),
                _after_error,
            ],
        ),
    )

    result = parent.run("handle it", max_iterations=2)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "invalid allowlist handled"


def test_subagent_template_allowed_tools_blocks_reserved_runtime_tools():
    seen_children = []

    def factory(spec, ctx):
        if spec.name == "manager":
            return SequenceModelIO(
                "openai",
                [
                    _openai_tool_turn(
                        call_id="call_1",
                        name="delegate_to_subagent",
                        arguments={"target": "scoped_child", "task": "try nested"},
                    ),
                    lambda request: _text_turn(json.loads(request.messages[-1]["output"])["summary"]),
                ],
            )

        seen_children.append(spec.name)
        return SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="child_call",
                    name="delegate_to_subagent",
                    arguments={"target": "ghost", "task": "nested"},
                ),
                lambda request: (
                    _text_turn(
                        "nested blocked"
                        if json.loads(request.messages[-1]["output"])["error"] == "tool not found: delegate_to_subagent"
                        else "unexpected"
                    )
                ),
            ],
        )

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="scoped_child",
                        description="Scoped child",
                        allowed_modes=("delegate",),
                        allowed_tools=(),
                    ),
                ),
            ),
        ),
        model_io_factory=factory,
    )

    result = parent.run("handle it", max_iterations=2)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "nested blocked"
    assert len(seen_children) == 1


def test_subagent_template_agent_keeps_provider_and_overrides_model():
    seen_child = {}

    specialist = Agent(
        name="specialist",
        provider="anthropic",
        model="claude-base",
        model_io_factory=lambda spec, ctx: (
            seen_child.update(
                {
                    "name": spec.name,
                    "provider": spec.provider,
                    "model": spec.model,
                    "allowed_tools": spec.allowed_tools,
                }
            )
            or SequenceModelIO("anthropic", [_text_turn("specialist override ok")])
        ),
    )

    parent = Agent(
        name="manager",
        provider="openai",
        model="gpt-parent",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="specialist",
                        description="Specialist",
                        agent=specialist,
                        allowed_modes=("delegate",),
                        model="claude-override",
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="delegate_to_subagent",
                    arguments={"target": "specialist", "task": "Handle it"},
                ),
                lambda request: _text_turn(json.loads(request.messages[-1]["output"])["summary"]),
            ],
        ),
    )

    result = parent.run("start", max_iterations=2)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "specialist override ok"
    assert seen_child["name"].startswith("manager.specialist.")
    assert seen_child["provider"] == "anthropic"
    assert seen_child["model"] == "claude-override"
    assert seen_child["allowed_tools"] is None
