from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from unchain.retry.classifier import (
    RETRYABLE_STATUS_CODES,
    extract_retry_after_ms,
    is_retryable,
)


def test_retryable_status_codes_includes_expected():
    for code in (408, 409, 429, 500, 502, 503, 504, 529):
        assert code in RETRYABLE_STATUS_CODES


def test_retryable_status_codes_excludes_expected():
    for code in (200, 201, 400, 401, 403, 404):
        assert code not in RETRYABLE_STATUS_CODES


def test_httpx_connect_error_is_retryable():
    assert is_retryable(httpx.ConnectError("tcp fail"))


def test_httpx_connect_timeout_is_retryable():
    assert is_retryable(httpx.ConnectTimeout("timed out"))


def test_httpx_read_timeout_is_retryable():
    assert is_retryable(httpx.ReadTimeout("slow server"))


def test_httpx_remote_protocol_error_is_retryable():
    assert is_retryable(httpx.RemoteProtocolError("stream truncated"))


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


def test_plain_value_error_not_retryable():
    assert not is_retryable(ValueError("bad json"))


def test_plain_key_error_not_retryable():
    assert not is_retryable(KeyError("missing"))


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
    exc.response = object()
    assert extract_retry_after_ms(exc) is None
