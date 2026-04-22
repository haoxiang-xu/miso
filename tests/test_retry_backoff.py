from __future__ import annotations

import pytest

from unchain.retry.backoff import compute_delay_ms
from unchain.retry.types import RetryConfig


def test_first_attempt_uses_base_delay():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=32_000, jitter_ratio=0.0)
    assert compute_delay_ms(attempt=1, config=config) == 500


def test_second_attempt_doubles():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=32_000, jitter_ratio=0.0)
    assert compute_delay_ms(attempt=2, config=config) == 1000


def test_third_attempt_doubles_again():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=32_000, jitter_ratio=0.0)
    assert compute_delay_ms(attempt=3, config=config) == 2000


def test_exponential_capped_at_max():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=5000, jitter_ratio=0.0)
    assert compute_delay_ms(attempt=10, config=config) == 5000


def test_jitter_stays_in_bounds():
    config = RetryConfig(base_delay_ms=1000, max_delay_ms=32_000, jitter_ratio=0.25)
    samples = [compute_delay_ms(attempt=1, config=config) for _ in range(200)]
    assert all(1000 <= s <= 1250 for s in samples)
    assert len(set(samples)) > 1


def test_retry_after_ms_takes_precedence_over_backoff():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=32_000, jitter_ratio=0.25)
    assert compute_delay_ms(attempt=5, config=config, retry_after_ms=3000) == 3000


def test_retry_after_ms_is_capped_by_max_delay():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=10_000, jitter_ratio=0.0)
    assert compute_delay_ms(attempt=1, config=config, retry_after_ms=60_000) == 10_000


def test_retry_after_zero_returns_zero():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=10_000)
    assert compute_delay_ms(attempt=1, config=config, retry_after_ms=0) == 0


def test_attempt_zero_rejected():
    config = RetryConfig()
    with pytest.raises(ValueError):
        compute_delay_ms(attempt=0, config=config)
