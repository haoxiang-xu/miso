"""Type definitions for the retry module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class RetryConfig:
    """Static configuration for the retry executor."""

    max_retries: int = 10
    base_delay_ms: int = 500
    max_delay_ms: int = 32_000
    jitter_ratio: float = 0.25


@dataclass
class RetryAttempt:
    """Record of one attempted retry, passed to the on_retry callback."""

    attempt: int
    error: BaseException
    delay_ms: int
    max_retries: int


@dataclass(frozen=True)
class RetryContext:
    """Per-call context: identifies the request and allows observing retries."""

    run_id: str
    iteration: int
    is_background: bool
    on_retry: Optional[Callable[["RetryAttempt"], None]] = None


class RetriesExhaustedError(Exception):
    """Raised when all retries are used up."""

    def __init__(self, last_error: BaseException, attempts: int) -> None:
        super().__init__(
            f"Exhausted {attempts} retry attempts. Last error: {last_error!r}"
        )
        self.last_error = last_error
        self.attempts = attempts
