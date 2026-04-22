from __future__ import annotations

import pytest

from unchain.retry.types import (
    RetryAttempt,
    RetryConfig,
    RetryContext,
    RetriesExhaustedError,
)


def test_retry_config_defaults():
    config = RetryConfig()
    assert config.max_retries == 10
    assert config.base_delay_ms == 500
    assert config.max_delay_ms == 32_000
    assert config.jitter_ratio == 0.25


def test_retry_config_override():
    config = RetryConfig(max_retries=3, base_delay_ms=100, max_delay_ms=5000, jitter_ratio=0.1)
    assert config.max_retries == 3
    assert config.base_delay_ms == 100
    assert config.max_delay_ms == 5000
    assert config.jitter_ratio == 0.1


def test_retry_config_is_frozen():
    config = RetryConfig()
    with pytest.raises((AttributeError, TypeError)):
        config.max_retries = 99  # type: ignore[misc]


def test_retry_attempt_fields():
    err = ValueError("boom")
    attempt = RetryAttempt(attempt=2, error=err, delay_ms=750, max_retries=10)
    assert attempt.attempt == 2
    assert attempt.error is err
    assert attempt.delay_ms == 750
    assert attempt.max_retries == 10


def test_retry_context_defaults():
    ctx = RetryContext(run_id="kernel", iteration=3, is_background=False)
    assert ctx.run_id == "kernel"
    assert ctx.iteration == 3
    assert ctx.is_background is False
    assert ctx.on_retry is None


def test_retry_context_background_flag():
    ctx = RetryContext(run_id="observe", iteration=0, is_background=True)
    assert ctx.is_background is True


def test_retries_exhausted_error_wraps_last_error():
    underlying = ConnectionError("eof")
    err = RetriesExhaustedError(last_error=underlying, attempts=10)
    assert err.last_error is underlying
    assert err.attempts == 10
    assert "10" in str(err)
    assert "eof" in str(err)


def test_public_api_exports():
    import unchain.retry as retry

    assert retry.RetryConfig is not None
    assert retry.RetryAttempt is not None
    assert retry.RetryContext is not None
    assert retry.RetriesExhaustedError is not None

    assert callable(retry.is_retryable)
    assert callable(retry.extract_retry_after_ms)
    assert callable(retry.compute_delay_ms)
    assert callable(retry.execute_with_retry)
    assert callable(retry.fetch_turn_with_retry)
