from __future__ import annotations

import pytest

from unchain.agent import Agent, AgentCallContext
from unchain.agent.modules import ContextModule, SubagentModule
from unchain.memory import (
    MEMORY_CANDIDATE_PROPOSE,
    MEMORY_CONTEXT_READ,
    MEMORY_EXECUTION_COMPLETE,
    MEMORY_V2_MODULE_KEY,
    MEMORY_WORKSPACE_READ,
    MemoryAttachment,
    MemoryAttachmentRequest,
    MemoryV2Module,
)
from unchain.kernel import ModelTurnResult
from unchain.memory.curator.host import MemoryAgentHostAdapter
from unchain.memory.toolkit import MemoryToolkitRunBinding
from unchain.runtime import AgentRuntimeContext, ExecutionIdentity, ModuleGrant
from unchain.subagents import SubagentExecutor, SubagentPolicy, SubagentTemplate
from unchain.subagents.plugin import (
    SubagentRuntimeContextError,
    SubagentToolPlugin,
)

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
        return MemoryAttachment(
            binding=MemoryToolkitRunBinding(
                binding_id=self.binding_id,
                session_id=request.session_id,
                attempt_id=request.attempt_id or "missing-child-attempt",
                run_id=request.run_id,
            ),
            capabilities=_normal_capabilities(),
        )


def _root_runtime_context(
    *,
    execution_id="root-execution-a",
    attempt_id="root-attempt-a",
    run_id="root-run-a",
):
    delegable = frozenset(
        {
            MEMORY_CONTEXT_READ,
            MEMORY_WORKSPACE_READ,
            MEMORY_CANDIDATE_PROPOSE,
        }
    )
    return AgentRuntimeContext(
        identity=ExecutionIdentity(
            execution_id=execution_id,
            attempt_id=attempt_id,
            run_id=run_id,
            run_lineage=(run_id,),
        ),
        module_grants=(
            ModuleGrant(
                module_key=MEMORY_V2_MODULE_KEY,
                capabilities=delegable | {MEMORY_EXECUTION_COMPLETE},
                delegable_capabilities=delegable,
                authority="completion-authority-a",
            ),
        ),
    )


_DEFAULT_RUNTIME_CONTEXT = object()


def _plugin(
    parent,
    *,
    templates=(),
    runtime_context=_DEFAULT_RUNTIME_CONTEXT,
):
    if runtime_context is _DEFAULT_RUNTIME_CONTEXT:
        runtime_context = _root_runtime_context()
    return SubagentToolPlugin(
        parent_agent=parent,
        templates=templates,
        policy=SubagentPolicy(allow_dynamic_delegate=True),
        executor=SubagentExecutor(),
        runtime_context=runtime_context,
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
def test_child_memory_attachment_uses_delegated_grant_and_parent_lineage(
    template_backed,
):
    host, _, _, _ = _enabled_host()
    factory = _ChildAttachmentFactory()
    model_io = _FinalModelIO()
    memory_module = MemoryV2Module(host=host, attachment_factory=factory)
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
    runtime_context = _root_runtime_context()
    plugin = _plugin(
        parent,
        templates=(template,) if template is not None else (),
        runtime_context=runtime_context,
    )
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
        MemoryAttachmentRequest(
            agent_name="parent.specialist.1",
            mode="run",
            identity=ExecutionIdentity(
                execution_id="child-session",
                attempt_id="child-run-a",
                run_id="child-run-a",
                run_lineage=("root-run-a", "child-run-a"),
            ),
            grant=runtime_context.grant_for(MEMORY_V2_MODULE_KEY).delegated(),
        )
    ]
    assert factory.requests[0].grant.authority is None
    assert not factory.requests[0].grant.allows(MEMORY_EXECUTION_COMPLETE)
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


def test_enabled_memory_child_fails_closed_without_parent_runtime_context():
    host, _, _, _ = _enabled_host()
    factory = _ChildAttachmentFactory()
    memory_module = MemoryV2Module(host=host, attachment_factory=factory)
    parent = Agent(
        name="parent",
        modules=(memory_module,),
        model_io_factory=lambda spec, context: _FinalModelIO(),
    )
    plugin = _plugin(parent, runtime_context=None)
    child = _build_child(plugin)

    with pytest.raises(
        SubagentRuntimeContextError,
        match="explicit parent runtime context",
    ):
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


