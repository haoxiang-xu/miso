from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.context_v2.test_context_runtime_factory import (
    _Journal,
    _bundle,
    _context,
    _current_input,
)
from unchain.agent import Agent
from unchain.agent.modules import ContextModule, ContextShadowModule
from unchain.context.factory import (
    ContextExecutionBundleError,
    DurableContextRuntimeFactory,
)
from unchain.context.runtime import ContextRuntime
from unchain.durability import is_durable_persistence_failure
from unchain.execution import ExecutionRuntime
from unchain.journal import ArtifactRef, JournalRepositoryError
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.kernel.types import KernelRunResult, ToolCall
from unchain.memory import InMemorySessionStore
from unchain.subagents.executor import SubagentExecutor
from unchain.subagents.plugin import SubagentToolPlugin
from unchain.subagents.types import SubagentPolicy, SubagentResult
from unchain.tools import Toolkit
from unchain.tools.runtime import run_tool_runtime_plugins


def _plugin() -> SubagentToolPlugin:
    return SubagentToolPlugin(
        parent_agent=Agent(name="parent", provider="openai"),
        templates=(),
        policy=SubagentPolicy(),
        executor=SubagentExecutor(),
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "delegate_to_subagent",
        "spawn_worker_batch",
        "spawn_agent_thread",
        "send_agent_message",
        "wait_agent_messages",
        "close_agent_thread",
        "write_agent_board",
        "read_agent_board",
        "return_handoff_to_subagent",
        "return_to_parent",
    ],
)
def test_subagent_plugin_declares_stable_snapshot_runtime_manifest(tool_name) -> None:
    plugin = _plugin()
    call = ToolCall(call_id="call-1", name=tool_name, arguments={})

    first = plugin.durable_runtime_manifest(tool_call=call, context=object())
    second = plugin.durable_runtime_manifest(tool_call=call, context=object())

    assert (
        first
        == second
        == {
            "schema": "unchain.subagent_tool_runtime.v1",
            "handler": "subagent_tool_plugin",
            "tool_name": tool_name,
            "terminal_handler": True,
            "completion_contract": {
                "schema": "unchain.tool_completion_contract.v1",
                "state_transition": "subagent_snapshot.v1",
                "allowed_state_keys": ["subagent_state"],
            },
        }
    )


def test_subagent_plugin_declares_terminal_handoff_completion_contract() -> None:
    plugin = _plugin()
    call = ToolCall(
        call_id="call-handoff",
        name="handoff_to_subagent",
        arguments={},
    )

    manifest = plugin.durable_runtime_manifest(tool_call=call, context=object())
    replayed_manifest = json.loads(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )

    assert manifest["completion_contract"] == {
        "schema": "unchain.tool_completion_contract.v1",
        "state_transition_variants": [
            {
                "state_transition": "subagent_snapshot.v1",
                "allowed_state_keys": ["subagent_state"],
            },
            {
                "state_transition": "subagent_terminal_handoff.v1",
                "allowed_state_keys": [
                    "subagent_state",
                    "transcript",
                    "run_status",
                    "pending_tool_calls",
                    "tool_batch_state",
                    "last_continuation",
                    "next_model_input",
                ],
            },
        ],
    }
    assert replayed_manifest == manifest


class _CompletedChild:
    name = "child"

    def run(self, _messages, **_kwargs) -> KernelRunResult:
        return KernelRunResult(
            messages=[
                {
                    "role": "assistant",
                    "content": "x" * 24_000,
                }
            ],
            status="completed",
        )


class _RecordingCompletionSink:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, SubagentResult]] = []

    def record(self, *, child_run_id: str, result: SubagentResult):
        self.calls.append((child_run_id, result))
        if self.failure is not None:
            raise self.failure
        return object()


def test_run_child_records_exact_completion_before_returning_large_result() -> None:
    sink = _RecordingCompletionSink()

    result = _plugin()._run_child(
        agent=_CompletedChild(),
        mode="delegate",
        child_id="child",
        lineage=["parent", "child"],
        template_name=None,
        session_id="root:child",
        memory_namespace="",
        input_messages="work",
        max_iterations=1,
        child_run_id="child-run",
        completion_sink=sink,
    )

    assert sink.calls == [("child-run", result)]
    assert len(result.output) == 24_000


def test_run_child_propagates_completion_persistence_failure() -> None:
    failure = JournalRepositoryError("handoff append failed")
    sink = _RecordingCompletionSink(failure=failure)

    with pytest.raises(JournalRepositoryError) as caught:
        _plugin()._run_child(
            agent=_CompletedChild(),
            mode="delegate",
            child_id="child",
            lineage=["parent", "child"],
            template_name=None,
            session_id="root:child",
            memory_namespace="",
            input_messages="work",
            max_iterations=1,
            child_run_id="child-run",
            completion_sink=sink,
        )

    assert caught.value is failure
    assert len(sink.calls) == 1


