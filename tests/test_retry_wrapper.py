from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx
import pytest

from unchain.retry.types import RetryConfig, RetryContext, RetriesExhaustedError
from unchain.retry.wrapper import fetch_turn_with_retry


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
    def __init__(self, script: list):
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

    assert len(io.requests_seen) == 1
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
    assert len(io.requests_seen) == 4


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
