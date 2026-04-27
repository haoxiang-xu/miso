from __future__ import annotations

import httpx
import pytest

from unchain.retry.executor import execute_with_retry
from unchain.retry.types import (
    RetryAttempt,
    RetryConfig,
    RetryContext,
    RetriesExhaustedError,
)


def _noop_sleep(_seconds: float) -> None:
    return None


def _make_config(**overrides):
    defaults = dict(max_retries=3, base_delay_ms=10, max_delay_ms=100, jitter_ratio=0.0)
    defaults.update(overrides)
    return RetryConfig(**defaults)


def _make_ctx(**overrides):
    defaults = dict(run_id="kernel", iteration=0, is_background=False)
    defaults.update(overrides)
    return RetryContext(**defaults)


def test_success_on_first_try_returns_result():
    calls = []

    def op():
        calls.append(1)
        return "ok"

    result = execute_with_retry(op, _make_config(), _make_ctx(), sleep=_noop_sleep)
    assert result == "ok"
    assert calls == [1]


def test_retries_on_retryable_error_then_succeeds():
    attempts = {"count": 0}

    def op():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.ConnectError("fail")
        return "ok"

    result = execute_with_retry(op, _make_config(max_retries=5), _make_ctx(), sleep=_noop_sleep)
    assert result == "ok"
    assert attempts["count"] == 3


def test_non_retryable_error_raises_immediately():
    attempts = {"count": 0}

    def op():
        attempts["count"] += 1
        raise ValueError("business error")

    with pytest.raises(ValueError, match="business error"):
        execute_with_retry(op, _make_config(), _make_ctx(), sleep=_noop_sleep)
    assert attempts["count"] == 1


def test_exhausted_retries_raises_retries_exhausted_error():
    last_exc = httpx.ConnectError("persistent")

    def op():
        raise last_exc

    with pytest.raises(RetriesExhaustedError) as excinfo:
        execute_with_retry(op, _make_config(max_retries=3), _make_ctx(), sleep=_noop_sleep)
    assert excinfo.value.attempts == 3
    assert excinfo.value.last_error is last_exc


def test_on_retry_callback_invoked_with_attempt_info():
    events: list[RetryAttempt] = []
    attempts = {"count": 0}

    def op():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.ConnectError("fail")
        return "done"

    ctx = _make_ctx(on_retry=events.append)
    execute_with_retry(op, _make_config(max_retries=5), ctx, sleep=_noop_sleep)

    assert [e.attempt for e in events] == [1, 2]
    assert all(isinstance(e.error, httpx.ConnectError) for e in events)
    assert all(e.max_retries == 5 for e in events)
    assert all(e.delay_ms >= 0 for e in events)


def test_sleep_called_with_computed_delay_in_seconds():
    slept: list[float] = []

    def op():
        raise httpx.ConnectError("fail")

    config = _make_config(max_retries=3, base_delay_ms=100, max_delay_ms=1000, jitter_ratio=0.0)
    with pytest.raises(RetriesExhaustedError):
        execute_with_retry(op, config, _make_ctx(), sleep=slept.append)

    # max_retries=3 → 1 initial call + 3 retries = 4 attempts, 3 sleeps between them.
    assert slept == [0.1, 0.2, 0.4]


def test_should_stop_halts_retries_and_re_raises_last_error():
    attempts = {"count": 0}
    stop_after = {"fired": False}

    def op():
        attempts["count"] += 1
        raise httpx.ConnectError(f"fail {attempts['count']}")

    def should_stop() -> bool:
        if attempts["count"] >= 1:
            stop_after["fired"] = True
            return True
        return False

    with pytest.raises(httpx.ConnectError, match="fail 1"):
        execute_with_retry(
            op,
            _make_config(max_retries=5),
            _make_ctx(),
            sleep=_noop_sleep,
            should_stop=should_stop,
        )
    assert stop_after["fired"] is True
    assert attempts["count"] == 1


def test_retry_after_header_overrides_backoff():
    slept: list[float] = []
    request = httpx.Request("POST", "https://x")
    response = httpx.Response(
        status_code=429, request=request, headers={"retry-after": "3"}
    )
    status_err = httpx.HTTPStatusError("429", request=request, response=response)

    def op():
        raise status_err

    config = _make_config(max_retries=2, base_delay_ms=500, max_delay_ms=60_000, jitter_ratio=0.0)
    with pytest.raises(RetriesExhaustedError):
        execute_with_retry(op, config, _make_ctx(), sleep=slept.append)

    # max_retries=2 → 1 initial + 2 retries = 3 attempts, 2 sleeps; retry-after wins each time.
    assert slept == [3.0, 3.0]