def test_run_child_keeps_legacy_behavior_when_no_completion_sink() -> None:
    result = _plugin()._run_child(
        agent=_CompletedChild(),
        mode="delegate",
        child_id="child",
        lineage=["parent", "child"],
        template_name=None,
        session_id="root:child",
        memory_namespace="",
        input_messages="work",
        max_iterations=1,
        child_run_id="child-run",
    )

    assert result.status == "completed"
    assert len(result.output) == 24_000


def test_legacy_delegate_plugin_route_runs_without_context_module_or_sink() -> None:
    plugin = SubagentToolPlugin(
        parent_agent=Agent(name="parent", provider="openai"),
        templates=(),
        policy=SubagentPolicy(allow_dynamic_delegate=True),
        executor=SubagentExecutor(),
    )
    child = _CompletedChild()
    plugin._build_subagent = lambda **_kwargs: (child, "ephemeral", None)
    toolkit = Toolkit()

    @toolkit.tool(name="delegate_to_subagent")
    def legacy_delegate(target: str, task: str):
        raise AssertionError((target, task))

    context = type(
        "LegacyToolContext",
        (),
        {
            "state": type(
                "LegacyState",
                (),
                {"subagent_state": None},
            )(),
            "session_id": "legacy-session",
            "memory_namespace": "",
            "run_id": "legacy-run",
            "iteration": 1,
            "event": {},
            "callback": None,
            "loop": None,
            "execution_guard": None,
            "toolkit": toolkit,
            "latest_messages": staticmethod(
                lambda: [{"role": "user", "content": "work"}]
            ),
        },
    )()
    call = ToolCall(
        call_id="call-legacy",
        name="delegate_to_subagent",
        arguments={"target": "researcher", "task": "investigate"},
    )

    outcome = run_tool_runtime_plugins(
        [plugin],
        tool_call=call,
        context=context,
    )

    assert outcome is not None
    assert outcome.handled is True
    assert outcome.tool_result["status"] == "completed"
    assert len(outcome.tool_result["output"]) == 24_000


def _runtime_fixture(*, parent_journal=None, partials=None, nested_child=False):
    bundles = {}

    def build(attempt):
        journal = (
            parent_journal
            if attempt.attempt_id == "parent-run" and parent_journal is not None
            else None
        )
        bundle = _bundle(attempt, journal=journal)
        if attempt.attempt_id == "parent-run" and partials is not None:
            bundle = replace(
                bundle,
                partial_attempt_sink=lambda event, error: partials.append(
                    (event, error)
                ),
            )
        bundles[(attempt.generation.execution_id, attempt.attempt_id)] = bundle
        return bundle

    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=DurableContextRuntimeFactory(
            bundle_builder=build,
            generation_resolver=lambda context, execution_id: (
                f"generation-{execution_id}"
            ),
            current_input_resolver=_current_input,
        ),
    )
    parent_bootstrap = _context(
        session_id="parent-execution",
        run_id="parent-run",
    )
    child_bootstrap = _context(
        session_id="child-execution",
        run_id="child-run",
    )
    runtime.bind_context(parent_bootstrap)
    runtime.bind_context(child_bootstrap)
    child_bundle = bundles[("child-execution", "child-run")]
    child_bundle.durable_event_sink(
        {
            "type": "run_started",
            "run_id": "child-run",
            "iteration": 0,
        }
    )
    if nested_child:
        nested_attempt = replace(child_bundle.attempt, attempt_id="nested-child-run")
        nested_bundle = _bundle(nested_attempt, journal=child_bundle.journal)
        nested_bundle.durable_event_sink(
            {
                "type": "run_started",
                "run_id": "nested-child-run",
                "iteration": 0,
            }
        )
        nested_bundle.durable_event_sink(
            {
                "type": "final_message",
                "run_id": "nested-child-run",
                "iteration": 1,
                "status": "completed",
            }
        )
    child_bundle.durable_event_sink(
        {
            "type": "final_message",
            "run_id": "child-run",
            "iteration": 1,
            "status": "completed",
        }
    )
    parent_state = RunState()
    parent_state.session_state.session_id = "parent-execution"
    parent_context = HarnessContext(
        state=parent_state,
        phase="on_tool_call",
        event={"run_id": "parent-run"},
    )
    return runtime, parent_context, bundles


def _large_result() -> SubagentResult:
    return SubagentResult(
        mode="delegate",
        agent_name="child",
        template_name=None,
        status="completed",
        output="x" * 24_000,
        summary="child summary",
        messages=[{"role": "assistant", "content": "x" * 24_000}],
        lineage=["parent", "child"],
    )


