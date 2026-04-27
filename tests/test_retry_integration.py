"""End-to-end: KernelLoop.fetch_model_turn retries a flaky ModelIO."""
from __future__ import annotations

import httpx
import pytest

from unchain.kernel.loop import KernelLoop
from unchain.kernel.state import RunState
from unchain.kernel.types import ModelTurnResult
from unchain.retry import RetriesExhaustedError, RetryConfig


class _FlakyModelIO:
    """A ModelIO stand-in: fails the first N calls with a retryable error, then succeeds."""

    def __init__(self, fail_count: int, succeed_with: ModelTurnResult):
        self._remaining_failures = fail_count
        self._success_value = succeed_with
        self.call_count = 0

    def fetch_turn(self, request):  # noqa: ANN001
        self.call_count += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise httpx.ConnectError("transient")
        return self._success_value


def _success_result(text: str = "hello") -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[],
        tool_calls=[],
        final_text=text,
        response_id=None,
        reasoning_items=[],
        consumed_tokens=1,
        input_tokens=1,
        output_tokens=1,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def _fast_retry_config(max_retries: int = 5) -> RetryConfig:
    return RetryConfig(
        max_retries=max_retries,
        base_delay_ms=1,
        max_delay_ms=10,
        jitter_ratio=0.0,
    )


def test_fetch_model_turn_retries_transient_failures():
    io = _FlakyModelIO(fail_count=2, succeed_with=_success_result("hello"))
    kernel = KernelLoop(model_io=io, retry_config=_fast_retry_config())

    result = kernel.fetch_model_turn(RunState())
    assert result.final_text == "hello"
    assert io.call_count == 3  # 2 failures + 1 success


def test_fetch_model_turn_raises_when_retries_exhausted():
    io = _FlakyModelIO(fail_count=100, succeed_with=_success_result("never"))
    kernel = KernelLoop(model_io=io, retry_config=_fast_retry_config(max_retries=2))

    with pytest.raises(RetriesExhaustedError):
        kernel.fetch_model_turn(RunState())
    assert io.call_count == 3  # 1 initial + 2 retries


def test_observe_tool_batch_is_not_affected_by_retry_config():
    """The background observe path must keep its 'swallow exceptions, return empty' behavior."""

    class _AlwaysFailIO:
        def __init__(self):
            self.call_count = 0

        def fetch_turn(self, request):
            self.call_count += 1
            raise httpx.ConnectError("always")

    io = _AlwaysFailIO()
    kernel = KernelLoop(model_io=io, retry_config=_fast_retry_config(max_retries=5))

    observation, _tokens = kernel.observe_tool_batch(
        full_messages=[{"role": "user", "content": "x"}],
        tool_messages=[],
        payload={},
        iteration=0,
    )
    assert observation == ""
    # observe_tool_batch bypasses the retry wrapper: it still calls fetch_turn
    # directly, so only 1 attempt should occur even with retry_config set.
    assert io.call_count == 1


def test_default_retry_config_used_when_none_provided():
    """Sanity: KernelLoop() without retry_config still works (uses default RetryConfig)."""
    io = _FlakyModelIO(fail_count=0, succeed_with=_success_result("ok"))
    kernel = KernelLoop(model_io=io)  # no retry_config kwarg

    result = kernel.fetch_model_turn(RunState())
    assert result.final_text == "ok"
    assert io.call_count == 1
