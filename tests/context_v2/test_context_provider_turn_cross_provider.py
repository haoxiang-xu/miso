from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from unchain.context.artifacts import ArtifactService
from unchain.context.coordinator import ContextCompileCoordinator
from unchain.context.factory import ContextExecutionBundle, DurableContextRuntimeFactory
from unchain.context.handoff import DurableHandoffRecorder, HandoffService
from unchain.context.ingress import ContextInputIngress, HostResolvedCurrentInput
from unchain.context.ports import ContextBuildReceipt
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.context.provider_execution import ContextProviderTurnExecutionService
from unchain.context.request_factory import JournalContextRequestFactory
from unchain.context.runtime import ContextRuntime
from unchain.context.tool_boundary import DurableToolBoundary
from unchain.journal import ArtifactRef, DurableEventSink
from unchain.kernel.loop import KernelLoop
from unchain.persistence import SQLiteContextV2Store
from unchain.providers import AnthropicModelIO, HyperspaceModelIO, OllamaModelIO
from unchain.providers.durable_turn_runtime import DurableProviderTurnMode
from unchain.tools import Toolkit


TARGET_SHA256 = "d" * 64


class _CheckpointRepository:
    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    def prepare(self, **kwargs):
        raise AssertionError(kwargs)

    def commit(self, **kwargs):
        raise AssertionError(kwargs)

    def get_by_operation(self, **_kwargs):
        return None


class _BuildRepository:
    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self._operations = {}
        self._triggers = {}

    def record(self, *, envelope, operation, trigger_cursor):
        receipt = ContextBuildReceipt(
            envelope=envelope,
            operation=operation,
            trigger_cursor=trigger_cursor,
        )
        self._operations[operation.operation_id] = receipt
        self._triggers[trigger_cursor] = receipt
        return receipt

    def get_by_operation(self, *, operation):
        return self._operations.get(operation.operation_id)

    def get_by_trigger(self, *, trigger_cursor):
        return self._triggers.get(trigger_cursor)


def _current_input(context, attempt):
    users = [
        message
        for message in context.latest_messages()
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    if not users:
        return None
    return HostResolvedCurrentInput(
        attempt=attempt,
        content=str(users[-1].get("content") or ""),
    )


def _runtime(tmp_path):
    store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )

    def build(attempt):
        repository = store.bind_execution(attempt.generation.execution_id)
        artifacts = ArtifactService(
            repository,
            sanitizer=lambda content, media_type: content,
        )
        projector = CanonicalSemanticEventProjector(
            attempt=attempt,
            artifacts=artifacts,
            payload_sanitizer=lambda event_type, payload: payload,
        )
        sink = DurableEventSink(repository, attempt, projector)
        handoffs = HandoffService(artifacts)
        return ContextExecutionBundle(
            attempt=attempt,
            journal=repository,
            projector=projector,
            durable_event_sink=sink,
            coordinator=ContextCompileCoordinator(
                journal=repository,
                checkpoint_repository=_CheckpointRepository(
                    attempt.generation.execution_id
                ),
                build_repository=_BuildRepository(attempt.generation.execution_id),
                partial_attempt_sink=lambda request, error: None,
            ),
            artifacts=artifacts,
            handoffs=handoffs,
            ingress=ContextInputIngress(
                attempt=attempt,
                projector=projector,
                sink=sink,
            ),
            request_factory=JournalContextRequestFactory(
                attempt=attempt,
                journal=repository,
                model_window_fallback=lambda provider, model: 16_384,
            ),
            tool_boundary=DurableToolBoundary(
                attempt=attempt,
                projector=projector,
                sink=sink,
            ),
            handoff_recorder=DurableHandoffRecorder(
                attempt=attempt,
                handoffs=handoffs,
                projector=projector,
                sink=sink,
            ),
            partial_attempt_sink=lambda event, error: None,
            provider_turn_service=ContextProviderTurnExecutionService(
                attempt=attempt,
                store=repository,
                mode=DurableProviderTurnMode.ENFORCE_TEST,
                transport_target_sha256=TARGET_SHA256,
                sleep=lambda _seconds: None,
            ),
        )

    return ContextRuntime.from_factory(
        owner_id="context-v2-cross-provider-turn",
        execution_factory=DurableContextRuntimeFactory(
            bundle_builder=build,
            generation_resolver=lambda context, execution_id: (
                f"generation-{execution_id}"
            ),
            current_input_resolver=_current_input,
        ),
        provider_turns_enabled=True,
    )


