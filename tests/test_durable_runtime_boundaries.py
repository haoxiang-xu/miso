from __future__ import annotations

import copy
import threading
import traceback
from types import SimpleNamespace

import httpx
import pytest

from unchain.agent import Agent, SubagentModule
from unchain.durability import mark_durable_persistence_failure
from unchain.execution import ExecutionLeaseConfig, ExecutionRuntime
from unchain.kernel.types import KernelRunResult, ModelTurnResult, ToolCall
from unchain.providers.base import ModelTurnRequest
from unchain.providers.openai import OpenAIModelIO
from unchain.retry.classifier import is_retryable
from unchain.retry.executor import execute_with_retry
from unchain.retry.types import RetryConfig, RetryContext, RetriesExhaustedError
from unchain.subagents.executor import SubagentExecutor
from unchain.subagents.plugin import SubagentToolPlugin
from unchain.subagents.types import (
    SubagentPolicy,
    SubagentResult,
    SubagentState,
    SubagentTemplate,
)
from unchain.tools import Toolkit
from unchain.memory import InMemorySessionStore


class _NoStringDurableError(RuntimeError):
    def __str__(self) -> str:
        raise AssertionError("durable failures must be classified before stringification")

    def __repr__(self) -> str:
        raise AssertionError("durable failures must be classified before repr")


def _marked_no_string_error() -> _NoStringDurableError:
    failure = _NoStringDurableError("secret provider payload")
    assert mark_durable_persistence_failure(failure) is failure
    return failure


def _plugin(*, executor: SubagentExecutor | None = None) -> SubagentToolPlugin:
    return SubagentToolPlugin(
        parent_agent=Agent(name="parent", provider="openai"),
        templates=(),
        policy=SubagentPolicy(),
        executor=executor or SubagentExecutor(),
    )


def _subagent_context(*, subagent_state: SubagentState | None = None):
    return SimpleNamespace(
        state=SimpleNamespace(subagent_state=subagent_state),
        session_id="root-session",
        memory_namespace="root-memory",
        run_id="root-run",
        iteration=1,
        event={},
        callback=None,
        loop=None,
        latest_messages=lambda: [{"role": "user", "content": "current task"}],
        execution_guard=None,
    )


def _templated_plugin() -> SubagentToolPlugin:
    child = Agent(name="child", provider="openai")
    return SubagentToolPlugin(
        parent_agent=Agent(name="parent", provider="openai"),
        templates=(
            SubagentTemplate(
                name="child",
                description="test child",
                agent=child,
                allowed_modes=("delegate", "handoff", "worker"),
                parallel_safe=True,
            ),
        ),
        policy=SubagentPolicy(),
        executor=SubagentExecutor(),
    )


def test_retry_classifier_rejects_marked_transient_error_before_normal_rules() -> None:
    failure = httpx.ConnectError("journal transport failed")
    assert mark_durable_persistence_failure(failure) is failure
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise failure

    assert is_retryable(failure) is False
    with pytest.raises(httpx.ConnectError) as caught:
        execute_with_retry(
            operation,
            RetryConfig(max_retries=3, jitter_ratio=0),
            RetryContext(run_id="run-1", iteration=0, is_background=False),
            sleep=lambda _seconds: None,
        )
    assert caught.value is failure
    assert calls == 1


def test_retry_exhaustion_preserves_structure_without_leaking_last_error() -> None:
    secret = "retry-executor-secret"
    failure = httpx.ConnectError(secret)

    with pytest.raises(RetriesExhaustedError) as caught:
        execute_with_retry(
            lambda: (_ for _ in ()).throw(failure),
            RetryConfig(max_retries=0, jitter_ratio=0),
            RetryContext(run_id="run-1", iteration=0, is_background=False),
            sleep=lambda _seconds: None,
        )

    assert caught.value.last_error is failure
    assert caught.value.attempts == 0
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert secret not in "".join(traceback.format_exception(caught.value))


def test_openai_previous_response_fallback_rethrows_durable_failure_without_rendering_it() -> None:
    failure = _marked_no_string_error()
    calls: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    model_io = OpenAIModelIO(
        model="gpt-5",
        api_key="test-key",
        client_factory=lambda **_kwargs: object(),
    )

    def fetch(_client, _request, request_kwargs):
        calls.append(copy.deepcopy(request_kwargs))
        if request_kwargs.get("previous_response_id"):
            raise failure
        return ModelTurnResult(final_text="fallback must not run")

    model_io._fetch_turn_streaming = fetch  # type: ignore[method-assign]

    with pytest.raises(_NoStringDurableError) as caught:
        model_io.fetch_turn(
            ModelTurnRequest(
                messages=[{"role": "user", "content": "continue"}],
                previous_response_id="missing-response",
                fallback_messages=[{"role": "user", "content": "full replay"}],
                callback=events.append,
                toolkit=Toolkit(),
            )
        )

    assert caught.value is failure
    assert len(calls) == 1
    assert not any(
        event.get("type") == "previous_response_id_fallback" for event in events
    )


