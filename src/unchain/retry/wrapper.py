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
    """Call `model_io.fetch_turn(request)` with transparent retry on transient errors.

    First-event gate: if `request.callback` is set and has been invoked at least
    once during a failing attempt, the wrapper will NOT retry - retrying would
    re-emit already-shown content to the caller.
    """

    original_callback = getattr(request, "callback", None)

    if original_callback is None:
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
