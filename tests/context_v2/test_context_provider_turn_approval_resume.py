from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from tests.context_v2.test_context_provider_turn_boundary import _runtime
from unchain.interaction import (
    INTERACTION_JOURNAL_KEY,
    InteractionIntegrityError,
)
from unchain.interaction.durable import validate_interaction_journal
from unchain.memory import InMemorySessionStore, KernelMemoryRuntime
from unchain.execution import ExecutionRuntime
from unchain.providers import OpenAIModelIO
from unchain.runtime import build_runtime_loop
from unchain.tools import Toolkit


def _approval_model_io(send_calls: list[dict]):
    class _Stream:
        def __init__(self, send_number: int) -> None:
            self._send_number = send_number

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            if self._send_number == 1:
                output = [
                    {
                        "type": "function_call",
                        "call_id": "call-approved-write",
                        "name": "approved_write",
                        "arguments": '{"value":"durable"}',
                    }
                ]
            else:
                output = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "approval complete"}
                        ],
                    }
                ]
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id=f"response-approval-{self._send_number}",
                    output=output,
                    usage={
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "total_tokens": 5,
                    },
                ),
            )

    class _Responses:
        def create(self, **kwargs):
            send_calls.append(copy.deepcopy(kwargs))
            return _Stream(len(send_calls))

    class _Client:
        responses = _Responses()

    model_io = OpenAIModelIO(
        model="gpt-boundary",
        api_key="test-key",
        client_factory=lambda **_kwargs: _Client(),
        default_payloads={},
        model_capabilities={},
    )
    model_io.fetch_turn = lambda request: (_ for _ in ()).throw(
        AssertionError("legacy provider path was called")
    )
    return model_io


def _approval_toolkit(invocations: list[str]) -> Toolkit:
    toolkit = Toolkit()

    def approved_write(value: str) -> dict[str, str]:
        invocations.append(value)
        return {"written": value}

    toolkit.register(
        approved_write,
        name="approved_write",
        requires_confirmation=True,
    )
    return toolkit


def _sequential_approval_model_io(send_calls: list[dict]):
    class _Stream:
        def __init__(self, send_number: int) -> None:
            self._send_number = send_number

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            if self._send_number in {1, 2}:
                output = [
                    {
                        "type": "function_call",
                        "call_id": f"call-approval-{self._send_number}",
                        "name": "approved_write",
                        "arguments": (
                            f'{{"value":"durable-{self._send_number}"}}'
                        ),
                    }
                ]
            else:
                output = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "sequential approvals complete",
                            }
                        ],
                    }
                ]
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id=f"response-sequential-{self._send_number}",
                    output=output,
                    usage={
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "total_tokens": 5,
                    },
                ),
            )

    class _Responses:
        def create(self, **kwargs):
            send_calls.append(copy.deepcopy(kwargs))
            return _Stream(len(send_calls))

    class _Client:
        responses = _Responses()

    model_io = OpenAIModelIO(
        model="gpt-boundary",
        api_key="test-key",
        client_factory=lambda **_kwargs: _Client(),
        default_payloads={},
        model_capabilities={},
    )
    model_io.fetch_turn = lambda request: (_ for _ in ()).throw(
        AssertionError("legacy provider path was called")
    )
    return model_io


def test_official_context_boundary_sync_approval_resume_uses_bound_toolkit(
    tmp_path,
):
    runtime, _bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
    )
    send_calls: list[dict] = []
    invocations: list[str] = []
    session_store = InMemorySessionStore()
    memory_runtime = KernelMemoryRuntime.from_config(store=session_store)
    loop = build_runtime_loop(
        harnesses=list(runtime.build_harnesses()),
        model_io=_approval_model_io(send_calls),
        memory_runtime=memory_runtime,
        semantic_context_owner=runtime.owner_id,
    )

    result = loop.run(
        messages=[{"role": "user", "content": "write after approval"}],
        callback=runtime.compose_event_callback(None),
        session_id="execution-sync-approval",
        provider="openai",
        model="gpt-boundary",
        toolkit=_approval_toolkit(invocations),
        run_id="attempt-sync-approval",
        max_iterations=2,
        on_tool_confirm=lambda _request: {"approved": True},
    )

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "approval complete"
    assert invocations == ["durable"]
    assert len(send_calls) == 2


