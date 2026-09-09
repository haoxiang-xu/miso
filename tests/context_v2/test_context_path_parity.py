from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from unchain.agent import Agent, AgentBuilder, AgentCallContext, AgentSpec, AgentState
from unchain.agent.model_io import ModelIOFactoryRegistry
from unchain.agent.modules import ContextModule, ContextShadowModule
from unchain.context import (
    ContextCompileRequest,
    ContextCompiler,
    ContextRuntime,
    resolve_context_budget,
)
from unchain.kernel import KernelRunResult, ModelTurnResult
from unchain.subagents import SubagentExecutor, SubagentPolicy, SubagentTemplate
from unchain.subagents.plugin import SubagentToolPlugin


class _RecordingCompiler(ContextCompiler):
    def __init__(self) -> None:
        super().__init__()
        self.results = []

    def compile(self, request):
        result = super().compile(request)
        self.results.append(result)
        return result


class _FinalModelIO:
    provider = "openai"
    model = "gpt-test"

    def __init__(self) -> None:
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        return ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
        )


def _request_factory(context):
    run_id = str(context.event.get("run_id") or "path-run")
    execution_id = str(context.state.session_state.session_id or run_id)
    return ContextCompileRequest(
        case="path-parity",
        source_messages=tuple(context.latest_messages()),
        current_generation=f"generation-{execution_id}",
        fixed_overhead_tokens=0,
        budget=resolve_context_budget(context_window_tokens=16_384),
        provider=str(context.state.provider_state.provider or "openai"),
        model=str(context.state.provider_state.model or "gpt-test"),
        build_id=f"build-{run_id}-{context.state.iteration}",
        execution_id=execution_id,
        generation_id=f"generation-{execution_id}",
        attempt_id=run_id,
    )


def _runtime(compiler, order=None, fail=None):
    order = order if order is not None else []

    def sink(event):
        order.append(("durable", event["type"]))
        if fail is not None:
            raise fail

    return ContextRuntime._for_test(
        owner_id="context-v2",
        compiler=compiler,
        request_factory=_request_factory,
        durable_event_sink=sink,
        partial_attempt_sink=lambda event, error: order.append(
            ("partial", event["type"], error)
        ),
    )


def _builder(runtime, *, mode="run", callback=None):
    model_io = _FinalModelIO()
    builder = AgentBuilder(
        agent=SimpleNamespace(name="path-agent"),
        spec=AgentSpec(name="path-agent", provider="openai", model="gpt-test"),
        state=AgentState(),
        call_context=AgentCallContext(
            mode=mode,
            input_messages=[{"role": "user", "content": mode}],
            callback=callback,
            session_id=f"session-{mode}",
            run_id=f"run-{mode}",
            max_iterations=1,
            max_context_window_tokens=16_384,
        ),
        model_io_registry=ModelIOFactoryRegistry(),
    )
    builder.set_model_io(model_io)
    ContextModule(runtime=runtime).configure(builder)
    return builder


@pytest.mark.parametrize("mode", ["run", "graph", "resume", "subagent"])
def test_all_agent_build_modes_create_envelopes_through_the_same_runtime(mode):
    compiler = _RecordingCompiler()
    runtime = _runtime(compiler)
    prepared = _builder(runtime, mode=mode).build()
    state = prepared.loop.seed_state(
        [{"role": "user", "content": mode}],
        provider="openai",
        model="gpt-test",
        session_id=f"session-{mode}",
        max_context_window_tokens=16_384,
    )

    prepared.loop.dispatch_phase(
        state,
        phase="before_model",
        event={
            "run_id": f"run-{mode}",
            "callback": prepared.call_context.callback,
        },
    )

    assert prepared.context_runtime is runtime
    assert compiler.results[-1].envelope is not None
    assert compiler.results[-1].envelope.execution_id == f"session-{mode}"
    assert state.metadata["context_v2"]["owner_id"] == "context-v2"
    assert state.metadata["context_v2"]["last_build"]["schema"] == (
        "unchain.context_build_envelope.v1"
    )


def test_graph_adapter_mode_runs_through_the_configured_context_module():
    compiler = _RecordingCompiler()
    runtime = _runtime(compiler)
    agent = Agent(
        name="graph-agent",
        provider="openai",
        model="gpt-test",
        modules=(ContextModule(runtime=runtime),),
        model_io_factory=lambda spec, context: _FinalModelIO(),
    )
    prepared = agent._prepare(
        AgentCallContext(
            mode="graph",
            input_messages=[{"role": "user", "content": "graph node"}],
            session_id="session-graph-adapter",
            run_id="run-graph-adapter",
            max_iterations=1,
            max_context_window_tokens=16_384,
        )
    )

    assert prepared.run().status == "completed"
    assert compiler.results[-1].envelope is not None
    assert compiler.results[-1].envelope.execution_id == "session-graph-adapter"


