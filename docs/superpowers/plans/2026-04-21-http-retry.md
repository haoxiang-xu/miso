# unchain HTTP Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **User preference (from memory):** do NOT run `git commit` on the user's behalf at any point. After each task's tests pass, stop in a dirty state. The user commits manually. All "commit" mentions below are for the user's reference only — the executing worker must not run `git commit`.

**Goal:** Add a self-contained `src/unchain/retry/` module that wraps `ModelIO.fetch_turn` with exponential-backoff + jitter + `Retry-After` retry for HTTP connection / timeout / 5xx / 429 / 529 errors, without breaking in-flight streaming requests that have already emitted content.

**Architecture:** A new sibling module `src/unchain/retry/` (peer of `agent/` and `subagents/`, per user's module-separation preference) contains five focused files: `types.py`, `classifier.py`, `backoff.py`, `executor.py`, `wrapper.py`. The wrapper inspects the `request.callback` to implement a **"first-event gate"**: if any stream event has already been emitted to the upstream callback, the request is considered *committed* and will not be retried even on a retryable error (prevents duplicate token output). Integration happens in one place: `KernelLoop.fetch_model_turn` in `src/unchain/kernel/loop.py`. `observe_tool_batch` (the existing "background swallow-on-error" path at `loop.py:512-528`) is left untouched — its existing semantics already match "background request, no retry".

**Tech Stack:** Python 3.12+, `pytest`, `httpx`, `openai>=2.7.1`, `anthropic>=0.83.0`. No new third-party dependencies (exponential backoff + jitter implemented by hand; `tenacity`/`backoff` intentionally not added).

**Non-goals (deferred):**
- Streaming mid-stream fallback to non-streaming (complex; needs separate plan).
- Retry budgets / circuit breakers across calls.
- Model fallback (Opus→Sonnet on repeated 529).
- Fast-mode cool-down.

---

## File Structure

**New files:**

| Path | Responsibility |
|------|----------------|
| `src/unchain/retry/__init__.py` | Public API re-exports |
| `src/unchain/retry/types.py` | `RetryConfig`, `RetryAttempt`, `RetryContext`, `RetriesExhaustedError` |
| `src/unchain/retry/classifier.py` | `is_retryable(exc)` + `extract_retry_after_ms(exc)` |
| `src/unchain/retry/backoff.py` | `compute_delay_ms(attempt, config, retry_after_ms)` |
| `src/unchain/retry/executor.py` | `execute_with_retry(operation, config, context, ...)` core loop |
| `src/unchain/retry/wrapper.py` | `fetch_turn_with_retry(model_io, request, config, context)` high-level wrapper with first-event gate |
| `tests/test_retry_types.py` | Unit tests for dataclasses/errors |
| `tests/test_retry_classifier.py` | Unit tests for exception classification + retry-after parsing |
| `tests/test_retry_backoff.py` | Unit tests for backoff math |
| `tests/test_retry_executor.py` | Unit tests for retry loop (with injected sleep + injected should_stop) |
| `tests/test_retry_wrapper.py` | Unit tests for the first-event-gate wrapper |
| `tests/test_retry_integration.py` | End-to-end: `KernelLoop.fetch_model_turn` calls a flaky fake `ModelIO` and retries successfully |

**Modified files:**

| Path:lines | Change |
|------------|--------|
| `src/unchain/kernel/loop.py:107-140` | `fetch_model_turn` calls `fetch_turn_with_retry(self._model_io, request, ...)` instead of `self._model_io.fetch_turn(request)` directly |
| `src/unchain/kernel/loop.py` (`KernelLoop.__init__`) | Accept an optional `retry_config: RetryConfig \| None = None` kwarg, store on self |

**Untouched:** `src/unchain/kernel/loop.py:512-528` (`observe_tool_batch`) — background swallow-on-error pathway, preserve as-is.

---

## Task 1: `types.py` — Dataclasses and error type

**Files:**
- Create: `src/unchain/retry/types.py`
- Create: `src/unchain/retry/__init__.py` (empty placeholder, filled in Task 6)
- Test: `tests/test_retry_types.py`

- [ ] **Step 1.1: Create the empty `__init__.py`**

```bash
mkdir -p src/unchain/retry
: > src/unchain/retry/__init__.py
```

- [ ] **Step 1.2: Write the failing test**

Create `tests/test_retry_types.py`:

```python
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
```

- [ ] **Step 1.3: Run test to verify it fails**

Run: `pytest tests/test_retry_types.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'unchain.retry.types'`)

- [ ] **Step 1.4: Implement `types.py`**

Create `src/unchain/retry/types.py`:

```python
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

    attempt: int            # 1-indexed: attempt=1 means the FIRST retry (after initial failure)
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
```

- [ ] **Step 1.5: Run test to verify it passes**

Run: `pytest tests/test_retry_types.py -v`
Expected: PASS (7 tests passed)

