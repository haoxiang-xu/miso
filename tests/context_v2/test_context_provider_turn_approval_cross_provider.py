from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from tests.context_v2.test_context_provider_turn_approval_resume import (
    _approval_toolkit,
)
from tests.context_v2.test_context_provider_turn_cross_provider import _runtime
from unchain.memory import InMemorySessionStore, KernelMemoryRuntime
from unchain.persistence import SQLiteContextV2Store
from unchain.providers import AnthropicModelIO, HyperspaceModelIO, OllamaModelIO
from unchain.runtime import build_runtime_loop


def _anthropic_approval_events(*, send_number: int, call_id: str):
    if send_number == 1:
        content_events = [
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block=SimpleNamespace(
                    type="tool_use",
                    id=call_id,
                    name="approved_write",
                    input={},
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(
                    type="input_json_delta",
                    partial_json='{"value":"durable"}',
                ),
            ),
            SimpleNamespace(type="content_block_stop", index=0),
        ]
    else:
        content_events = [
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block=SimpleNamespace(type="text", text=""),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(
                    type="text_delta",
                    text="approval complete",
                ),
            ),
            SimpleNamespace(type="content_block_stop", index=0),
        ]
    return [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage={"input_tokens": 3, "output_tokens": 0}
            ),
        ),
        *content_events,
        SimpleNamespace(
            type="message_delta",
            usage={"input_tokens": 3, "output_tokens": 2},
        ),
    ]


class _AnthropicApprovalStream:
    def __init__(self, events) -> None:
        self._events = list(events)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._events)


def _anthropic_family_approval_model_io(
    provider: str,
    send_calls: list[dict],
):
    call_id = f"call-{provider}-approved-write"

    class _Messages:
        def stream(self, **kwargs):
            send_calls.append(copy.deepcopy(kwargs))
            return _AnthropicApprovalStream(
                _anthropic_approval_events(
                    send_number=len(send_calls),
                    call_id=call_id,
                )
            )

    class _Client:
        messages = _Messages()

    common = {
        "model": f"{provider}-approval-model",
        "api_key": "test-key",
        "client_factory": lambda **_kwargs: _Client(),
        "default_payloads": {},
        "model_capabilities": {},
    }
    if provider == "anthropic":
        model_io = AnthropicModelIO(**common)
    else:
        model_io = HyperspaceModelIO(**common)
    model_io.fetch_turn = lambda request: (_ for _ in ()).throw(
        AssertionError("legacy provider path was called")
    )
    return model_io, call_id


def _ollama_approval_model_io(send_calls: list[dict]):
    call_id = "call-ollama-approved-write"

    class _Response:
        status_code = 200

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield json.dumps(self._payload)

        def read(self):
            return b""

    def stream_factory(method, url, **kwargs):
        send_calls.append(
            {
                "method": method,
                "url": url,
                **copy.deepcopy(kwargs),
            }
        )
        if len(send_calls) == 1:
            payload = {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "function": {
                                "name": "approved_write",
                                "arguments": {"value": "durable"},
                            },
                        }
                    ],
                },
                "done": False,
                "prompt_eval_count": 3,
                "eval_count": 2,
            }
        else:
            payload = {
                "message": {
                    "role": "assistant",
                    "content": "approval complete",
                },
                "done": True,
                "prompt_eval_count": 3,
                "eval_count": 2,
            }
        return _Response(payload)

    model_io = OllamaModelIO(
        model="ollama-approval-model",
        base_url="http://ollama.test",
        stream_factory=stream_factory,
        default_payloads={},
        model_capabilities={},
    )
    model_io.fetch_turn = lambda request: (_ for _ in ()).throw(
        AssertionError("legacy provider path was called")
    )
    return model_io, call_id


def _approval_model_io(provider: str, send_calls: list[dict]):
    if provider in {"anthropic", "hyperspace"}:
        return _anthropic_family_approval_model_io(provider, send_calls)
    return _ollama_approval_model_io(send_calls)


