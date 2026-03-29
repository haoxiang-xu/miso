import json
import threading
import time

from unchain.agent import Agent, MemoryModule, PoliciesModule, SubagentModule
from unchain.kernel import ModelTurnResult, ToolCall
from unchain.subagents import SubagentPolicy, SubagentTemplate
from miso.memory import MemoryManager


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
