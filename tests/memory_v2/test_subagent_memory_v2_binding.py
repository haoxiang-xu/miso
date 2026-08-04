from __future__ import annotations

import pytest

from unchain.agent import Agent, AgentCallContext
from unchain.agent.modules import ContextModule, SubagentModule
from unchain.agent.modules.memory_v2 import (
    MemoryV2AgentAttachment,
    MemoryV2AgentAttachmentRequest,
    MemoryV2AgentModule,
    MemoryV2AgentModuleError,
    MemoryV2RunRole,
)
from unchain.kernel import ModelTurnResult
from unchain.memory.curator.host import MemoryAgentHostAdapter
from unchain.memory.toolkit import MemoryToolkitRunBinding
from unchain.subagents import SubagentExecutor, SubagentPolicy, SubagentTemplate
from unchain.subagents.plugin import SubagentToolPlugin

from .test_curator_coordinator import FakeCurationRepository
from .test_memory_agent_host import _enabled_host, _normal_capabilities


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


class _ChildAttachmentFactory:
    binding_id = "binding-a"

    def __init__(self) -> None:
        self.requests = []

    def attach(self, request):
        self.requests.append(request)
        return MemoryV2AgentAttachment(
            binding=MemoryToolkitRunBinding(
                binding_id=self.binding_id,
                session_id=request.session_id,
                attempt_id=request.attempt_id or "missing-child-attempt",
                run_id=request.run_id,
            ),
            capabilities=_normal_capabilities(),
        )


def _plugin(
    parent,
    *,
    templates=(),
    run_role=MemoryV2RunRole.ROOT,
    root_run_id="root-run-a",
):
    return SubagentToolPlugin(
        parent_agent=parent,
        templates=templates,
        policy=SubagentPolicy(allow_dynamic_delegate=True),
        executor=SubagentExecutor(),
        memory_v2_run_role=run_role,
        root_run_id=root_run_id,
    )


def _build_child(plugin, *, template=None):
    return plugin._build_subagent(
        template=template,
        child_id="parent.specialist.1",
        lineage=["parent", "parent.specialist.1"],
        mode="delegate",
        target="specialist",
        task="inspect",
        instructions="",
        expected_output="summary",
    )[0]


@pytest.mark.parametrize("template_backed", [False, True])
def test_child_memory_attachment_uses_explicit_subagent_role_and_parent_root_identity(
    template_backed,
):
    host, _, _, _ = _enabled_host()
    factory = _ChildAttachmentFactory()
    model_io = _FinalModelIO()
    memory_module = MemoryV2AgentModule(host=host, attachment_factory=factory)
    parent = Agent(
        name="parent",
        provider="openai",
        model="gpt-test",
        modules=(memory_module,),
        model_io_factory=lambda spec, context: model_io,
    )
    template = (
        SubagentTemplate(
            name="specialist",
            description="template specialist",
            agent=Agent(
                name="template",
                model_io_factory=lambda spec, context: model_io,
            ),
        )
        if template_backed
        else None
    )
    plugin = _plugin(parent, templates=(template,) if template is not None else ())
    child = _build_child(plugin, template=template)

    result = plugin._run_child(
        agent=child,
        mode="delegate",
        child_id="parent.specialist.1",
        lineage=["parent", "parent.specialist.1"],
        template_name=None,
        session_id="child-session",
        memory_namespace="",
        input_messages="inspect",
        max_iterations=1,
        child_run_id="child-run-a",
    )

    assert result.status == "completed"
    assert factory.requests == [
        MemoryV2AgentAttachmentRequest(
            agent_name="parent.specialist.1",
            mode="run",
            session_id="child-session",
            attempt_id="child-run-a",
            run_id="child-run-a",
            role=MemoryV2RunRole.SUBAGENT,
            root_run_id="root-run-a",
        )
    ]
    assert tuple(
        name
        for name in model_io.requests[0].toolkit.tools
        if name.startswith("memory_")
    ) == (
        "memory_list",
        "memory_search",
        "memory_read",
        "memory_propose",
    )