class _AnthropicStream:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        yield SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage={"input_tokens": 3, "output_tokens": 0}),
        )
        yield SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text=self._text),
        )
        yield SimpleNamespace(
            type="message_delta",
            usage={"input_tokens": 3, "output_tokens": 2},
        )


def _anthropic_family_model_io(provider: str, send_calls: list[dict]):
    text = f"exact {provider}"

    class _Messages:
        def stream(self, **kwargs):
            send_calls.append(copy.deepcopy(kwargs))
            return _AnthropicStream(text)

    class _Client:
        messages = _Messages()

    common = {
        "model": f"{provider}-boundary-model",
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
    return model_io, text


def _ollama_model_io(send_calls: list[dict]):
    text = "exact ollama"

    class _Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield json.dumps(
                {
                    "message": {"role": "assistant", "content": text},
                    "prompt_eval_count": 3,
                    "eval_count": 2,
                    "done": True,
                }
            )

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
        return _Response()

    model_io = OllamaModelIO(
        model="ollama-boundary-model",
        base_url="http://ollama.test",
        stream_factory=stream_factory,
        default_payloads={},
        model_capabilities={},
    )
    model_io.fetch_turn = lambda request: (_ for _ in ()).throw(
        AssertionError("legacy provider path was called")
    )
    return model_io, text


def _provider_model_io(provider: str, send_calls: list[dict]):
    if provider in {"anthropic", "hyperspace"}:
        return _anthropic_family_model_io(provider, send_calls)
    return _ollama_model_io(send_calls)


@pytest.mark.parametrize("provider", ["anthropic", "hyperspace", "ollama"])
def test_enabled_context_runtime_owns_empty_tool_provider_turn_for_non_openai_providers(
    tmp_path,
    provider,
):
    runtime = _runtime(tmp_path)
    send_calls: list[dict] = []
    model_io, expected_text = _provider_model_io(provider, send_calls)
    execution_id = f"execution-boundary-{provider}"
    attempt_id = f"attempt-boundary-{provider}"
    loop = KernelLoop(
        model_io=model_io,
        harnesses=list(runtime.build_harnesses()),
    )

    result = loop.run(
        messages=[{"role": "user", "content": f"use durable {provider}"}],
        callback=runtime.compose_event_callback(None),
        session_id=execution_id,
        provider=provider,
        model=model_io.model,
        toolkit=Toolkit(),
        run_id=attempt_id,
        max_iterations=1,
    )

    assert result.messages[-1]["content"] == expected_text
    assert len(send_calls) == 1
    sent_request = send_calls[0]
    if provider == "ollama":
        assert sent_request["method"] == "POST"
        assert sent_request["url"] == "http://ollama.test/api/chat"
        sent_request = sent_request["json"]
    assert sent_request["model"] == model_io.model
    assert "tools" not in sent_request

    reopened_store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )
    reopened = reopened_store.bind_execution(execution_id)
    events = reopened.capture_snapshot().events
    wire_events = [
        event for event in events if event.event_type == "provider.wire_snapshot"
    ]
    result_events = [
        event for event in events if event.event_type == "provider.turn_result"
    ]

    assert len(wire_events) == 1
    assert len(result_events) == 1
    assert wire_events[0].payload["provider"] == provider
    wire_artifact = ArtifactRef.from_dict(wire_events[0].payload["wire_artifact"])
    result_artifact = ArtifactRef.from_dict(result_events[0].payload["result_artifact"])
    wire_payload = json.loads(
        reopened.read_provider_wire_full_verified(artifact=wire_artifact)
    )
    result_payload = json.loads(
        reopened.read_provider_turn_result_full_verified(artifact=result_artifact)
    )
    assert wire_payload["provider"] == provider
    assert result_payload["result"]["final_text"] == expected_text