def test_official_context_boundary_cold_approval_resume_reuses_original_attempt(
    tmp_path,
):
    first_runtime, _first_bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
    )
    send_calls: list[dict] = []
    invocations: list[str] = []
    session_store = InMemorySessionStore()
    first_loop = build_runtime_loop(
        harnesses=list(first_runtime.build_harnesses()),
        model_io=_approval_model_io(send_calls),
        memory_runtime=KernelMemoryRuntime.from_config(store=session_store),
        semantic_context_owner=first_runtime.owner_id,
    )

    suspended = first_loop.run(
        messages=[{"role": "user", "content": "write after cold approval"}],
        callback=first_runtime.compose_event_callback(None),
        session_id="execution-cold-approval",
        provider="openai",
        model="gpt-boundary",
        toolkit=_approval_toolkit(invocations),
        run_id="attempt-cold-approval",
        max_iterations=2,
    )

    assert suspended.status == "awaiting_interaction"
    assert suspended.continuation is not None
    assert suspended.continuation["run_id"] == "attempt-cold-approval"
    assert invocations == []
    assert len(send_calls) == 1

    resumed_runtime, _resumed_bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
    )
    resumed_loop = build_runtime_loop(
        harnesses=list(resumed_runtime.build_harnesses()),
        model_io=_approval_model_io(send_calls),
        memory_runtime=KernelMemoryRuntime.from_config(store=session_store),
        semantic_context_owner=resumed_runtime.owner_id,
    )

    resumed = resumed_loop.resume_interaction(
        session_id="execution-cold-approval",
        response={"approved": True},
        callback=resumed_runtime.compose_event_callback(None),
        toolkit=_approval_toolkit(invocations),
    )

    assert resumed.status == "completed"
    assert resumed.messages[-1]["content"] == "approval complete"
    assert invocations == ["durable"]
    assert len(send_calls) == 2


def test_cold_resume_projects_artifact_only_before_second_provider_turn(tmp_path):
    first_runtime, _first_bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
        tool_output_management_active=True,
    )
    send_calls: list[dict] = []
    invocations: list[str] = []
    session_store = InMemorySessionStore()
    raw_result_marker = "RESUME_RAW_TOOL_RESULT_MUST_NOT_REACH_SECOND_PROVIDER_TURN"

    def toolkit() -> Toolkit:
        tools = Toolkit()

        def approved_write(value: str) -> dict[str, str]:
            invocations.append(value)
            return {"written": value, "status": raw_result_marker}

        tools.register(
            approved_write,
            name="approved_write",
            requires_confirmation=True,
            output_policy="artifact_only",
        )
        return tools

    first_loop = build_runtime_loop(
        harnesses=list(first_runtime.build_harnesses()),
        model_io=_approval_model_io(send_calls),
        memory_runtime=KernelMemoryRuntime.from_config(store=session_store),
        execution_runtime=ExecutionRuntime(session_store),
        semantic_context_owner=first_runtime.owner_id,
    )
    suspended = first_loop.run(
        messages=[{"role": "user", "content": "write after cold approval"}],
        callback=first_runtime.compose_event_callback(None),
        session_id="execution-output-cold-resume",
        provider="openai",
        model="gpt-boundary",
        toolkit=toolkit(),
        run_id="attempt-output-cold-resume",
        max_iterations=2,
    )

    assert suspended.status == "awaiting_interaction"
    assert len(send_calls) == 1
    resumed_runtime, _resumed_bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
        tool_output_management_active=True,
    )
    resumed_loop = build_runtime_loop(
        harnesses=list(resumed_runtime.build_harnesses()),
        model_io=_approval_model_io(send_calls),
        memory_runtime=KernelMemoryRuntime.from_config(store=session_store),
        execution_runtime=ExecutionRuntime(session_store),
        semantic_context_owner=resumed_runtime.owner_id,
    )

    resumed = resumed_loop.resume_interaction(
        session_id="execution-output-cold-resume",
        response={"approved": True},
        callback=resumed_runtime.compose_event_callback(None),
        toolkit=toolkit(),
    )

    assert resumed.status == "completed"
    assert invocations == ["durable"]
    assert len(send_calls) == 2
    assert raw_result_marker not in repr(send_calls[1])
    assert "artifact_only" in repr(send_calls[1])