def _assert_native_tool_history(
    provider: str,
    sent_request: dict,
    *,
    call_id: str,
) -> None:
    if provider == "ollama":
        assert sent_request["method"] == "POST"
        assert sent_request["url"] == "http://ollama.test/api/chat"
        messages = sent_request["json"]["messages"]
        tool_use_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("role") == "assistant" and message.get("tool_calls")
        )
        tool_result_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("role") == "tool"
            and message.get("tool_call_id") == call_id
        )
        tool_use = messages[tool_use_index]["tool_calls"][0]
        assert tool_use["id"] == call_id
        assert tool_use["function"] == {
            "name": "approved_write",
            "arguments": {"value": "durable"},
        }
        assert json.loads(messages[tool_result_index]["content"]) == {
            "written": "durable"
        }
    else:
        messages = sent_request["messages"]
        tool_use_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("role") == "assistant"
            and isinstance(message.get("content"), list)
            and any(
                block.get("type") == "tool_use"
                for block in message["content"]
            )
        )
        tool_result_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user"
            and isinstance(message.get("content"), list)
            and any(
                block.get("type") == "tool_result"
                for block in message["content"]
            )
        )
        tool_use = next(
            block
            for block in messages[tool_use_index]["content"]
            if block.get("type") == "tool_use"
        )
        tool_result = next(
            block
            for block in messages[tool_result_index]["content"]
            if block.get("type") == "tool_result"
        )
        assert tool_use == {
            "type": "tool_use",
            "id": call_id,
            "name": "approved_write",
            "input": {"value": "durable"},
        }
        assert tool_result["tool_use_id"] == call_id
        assert json.loads(tool_result["content"]) == {"written": "durable"}
    assert tool_result_index == tool_use_index + 1


@pytest.mark.parametrize("provider", ["anthropic", "hyperspace", "ollama"])
def test_official_context_boundary_cold_approval_resume_uses_native_tool_history(
    tmp_path,
    provider,
):
    send_calls: list[dict] = []
    invocations: list[str] = []
    session_store = InMemorySessionStore()
    execution_id = f"execution-cold-approval-{provider}"
    attempt_id = f"attempt-cold-approval-{provider}"

    first_runtime = _runtime(tmp_path)
    first_model_io, call_id = _approval_model_io(provider, send_calls)
    first_loop = build_runtime_loop(
        harnesses=list(first_runtime.build_harnesses()),
        model_io=first_model_io,
        memory_runtime=KernelMemoryRuntime.from_config(store=session_store),
        semantic_context_owner=first_runtime.owner_id,
    )
    suspended = first_loop.run(
        messages=[
            {
                "role": "user",
                "content": f"write after cold {provider} approval",
            }
        ],
        callback=first_runtime.compose_event_callback(None),
        session_id=execution_id,
        provider=provider,
        model=first_model_io.model,
        toolkit=_approval_toolkit(invocations),
        run_id=attempt_id,
        max_iterations=2,
    )

    assert suspended.status == "awaiting_interaction"
    assert suspended.continuation is not None
    assert suspended.continuation["run_id"] == attempt_id
    assert invocations == []
    assert len(send_calls) == 1

    resumed_runtime = _runtime(tmp_path)
    resumed_model_io, resumed_call_id = _approval_model_io(
        provider,
        send_calls,
    )
    assert resumed_call_id == call_id
    resumed_loop = build_runtime_loop(
        harnesses=list(resumed_runtime.build_harnesses()),
        model_io=resumed_model_io,
        memory_runtime=KernelMemoryRuntime.from_config(store=session_store),
        semantic_context_owner=resumed_runtime.owner_id,
    )
    resumed = resumed_loop.resume_interaction(
        session_id=execution_id,
        response={"approved": True},
        callback=resumed_runtime.compose_event_callback(None),
        toolkit=_approval_toolkit(invocations),
    )

    assert resumed.status == "completed"
    assert resumed.messages[-1]["content"] == "approval complete"
    assert invocations == ["durable"]
    assert len(send_calls) == 2
    _assert_native_tool_history(
        provider,
        send_calls[1],
        call_id=call_id,
    )

    reopened = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    ).bind_execution(execution_id)
    assert {
        event.attempt.attempt_id for event in reopened.capture_snapshot().events
    } == {attempt_id}