def test_subagent_outer_backstop_rethrows_durable_failure_and_keeps_ordinary_errors() -> None:
    plugin = _plugin()
    failure = _marked_no_string_error()

    def durable_delegate(**_kwargs):
        raise failure

    plugin._delegate = durable_delegate  # type: ignore[method-assign]
    call = ToolCall(call_id="call-1", name="delegate_to_subagent", arguments={})
    with pytest.raises(_NoStringDurableError) as caught:
        plugin.execute(tool_call=call, context=SimpleNamespace())
    assert caught.value is failure

    ordinary = RuntimeError("ordinary child failure")

    def ordinary_delegate(**_kwargs):
        raise ordinary

    plugin._delegate = ordinary_delegate  # type: ignore[method-assign]
    outcome = plugin.execute(tool_call=call, context=SimpleNamespace())
    assert outcome.tool_result == {
        "error": "ordinary child failure",
        "tool": "delegate_to_subagent",
    }


def test_run_child_rethrows_durable_failure_without_child_error_conversion() -> None:
    plugin = _plugin()
    failure = _marked_no_string_error()

    class _FailingChild:
        name = "failing-child"

        def run(self, _messages, **_kwargs) -> KernelRunResult:
            raise failure

    with pytest.raises(_NoStringDurableError) as caught:
        plugin._run_child(
            agent=_FailingChild(),  # type: ignore[arg-type]
            mode="delegate",
            child_id="failing-child",
            lineage=["parent", "failing-child"],
            template_name=None,
            session_id="root:failing-child",
            memory_namespace="",
            input_messages="work",
            max_iterations=1,
        )
    assert caught.value is failure


