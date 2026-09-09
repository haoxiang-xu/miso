from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.context_v2.test_context_provider_turn_boundary import _runtime
from unchain.interaction import (
    INTERACTION_JOURNAL_KEY,
    InteractionNotPendingError,
)
from unchain.interaction.durable import validate_interaction_journal
from unchain.interaction.runtime import DurableInteractionRuntime
from unchain.kernel.loop import KernelLoop
from unchain.memory import JsonFileSessionStore, KernelMemoryRuntime
from unchain.memory.checkpoint_state import (
    ExecutionCheckpointCompatibilityError,
)
from unchain.providers import OpenAIModelIO
from unchain.runtime import build_runtime_loop
from unchain.tools import Toolkit


def _final_text_model_io(
    send_calls: list[dict],
    *,
    text: str,
    response_id: str,
) -> OpenAIModelIO:
    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id=response_id,
                    output=[
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": text}
                            ],
                        }
                    ],
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
            return _Stream()

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


def _sequential_approval_model_io(
    send_calls: list[dict],
) -> OpenAIModelIO:
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


def _cold_approval_loop(
    root: Path,
    *,
    session_directory: Path,
    send_calls: list[dict],
):
    runtime, _bundles = _runtime(
        root,
        enabled=True,
        with_provider_service=True,
    )
    loop = build_runtime_loop(
        harnesses=list(runtime.build_harnesses()),
        model_io=_sequential_approval_model_io(send_calls),
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(session_directory)
        ),
        semantic_context_owner=runtime.owner_id,
    )
    return runtime, loop


def test_sqlite_reopen_second_chat_projects_real_kernel_terminal_history(
    tmp_path: Path,
) -> None:
    execution_id = "execution-cold-second-chat"
    first_send_calls: list[dict] = []
    first_runtime, first_bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
    )
    first_loop = KernelLoop(
        model_io=_final_text_model_io(
            first_send_calls,
            text="ASSISTANT_ONE",
            response_id="response-chat-one",
        ),
        harnesses=list(first_runtime.build_harnesses()),
    )

    first = first_loop.run(
        messages=[{"role": "user", "content": "USER_ONE"}],
        callback=first_runtime.compose_event_callback(None),
        session_id=execution_id,
        provider="openai",
        model="gpt-boundary",
        toolkit=Toolkit(),
        run_id="attempt-chat-one",
        max_iterations=1,
    )

    assert first.status == "completed"
    assert first_send_calls[0]["input"] == [
        {"content": "USER_ONE", "role": "user"}
    ]
    first_events = first_bundles[
        "attempt-chat-one"
    ].journal.capture_snapshot().events
    terminals = [
        event
        for event in first_events
        if event.attempt.attempt_id == "attempt-chat-one"
        and event.event_type in {"final_message", "run_completed"}
    ]
    assert [event.event_type for event in terminals] == [
        "final_message",
        "run_completed",
    ]
    assert all(event.payload["iteration"] == 0 for event in terminals)
    assert all("step_id" not in event.payload for event in terminals)
    assert all("workflow_node_id" not in event.payload for event in terminals)

    second_send_calls: list[dict] = []
    reopened_runtime, _reopened_bundles = _runtime(
        tmp_path,
        enabled=True,
        with_provider_service=True,
    )
    reopened_loop = KernelLoop(
        model_io=_final_text_model_io(
            second_send_calls,
            text="ASSISTANT_TWO",
            response_id="response-chat-two",
        ),
        harnesses=list(reopened_runtime.build_harnesses()),
    )

    second = reopened_loop.run(
        messages=[{"role": "user", "content": "USER_TWO"}],
        callback=reopened_runtime.compose_event_callback(None),
        session_id=execution_id,
        provider="openai",
        model="gpt-boundary",
        toolkit=Toolkit(),
        run_id="attempt-chat-two",
        max_iterations=1,
    )

    assert second.status == "completed"
    assert second_send_calls[0]["input"] == [
        {"content": "USER_ONE", "role": "user"},
        {"content": "ASSISTANT_ONE", "role": "assistant"},
        {"content": "USER_TWO", "role": "user"},
    ]


