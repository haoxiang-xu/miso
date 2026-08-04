from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from unchain.journal import AttemptRef, GenerationRef
from unchain.persistence import SQLiteContextV2Store
from unchain.providers import OpenAIModelIO
from unchain.providers.base import ModelTurnRequest
from unchain.providers.durable_turn_runtime import (
    DurableProviderTurnError,
    DurableProviderTurnMode,
    DurableProviderTurnUncertainError,
)
from unchain.retry import RetryConfig
from unchain.tools import Toolkit


ATTEMPT = AttemptRef(
    GenerationRef("execution-provider-service", "generation-provider-service"),
    "attempt-provider-service",
)
TARGET_SHA256 = "b" * 64


class _OpenAIStream:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        yield SimpleNamespace(type="response.output_text.delta", delta="durable ")
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="response-provider-service",
                output=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "durable result"}],
                    }
                ],
                usage={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
            ),
        )


def _model_io(send_calls: list[dict]):
    class _Responses:
        def create(self, **kwargs):
            send_calls.append(copy.deepcopy(kwargs))
            return _OpenAIStream()

    class _Client:
        responses = _Responses()

    return OpenAIModelIO(
        model="gpt-test",
        api_key="test-key",
        client_factory=lambda **_kwargs: _Client(),
        default_payloads={},
        model_capabilities={},
    )


def _request(events: list[dict] | None = None) -> ModelTurnRequest:
    return ModelTurnRequest(
        messages=[{"role": "user", "content": "execute exactly once"}],
        payload={},
        callback=(events.append if events is not None else None),
        run_id=ATTEMPT.attempt_id,
        iteration=2,
        toolkit=Toolkit(),
        emit_stream=True,
    )


def _repository(tmp_path):
    store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )
    return store.bind_execution(ATTEMPT.generation.execution_id)


def _service(tmp_path, mode):
    from unchain.context.provider_execution import (
        ContextProviderTurnExecutionService,
    )

    return ContextProviderTurnExecutionService(
        attempt=ATTEMPT,
        store=_repository(tmp_path),
        mode=mode,
        transport_target_sha256=TARGET_SHA256,
        sleep=lambda _seconds: None,
    )


def test_off_is_a_read_only_legacy_fallthrough_without_durable_authority(tmp_path):
    send_calls: list[dict] = []
    service = _service(tmp_path, DurableProviderTurnMode.OFF)

    result = service.fetch_prepared(
        model_io=_model_io(send_calls),
        request=_request(),
        retry_config=RetryConfig(max_retries=0),
    )

    assert result is None
    assert send_calls == []
    assert service.store.capture_snapshot().events == ()


def test_shadow_persists_exact_authority_but_never_sends(tmp_path):
    send_calls: list[dict] = []
    service = _service(tmp_path, DurableProviderTurnMode.SHADOW)

    result = service.fetch_prepared(
        model_io=_model_io(send_calls),
        request=_request(),
        retry_config=RetryConfig(max_retries=0),
    )

    assert result is None
    assert send_calls == []
    assert [event.event_type for event in service.store.capture_snapshot().events] == [
        "tool.catalog_snapshot",
        "provider.wire_snapshot",
    ]


def test_enforce_sends_once_then_releases_buffered_callbacks(tmp_path):
    send_calls: list[dict] = []
    events: list[dict] = []
    attempts: list[int] = []
    service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)

    result = service.fetch_prepared(
        model_io=_model_io(send_calls),
        request=_request(events),
        retry_config=RetryConfig(max_retries=0),
        before_attempt=attempts.append,
    )

    assert result.final_text == "durable result"
    assert len(send_calls) == 1
    assert attempts == [0]
    assert [event["type"] for event in events] == [
        "request_messages",
        "token_delta",
    ]


def test_restart_recovers_result_without_resend_or_callback_replay(tmp_path):
    first_sends: list[dict] = []
    first_events: list[dict] = []
    first = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
    expected = first.fetch_prepared(
        model_io=_model_io(first_sends),
        request=_request(first_events),
        retry_config=RetryConfig(max_retries=0),
    )

    restart_sends: list[dict] = []
    restart_events: list[dict] = []
    restarted = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
    recovered = restarted.fetch_prepared(
        model_io=_model_io(restart_sends),
        request=_request(restart_events),
        retry_config=RetryConfig(max_retries=0),
    )

    assert recovered == expected
    assert len(first_sends) == 1
    assert restart_sends == []
    assert restart_events == []


def test_guard_failure_preserves_uncertain_fence_without_network_or_callbacks(
    tmp_path,
):
    send_calls: list[dict] = []
    events: list[dict] = []
    service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)

    def fail_guard(_attempt: int) -> None:
        raise RuntimeError("execution lease was lost")

    with pytest.raises(DurableProviderTurnUncertainError):
        service.fetch_prepared(
            model_io=_model_io(send_calls),
            request=_request(events),
            retry_config=RetryConfig(max_retries=0),
            before_attempt=fail_guard,
        )

    assert send_calls == []
    assert events == []
    with pytest.raises(DurableProviderTurnUncertainError):
        service.fetch_prepared(
            model_io=_model_io(send_calls),
            request=_request(events),
            retry_config=RetryConfig(max_retries=0),
        )
    assert send_calls == []


def test_closed_mode_rejects_prior_enforce_evidence_instead_of_falling_through(
    tmp_path,
):
    send_calls: list[dict] = []
    _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST).fetch_prepared(
        model_io=_model_io(send_calls),
        request=_request(),
        retry_config=RetryConfig(max_retries=0),
    )

    with pytest.raises(DurableProviderTurnError, match="durable evidence"):
        _service(tmp_path, DurableProviderTurnMode.OFF).fetch_prepared(
            model_io=_model_io([]),
            request=_request(),
            retry_config=RetryConfig(max_retries=0),
        )