@pytest.mark.parametrize(
    ("method_name", "tool_name", "arguments"),
    [
        (
            "_spawn_agent_thread",
            "spawn_agent_thread",
            {"target": "child", "task": "work"},
        ),
        (
            "_return_handoff_to_subagent",
            "return_handoff_to_subagent",
            {"target": "child", "reason": "work"},
        ),
        (
            "_delegate",
            "delegate_to_subagent",
            {"target": "child", "task": "work"},
        ),
        (
            "_handoff",
            "handoff_to_subagent",
            {"target": "child", "reason": "work"},
        ),
    ],
)
def test_each_subagent_child_boundary_bare_raises_durable_failure(
    method_name: str,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    plugin = _templated_plugin()
    failure = _marked_no_string_error()

    def fail_child(**_kwargs):
        raise failure

    plugin._run_child = fail_child  # type: ignore[method-assign]
    method = getattr(plugin, method_name)
    with pytest.raises(_NoStringDurableError) as caught:
        method(
            tool_call=ToolCall(
                call_id=f"{tool_name}-call",
                name=tool_name,
                arguments=arguments,
            ),
            context=_subagent_context(),
        )
    assert caught.value is failure


def test_send_agent_message_boundary_bare_raises_durable_failure() -> None:
    plugin = _templated_plugin()
    failure = _marked_no_string_error()
    state = SubagentState(
        root_agent_id="parent",
        active_agent_id="parent",
        active_lineage=["parent"],
        threads={
            "child-thread": {
                "thread_id": "child-thread",
                "agent_id": "child-agent",
                "parent_agent_id": "parent",
                "target": "child",
                "template_name": "child",
                "mode": "thread",
                "status": "completed",
                "session_id": "root-session:child-agent",
                "memory_namespace": "",
                "lineage": ["parent", "child-agent"],
                "created_iteration": 0,
                "last_activity_iteration": 0,
                "context_mode": "none",
                "instructions": "",
                "expected_output": "",
            }
        },
    )

    def fail_child(**_kwargs):
        raise failure

    plugin._run_child = fail_child  # type: ignore[method-assign]
    with pytest.raises(_NoStringDurableError) as caught:
        plugin._send_agent_message(
            tool_call=ToolCall(
                call_id="message-call",
                name="send_agent_message",
                arguments={
                    "recipient": "child-agent",
                    "thread_id": "child-thread",
                    "content": "continue",
                },
            ),
            context=_subagent_context(subagent_state=state),
        )
    assert caught.value is failure


def test_batch_stop_is_checked_before_model_effect() -> None:
    plugin = _plugin()
    failure_event = threading.Event()
    model_called = False
    callback_events: list[dict[str, object]] = []
    execution_runtime = ExecutionRuntime(
        InMemorySessionStore(),
        ExecutionLeaseConfig(ttl_ms=60_000, heartbeat_interval_ms=0),
    )
    root_guard = execution_runtime.acquire("root-session", owner_id="attempt-1")

    class _ModelIO:
        provider = "openai"

        def fetch_turn(self, _request):
            nonlocal model_called
            model_called = True
            return ModelTurnResult(final_text="must not run")

    class _Child:
        name = "child"

        def run(self, _messages, **kwargs):
            failure_event.set()
            guarded_model = kwargs["_execution_guard"].guard_model_io(
                _ModelIO(),
                "batch-test-model",
            )
            guarded_model.fetch_turn(None)
            kwargs["callback"]({"type": "must_not_emit"})
            return KernelRunResult(
                messages=[{"role": "assistant", "content": "must not return"}],
                status="completed",
            )

    with pytest.raises(RuntimeError, match="subagent_batch_durable_failure"):
        plugin._run_child(
            agent=_Child(),  # type: ignore[arg-type]
            mode="worker",
            child_id="child",
            lineage=["parent", "child"],
            template_name=None,
            session_id="root-session:child",
            memory_namespace="",
            input_messages="work",
            max_iterations=1,
            callback=callback_events.append,
            execution_guard=root_guard,
            batch_failure_event=failure_event,
        )

    assert model_called is False
    assert callback_events == []
    root_guard.assert_active()
    root_guard.release()


def test_batch_stop_is_checked_before_callback_and_after_child_return() -> None:
    plugin = _plugin()
    callback_events: list[dict[str, object]] = []

    callback_failure = threading.Event()

    class _CallbackChild:
        name = "callback-child"

        def run(self, _messages, **kwargs):
            callback_failure.set()
            kwargs["callback"]({"type": "must_not_emit"})
            raise AssertionError("callback guard did not stop the child")

    with pytest.raises(RuntimeError, match="subagent_batch_durable_failure"):
        plugin._run_child(
            agent=_CallbackChild(),  # type: ignore[arg-type]
            mode="worker",
            child_id="callback-child",
            lineage=["parent", "callback-child"],
            template_name=None,
            session_id="root-session:callback-child",
            memory_namespace="",
            input_messages="work",
            max_iterations=1,
            callback=callback_events.append,
            batch_failure_event=callback_failure,
        )
    assert callback_events == []

    return_failure = threading.Event()

    class _ReturningChild:
        name = "returning-child"

        def run(self, _messages, **_kwargs):
            return_failure.set()
            return KernelRunResult(
                messages=[{"role": "assistant", "content": "must not commit"}],
                status="completed",
            )

    with pytest.raises(RuntimeError, match="subagent_batch_durable_failure"):
        plugin._run_child(
            agent=_ReturningChild(),  # type: ignore[arg-type]
            mode="worker",
            child_id="returning-child",
            lineage=["parent", "returning-child"],
            template_name=None,
            session_id="root-session:returning-child",
            memory_namespace="",
            input_messages="work",
            max_iterations=1,
            batch_failure_event=return_failure,
        )


def test_batch_first_durable_failure_cancels_pending_joins_running_and_preserves_identity() -> None:
    executor = SubagentExecutor(max_parallel_workers=2, worker_timeout_seconds=2)
    failure_event = threading.Event()
    second_started = threading.Event()
    sibling_joined = threading.Event()
    first = _marked_no_string_error()
    second = _marked_no_string_error()
    invoked: list[int] = []

    def run_item(index: int, _item: dict) -> SubagentResult:
        invoked.append(index)
        if index == 0:
            assert second_started.wait(1)
            raise first
        if index == 1:
            second_started.set()
            assert failure_event.wait(1)
            sibling_joined.set()
            raise second
        raise AssertionError("pending work ran after the durable batch failure")

    with pytest.raises(_NoStringDurableError) as caught:
        executor.execute_batch(
            items=[{"task": "first"}, {"task": "second"}, {"task": "pending"}],
            run_item=run_item,
            failure_event=failure_event,
        )

    assert caught.value is first
    assert failure_event.is_set()
    assert sibling_joined.is_set()
    assert 2 not in invoked


def test_worker_batch_durable_failure_has_no_join_tool_result_or_kernel_partial() -> None:
    failure = _marked_no_string_error()
    parent_requests: list[ModelTurnRequest] = []
    events: list[dict[str, object]] = []

    class _FailingWorkerIO:
        provider = "openai"

        def fetch_turn(self, _request):
            raise failure

    worker = Agent(
        name="worker",
        provider="openai",
        model_io_factory=lambda _spec, _context: _FailingWorkerIO(),
    )

    class _ParentIO:
        provider = "openai"

        def fetch_turn(self, request):
            parent_requests.append(request)
            if len(parent_requests) > 1:
                raise AssertionError("root model continued after durable failure")
            return ModelTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "type": "function_call",
                        "call_id": "batch-call",
                        "name": "spawn_worker_batch",
                        "arguments": '{"target":"worker","tasks":[{"task":"fail"}]}',
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="batch-call",
                        name="spawn_worker_batch",
                        arguments={
                            "target": "worker",
                            "tasks": [{"task": "fail"}],
                        },
                    )
                ],
                final_text="",
            )

    parent = Agent(
        name="parent",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="worker",
                        description="failing worker",
                        agent=worker,
                        allowed_modes=("worker",),
                        parallel_safe=True,
                    ),
                ),
            ),
        ),
        model_io_factory=lambda _spec, _context: _ParentIO(),
    )

    with pytest.raises(_NoStringDurableError) as caught:
        parent.run("fan out", max_iterations=2, callback=events.append)

    assert caught.value is failure
    assert len(parent_requests) == 1
    assert not any(event.get("type") == "subagent_batch_joined" for event in events)
    assert not any(
        event.get("type") == "tool_result"
        and event.get("call_id") == "batch-call"
        for event in events
    )
