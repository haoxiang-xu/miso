from __future__ import annotations

from urllib.parse import quote

import pytest

from unchain.journal import ModelValidationError
from unchain.memory.workspace import models as workspace_models


@pytest.mark.parametrize(
    "marker",
    [
        "password",
        "authorization",
        "code",
        "sig",
        "signature",
        "cookie",
        "ｐａｓｓｗｏｒｄ",
        "ａｕｔｈｏｒｉｚａｔｉｏｎ",
    ],
)
def test_link_url_path_rejects_every_sensitive_marker(marker: str) -> None:
    encoded_marker = quote(marker, safe="")

    with pytest.raises(ModelValidationError, match="credentials"):
        workspace_models.canonical_memory_link_url(
            f"https://example.test/download/{encoded_marker}/abcDEF0123456789"
        )


def test_link_url_rejects_percent_encoding_beyond_the_decode_limit() -> None:
    encoded_marker = "%70assword"
    for _ in range(17):
        encoded_marker = encoded_marker.replace("%", "%25")

    with pytest.raises(ModelValidationError, match="nested encoding"):
        workspace_models.canonical_memory_link_url(
            f"https://example.test/{encoded_marker}/abcDEF0123456789"
        )


@pytest.mark.parametrize(
    "path",
    [
        "/password/hunter2",
        "/authorization/Bearer%20x",
        "/code/123456",
        "/sig/x",
        "/signature/short",
        "/cookie/a",
        "/ｐａｓｓｗｏｒｄ/短",
    ],
)
def test_link_url_sensitive_path_marker_rejects_any_nonempty_value(path: str) -> None:
    with pytest.raises(ModelValidationError, match="credentials"):
        workspace_models.canonical_memory_link_url(f"https://example.test{path}")


@pytest.mark.parametrize(
    "url",
    [
        "https://ｈｏｏｋｓ．ｓｌａｃｋ．ｃｏｍ/services/T/B/short",
        "https://ｄｉｓｃｏｒｄ．ｃｏｍ/api/webhooks/1/short",
    ],
)
def test_link_url_normalizes_unicode_webhook_hosts_before_classification(url: str) -> None:
    with pytest.raises(ModelValidationError, match="credentials"):
        workspace_models.canonical_memory_link_url(url)