@pytest.mark.parametrize("shadow", [False, True], ids=("active", "shadow"))
def test_prepared_subagent_input_is_exportable_for_active_and_shadow_parent(
    shadow: bool,
) -> None:
    runtime, parent_context, bundles = _runtime_fixture()
    call_id = "call-export-prepared-input"
    child_run_id = "prepared-child-run"
    parent_bundle = bundles[("parent-execution", "parent-run")]
    parent_bundle.durable_event_sink(
        {
            "type": "tool_call",
            "run_id": "parent-run",
            "iteration": 0,
            "tool_name": "delegate_to_subagent",
            "call_id": call_id,
            "arguments": {"target": "child", "task": "inspect"},
            "source_provider": "openai",
        }
    )
    context_module = (
        ContextShadowModule(runtime=runtime, enabled=True)
        if shadow
        else ContextModule(runtime=runtime)
    )
    plugin = SubagentToolPlugin(
        parent_agent=Agent(
            name="parent",
            provider="openai",
            modules=(context_module,),
        ),
        templates=(),
        policy=SubagentPolicy(),
        executor=SubagentExecutor(),
    )
    sink = plugin._subagent_completion_sink(
        SimpleNamespace(harness_context=parent_context),
        ToolCall(
            call_id=call_id,
            name="delegate_to_subagent",
            arguments={"target": "child", "task": "inspect"},
        ),
    )

    assert sink is not None
    preparation = sink.prepare_input(
        child_run_id=child_run_id,
        child_id="child",
        mode="delegate",
        lineage=["parent", "child"],
        template_name=None,
        input_messages="preserve this exact task",
    )

    exported = runtime.prepared_subagent_input(child_run_id)
    assert exported is preparation.prepared
    assert exported.child_run_id == child_run_id
    assert exported.full_output["input_messages"] == (
        {"role": "user", "content": "preserve this exact task"},
    )
    with pytest.raises(TypeError):
        exported.full_output["child_id"] = "changed"


def test_prepared_subagent_input_lookup_distinguishes_unknown_and_invalid_ids() -> (
    None
):
    runtime, _, _ = _runtime_fixture()

    assert runtime.prepared_subagent_input("unknown-child-run") is None
    with pytest.raises(ContextExecutionBundleError, match="was not prepared"):
        runtime.bind_prepared_subagent_input("unknown-child-run")
    with pytest.raises(ValueError, match="child_run_id"):
        runtime.prepared_subagent_input("")
    with pytest.raises(TypeError, match="child_run_id must be text"):
        runtime.prepared_subagent_input(None)


def test_attempt_bound_sink_persists_parent_handoff_once_with_exact_child_range() -> (
    None
):
    runtime, parent_context, bundles = _runtime_fixture()
    sink = runtime.prepare_subagent_completion_sink(
        parent_context,
        call_id="call-delegate",
    )

    first = sink.record(child_run_id="child-run", result=_large_result())
    replay = sink.record(child_run_id="child-run", result=_large_result())

    parent_bundle = bundles[("parent-execution", "parent-run")]
    child_bundle = bundles[("child-execution", "child-run")]
    handoff_events = [
        event
        for event in parent_bundle.journal.events
        if event.event_type == "handoff.recorded"
    ]
    assert len(handoff_events) == 1
    assert replay.duplicate is True
    assert first.envelope == replay.envelope
    assert first.envelope.child_attempt == child_bundle.attempt
    assert (
        first.envelope.source_event_range.start
        == first.envelope.source_event_range.end
    )
    checkpoint = next(
        event
        for event in child_bundle.journal.events
        if event.event_type == "subagent_completed"
        and event.attempt == child_bundle.attempt
        and event.payload.get("child_run_id") == "child-run"
    )
    assert first.envelope.source_event_range.start.store_seq == checkpoint.store_seq
    assert checkpoint.payload["result_checkpoint"]["schema"] == (
        "unchain.subagent_result_checkpoint.v1"
    )
    checkpoint_artifact = ArtifactRef.from_dict(
        checkpoint.payload["result_checkpoint"]["result_artifact"]
    )
    assert checkpoint.resource_refs == (checkpoint_artifact.ref,)
    assert json.loads(
        child_bundle.artifacts.read_full(
            checkpoint_artifact,
            remaining_budget_bytes=checkpoint_artifact.byte_length,
        )
    ) == _large_result().to_dict()
    assert first.envelope.artifact_refs == ()
    content = parent_bundle.artifacts.read_full(
        first.full_output_artifact,
        remaining_budget_bytes=first.full_output_artifact.byte_length,
    )
    assert json.loads(content) == _large_result().to_dict()