- [ ] **Step 1.6: Stop. Leave working tree dirty; user will commit.**

Suggested commit message (for user):
`feat(retry): add retry module type definitions`

---

## Task 2: `classifier.py` — Decide whether an exception is retryable

**Files:**
- Create: `src/unchain/retry/classifier.py`
- Test: `tests/test_retry_classifier.py`

**Rationale:** The core of retry logic is knowing *which* exceptions deserve retry. We classify by type and (where available) HTTP status code. Three SDK-specific paths: `httpx`, `anthropic`, `openai`. Ollama uses `httpx` directly, so `httpx` coverage handles it.

**Retryable statuses:** 408, 409, 429, 529, and all 5xx.

- [ ] **Step 2.1: Write the failing test**

Create `tests/test_retry_classifier.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from unchain.retry.classifier import (
    RETRYABLE_STATUS_CODES,
    extract_retry_after_ms,
    is_retryable,
)


# ----- status code set -----

def test_retryable_status_codes_includes_expected():
    for code in (408, 409, 429, 500, 502, 503, 504, 529):
        assert code in RETRYABLE_STATUS_CODES


def test_retryable_status_codes_excludes_expected():
    for code in (200, 201, 400, 401, 403, 404):
        assert code not in RETRYABLE_STATUS_CODES


# ----- httpx network errors -----

def test_httpx_connect_error_is_retryable():
    assert is_retryable(httpx.ConnectError("tcp fail"))


def test_httpx_connect_timeout_is_retryable():
    assert is_retryable(httpx.ConnectTimeout("timed out"))


def test_httpx_read_timeout_is_retryable():
    assert is_retryable(httpx.ReadTimeout("slow server"))


def test_httpx_remote_protocol_error_is_retryable():
    assert is_retryable(httpx.RemoteProtocolError("stream truncated"))


# ----- httpx status-bearing errors -----

def _httpx_status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(status_code=code, request=request)
    return httpx.HTTPStatusError(f"{code}", request=request, response=response)


@pytest.mark.parametrize("code", [408, 409, 429, 500, 502, 503, 504, 529])
def test_httpx_retryable_status_errors(code: int):
    assert is_retryable(_httpx_status_error(code))


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_httpx_non_retryable_status_errors(code: int):
    assert not is_retryable(_httpx_status_error(code))


# ----- Non-retryable python errors -----

def test_plain_value_error_not_retryable():
    assert not is_retryable(ValueError("bad json"))


def test_plain_key_error_not_retryable():
    assert not is_retryable(KeyError("missing"))


# ----- retry-after header extraction -----

def test_extract_retry_after_ms_from_httpx_response():
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(
        status_code=429, request=request, headers={"retry-after": "7"}
    )
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    assert extract_retry_after_ms(exc) == 7000


def test_extract_retry_after_ms_missing_header():
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(status_code=429, request=request, headers={})
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    assert extract_retry_after_ms(exc) is None


def test_extract_retry_after_ms_non_numeric_returns_none():
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(
        status_code=429, request=request, headers={"retry-after": "Wed, 21 Oct 2026"}
    )
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    assert extract_retry_after_ms(exc) is None


def test_extract_retry_after_ms_no_response_attr():
    assert extract_retry_after_ms(ValueError("no response")) is None


def test_extract_retry_after_ms_response_without_headers():
    exc = MagicMock(spec=[])
    exc.response = object()  # has no .headers
    # MagicMock with spec=[] raises AttributeError for missing attrs, which is caught
    assert extract_retry_after_ms(exc) is None
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `pytest tests/test_retry_classifier.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'unchain.retry.classifier'`)

- [ ] **Step 2.3: Implement `classifier.py`**

Create `src/unchain/retry/classifier.py`:

```python
"""Exception classification for retry decisions."""
from __future__ import annotations

from typing import Optional

import httpx

# HTTP status codes worth retrying. 408 request-timeout, 409 conflict/lock,
# 429 rate-limit, 5xx server errors, 529 Anthropic "overloaded".
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset(
    {408, 409, 429, 529} | set(range(500, 600))
)