@pytest.mark.parametrize("first_approved", [False, True])
def test_file_backed_cold_rebuild_keeps_sequential_approvals_distinct_and_once(
    tmp_path: Path,
    first_approved: bool,
) -> None:
    session_id = f"execution-cold-sequential-{first_approved}"
    session_directory = tmp_path / "sessions"
    send_calls: list[dict] = []
    invocations: list[str] = []

    first_runtime, first_loop = _cold_approval_loop(
        tmp_path,
        session_directory=session_directory,
        send_calls=send_calls,
    )
    first_pending = first_loop.run(
        messages=[
            {"role": "user", "content": "write twice with approval"}
        ],
        callback=first_runtime.compose_event_callback(None),
        session_id=session_id,
        provider="openai",
        model="gpt-boundary",
        toolkit=_approval_toolkit(invocations),
        run_id="attempt-cold-sequential",
        max_iterations=3,
    )

    assert first_pending.status == "awaiting_interaction"
    assert first_pending.interaction_request is not None
    first_interaction_id = first_pending.interaction_request["interaction_id"]
    first_response = {
        "approved": first_approved,
        "modified_arguments": None,
        "reason": "" if first_approved else "deny first",
    }

    second_runtime, second_loop = _cold_approval_loop(
        tmp_path,
        session_directory=session_directory,
        send_calls=send_calls,
    )
    second_pending = second_loop.resume_interaction(
        session_id=session_id,
        response=first_response,
        callback=second_runtime.compose_event_callback(None),
        toolkit=_approval_toolkit(invocations),
    )

    assert second_pending.status == "awaiting_interaction"
    assert second_pending.interaction_request is not None
    second_interaction_id = second_pending.interaction_request["interaction_id"]
    assert second_interaction_id != first_interaction_id
    assert second_pending.interaction_request["payload"]["call_id"] == (
        "call-approval-2"
    )
    assert invocations == (["durable-1"] if first_approved else [])
    assert len(send_calls) == 2

    mid_store = JsonFileSessionStore(session_directory)
    mid_journal = validate_interaction_journal(
        mid_store.load(session_id)[INTERACTION_JOURNAL_KEY]
    )
    assert mid_journal["active_id"] == second_interaction_id
    assert mid_journal["entries"][first_interaction_id]["application"] is not None
    assert mid_journal["entries"][second_interaction_id]["receipt"] is None

    stale_runtime = DurableInteractionRuntime(
        KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(session_directory)
        )
    )
    with pytest.raises(
        ExecutionCheckpointCompatibilityError,
        match="different checkpoint",
    ):
        stale_runtime.record_receipt(
            session_id,
            interaction_id=first_interaction_id,
            response=first_response,
        )
    after_stale = validate_interaction_journal(
        JsonFileSessionStore(session_directory).load(session_id)[
            INTERACTION_JOURNAL_KEY
        ]
    )
    assert after_stale["active_id"] == second_interaction_id
    assert after_stale["entries"][second_interaction_id]["receipt"] is None

    final_runtime, final_loop = _cold_approval_loop(
        tmp_path,
        session_directory=session_directory,
        send_calls=send_calls,
    )
    completed = final_loop.resume_interaction(
        session_id=session_id,
        response={
            "approved": True,
            "modified_arguments": None,
            "reason": "",
        },
        callback=final_runtime.compose_event_callback(None),
        toolkit=_approval_toolkit(invocations),
    )

    assert completed.status == "completed"
    assert completed.messages[-1]["content"] == (
        "sequential approvals complete"
    )
    assert invocations == (
        ["durable-1", "durable-2"]
        if first_approved
        else ["durable-2"]
    )
    assert len(send_calls) == 3

    final_journal = validate_interaction_journal(
        JsonFileSessionStore(session_directory).load(session_id)[
            INTERACTION_JOURNAL_KEY
        ]
    )
    assert final_journal["active_id"] is None
    assert final_journal["entries"][first_interaction_id]["application"] is not None
    assert final_journal["entries"][second_interaction_id]["application"] is not None

    retry_runtime, retry_loop = _cold_approval_loop(
        tmp_path,
        session_directory=session_directory,
        send_calls=send_calls,
    )
    with pytest.raises(InteractionNotPendingError):
        retry_loop.resume_interaction(
            session_id=session_id,
            response={"approved": True},
            callback=retry_runtime.compose_event_callback(None),
            toolkit=_approval_toolkit(invocations),
        )
    assert invocations == (
        ["durable-1", "durable-2"]
        if first_approved
        else ["durable-2"]
    )
    assert len(send_calls) == 3
