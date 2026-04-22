"""Delay calculation for retries: exponential + jitter, with Retry-After override."""
from __future__ import annotations

import random
from typing import Optional

from .types import RetryConfig


def compute_delay_ms(
    attempt: int,
    config: RetryConfig,
    retry_after_ms: Optional[int] = None,
) -> int:
    """Compute the delay (ms) before the given retry attempt.

    `attempt` is 1-indexed. If `retry_after_ms` is provided, it overrides the
    exponential backoff but is still capped by `config.max_delay_ms`.
    """

    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")

    if retry_after_ms is not None:
        return max(0, min(retry_after_ms, config.max_delay_ms))

    base = min(
        config.base_delay_ms * (2 ** (attempt - 1)),
        config.max_delay_ms,
    )
    jitter = random.random() * config.jitter_ratio * base
    return int(base + jitter)