def test_subagent_fork_retains_the_same_context_runtime_and_compiles():
    compiler = _RecordingCompiler()
    runtime = _runtime(compiler)
    parent = Agent(
        name="parent-agent",
        provider="openai",
        model="gpt-test",
        modules=(ContextModule(runtime=runtime),),
        model_io_factory=lambda spec, context: _FinalModelIO(),
    )
    child = parent.fork_for_subagent(
        subagent_name="child-agent",
        mode="delegate",
        parent_name="parent-agent",
        lineage=["parent-agent", "child-agent"],
        task="inspect the result",
        instructions="",
        expected_output="short answer",
        disabled_module_keys=("memory",),
    )

    assert child.run(
        "inspect",
        session_id="session-child",
        run_id="run-child",
        max_iterations=1,
        max_context_window_tokens=16_384,
    ).status == "completed"
    child_context_module = next(
        module
        for module in child.spec.modules
        if isinstance(module, ContextModule)
    )
    assert child_context_module.runtime is runtime
    assert compiler.results[-1].envelope is not None
    assert compiler.results[-1].envelope.execution_id == "session-child"


def _template_child(
    *,
    parent: Agent,
    template_agent: Agent,
) -> Agent:
    template = SubagentTemplate(
        name="specialist",
        description="A template-backed specialist",
        agent=template_agent,
    )
    plugin = SubagentToolPlugin(
        parent_agent=parent,
        templates=(template,),
        policy=SubagentPolicy(),
        executor=SubagentExecutor(),
    )
    child, _, _ = plugin._build_subagent(
        template=template,
        child_id="parent.specialist.1",
        lineage=["parent", "parent.specialist.1"],
        mode="delegate",
        target="specialist",
        task="inspect",
        instructions="",
        expected_output="summary",
    )
    return child


def test_template_child_without_context_inherits_exact_parent_runtime():
    parent_runtime = _runtime(_RecordingCompiler())
    parent = Agent(
        name="parent",
        provider="openai",
        model="gpt-test",
        modules=(ContextModule(runtime=parent_runtime),),
    )
    template_agent = Agent(
        name="template",
        provider="openai",
        model="gpt-test",
    )

    child = _template_child(parent=parent, template_agent=template_agent)

    context_modules = [
        module for module in child.spec.modules if isinstance(module, ContextModule)
    ]
    assert len(context_modules) == 1
    assert context_modules[0].runtime is parent_runtime


def test_template_child_without_context_inherits_exact_parent_shadow_runtime():
    parent_runtime = _runtime(_RecordingCompiler())
    parent = Agent(
        name="parent",
        provider="openai",
        model="gpt-test",
        modules=(
            ContextShadowModule(
                runtime=parent_runtime,
                enabled=True,
            ),
        ),
    )
    template_agent = Agent(
        name="template",
        provider="openai",
        model="gpt-test",
    )

    child = _template_child(parent=parent, template_agent=template_agent)

    context_modules = [
        module
        for module in child.spec.modules
        if isinstance(module, ContextShadowModule) and module.enabled
    ]
    assert len(context_modules) == 1
    assert context_modules[0].runtime is parent_runtime


def test_template_child_rejects_active_context_for_shadow_parent():
    parent_runtime = _runtime(_RecordingCompiler())
    parent = Agent(
        name="parent",
        provider="openai",
        model="gpt-test",
        modules=(
            ContextShadowModule(
                runtime=parent_runtime,
                enabled=True,
            ),
        ),
    )
    template_agent = Agent(
        name="template",
        provider="openai",
        model="gpt-test",
        modules=(ContextModule(runtime=parent_runtime),),
    )

    with pytest.raises(ValueError, match="exact parent ContextRuntime"):
        _template_child(parent=parent, template_agent=template_agent)


def test_template_child_accepts_only_the_identical_parent_runtime():
    parent_runtime = _runtime(_RecordingCompiler())
    parent = Agent(
        name="parent",
        provider="openai",
        model="gpt-test",
        modules=(ContextModule(runtime=parent_runtime),),
    )
    template_agent = Agent(
        name="template",
        provider="openai",
        model="gpt-test",
        modules=(ContextModule(runtime=parent_runtime),),
    )

    child = _template_child(parent=parent, template_agent=template_agent)

    module = next(
        module for module in child.spec.modules if isinstance(module, ContextModule)
    )
    assert module.runtime is parent_runtime


