from __future__ import annotations

import json
from types import SimpleNamespace

from unchain.kernel.run_ledger import request_sha256


def _canonical_input_bytes(messages):
    return len(
        json.dumps(
            {"messages": messages},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def test_request_sha256_accepts_large_provider_request_without_runbundle_limit() -> None:
    large_content = "x" * 2_500_000
    messages = [
        {
            "role": "user",
            "content": large_content,
        }
    ]
    state = SimpleNamespace(
        provider_state=SimpleNamespace(previous_response_id="resp-large"),
    )
    toolkit = SimpleNamespace(tools={})

    assert _canonical_input_bytes(messages) > 2 * 1024 * 1024
    first = request_sha256(
        state=state,
        payload={"query": "ping"},
        toolkit=toolkit,
        response_format=None,
        openai_text_format=None,
        provider="openai",
        model="gpt-test",
        messages=messages,
    )

    reversed_order = [{"content": large_content, "role": "user"}]
    second = request_sha256(
        state=state,
        payload={"query": "ping"},
        toolkit=toolkit,
        response_format=None,
        openai_text_format=None,
        provider="openai",
        model="gpt-test",
        messages=reversed_order,
    )

    assert len(first) == 64
    assert first == second