def test_nested_child_events_do_not_break_outer_child_completion_checkpoint() -> None:
    runtime, parent_context, bundles = _runtime_fixture(nested_child=True)
    sink = runtime.prepare_subagent_completion_sink(
        parent_context,
        call_id="call-delegate",
    )

    first = sink.record(child_run_id="child-run", result=_large_result())
    replay = sink.record(child_run_id="child-run", result=_large_result())

    child_bundle = bundles[("child-execution", "child-run")]
    checkpoint_cursor = first.envelope.source_event_range.start
    checkpoint = next(
        event
        for event in child_bundle.journal.events
        if event.store_seq == checkpoint_cursor.store_seq
    )
    assert checkpoint.event_id == checkpoint_cursor.event_id
    assert checkpoint.attempt == child_bundle.attempt
    assert checkpoint.event_type == "subagent_completed"
    assert checkpoint.payload["result_checkpoint"]["schema"] == (
        "unchain.subagent_result_checkpoint.v1"
    )
    assert first.envelope.source_event_range.end == checkpoint_cursor
    assert replay.duplicate is True
    assert replay.envelope == first.envelope
    assert any(
        event.attempt.attempt_id == "nested-child-run"
        for event in child_bundle.journal.events
    )


class _RuntimeBoundCompletedChild:
    name = "parent.researcher.1"

    def __init__(self, runtime: ContextRuntime) -> None:
        self.runtime = runtime
        self.calls = 0

    def run(
        self,
        _messages,
        *,
        session_id,
        run_id,
        _execution_guard=None,
        **_kwargs,
    ) -> KernelRunResult:
        self.calls += 1
        state = RunState()
        state.session_state.session_id = session_id
        bootstrap = HarnessContext(
            state=state,
            phase="bootstrap",
            event={
                "run_id": run_id,
                "execution_guard": _execution_guard,
            },
        )
        self.runtime.build_harnesses()[0].build_delta(bootstrap)
        self.runtime.persist_event(
            {"type": "run_started", "run_id": run_id, "iteration": 0}
        )
        self.runtime.persist_event(
            {
                "type": "final_message",
                "run_id": run_id,
                "iteration": 1,
                "status": "completed",
            }
        )
        return KernelRunResult(
            messages=[{"role": "assistant", "content": "x" * 24_000}],
            status="completed",
        )


def test_official_delegate_route_records_handoff_before_parent_tool_result() -> None:
    bundles = {}
    journal = _Journal("parent-execution")

    def build(attempt):
        bundle = _bundle(attempt, journal=journal)
        bundles[(attempt.generation.execution_id, attempt.attempt_id)] = bundle
        return bundle

    runtime = ContextRuntime.from_factory(
        owner_id="context-v2-production-handoff",
        execution_factory=DurableContextRuntimeFactory(
            bundle_builder=build,
            generation_resolver=lambda context, execution_id: (
                f"generation-{execution_id}"
            ),
            current_input_resolver=_current_input,
        ),
    )
    guard = ExecutionRuntime(InMemorySessionStore()).acquire(
        "parent-execution",
        "parent-owner",
    )
    parent_bootstrap = _context(
        session_id="parent-execution",
        run_id="parent-run",
        current_input=None,
    )
    parent_bootstrap.event["execution_guard"] = guard
    runtime.build_harnesses()[0].build_delta(parent_bootstrap)
    parent_bundle = bundles[("parent-execution", "parent-run")]

    toolkit = Toolkit()

    @toolkit.tool(name="delegate_to_subagent")
    def legacy_delegate(target: str, task: str):
        raise AssertionError((target, task))

    parent = Agent(
        name="parent",
        provider="openai",
        modules=(ContextModule(runtime=runtime),),
    )
    plugin = SubagentToolPlugin(
        parent_agent=parent,
        templates=(),
        policy=SubagentPolicy(allow_dynamic_delegate=True),
        executor=SubagentExecutor(),
    )
    child = _RuntimeBoundCompletedChild(runtime)
    plugin._build_subagent = lambda **_kwargs: (child, "ephemeral", None)
    arguments = {"target": "researcher", "task": "investigate"}
    call = ToolCall(
        call_id="call-delegate",
        name="delegate_to_subagent",
        arguments=arguments,
    )
    parent_bundle.durable_event_sink(
        {
            "type": "tool_call",
            "run_id": "parent-run",
            "iteration": 0,
            "tool_name": call.name,
            "call_id": call.call_id,
            "arguments": arguments,
            "source_provider": "openai",
        }
    )
    parent_bootstrap.state.provider_state.provider = "openai"
    context = HarnessContext(
        state=parent_bootstrap.state,
        phase="on_tool_call",
        event={
            "run_id": "parent-run",
            "execution_guard": guard,
            "toolkit": toolkit,
            "tool_call": call,
            "tool_runtime_plugins": [plugin],
            "max_iterations": 2,
        },
    )
    try:
        delta = runtime.build_harnesses()[1].build_delta(context)

        event_types = [event.event_type for event in parent_bundle.journal.events]
        parent_handoffs = [
            event
            for event in parent_bundle.journal.events
            if event.event_type == "handoff.recorded"
            and event.attempt == parent_bundle.attempt
        ]
        assert child.calls == 1
        assert delta is not None
        assert len(parent_handoffs) == 1
        assert event_types.count("handoff.recorded") == 2
        assert event_types.count("tool_result") == 1
        assert parent_handoffs[0].store_seq < next(
            event.store_seq
            for event in parent_bundle.journal.events
            if event.event_type == "tool_result"
        )

        recovered_runtime = ContextRuntime.from_factory(
            owner_id="context-v2-production-handoff-recovered",
            execution_factory=DurableContextRuntimeFactory(
                bundle_builder=lambda attempt: parent_bundle,
                generation_resolver=lambda context, execution_id: (
                    f"generation-{execution_id}"
                ),
                current_input_resolver=_current_input,
            ),
        )
        recovered_bootstrap = _context(
            session_id="parent-execution",
            run_id="parent-run",
            current_input=None,
        )
        recovered_bootstrap.event["execution_guard"] = guard
        recovered_runtime.build_harnesses()[0].build_delta(recovered_bootstrap)
        recovered_bootstrap.state.provider_state.provider = "openai"
        recovered_context = HarnessContext(
            state=recovered_bootstrap.state,
            phase="on_tool_call",
            event={
                **context.event,
                "execution_guard": guard,
            },
        )

        replay_delta = recovered_runtime.build_harnesses()[1].build_delta(
            recovered_context
        )

        replay_events = parent_bundle.journal.events
        replay_types = [event.event_type for event in replay_events]
        assert replay_delta is not None
        assert replay_delta.trace["durable_tool_reused"] is True
        assert child.calls == 1
        assert sum(
            event.event_type == "handoff.recorded"
            and event.attempt == parent_bundle.attempt
            for event in replay_events
        ) == 1
        assert replay_types.count("handoff.recorded") == 2
        assert replay_types.count("tool_result") == 1
    finally:
        guard.release()