@pytest.mark.parametrize("other_owner", ["context-v2", "other-owner"])
def test_template_child_rejects_a_different_context_runtime(other_owner):
    parent_runtime = _runtime(_RecordingCompiler())
    parent = Agent(
        name="parent",
        provider="openai",
        model="gpt-test",
        modules=(ContextModule(runtime=parent_runtime),),
    )
    other_runtime = ContextRuntime._for_test(
        owner_id=other_owner,
        compiler=ContextCompiler(),
        request_factory=_request_factory,
        durable_event_sink=lambda event: None,
        partial_attempt_sink=lambda event, error: None,
    )
    template_agent = Agent(
        name="template",
        provider="openai",
        model="gpt-test",
        modules=(ContextModule(runtime=other_runtime),),
    )

    with pytest.raises(ValueError, match="exact parent ContextRuntime"):
        _template_child(parent=parent, template_agent=template_agent)


def test_legacy_parent_does_not_force_context_onto_template_child():
    parent = Agent(name="parent", provider="openai", model="gpt-test")
    template_agent = Agent(name="template", provider="openai", model="gpt-test")

    child = _template_child(parent=parent, template_agent=template_agent)

    assert not any(isinstance(module, ContextModule) for module in child.spec.modules)


def test_real_human_input_resume_compiles_both_sides_through_one_runtime():
    from unchain.agent import ToolsModule
    from unchain.input import HumanInputResponse, build_ask_user_question_tool
    from unchain.kernel import ToolCall

    compiler = _RecordingCompiler()
    runtime = _runtime(compiler)

    class HumanInputModelIO:
        provider = "openai"
        model = "gpt-test"

        def __init__(self):
            self.calls = 0

        def fetch_turn(self, request):
            del request
            self.calls += 1
            if self.calls == 1:
                arguments = {
                    "title": "Need input",
                    "question": "Pick one",
                    "selection_mode": "single",
                    "options": [
                        {"label": "A", "value": "a"},
                        {"label": "B", "value": "b"},
                    ],
                }
                return ModelTurnResult(
                    assistant_messages=[
                        {
                            "role": "assistant",
                            "type": "function_call",
                            "call_id": "call-human",
                            "name": "ask_user_question",
                            "arguments": json.dumps(arguments),
                        }
                    ],
                    tool_calls=[
                        ToolCall(
                            call_id="call-human",
                            name="ask_user_question",
                            arguments=arguments,
                        )
                    ],
                )
            return ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "resumed"}],
                tool_calls=[],
                final_text="resumed",
            )

    model_io = HumanInputModelIO()
    agent = Agent(
        name="resume-agent",
        provider="openai",
        model="gpt-test",
        modules=(
            ContextModule(runtime=runtime),
            ToolsModule(tools=(build_ask_user_question_tool(),)),
        ),
        model_io_factory=lambda spec, context: model_io,
    )

    suspended = agent.run(
        "ask me",
        session_id="session-resume-real",
        run_id="run-resume-start",
        max_iterations=2,
        max_context_window_tokens=16_384,
    )
    assert suspended.status == "awaiting_human_input"
    before_resume_build_count = len(compiler.results)

    resumed = agent.resume_human_input(
        conversation=suspended.messages,
        continuation=suspended.continuation,
        response=HumanInputResponse(
            request_id=suspended.human_input_request["request_id"],
            selected_values=["a"],
        ).to_dict(),
        session_id="session-resume-real",
        run_id="run-resume-finish",
    )

    assert resumed.status == "completed"
    assert len(compiler.results) > before_resume_build_count
    assert all(result.envelope is not None for result in compiler.results)
    assert {result.envelope.execution_id for result in compiler.results} == {
        "session-resume-real"
    }


def test_normal_run_delivers_wrapped_callback_to_kernel():
    compiler = _RecordingCompiler()
    order = []
    prepared = _builder(
        _runtime(compiler, order),
        callback=lambda event: order.append(("host", event["type"])),
    ).build()

    result = prepared.run()

    assert result.status == "completed"
    assert compiler.results
    assert order[0:2] == [
        ("durable", "run_started"),
        ("host", "run_started"),
    ]


def test_resume_human_input_passes_the_same_wrapped_callback(monkeypatch):
    compiler = _RecordingCompiler()
    order = []
    prepared = _builder(
        _runtime(compiler, order),
        mode="resume_human_input",
        callback=lambda event: order.append(("host", event["type"])),
    ).build()
    prepared.call_context = replace(
        prepared.call_context,
        conversation=[{"role": "user", "content": "before pause"}],
        continuation={"type": "legacy_resume", "provider": "openai", "model": "gpt-test"},
        response={"selected_values": ["yes"]},
    )

    def fake_resume_human_input(**kwargs):
        kwargs["callback"]({"type": "resume_human_probe"})
        return KernelRunResult(messages=[], status="completed")

    monkeypatch.setattr(prepared.loop, "resume_human_input", fake_resume_human_input)

    assert prepared.resume_human_input().status == "completed"
    assert order == [
        ("durable", "resume_human_probe"),
        ("host", "resume_human_probe"),
    ]