def test_official_context_boundary_starts_a_new_approval_after_resume(tmp_path):
    runtime, _bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
    )
    send_calls: list[dict] = []
    invocations: list[str] = []
    session_store = InMemorySessionStore()
    memory_runtime = KernelMemoryRuntime.from_config(store=session_store)
    loop = build_runtime_loop(
        harnesses=list(runtime.build_harnesses()),
        model_io=_sequential_approval_model_io(send_calls),
        memory_runtime=memory_runtime,
        semantic_context_owner=runtime.owner_id,
    )
    session_id = "execution-sequential-approval"

    first_pending = loop.run(
        messages=[{"role": "user", "content": "write twice with approval"}],
        callback=runtime.compose_event_callback(None),
        session_id=session_id,
        provider="openai",
        model="gpt-boundary",
        toolkit=_approval_toolkit(invocations),
        run_id="attempt-sequential-approval",
        max_iterations=3,
    )
    assert first_pending.status == "awaiting_interaction"
    assert first_pending.interaction_request is not None
    first_interaction_id = first_pending.interaction_request["interaction_id"]

    second_pending = loop.resume_interaction(
        session_id=session_id,
        response={
            "approved": False,
            "modified_arguments": None,
            "reason": "deny first",
        },
        callback=runtime.compose_event_callback(None),
        toolkit=_approval_toolkit(invocations),
    )

    assert second_pending.status == "awaiting_interaction"
    assert second_pending.interaction_request is not None
    second_interaction_id = second_pending.interaction_request["interaction_id"]
    assert second_interaction_id != first_interaction_id
    assert second_pending.interaction_request["payload"]["call_id"] == (
        "call-approval-2"
    )
    assert invocations == []
    assert len(send_calls) == 2

    journal = validate_interaction_journal(
        memory_runtime.load_session_state(session_id)[INTERACTION_JOURNAL_KEY]
    )
    assert journal["active_id"] == second_interaction_id
    assert journal["entries"][first_interaction_id]["application"] is not None
    assert journal["entries"][second_interaction_id]["receipt"] is None

    completed = loop.resume_interaction(
        session_id=session_id,
        response={
            "approved": True,
            "modified_arguments": None,
            "reason": "",
        },
        callback=runtime.compose_event_callback(None),
        toolkit=_approval_toolkit(invocations),
    )

    assert completed.status == "completed"
    assert completed.messages[-1]["content"] == "sequential approvals complete"
    assert invocations == ["durable-2"]
    assert len(send_calls) == 3


def test_official_context_boundary_rejects_resume_run_id_drift_before_bootstrap(
    tmp_path,
):
    first_runtime, _first_bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
    )
    send_calls: list[dict] = []
    invocations: list[str] = []
    session_store = InMemorySessionStore()
    first_loop = build_runtime_loop(
        harnesses=list(first_runtime.build_harnesses()),
        model_io=_approval_model_io(send_calls),
        memory_runtime=KernelMemoryRuntime.from_config(store=session_store),
        semantic_context_owner=first_runtime.owner_id,
    )
    suspended = first_loop.run(
        messages=[{"role": "user", "content": "do not cross attempts"}],
        callback=first_runtime.compose_event_callback(None),
        session_id="execution-run-id-drift",
        provider="openai",
        model="gpt-boundary",
        toolkit=_approval_toolkit(invocations),
        run_id="attempt-original",
        max_iterations=2,
    )
    assert suspended.status == "awaiting_interaction"

    resumed_runtime, resumed_bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
    )
    resumed_loop = build_runtime_loop(
        harnesses=list(resumed_runtime.build_harnesses()),
        model_io=_approval_model_io(send_calls),
        memory_runtime=KernelMemoryRuntime.from_config(store=session_store),
        semantic_context_owner=resumed_runtime.owner_id,
    )

    with pytest.raises(InteractionIntegrityError, match="run|attempt|source"):
        resumed_loop.resume_interaction(
            session_id="execution-run-id-drift",
            response={"approved": True},
            callback=resumed_runtime.compose_event_callback(None),
            toolkit=_approval_toolkit(invocations),
            run_id="attempt-forged",
        )

    assert resumed_bundles == {}
    assert invocations == []
    assert len(send_calls) == 1