class _DurableInputInspectingChild:
    name = "parent.researcher.1"

    def __init__(self, runtime: ContextRuntime, bundles, execution_id: str) -> None:
        self.runtime = runtime
        self.bundles = bundles
        self.execution_id = execution_id
        self.calls = 0
        self.requests = []

    def run(self, _messages, *, session_id, run_id, **_kwargs) -> KernelRunResult:
        self.calls += 1
        assert session_id == self.execution_id
        state = RunState()
        state.session_state.session_id = session_id
        state.provider_state.provider = "openai"
        state.provider_state.model = "gpt-durable-child"
        state.provider_state.max_context_window_tokens = 16_384
        bootstrap = HarnessContext(
            state=state,
            phase="bootstrap",
            event={"run_id": run_id},
        )
        self.runtime.build_harnesses()[0].build_delta(bootstrap)
        request = self.bundles[(session_id, run_id)].request_factory(
            HarnessContext(
                state=state,
                phase="before_model",
                event={"run_id": run_id, "toolkit": Toolkit()},
            )
        )
        self.requests.append(request)
        self.runtime.persist_event(
            {"type": "run_started", "run_id": run_id, "iteration": 0}
        )
        self.runtime.persist_event(
            {
                "type": "final_message",
                "run_id": run_id,
                "iteration": 1,
                "status": "completed",
            }
        )
        return KernelRunResult(
            messages=[{"role": "assistant", "content": "durable child complete"}],
            status="completed",
        )


