"""Core retry loop."""
from __future__ import annotations

import time
from typing import Callable, Optional, TypeVar

from .backoff import compute_delay_ms
from .classifier import extract_retry_after_ms, is_retryable
from .types import RetryAttempt, RetryConfig, RetryContext, RetriesExhaustedError

T = TypeVar("T")


def execute_with_retry(
    operation: Callable[[], T],
    config: RetryConfig,
    context: RetryContext,
    *,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Optional[Callable[[], bool]] = None,
) -> T:
    """Run `operation` with retry on transient errors.

    Raises:
        RetriesExhaustedError: when `config.max_retries` attempts all fail.
        The original exception: when the error is non-retryable, or when
            `should_stop()` returns True before the next retry.
    """

    last_error: Optional[BaseException] = None

    for attempt_number in range(config.max_retries + 1):
        try:
            return operation()
        except BaseException as exc:  # noqa: BLE001
            if not is_retryable(exc):
                raise
            last_error = exc

            if attempt_number >= config.max_retries:
                break

            if should_stop is not None and should_stop():
                raise

            retry_index = attempt_number + 1
            delay_ms = compute_delay_ms(
                attempt=retry_index,
                config=config,
                retry_after_ms=extract_retry_after_ms(exc),
            )

            if context.on_retry is not None:
                context.on_retry(
                    RetryAttempt(
                        attempt=retry_index,
                        error=exc,
                        delay_ms=delay_ms,
                        max_retries=config.max_retries,
                    )
                )

            sleep(delay_ms / 1000.0)

    assert last_error is not None
    raise RetriesExhaustedError(last_error=last_error, attempts=config.max_retries)