def test_legacy_fake_child_signature_runs_without_runtime_context_keyword():
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


def test_subagent_module_binds_generic_runtime_context_to_runtime_plugin():
    parent = Agent(
        name="parent",
        modules=(SubagentModule(),),
        model_io_factory=lambda spec, context: _FinalModelIO(),
    )

    runtime_context = _root_runtime_context()
    prepared = parent._prepare(
        AgentCallContext(mode="run", runtime_context=runtime_context)
    )

    plugin = next(
        plugin
        for plugin in prepared.tool_runtime_plugins
        if isinstance(plugin, SubagentToolPlugin)
    )
    assert plugin.runtime_context is runtime_context
    assert not hasattr(plugin, "memory_v2_run_role")


@pytest.mark.parametrize("template_backed", [False, True])
def test_dynamic_and_template_children_inherit_exact_context_and_memory_modules(
    template_backed,
):
    context_module = ContextModule(runtime=object())
    memory_module = MemoryV2Module()
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
        if isinstance(module, MemoryV2Module)
    )
    assert child_context == (context_module,)
    assert child_memory == (memory_module,)


@pytest.mark.parametrize(
    "template_memory_module",
    [
        MemoryV2Module(),
        MemoryV2Module(
            host=MemoryAgentHostAdapter(
                FakeCurationRepository(binding_id="binding-other")
            )
        ),
    ],
    ids=("different-instance", "different-binding"),
)
def test_template_child_rejects_non_parent_memory_module(template_memory_module):
    context_module = ContextModule(runtime=object())
    parent_memory_module = MemoryV2Module()
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

    with pytest.raises(ValueError, match="exact parent Memory V2 module"):
        _build_child(plugin, template=template)


_MISSING_RUNTIME_CONTEXT = object()


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
        runtime_context=_MISSING_RUNTIME_CONTEXT,
    ):
        self.calls.append(
            {
                "messages": messages,
                "session_id": session_id,
                "run_id": run_id,
                "runtime_context": runtime_context,
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
def test_recipe_ref_child_receives_derived_roleless_runtime_context(active):
    plugin = _plugin(
        Agent(name="parent"),
        runtime_context=_root_runtime_context(
            execution_id="parent-context-v2-execution"
        ),
    )
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
    assert len(child.calls) == 1
    call = child.calls[0]
    expected_execution_id = (
        "parent-context-v2-execution"
        if active
        else "parent-context-v2-execution:recipe-ref-child"
    )
    assert call["messages"] == "inspect"
    assert call["session_id"] == expected_execution_id
    assert call["run_id"] == "recipe-ref-run"
    child_context = call["runtime_context"]
    assert isinstance(child_context, AgentRuntimeContext)
    assert child_context.identity == ExecutionIdentity(
        execution_id=expected_execution_id,
        attempt_id="recipe-ref-run",
        run_id="recipe-ref-run",
        run_lineage=("root-run-a", "recipe-ref-run"),
    )
    child_grant = child_context.grant_for(MEMORY_V2_MODULE_KEY)
    assert child_grant is not None
    assert child_grant.authority is None
    assert not child_grant.allows(MEMORY_EXECUTION_COMPLETE)
    if active:
        assert len(completion_sink.preparations) == 1
        assert len(completion_sink.completions) == 1


def test_active_recipe_ref_rejects_prepared_parent_execution_identity_drift():
    plugin = _plugin(
        Agent(name="parent"),
        runtime_context=_root_runtime_context(
            execution_id="parent-context-v2-execution"
        ),
    )
    child = _RecipeRefChild()
    completion_sink = _PreparedInputCompletionSink("different-execution")

    with pytest.raises(
        SubagentRuntimeContextError,
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


def test_ordinary_child_without_parent_context_receives_no_runtime_context():
    plugin = _plugin(
        Agent(name="parent"),
        runtime_context=None,
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
    assert child.calls[0]["runtime_context"] is _MISSING_RUNTIME_CONTEXT