@pytest.mark.parametrize("template_name", [None, "research-template"])
def test_dynamic_and_template_child_use_exact_durable_input_and_restart_replay(
    template_name,
) -> None:
    execution_id = f"durable-child-{template_name or 'dynamic'}"
    journal = _Journal(execution_id)
    bundles = {}

    def build(attempt):
        bundle = _bundle(attempt, journal=journal)
        bundles[(attempt.generation.execution_id, attempt.attempt_id)] = bundle
        return bundle

    def resolve_current_input(context, attempt):
        if attempt.attempt_id != "parent-run":
            raise AssertionError("prepared child must bypass free-form input ingress")
        return _current_input(context, attempt)

    runtime = ContextRuntime.from_factory(
        owner_id=f"context-v2-{execution_id}",
        execution_factory=DurableContextRuntimeFactory(
            bundle_builder=build,
            generation_resolver=lambda _context, resolved_execution_id: (
                f"generation-{resolved_execution_id}"
            ),
            current_input_resolver=resolve_current_input,
        ),
    )
    parent = _context(
        session_id=execution_id,
        run_id="parent-run",
        current_input=None,
    )
    runtime.build_harnesses()[0].build_delta(parent)
    parent_bundle = bundles[(execution_id, "parent-run")]
    call_id = "call-durable-child"
    parent_bundle.durable_event_sink(
        {
            "type": "tool_call",
            "run_id": "parent-run",
            "iteration": 0,
            "tool_name": "delegate_to_subagent",
            "call_id": call_id,
            "arguments": {"target": "researcher", "task": "full task"},
            "source_provider": "openai",
        }
    )
    sink = runtime.prepare_subagent_completion_sink(parent, call_id=call_id)
    assert sink is not None
    plugin = _plugin()
    child = _DurableInputInspectingChild(runtime, bundles, execution_id)
    child_run_id = plugin._build_child_run_id(
        session_id=execution_id,
        child_id=child.name,
        parent_run_id="parent-run",
        call_id=call_id,
    )
    assert child_run_id == plugin._build_child_run_id(
        session_id=execution_id,
        child_id=child.name,
        parent_run_id="parent-run",
        call_id=call_id,
    )
    assert child_run_id != plugin._build_child_run_id(
        session_id=execution_id,
        child_id=child.name,
        parent_run_id="parent-run",
        call_id="different-call",
    )
    task = "early constraint: preserve this exactly\n" + ("x" * 24_000)
    run_kwargs = {
        "agent": child,
        "mode": "delegate",
        "child_id": child.name,
        "lineage": ["parent", child.name],
        "template_name": template_name,
        "session_id": f"{execution_id}:{child.name}",
        "memory_namespace": f"{execution_id}:{child.name}",
        "input_messages": task,
        "max_iterations": 2,
        "child_run_id": child_run_id,
        "completion_sink": sink,
    }

    result = plugin._run_child(**run_kwargs)

    assert child.calls == 1
    assert len(child.requests) == 1
    request = child.requests[0]
    derived_message = request.source_messages[-1]
    descriptor = json.loads(derived_message["content"])
    artifact = ArtifactRef.from_dict(descriptor["full_output_artifact"])
    child_bundle = bundles[(execution_id, child_run_id)]
    full_input = json.loads(
        child_bundle.artifacts.read_full(
            artifact,
            remaining_budget_bytes=artifact.byte_length,
        )
    )
    assert descriptor["schema"] == "unchain.derived_handoff_input.v1"
    assert descriptor["source_attempt"] == parent_bundle.attempt.to_dict()
    assert descriptor["consumer_attempt"] == child_bundle.attempt.to_dict()
    assert full_input["schema"] == "unchain.subagent_input.v1"
    assert full_input["child_run_id"] == child_run_id
    assert full_input["template_name"] == template_name
    assert full_input["lineage"] == ["parent", child.name]
    assert full_input["input_messages"] == [{"role": "user", "content": task}]
    first_event_count = len(journal.events)

    same_process = plugin._run_child(**run_kwargs)

    assert same_process == result
    assert child.calls == 1
    assert len(journal.events) == first_event_count

    restarted = ContextRuntime.from_factory(
        owner_id=f"context-v2-{execution_id}-restarted",
        execution_factory=DurableContextRuntimeFactory(
            bundle_builder=lambda _attempt: parent_bundle,
            generation_resolver=lambda _context, resolved_execution_id: (
                f"generation-{resolved_execution_id}"
            ),
            current_input_resolver=_current_input,
        ),
    )
    restarted_parent = _context(
        session_id=execution_id,
        run_id="parent-run",
        current_input=None,
    )
    restarted.build_harnesses()[0].build_delta(restarted_parent)
    restarted_sink = restarted.prepare_subagent_completion_sink(
        restarted_parent,
        call_id=call_id,
    )
    assert restarted_sink is not None
    restarted_kwargs = {**run_kwargs, "completion_sink": restarted_sink}

    after_restart = plugin._run_child(**restarted_kwargs)

    assert after_restart == result
    assert child.calls == 1
    assert len(journal.events) == first_event_count
    with pytest.raises(Exception, match="conflict") as changed:
        plugin._run_child(
            **{
                **restarted_kwargs,
                "input_messages": "changed task must not reuse the child identity",
            }
        )
    assert is_durable_persistence_failure(changed.value)
    assert child.calls == 1