def test_enabled_memory_child_fails_closed_without_parent_root_identity():
    host, _, _, _ = _enabled_host()
    factory = _ChildAttachmentFactory()
    memory_module = MemoryV2AgentModule(host=host, attachment_factory=factory)
    parent = Agent(
        name="parent",
        modules=(memory_module,),
        model_io_factory=lambda spec, context: _FinalModelIO(),
    )
    plugin = _plugin(parent, run_role=None, root_run_id="")
    child = _build_child(plugin)

    with pytest.raises(MemoryV2AgentModuleError, match="root run identity"):
        plugin._run_child(
            agent=child,
            mode="delegate",
            child_id="parent.specialist.1",
            lineage=["parent", "parent.specialist.1"],
            template_name=None,
            session_id="child-session",
            memory_namespace="",
            input_messages="inspect",
            max_iterations=1,
            child_run_id="child-run-a",
        )

    assert factory.requests == []


def test_legacy_fake_child_signature_runs_without_memory_identity_keywords():
    class LegacyChild:
        name = "legacy-child"

        def __init__(self):
            self.calls = []

        def run(
            self,
            messages,
            *,
            session_id,
            memory_namespace,
            max_iterations,
            callback,
            on_tool_confirm,
            on_human_input,
            on_max_iterations,
            run_id,
        ):
            self.calls.append(
                {
                    "messages": messages,
                    "session_id": session_id,
                    "run_id": run_id,
                }
            )
            return type(
                "Result",
                (),
                {
                    "status": "completed",
                    "messages": [{"role": "assistant", "content": "legacy done"}],
                    "human_input_request": None,
                },
            )()

    parent = Agent(name="parent")
    plugin = _plugin(parent)
    child = LegacyChild()

    result = plugin._run_child(
        agent=child,
        mode="delegate",
        child_id="legacy-child",
        lineage=["parent", "legacy-child"],
        template_name=None,
        session_id="legacy-session",
        memory_namespace="",
        input_messages="inspect",
        max_iterations=1,
        child_run_id="legacy-run",
    )

    assert result.status == "completed"
    assert child.calls == [
        {
            "messages": "inspect",
            "session_id": "legacy-session",
            "run_id": "legacy-run",
        }
    ]


def test_subagent_module_binds_explicit_parent_root_identity_to_runtime_plugin():
    parent = Agent(
        name="parent",
        modules=(SubagentModule(),),
        model_io_factory=lambda spec, context: _FinalModelIO(),
    )

    prepared = parent._prepare(
        AgentCallContext(
            mode="run",
            run_id="root-run-a",
            memory_v2_run_role=MemoryV2RunRole.ROOT,
            root_run_id="root-run-a",
        )
    )

    plugin = next(
        plugin
        for plugin in prepared.tool_runtime_plugins
        if isinstance(plugin, SubagentToolPlugin)
    )
    assert plugin.memory_v2_run_role is MemoryV2RunRole.ROOT
    assert plugin.root_run_id == "root-run-a"


@pytest.mark.parametrize("template_backed", [False, True])
def test_dynamic_and_template_children_inherit_exact_context_and_memory_modules(
    template_backed,
):
    context_module = ContextModule(runtime=object())
    memory_module = MemoryV2AgentModule()
    parent = Agent(
        name="parent",
        modules=(context_module, memory_module),
    )
    template = (
        SubagentTemplate(
            name="specialist",
            description="template specialist",
            agent=Agent(name="template"),
        )
        if template_backed
        else None
    )
    plugin = _plugin(parent, templates=(template,) if template is not None else ())

    child = _build_child(plugin, template=template)

    child_context = tuple(
        module for module in child.spec.modules if isinstance(module, ContextModule)
    )
    child_memory = tuple(
        module
        for module in child.spec.modules
        if isinstance(module, MemoryV2AgentModule)
    )
    assert child_context == (context_module,)
    assert child_memory == (memory_module,)


@pytest.mark.parametrize(
    "template_memory_module",
    [
        MemoryV2AgentModule(),
        MemoryV2AgentModule(
            host=MemoryAgentHostAdapter(
                FakeCurationRepository(binding_id="binding-other")
            )
        ),
    ],
    ids=("different-instance", "different-binding"),
)
def test_template_child_rejects_non_parent_memory_module(template_memory_module):
    context_module = ContextModule(runtime=object())
    parent_memory_module = MemoryV2AgentModule()
    parent = Agent(
        name="parent",
        modules=(context_module, parent_memory_module),
    )
    template = SubagentTemplate(
        name="specialist",
        description="template specialist",
        agent=Agent(name="template", modules=(template_memory_module,)),
    )
    plugin = _plugin(parent, templates=(template,))

    with pytest.raises(ValueError, match="exact parent MemoryV2AgentModule"):
        _build_child(plugin, template=template)


_MISSING_EXECUTION_ID = object()


