from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from tests.context_v2.test_context_provider_turn_boundary import _runtime
from unchain.interaction import InteractionIntegrityError
from unchain.memory import InMemorySessionStore, KernelMemoryRuntime
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