class _RuntimeBoundHandoffChild:
    name = "parent.researcher.1"

    def __init__(self, runtime: ContextRuntime, *, behavior: str) -> None:
        self.runtime = runtime
        self.behavior = behavior
        self.calls = 0

    def run(
        self,
        _messages,
        *,
        session_id,
        run_id,
        _execution_guard=None,
        **_kwargs,
    ) -> KernelRunResult:
        self.calls += 1
        state = RunState()
        state.session_state.session_id = session_id
        bootstrap = HarnessContext(
            state=state,
            phase="bootstrap",
            event={
                "run_id": run_id,
                "execution_guard": _execution_guard,
            },
        )
        self.runtime.build_harnesses()[0].build_delta(bootstrap)
        self.runtime.persist_event(
            {"type": "run_started", "run_id": run_id, "iteration": 0}
        )
        if self.behavior == "failure":
            raise RuntimeError("child failed")
        if self.behavior == "clarification":
            return KernelRunResult(
                messages=[],
                status="awaiting_human_input",
                human_input_request={
                    "request_id": "clarification-1",
                    "question": "Which environment?",
                },
            )
        if self.behavior in {
            "max_iterations",
            "awaiting_interaction",
            "status_failure",
        }:
            status = (
                "failed" if self.behavior == "status_failure" else self.behavior
            )
            return KernelRunResult(
                messages=[
                    {
                        "role": "assistant",
                        "content": f"child stopped with {status}",
                    }
                ],
                status=status,
            )
        self.runtime.persist_event(
            {
                "type": "final_message",
                "run_id": run_id,
                "iteration": 1,
                "status": "completed",
            }
        )
        return KernelRunResult(
            messages=[{"role": "assistant", "content": "handoff complete"}],
            status="completed",
        )


@pytest.mark.parametrize(
    ("behavior", "expected_transition", "expected_status", "handoff_count"),
    [
        ("success", "subagent_terminal_handoff", "completed", 1),
        ("failure", "subagent_snapshot", "running", 0),
        ("clarification", "subagent_snapshot", "running", 1),
        ("max_iterations", "subagent_snapshot", "running", 1),
        ("awaiting_interaction", "subagent_snapshot", "running", 1),
        ("status_failure", "subagent_snapshot", "running", 1),
    ],
)
def test_official_handoff_route_selects_exact_variant_and_cold_replays_without_child_rerun(
    behavior,
    expected_transition,
    expected_status,
    handoff_count,
) -> None:
    bundles = {}
    execution_id = f"parent-execution-{behavior}"
    journal = _Journal(execution_id)

    def build(attempt):
        bundle = _bundle(attempt, journal=journal)
        bundles[(attempt.generation.execution_id, attempt.attempt_id)] = bundle
        return bundle

    runtime = ContextRuntime.from_factory(
        owner_id=f"context-v2-production-handoff-{behavior}",
        execution_factory=DurableContextRuntimeFactory(
            bundle_builder=build,
            generation_resolver=lambda context, execution_id: (
                f"generation-{execution_id}"
            ),
            current_input_resolver=_current_input,
        ),
    )
    guard = ExecutionRuntime(InMemorySessionStore()).acquire(
        f"parent-execution-{behavior}",
        f"parent-owner-{behavior}",
    )
    call = ToolCall(
        call_id=f"call-handoff-{behavior}",
        name="handoff_to_subagent",
        arguments={"target": "researcher", "reason": "take over"},
    )
    parent_bootstrap = _context(
        session_id=f"parent-execution-{behavior}",
        run_id="parent-run",
        current_input=None,
    )
    parent_bootstrap.state.run_status = "running"
    parent_bootstrap.state.pending_tool_calls = [call]
    parent_bootstrap.state.provider_state.provider = "openai"
    parent_bootstrap.event["execution_guard"] = guard
    runtime.build_harnesses()[0].build_delta(parent_bootstrap)
    parent_bundle = bundles[(f"parent-execution-{behavior}", "parent-run")]

    toolkit = Toolkit()

    @toolkit.tool(name="handoff_to_subagent")
    def legacy_handoff(target: str, reason: str = ""):
        raise AssertionError((target, reason))

    parent = Agent(
        name="parent",
        provider="openai",
        modules=(ContextModule(runtime=runtime),),
    )
    plugin = SubagentToolPlugin(
        parent_agent=parent,
        templates=(),
        policy=SubagentPolicy(handoff_requires_template=False),
        executor=SubagentExecutor(),
    )
    child = _RuntimeBoundHandoffChild(runtime, behavior=behavior)
    plugin._build_subagent = lambda **_kwargs: (child, "ephemeral", None)
    parent_bundle.durable_event_sink(
        {
            "type": "tool_call",
            "run_id": "parent-run",
            "iteration": 0,
            "tool_name": call.name,
            "call_id": call.call_id,
            "arguments": call.arguments,
            "source_provider": "openai",
        }
    )
    context = HarnessContext(
        state=parent_bootstrap.state,
        phase="on_tool_call",
        event={
            "run_id": "parent-run",
            "execution_guard": guard,
            "toolkit": toolkit,
            "tool_call": call,
            "tool_runtime_plugins": [plugin],
            "max_iterations": 2,
        },
    )
    try:
        delta = runtime.build_harnesses()[1].build_delta(context)

        assert delta is not None
        assert child.calls == 1
        if expected_transition == "subagent_terminal_handoff":
            assert set(delta.state_updates) == {
                "subagent_state",
                "transcript",
                "run_status",
                "pending_tool_calls",
                "tool_batch_state",
                "last_continuation",
                "next_model_input",
            }
        else:
            assert set(delta.state_updates) == {
                "subagent_state",
                "tool_batch_state",
            }
        context.state.apply_delta(delta)
        assert context.state.run_status == expected_status

        event_types = [event.event_type for event in parent_bundle.journal.events]
        parent_handoffs = [
            event
            for event in parent_bundle.journal.events
            if event.event_type == "handoff.recorded"
            and event.attempt == parent_bundle.attempt
        ]
        sealed_event = next(
            event
            for event in parent_bundle.journal.events
            if event.event_type == "tool.subagent_completion.sealed"
        )
        assert sealed_event.payload["transition"]["kind"] == expected_transition
        assert len(parent_handoffs) == handoff_count
        assert event_types.count("handoff.recorded") == handoff_count + 1
        assert event_types.count("tool.subagent_completion.sealed") == 1
        assert event_types.count("tool_result") == 1
        assert event_types.index("tool.subagent_completion.sealed") < event_types.index(
            "tool_result"
        )
        if handoff_count:
            assert parent_handoffs[0].store_seq < sealed_event.store_seq

        recovered_runtime = ContextRuntime.from_factory(
            owner_id=f"context-v2-production-handoff-recovered-{behavior}",
            execution_factory=DurableContextRuntimeFactory(
                bundle_builder=lambda attempt: parent_bundle,
                generation_resolver=lambda context, execution_id: (
                    f"generation-{execution_id}"
                ),
                current_input_resolver=_current_input,
            ),
        )
        recovered_bootstrap = _context(
            session_id=f"parent-execution-{behavior}",
            run_id="parent-run",
            current_input=None,
        )
        recovered_bootstrap.state.run_status = "running"
        recovered_bootstrap.state.pending_tool_calls = [call]
        recovered_bootstrap.state.provider_state.provider = "openai"
        recovered_bootstrap.event["execution_guard"] = guard
        recovered_runtime.build_harnesses()[0].build_delta(recovered_bootstrap)
        recovered_context = HarnessContext(
            state=recovered_bootstrap.state,
            phase="on_tool_call",
            event={
                **context.event,
                "execution_guard": guard,
            },
        )

        replay_delta = recovered_runtime.build_harnesses()[1].build_delta(
            recovered_context
        )

        assert replay_delta is not None
        assert replay_delta.trace["durable_tool_reused"] is True
        assert child.calls == 1
        recovered_context.state.apply_delta(replay_delta)
        assert recovered_context.state.run_status == expected_status
        replay_events = parent_bundle.journal.events
        replay_types = [event.event_type for event in replay_events]
        assert sum(
            event.event_type == "handoff.recorded"
            and event.attempt == parent_bundle.attempt
            for event in replay_events
        ) == handoff_count
        assert replay_types.count("handoff.recorded") == handoff_count + 1
        assert replay_types.count("tool.subagent_completion.sealed") == 1
        assert replay_types.count("tool_result") == 1
    finally:
        guard.release()


