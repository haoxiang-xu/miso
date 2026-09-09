"""High-level wrapper: run ModelIO.fetch_turn with retry + first-visible-event gate."""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Callable, Protocol

from .executor import execute_with_retry
from .types import RetryConfig, RetryContext


class _ModelIOLike(Protocol):
    def fetch_turn(self, request: Any) -> Any: ...


_NON_USER_VISIBLE_EVENT_TYPES = frozenset({
    "previous_response_id_fallback",
    "request_messages",
})


def _event_commits_user_visible_output(event: Any) -> bool:
    """Return whether replaying the failed attempt could duplicate visible output."""

    if not isinstance(event, dict):
        return True
    event_type = str(event.get("type") or "")
    return event_type not in _NON_USER_VISIBLE_EVENT_TYPES


def fetch_turn_with_retry(
    model_io: _ModelIOLike,
    request: Any,
    config: RetryConfig,
    context: RetryContext,
    *,
    sleep: Callable[[float], None] = time.sleep,
    before_attempt: Callable[[int], None] | None = None,
    after_attempt: Callable[[int, str, str, str], None] | None = None,
) -> Any:
    """Call `model_io.fetch_turn(request)` with transparent retry on transient errors.

    First-visible-event gate: once a failing attempt emits model output, the
    wrapper will NOT retry because doing so would duplicate content already
    shown to the caller. Debug-only request events do not commit the attempt.
    """

    original_callback = getattr(request, "callback", None)

    if original_callback is None:
        return execute_with_retry(
            lambda: model_io.fetch_turn(request),
            config,
            context,
            sleep=sleep,
            before_attempt=before_attempt,
            after_attempt=after_attempt,
        )

    committed = {"value": False}

    def proxy_callback(event: Any) -> None:
        if _event_commits_user_visible_output(event):
            committed["value"] = True
        original_callback(event)

    gated_request = dataclasses.replace(request, callback=proxy_callback)

    return execute_with_retry(
        lambda: model_io.fetch_turn(gated_request),
        config,
        context,
        sleep=sleep,
        should_stop=lambda: committed["value"],
        before_attempt=before_attempt,
        after_attempt=after_attempt,
    )
