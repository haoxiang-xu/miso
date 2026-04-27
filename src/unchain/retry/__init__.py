"""Retry module: wraps ModelIO.fetch_turn with transient-error retry.

Why this module exists
----------------------
Provider SDKs (openai, anthropic, httpx for ollama) do not handle transient
errors (connection drops, 5xx, 429, 529) consistently — anthropic has no
retry, openai's built-in retry is not wired up here, ollama's raw httpx has
none. This module adds a unified, SDK-agnostic retry layer.

First-event gate
----------------
Every provider streams via ``request.callback``. If a call fails *after* the
callback has already emitted content, we must NOT retry — retrying would
deliver the partial content twice. The wrapper detects the first callback
invocation via a proxy callback; once committed, it re-raises the error
instead of retrying.

Consequences:
  - A network failure before any token is produced → retried (ideal case).
  - A network failure mid-stream after some tokens reached the caller → NOT
    retried; the caller sees the partial output plus the exception.

Non-goals (handled elsewhere / deferred)
----------------------------------------
  - Streaming resume / mid-stream fallback to non-streaming: requires SDK-
    specific work and a separate plan.
  - Retry budget across many requests (circuit breaker).
  - Model fallback (Opus → Sonnet on repeated 529).
  - ``observe_tool_batch`` is intentionally bypassed: it has its own
    swallow-on-error semantics for background observations.

Public API
----------
    - RetryConfig, RetryAttempt, RetryContext, RetriesExhaustedError
    - is_retryable, extract_retry_after_ms
    - compute_delay_ms
    - execute_with_retry
    - fetch_turn_with_retry   (primary entry point)
"""
from __future__ import annotations

from .backoff import compute_delay_ms
from .classifier import extract_retry_after_ms, is_retryable
from .executor import execute_with_retry
from .types import (
    RetriesExhaustedError,
    RetryAttempt,
    RetryConfig,
    RetryContext,
)
from .wrapper import fetch_turn_with_retry

__all__ = [
    "RetryConfig",
    "RetryAttempt",
    "RetryContext",
    "RetriesExhaustedError",
    "is_retryable",
    "extract_retry_after_ms",
    "compute_delay_ms",
    "execute_with_retry",
    "fetch_turn_with_retry",
]