class _FailingHandoffJournal(_Journal):
    def append(self, *, request):
        if request.event_type == "handoff.recorded":
            raise JournalRepositoryError("handoff append failed")
        return super().append(request=request)


def test_attempt_bound_sink_marks_parent_partial_when_handoff_append_fails() -> None:
    partials = []
    journal = _FailingHandoffJournal("parent-execution")
    runtime, parent_context, _bundles = _runtime_fixture(
        parent_journal=journal,
        partials=partials,
    )
    sink = runtime.prepare_subagent_completion_sink(
        parent_context,
        call_id="call-delegate",
    )

    with pytest.raises(JournalRepositoryError) as caught:
        sink.record(child_run_id="child-run", result=_large_result())

    assert is_durable_persistence_failure(caught.value)
    assert len(partials) == 1
    assert partials[0][0]["type"] == "subagent.handoff.partial"


def test_nonfactory_runtime_has_no_subagent_completion_sink() -> None:
    runtime = ContextRuntime._for_test(
        owner_id="legacy",
        request_factory=lambda context: None,
        durable_event_sink=lambda event: None,
        partial_attempt_sink=lambda event, error: None,
    )
    state = RunState()
    state.session_state.session_id = "legacy-session"
    context = HarnessContext(
        state=state,
        phase="on_tool_call",
        event={"run_id": "legacy-run"},
    )

    assert (
        runtime.prepare_subagent_completion_sink(context, call_id="call-legacy") is None
    )