class _RecipeRefChild:
    name = "recipe-ref-child"

    def __init__(self):
        self.calls = []

    def run(
        self,
        messages,
        *,
        session_id,
        memory_namespace,
        max_iterations,
        callback,
        on_tool_confirm,
        on_human_input,
        on_max_iterations,
        run_id,
        memory_v2_run_role=None,
        root_run_id=None,
        memory_v2_execution_id=_MISSING_EXECUTION_ID,
    ):
        self.calls.append(
            {
                "messages": messages,
                "session_id": session_id,
                "run_id": run_id,
                "memory_v2_run_role": memory_v2_run_role,
                "root_run_id": root_run_id,
                "memory_v2_execution_id": memory_v2_execution_id,
            }
        )
        return type(
            "Result",
            (),
            {
                "status": "completed",
                "messages": [{"role": "assistant", "content": "recipe done"}],
                "human_input_request": None,
            },
        )()


class _PreparedExecution:
    def __init__(self, execution_id):
        self.execution_id = execution_id
        self.recovered_result = None


class _PreparedInputCompletionSink:
    def __init__(self, execution_id):
        self.execution_id = execution_id
        self.preparations = []
        self.completions = []

    def prepare_input(self, **kwargs):
        self.preparations.append(kwargs)
        return _PreparedExecution(self.execution_id)

    def record(self, **kwargs):
        self.completions.append(kwargs)


@pytest.mark.parametrize("active", [False, True], ids=("shadow", "active"))
def test_recipe_ref_child_receives_explicit_parent_context_v2_execution_id(active):
    plugin = _plugin(Agent(name="parent"))
    child = _RecipeRefChild()
    completion_sink = (
        _PreparedInputCompletionSink("parent-context-v2-execution")
        if active
        else None
    )

    result = plugin._run_child(
        agent=child,
        mode="delegate",
        child_id=child.name,
        lineage=["parent", child.name],
        template_name="recipe-ref",
        session_id="parent-context-v2-execution:recipe-ref-child",
        parent_context_v2_execution_id="parent-context-v2-execution",
        memory_namespace="",
        input_messages="inspect",
        max_iterations=1,
        child_run_id="recipe-ref-run",
        completion_sink=completion_sink,
    )

    assert result.status == "completed"
    assert child.calls == [
        {
            "messages": "inspect",
            "session_id": (
                "parent-context-v2-execution"
                if active
                else "parent-context-v2-execution:recipe-ref-child"
            ),
            "run_id": "recipe-ref-run",
            "memory_v2_run_role": MemoryV2RunRole.SUBAGENT,
            "root_run_id": "root-run-a",
            "memory_v2_execution_id": "parent-context-v2-execution",
        }
    ]
    if active:
        assert len(completion_sink.preparations) == 1
        assert len(completion_sink.completions) == 1


def test_active_recipe_ref_rejects_prepared_parent_execution_identity_drift():
    plugin = _plugin(Agent(name="parent"))
    child = _RecipeRefChild()
    completion_sink = _PreparedInputCompletionSink("different-execution")

    with pytest.raises(
        MemoryV2AgentModuleError,
        match="changed its parent Context V2 execution identity",
    ):
        plugin._run_child(
            agent=child,
            mode="delegate",
            child_id=child.name,
            lineage=["parent", child.name],
            template_name="recipe-ref",
            session_id="parent-context-v2-execution:recipe-ref-child",
            parent_context_v2_execution_id="parent-context-v2-execution",
            memory_namespace="",
            input_messages="inspect",
            max_iterations=1,
            child_run_id="recipe-ref-run",
            completion_sink=completion_sink,
        )

    assert child.calls == []
    assert completion_sink.completions == []


def test_ordinary_child_does_not_receive_memory_v2_execution_id():
    plugin = _plugin(
        Agent(name="parent"),
        run_role=None,
        root_run_id="",
    )
    child = _RecipeRefChild()

    result = plugin._run_child(
        agent=child,
        mode="delegate",
        child_id=child.name,
        lineage=["parent", child.name],
        template_name=None,
        session_id="ordinary-child-session",
        parent_context_v2_execution_id="parent-context-v2-execution",
        memory_namespace="",
        input_messages="inspect",
        max_iterations=1,
        child_run_id="ordinary-child-run",
    )

    assert result.status == "completed"
    assert child.calls[0]["session_id"] == "ordinary-child-session"
    assert (
        child.calls[0]["memory_v2_execution_id"]
        is _MISSING_EXECUTION_ID
    )