def test_resume_interaction_passes_the_same_wrapped_callback(monkeypatch):
    compiler = _RecordingCompiler()
    order = []
    prepared = _builder(
        _runtime(compiler, order),
        mode="resume_interaction",
        callback=lambda event: order.append(("host", event["type"])),
    ).build()
    checkpoint = {
        "checkpoint_id": "checkpoint-1",
        "transcript": [{"role": "user", "content": "before pause"}],
        "continuation": {"type": "legacy_resume"},
    }
    prepared.memory_runtime = SimpleNamespace(
        load_execution_checkpoint=lambda session_id: checkpoint
    )
    prepared.call_context = replace(
        prepared.call_context,
        conversation=checkpoint["transcript"],
        continuation=checkpoint["continuation"],
        response={"approved": True},
    )
    monkeypatch.setattr(
        "unchain.agent.builder.restore_resume_checkpoint_messages",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        prepared,
        "_preflight_durable_interaction_resume",
        lambda **kwargs: None,
    )

    def fake_resume_interaction(**kwargs):
        kwargs["callback"]({"type": "resume_interaction_probe"})
        return KernelRunResult(messages=[], status="completed")

    monkeypatch.setattr(prepared.loop, "resume_interaction", fake_resume_interaction)

    assert prepared.resume_interaction().status == "completed"
    assert order == [
        ("durable", "resume_interaction_probe"),
        ("host", "resume_interaction_probe"),
    ]


def test_completion_policy_events_use_the_same_wrapped_callback():
    from unchain.runtime import CompletionPolicy

    compiler = _RecordingCompiler()
    order = []
    builder = _builder(
        _runtime(compiler, order),
        callback=lambda event: order.append(("host", event["type"])),
    )
    builder.set_completion_policy(
        CompletionPolicy(validator=lambda result: False, max_repair_turns=0)
    )

    result = builder.build().run()

    assert result.status == "completion_incomplete"
    assert (
        "durable",
        "completion_policy_evaluated",
    ) in order
    position = order.index(("durable", "completion_policy_evaluated"))
    assert order[position + 1] == ("host", "completion_policy_evaluated")


def test_tool_exposure_receives_the_same_wrapped_callback(monkeypatch):
    from unchain.tools import ToolOptimizerConfig

    compiler = _RecordingCompiler()
    order = []
    builder = _builder(
        _runtime(compiler, order),
        callback=lambda event: order.append(("host", event["type"])),
    )
    builder.set_tool_optimizer_config(
        ToolOptimizerConfig(enabled=True, trigger_tool_count=0)
    )
    builder.add_tool(lambda value=1: value)

    class FakeToolExposureRuntime:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.toolkit = kwargs["full_toolkit"]

        def prepare(self, replay_plan=None):
            del replay_plan
            self.callback({"type": "tool_exposure_probe"})
            return self.toolkit

        def build_plugins(self):
            return []

    monkeypatch.setattr(
        "unchain.agent.builder.ToolExposureRuntime",
        FakeToolExposureRuntime,
    )

    assert builder.build().run().status == "completed"
    assert order[0:2] == [
        ("durable", "tool_exposure_probe"),
        ("host", "tool_exposure_probe"),
    ]


def test_tool_exposure_sink_failure_stops_before_model_progress(monkeypatch):
    from unchain.tools import ToolOptimizerConfig

    compiler = _RecordingCompiler()
    failure = RuntimeError("persist failed")
    order = []
    builder = _builder(
        _runtime(compiler, order, fail=failure),
        callback=lambda event: order.append(("host", event["type"])),
    )
    builder.set_tool_optimizer_config(
        ToolOptimizerConfig(enabled=True, trigger_tool_count=0)
    )
    builder.add_tool(lambda value=1: value)

    class FakeToolExposureRuntime:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.toolkit = kwargs["full_toolkit"]

        def prepare(self, replay_plan=None):
            del replay_plan
            self.callback({"type": "tool_exposure_probe"})
            return self.toolkit

        def build_plugins(self):
            return []

    monkeypatch.setattr(
        "unchain.agent.builder.ToolExposureRuntime",
        FakeToolExposureRuntime,
    )

    prepared = builder.build()
    with pytest.raises(RuntimeError) as raised:
        prepared.run()

    assert raised.value is failure
    assert prepared.loop.model_io.requests == []
    assert order == [
        ("durable", "tool_exposure_probe"),
        ("partial", "tool_exposure_probe", failure),
    ]