def is_retryable(error: BaseException) -> bool:
    """Return True if the error is a transient network / server failure."""

    # 1. httpx network-layer errors
    if isinstance(
        error,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
        ),
    ):
        return True

    # 2. httpx status-bearing errors
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUS_CODES

    # 3. anthropic SDK errors (import guarded — anthropic is a hard dep but stay defensive)
    try:
        import anthropic  # type: ignore

        if isinstance(error, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
            return True
        if isinstance(error, anthropic.APIStatusError):
            status = getattr(error, "status_code", None)
            return status in RETRYABLE_STATUS_CODES
    except ImportError:
        pass

    # 4. openai SDK errors
    try:
        import openai  # type: ignore

        if isinstance(error, (openai.APIConnectionError, openai.APITimeoutError)):
            return True
        if isinstance(error, openai.APIStatusError):
            status = getattr(error, "status_code", None)
            return status in RETRYABLE_STATUS_CODES
    except ImportError:
        pass

    return False


def extract_retry_after_ms(error: BaseException) -> Optional[int]:
    """If the error carries a Retry-After header (integer seconds), return milliseconds."""

    response = getattr(error, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("retry-after")
    except AttributeError:
        return None
    if not value:
        return None
    try:
        seconds = int(value)
    except (ValueError, TypeError):
        return None
    if seconds < 0:
        return None
    return seconds * 1000
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `pytest tests/test_retry_classifier.py -v`
Expected: PASS (all tests green)

- [ ] **Step 2.5: Stop. Leave working tree dirty.**

Suggested commit message: `feat(retry): classify retryable exceptions`

---

## Task 3: `backoff.py` — Compute delay for each attempt

**Files:**
- Create: `src/unchain/retry/backoff.py`
- Test: `tests/test_retry_backoff.py`

- [ ] **Step 3.1: Write the failing test**

Create `tests/test_retry_backoff.py`:

```python
from __future__ import annotations

import pytest

from unchain.retry.backoff import compute_delay_ms
from unchain.retry.types import RetryConfig


def test_first_attempt_uses_base_delay():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=32_000, jitter_ratio=0.0)
    # No jitter, attempt 1 → exactly 500ms
    assert compute_delay_ms(attempt=1, config=config) == 500


def test_second_attempt_doubles():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=32_000, jitter_ratio=0.0)
    assert compute_delay_ms(attempt=2, config=config) == 1000


def test_third_attempt_doubles_again():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=32_000, jitter_ratio=0.0)
    assert compute_delay_ms(attempt=3, config=config) == 2000


def test_exponential_capped_at_max():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=5000, jitter_ratio=0.0)
    # 500 * 2^9 = 256_000, capped to 5000
    assert compute_delay_ms(attempt=10, config=config) == 5000


def test_jitter_stays_in_bounds():
    config = RetryConfig(base_delay_ms=1000, max_delay_ms=32_000, jitter_ratio=0.25)
    samples = [compute_delay_ms(attempt=1, config=config) for _ in range(200)]
    # Each sample should be in [base, base + 25%*base] = [1000, 1250]
    assert all(1000 <= s <= 1250 for s in samples)
    # With 200 samples we should see some variation
    assert len(set(samples)) > 1


def test_retry_after_ms_takes_precedence_over_backoff():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=32_000, jitter_ratio=0.25)
    # retry_after dictates; jitter/exponential ignored
    assert compute_delay_ms(attempt=5, config=config, retry_after_ms=3000) == 3000


def test_retry_after_ms_is_capped_by_max_delay():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=10_000, jitter_ratio=0.0)
    # server says wait 60s, but max is 10s → 10s
    assert compute_delay_ms(attempt=1, config=config, retry_after_ms=60_000) == 10_000


def test_retry_after_zero_returns_zero():
    config = RetryConfig(base_delay_ms=500, max_delay_ms=10_000)
    assert compute_delay_ms(attempt=1, config=config, retry_after_ms=0) == 0


def test_attempt_zero_rejected():
    config = RetryConfig()
    with pytest.raises(ValueError):
        compute_delay_ms(attempt=0, config=config)
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `pytest tests/test_retry_backoff.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3.3: Implement `backoff.py`**

Create `src/unchain/retry/backoff.py`:

```python
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

    `attempt` is 1-indexed: attempt=1 is the first retry, attempt=2 the second, etc.

    If `retry_after_ms` is provided (from a Retry-After header), it overrides the
    exponential backoff entirely (but is still capped by `config.max_delay_ms`).
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
```

- [ ] **Step 3.4: Run test to verify it passes**

Run: `pytest tests/test_retry_backoff.py -v`
Expected: PASS (9 tests green)

- [ ] **Step 3.5: Stop. Leave working tree dirty.**

Suggested commit message: `feat(retry): exponential backoff with jitter and retry-after`

---

## Task 4: `executor.py` — The core retry loop

**Files:**
- Create: `src/unchain/retry/executor.py`
- Test: `tests/test_retry_executor.py`

**Design notes:**
- Accepts `sleep` as an injected callable (defaults to `time.sleep`) so tests can avoid real sleep.
- Accepts `should_stop` as an optional callable. Before each *retry attempt* (not the first try), if `should_stop()` returns True, the executor re-raises the last error immediately instead of retrying. This is the hook `wrapper.py` uses to implement the first-event gate.
- Counts `attempt` starting at 1 for the FIRST retry (i.e., after the initial call fails). The initial call itself is "attempt 0" internally — but we expose only retry attempts in `RetryAttempt`.

- [ ] **Step 4.1: Write the failing test**

Create `tests/test_retry_executor.py`:

```python
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

    # 2 failures → 2 retry callbacks (attempt 1 and 2)
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

    # 3 max_retries → fail → retry → fail → retry → fail → exhaust
    # Two sleeps in between: 100ms, 200ms (in seconds)
    assert slept == [0.1, 0.2]


def test_should_stop_halts_retries_and_re_raises_last_error():
    attempts = {"count": 0}
    stop_after = {"fired": False}

    def op():
        attempts["count"] += 1
        raise httpx.ConnectError(f"fail {attempts['count']}")

    def should_stop() -> bool:
        # Stop before the second retry
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
    # op was called exactly once because should_stop fired before the retry
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

    # One retry gap: retry-after 3s wins over the 500ms base backoff
    assert slept == [3.0]
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `pytest tests/test_retry_executor.py -v`
Expected: FAIL (module not found)

- [ ] **Step 4.3: Implement `executor.py`**

Create `src/unchain/retry/executor.py`:

```python
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

    # We make up to (max_retries + 1) total calls: 1 initial + max_retries retries.
    # Loop index `attempt_number`: 0 = initial, 1..max_retries = retries.
    for attempt_number in range(config.max_retries + 1):
        try:
            return operation()
        except BaseException as exc:  # noqa: BLE001 - we classify and decide below
            if not is_retryable(exc):
                raise
            last_error = exc

            # No retries left?
            if attempt_number >= config.max_retries:
                break

            # External gate (e.g. stream already committed content → don't retry)
            if should_stop is not None and should_stop():
                raise

            retry_index = attempt_number + 1  # 1-indexed for the upcoming retry
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

    assert last_error is not None  # loop always recorded one if we fell out
    raise RetriesExhaustedError(last_error=last_error, attempts=config.max_retries)
```

- [ ] **Step 4.4: Run test to verify it passes**

Run: `pytest tests/test_retry_executor.py -v`
Expected: PASS (8 tests green)

- [ ] **Step 4.5: Stop. Leave working tree dirty.**

Suggested commit message: `feat(retry): core retry executor with should_stop gate`

---

## Task 5: `wrapper.py` — Wrap `fetch_turn` with the first-event gate

**Files:**
- Create: `src/unchain/retry/wrapper.py`
- Test: `tests/test_retry_wrapper.py`

**Design notes:**
- The wrapper takes `model_io`, `request`, `config`, `context`.
- Detects if `request.callback` is set. If so, installs a proxy callback that sets a flag `committed = True` the first time it's called.
- The retry executor's `should_stop` is `lambda: committed`. So:
  - If `fetch_turn` fails **before** any event is emitted → free to retry.
  - If `fetch_turn` fails **after** at least one event has been emitted → do NOT retry (stream already produced user-visible content; retrying would duplicate).
- Uses `dataclasses.replace(request, callback=proxy)` to avoid mutating the caller's request.
- `observe_tool_batch` passes `callback=None`, so its calls get unconditional retry — but kernel's main path will also call with `is_background=False`. The wrapper does NOT use `context.is_background` directly; it's available for future policy tuning (see Task 6's public API).

- [ ] **Step 5.1: Write the failing test**

Create `tests/test_retry_wrapper.py`:

```python
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx
import pytest

from unchain.retry.types import RetryConfig, RetryContext, RetriesExhaustedError
from unchain.retry.wrapper import fetch_turn_with_retry


# --- Test doubles: mimic the shape of ModelTurnRequest / ModelTurnResult / ModelIO ---

@dataclass
class _FakeRequest:
    messages: list
    callback: Optional[Callable[[Any], None]] = None
    run_id: str = "kernel"


@dataclass
class _FakeResult:
    final_text: str
    consumed_tokens: int = 0


class _FakeModelIO:
    """Simulates a flaky ModelIO. Records received requests and emits events
    via request.callback before (optionally) raising."""

    def __init__(self, script: list):
        # each script entry: ("raise_before_emit", exc) or ("raise_after_emit", exc) or ("ok", result)
        self._script = list(script)
        self.requests_seen: list[_FakeRequest] = []

    def fetch_turn(self, request: _FakeRequest) -> _FakeResult:
        self.requests_seen.append(request)
        step = self._script.pop(0)
        kind = step[0]
        if kind == "ok":
            return step[1]
        if kind == "raise_before_emit":
            raise step[1]
        if kind == "emit_then_raise":
            if request.callback is not None:
                request.callback({"type": "text.delta", "value": "hi"})
            raise step[1]
        raise AssertionError(f"unknown script kind {kind!r}")


def _config():
    return RetryConfig(max_retries=5, base_delay_ms=1, max_delay_ms=10, jitter_ratio=0.0)


def _ctx():
    return RetryContext(run_id="kernel", iteration=0, is_background=False)


def _noop_sleep(_sec: float) -> None:
    return None


# --- Tests ---

def test_success_no_retry_needed():
    result = _FakeResult(final_text="hello")
    io = _FakeModelIO(script=[("ok", result)])
    req = _FakeRequest(messages=[])

    got = fetch_turn_with_retry(io, req, _config(), _ctx(), sleep=_noop_sleep)
    assert got is result
    assert len(io.requests_seen) == 1


def test_retries_on_connect_error_before_any_emit():
    result = _FakeResult(final_text="later")
    io = _FakeModelIO(
        script=[
            ("raise_before_emit", httpx.ConnectError("boom1")),
            ("raise_before_emit", httpx.ConnectError("boom2")),
            ("ok", result),
        ]
    )
    req = _FakeRequest(messages=[], callback=lambda _e: None)

    got = fetch_turn_with_retry(io, req, _config(), _ctx(), sleep=_noop_sleep)
    assert got is result
    assert len(io.requests_seen) == 3


def test_does_not_retry_after_callback_has_emitted():
    """If the stream emitted any event to the caller's callback, retrying
    would duplicate user-visible output. Re-raise immediately."""
    callback_events: list = []
    io = _FakeModelIO(
        script=[
            ("emit_then_raise", httpx.ConnectError("mid-stream fail")),
        ]
    )
    req = _FakeRequest(messages=[], callback=callback_events.append)

    with pytest.raises(httpx.ConnectError, match="mid-stream fail"):
        fetch_turn_with_retry(io, req, _config(), _ctx(), sleep=_noop_sleep)

    # Exactly one call made; no retry attempted.
    assert len(io.requests_seen) == 1
    # The original callback received the event from the failed attempt.
    assert callback_events == [{"type": "text.delta", "value": "hi"}]


def test_forwards_events_to_original_callback():
    callback_events: list = []
    result = _FakeResult(final_text="ok")

    class _EmitOnSuccessIO:
        def __init__(self):
            self.requests_seen = []

        def fetch_turn(self, request):
            self.requests_seen.append(request)
            if request.callback is not None:
                request.callback({"type": "text.delta", "value": "a"})
                request.callback({"type": "text.delta", "value": "b"})
            return result

    io = _EmitOnSuccessIO()
    req = _FakeRequest(messages=[], callback=callback_events.append)

    got = fetch_turn_with_retry(io, req, _config(), _ctx(), sleep=_noop_sleep)
    assert got is result
    assert callback_events == [
        {"type": "text.delta", "value": "a"},
        {"type": "text.delta", "value": "b"},
    ]


def test_original_request_callback_is_not_mutated():
    original_cb = lambda _e: None
    req = _FakeRequest(messages=[], callback=original_cb)
    io = _FakeModelIO(script=[("ok", _FakeResult(final_text="x"))])

    fetch_turn_with_retry(io, req, _config(), _ctx(), sleep=_noop_sleep)

    # Caller's request object retains its original callback; wrapper uses dataclasses.replace.
    assert req.callback is original_cb


def test_non_retryable_error_raised_immediately():
    io = _FakeModelIO(script=[("raise_before_emit", ValueError("bad json"))])
    req = _FakeRequest(messages=[])

    with pytest.raises(ValueError, match="bad json"):
        fetch_turn_with_retry(io, req, _config(), _ctx(), sleep=_noop_sleep)
    assert len(io.requests_seen) == 1


def test_exhausted_retries_raises_retries_exhausted():
    io = _FakeModelIO(
        script=[("raise_before_emit", httpx.ConnectError("fail"))] * 4
    )
    config = RetryConfig(max_retries=3, base_delay_ms=1, max_delay_ms=10, jitter_ratio=0.0)
    req = _FakeRequest(messages=[])

    with pytest.raises(RetriesExhaustedError):
        fetch_turn_with_retry(io, req, config, _ctx(), sleep=_noop_sleep)
    assert len(io.requests_seen) == 4  # 1 initial + 3 retries


def test_callback_none_path_still_retries_on_transient_error():
    result = _FakeResult(final_text="ok")
    io = _FakeModelIO(
        script=[
            ("raise_before_emit", httpx.ConnectError("nope")),
            ("ok", result),
        ]
    )
    req = _FakeRequest(messages=[], callback=None)

    got = fetch_turn_with_retry(io, req, _config(), _ctx(), sleep=_noop_sleep)
    assert got is result
    assert len(io.requests_seen) == 2
```

- [ ] **Step 5.2: Run test to verify it fails**

Run: `pytest tests/test_retry_wrapper.py -v`
Expected: FAIL (module not found)

- [ ] **Step 5.3: Implement `wrapper.py`**

Create `src/unchain/retry/wrapper.py`:

```python
"""High-level wrapper: run ModelIO.fetch_turn with retry + first-event gate."""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Callable, Protocol

from .executor import execute_with_retry
from .types import RetryConfig, RetryContext


class _ModelIOLike(Protocol):
    def fetch_turn(self, request: Any) -> Any: ...


def fetch_turn_with_retry(
    model_io: _ModelIOLike,
    request: Any,
    config: RetryConfig,
    context: RetryContext,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Call `model_io.fetch_turn(request)` with transparent retry on transient
    network / server errors.

    First-event gate: if `request.callback` is set and it has been invoked at
    least once during a failing attempt, we do NOT retry — retrying would
    re-emit already-shown content to the caller.

    Args:
        model_io: object with `fetch_turn(request) -> result`
        request: a ModelTurnRequest-like dataclass with a `callback` field
        config: retry policy
        context: run_id + iteration + on_retry observer
        sleep: injectable for testing

    Returns:
        The result of the first successful `fetch_turn` call.

    Raises:
        The original non-retryable exception from `fetch_turn`.
        The original exception if we stop due to the first-event gate.
        RetriesExhaustedError when all retries are used up.
    """

    original_callback = getattr(request, "callback", None)

    if original_callback is None:
        # No callback → nothing to gate, retry freely.
        return execute_with_retry(
            lambda: model_io.fetch_turn(request),
            config,
            context,
            sleep=sleep,
        )

    committed = {"value": False}

    def proxy_callback(event: Any) -> None:
        committed["value"] = True
        original_callback(event)

    gated_request = dataclasses.replace(request, callback=proxy_callback)

    return execute_with_retry(
        lambda: model_io.fetch_turn(gated_request),
        config,
        context,
        sleep=sleep,
        should_stop=lambda: committed["value"],
    )
```

- [ ] **Step 5.4: Run test to verify it passes**

Run: `pytest tests/test_retry_wrapper.py -v`
Expected: PASS (8 tests green)

- [ ] **Step 5.5: Stop. Leave working tree dirty.**

Suggested commit message: `feat(retry): fetch_turn wrapper with first-event gate`

---

## Task 6: Public API — `__init__.py`

**Files:**
- Modify: `src/unchain/retry/__init__.py`
- Test: `tests/test_retry_types.py` (extend)

- [ ] **Step 6.1: Write the failing test**

Append to `tests/test_retry_types.py`:

```python


def test_public_api_exports():
    import unchain.retry as retry

    # Core types
    assert retry.RetryConfig is not None
    assert retry.RetryAttempt is not None
    assert retry.RetryContext is not None
    assert retry.RetriesExhaustedError is not None

    # Functions
    assert callable(retry.is_retryable)
    assert callable(retry.extract_retry_after_ms)
    assert callable(retry.compute_delay_ms)
    assert callable(retry.execute_with_retry)
    assert callable(retry.fetch_turn_with_retry)
```

- [ ] **Step 6.2: Run test to verify it fails**

Run: `pytest tests/test_retry_types.py::test_public_api_exports -v`
Expected: FAIL (`AttributeError: module 'unchain.retry' has no attribute 'RetryConfig'`)

- [ ] **Step 6.3: Fill in `__init__.py`**

Replace `src/unchain/retry/__init__.py` with:

```python
"""Retry module: wraps ModelIO.fetch_turn with transient-error retry.

Public API:
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
```

- [ ] **Step 6.4: Run the full retry test suite**

Run: `pytest tests/test_retry_types.py tests/test_retry_classifier.py tests/test_retry_backoff.py tests/test_retry_executor.py tests/test_retry_wrapper.py -v`
Expected: PASS (all tests green across the module)

- [ ] **Step 6.5: Stop. Leave working tree dirty.**

Suggested commit message: `feat(retry): public API exports`

---

## Task 7: Integrate into `KernelLoop.fetch_model_turn`

**Files:**
- Modify: `src/unchain/kernel/loop.py:107-140` (the `fetch_model_turn` method, and `__init__`)
- Test: `tests/test_retry_integration.py`

**Design notes:**
- `KernelLoop.__init__` gains an optional `retry_config: RetryConfig | None = None` kwarg. Default `None` ⇒ fall back to a module-level default `RetryConfig()`.
- `fetch_model_turn` changes one line: replace the final `return self._model_io.fetch_turn(request)` with a call to `fetch_turn_with_retry(self._model_io, request, config, context)`.
- `observe_tool_batch` (loop.py:512-528) is NOT touched. It keeps its current "swallow all exceptions" behavior.
- Nothing else about the kernel changes.

- [ ] **Step 7.1: Write the failing integration test**

Create `tests/test_retry_integration.py`:

```python
"""End-to-end: KernelLoop.fetch_model_turn retries a flaky ModelIO."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from unchain.kernel.loop import KernelLoop
from unchain.providers.model_io import ModelTurnResult
from unchain.retry import RetryConfig


class _FlakyModelIO:
    """A ModelIO stand-in: fails the first N calls with a retryable error, then succeeds."""

    def __init__(self, fail_count: int, succeed_with: ModelTurnResult):
        self._remaining_failures = fail_count
        self._success_value = succeed_with
        self.call_count = 0

    def fetch_turn(self, request):  # noqa: ANN001 - duck-typed
        self.call_count += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise httpx.ConnectError("transient")
        return self._success_value


def _make_kernel(model_io, *, retry_config=None):
    # KernelLoop requires at minimum a model_io. Any other required constructor
    # args should be filled with test-appropriate defaults. If the real signature
    # differs, adjust here; the goal is "minimum viable KernelLoop for fetch_model_turn".
    return KernelLoop(model_io=model_io, retry_config=retry_config)


def _make_state():
    # Minimal RunState-ish object compatible with fetch_model_turn.
    # If RunState needs specific fields, import and construct per its real shape.
    from unchain.kernel.state import RunState  # adjust import path if needed
    return RunState(next_model_input=[{"role": "user", "content": "hi"}])


def test_fetch_model_turn_retries_transient_failures():
    success = ModelTurnResult(
        final_text="hello",
        assistant_messages=[],
        consumed_tokens=1,
        input_tokens=1,
        output_tokens=1,
    )
    io = _FlakyModelIO(fail_count=2, succeed_with=success)
    kernel = _make_kernel(
        io,
        retry_config=RetryConfig(
            max_retries=5, base_delay_ms=1, max_delay_ms=10, jitter_ratio=0.0
        ),
    )

    result = kernel.fetch_model_turn(_make_state())
    assert result.final_text == "hello"
    assert io.call_count == 3  # 2 failures + 1 success


def test_fetch_model_turn_raises_when_retries_exhausted():
    success = ModelTurnResult(
        final_text="never", assistant_messages=[], consumed_tokens=0, input_tokens=0, output_tokens=0
    )
    io = _FlakyModelIO(fail_count=100, succeed_with=success)
    kernel = _make_kernel(
        io,
        retry_config=RetryConfig(
            max_retries=2, base_delay_ms=1, max_delay_ms=10, jitter_ratio=0.0
        ),
    )

    from unchain.retry import RetriesExhaustedError

    with pytest.raises(RetriesExhaustedError):
        kernel.fetch_model_turn(_make_state())
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
    kernel = _make_kernel(
        io,
        retry_config=RetryConfig(
            max_retries=5, base_delay_ms=1, max_delay_ms=10, jitter_ratio=0.0
        ),
    )

    # Call observe_tool_batch with minimal arguments. If its signature requires
    # specific fixtures, build them here. The observation should be the empty
    # string (swallowed), and io.call_count should be exactly 1 (NO retry).
    observation, _tokens = kernel.observe_tool_batch(
        observe_messages=[{"role": "user", "content": "x"}],
        observe_payload={},
        iteration=0,
    )
    assert observation == ""
    assert io.call_count == 1
```

**Adaptation note:** `_make_state()` and `observe_tool_batch()` call shapes above are best-effort per the reconnaissance report. Before running, verify:
- `unchain.kernel.state.RunState` import path (grep `class RunState` in the repo)
- `KernelLoop.__init__` signature (may need more than just `model_io`)
- `observe_tool_batch` parameter names (match `loop.py` around line 500)

If signatures differ, adjust the helper functions — don't weaken the assertions.

- [ ] **Step 7.2: Run integration test to verify it fails**

Run: `pytest tests/test_retry_integration.py -v`
Expected: FAIL (either `TypeError: __init__() got an unexpected keyword argument 'retry_config'` or retries don't happen → call_count mismatch)

- [ ] **Step 7.3: Modify `KernelLoop.__init__` to accept `retry_config`**

Find the `KernelLoop` constructor in `src/unchain/kernel/loop.py`. Add these changes:

1. Add imports near the top of the file:

```python
from unchain.retry import RetryConfig, RetryContext, fetch_turn_with_retry
```

2. In `__init__`, after the existing parameters, add:

```python
def __init__(
    self,
    # ... existing parameters ...
    retry_config: RetryConfig | None = None,
) -> None:
    # ... existing body ...
    self._retry_config: RetryConfig = retry_config if retry_config is not None else RetryConfig()
```

**Verification step (before editing):** open `src/unchain/kernel/loop.py` and locate the *actual* `__init__` signature. Preserve every existing parameter and add `retry_config` at the end with a default. If `__init__` does not currently exist (unlikely — it's used as `KernelLoop(model_io=...)`), add one. Do not delete or reorder existing parameters.

- [ ] **Step 7.4: Rewrite `fetch_model_turn` to use the retry wrapper**

In `src/unchain/kernel/loop.py:107-140`, change only the final return statement.

**Before (line ~139-140):**

```python
return self._model_io.fetch_turn(request)
```

**After:**

```python
ctx = RetryContext(
    run_id=run_id,
    iteration=state.iteration,
    is_background=(run_id == "observe"),
)
return fetch_turn_with_retry(
    model_io=self._model_io,
    request=request,
    config=self._retry_config,
    context=ctx,
)
```

Do not touch anything else in `fetch_model_turn`. The `request` construction, messages deep-copy, payload handling all stay identical.

- [ ] **Step 7.5: Run integration test to verify it passes**

Run: `pytest tests/test_retry_integration.py -v`
Expected: PASS (3 tests green)

- [ ] **Step 7.6: Run the entire test suite to make sure nothing else broke**

Run: `pytest -v`
Expected: PASS (all pre-existing tests still green)

Specifically confirm these still pass:
- `tests/test_kernel_core.py`
- `tests/test_kernel_model_io.py`

- [ ] **Step 7.7: Stop. Leave working tree dirty.**

Suggested commit message: `feat(kernel): wire fetch_model_turn through retry module`

---

## Task 8: Verify `observe_tool_batch` is unchanged

**Files:**
- No changes.
- Verification only.

**Rationale:** A defensive task: the `observe_tool_batch` path at `loop.py:512-528` catches `except Exception` and swallows. We want to be certain Task 7 did not accidentally route it through the retry wrapper.

- [ ] **Step 8.1: Read `loop.py:512-528` and confirm**

Run: `grep -n "def observe_tool_batch" src/unchain/kernel/loop.py`

Open that function and confirm:
- It still calls `self._model_io.fetch_turn(...)` directly (not `fetch_turn_with_retry`)
- The surrounding `try / except Exception: return "", TokenUsage()` is intact
- The `run_id="observe"` argument is preserved

- [ ] **Step 8.2: Confirm via the integration test already written**

Run: `pytest tests/test_retry_integration.py::test_observe_tool_batch_is_not_affected_by_retry_config -v`
Expected: PASS (io.call_count == 1; observation == "")

If the test fails with `io.call_count > 1`, the observe path was accidentally retried — revert and fix Task 7.

- [ ] **Step 8.3: No commit needed (no changes).**

---

## Task 9: Module docstring / readme

**Files:**
- Modify: `src/unchain/retry/__init__.py` (expand the module docstring)

**Rationale:** The next engineer who touches this should know WHY the first-event gate exists. Per user feedback: "改代码前必须先读文档". So document the non-obvious bits.

- [ ] **Step 9.1: Replace the short docstring at the top of `__init__.py` with:**

```python
"""Retry module: wraps ModelIO.fetch_turn with transient-error retry.

Why this module exists
----------------------
Provider SDKs (openai, anthropic, httpx for ollama) do not handle transient
errors (connection drops, 5xx, 429, 529) consistently — anthropic has no
retry, openai's built-in retry is not wired up here, ollama's raw httpx has
none. This module adds a unified, SDK-agnostic retry layer.

First-event gate
----------------
Every provider streams via `request.callback`. If a call fails *after* the
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
  - `observe_tool_batch` is intentionally bypassed: it has its own
    swallow-on-error semantics for background observations.

Public API
----------
    - RetryConfig, RetryAttempt, RetryContext, RetriesExhaustedError
    - is_retryable, extract_retry_after_ms
    - compute_delay_ms
    - execute_with_retry
    - fetch_turn_with_retry   (primary entry point)
"""
```

Keep the existing `from ... import ...` block and `__all__` below the docstring.

- [ ] **Step 9.2: Verify nothing broke**

Run: `pytest tests/test_retry_types.py::test_public_api_exports -v`
Expected: PASS

- [ ] **Step 9.3: Stop. Leave working tree dirty.**

Suggested commit message: `docs(retry): document first-event gate rationale`

---

## Acceptance Checklist

Before calling this complete, verify all of:

- [ ] `src/unchain/retry/` exists with 6 files (`__init__.py`, `types.py`, `classifier.py`, `backoff.py`, `executor.py`, `wrapper.py`).
- [ ] All new tests pass: `pytest tests/test_retry_types.py tests/test_retry_classifier.py tests/test_retry_backoff.py tests/test_retry_executor.py tests/test_retry_wrapper.py tests/test_retry_integration.py -v`
- [ ] Full suite passes: `pytest -v` — no pre-existing tests regressed.
- [ ] `observe_tool_batch` behavior unchanged (see Task 8).
- [ ] `KernelLoop.fetch_model_turn` now routes through `fetch_turn_with_retry`.
- [ ] `KernelLoop.__init__` accepts optional `retry_config: RetryConfig | None = None`.
- [ ] No changes to `src/unchain/providers/model_io.py`.
- [ ] No new third-party dependencies added to `pyproject.toml`.
- [ ] `docs/superpowers/plans/2026-04-21-http-retry.md` (this file) left in place for the record.

## Follow-up work (not in this plan)

Things a subsequent plan would address:
1. **Streaming mid-stream fallback**: detect idle-timeout / truncated stream / no `message_start`, re-issue as non-streaming.
2. **Retry budget / circuit breaker**: avoid storms of retries across many simultaneous calls.
3. **Model fallback on repeated 529**: Opus → Sonnet switch.
4. **Retry instrumentation**: hook `on_retry` into the existing event bus so the PuPu UI can surface "retrying..." to users.
5. **Per-provider nuances**: OpenAI's `x-should-retry: false` header, Anthropic's `anthropic-ratelimit-unified-reset`.
