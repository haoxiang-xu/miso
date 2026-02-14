import os

import pytest

from miso import LLM_agent


def _last_assistant_text(messages):
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return (msg.get("content") or "").strip()
    return ""


def test_openai_smoke():
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL")
    if not api_key or not model:
        pytest.skip("OPENAI_API_KEY or OPENAI_MODEL not set")

    agent = LLM_agent()
    agent.provider = "openai"
    agent.openai_api_key = api_key
    agent.openai_base_url = os.environ.get("OPENAI_BASE_URL")
    agent.model = model

    messages = [{"role": "user", "content": "Reply with OK only."}]
    result = agent.run(messages=messages, payload={"max_output_tokens": 32}, max_iterations=1)

    assert _last_assistant_text(result) != ""


def test_openai_compatible_smoke():
    base_url = os.environ.get("OPENAI_COMPAT_BASE_URL")
    api_key = os.environ.get("OPENAI_COMPAT_API_KEY")
    model = os.environ.get("OPENAI_COMPAT_MODEL")
    if not base_url or not api_key or not model:
        pytest.skip("OPENAI_COMPAT_BASE_URL / OPENAI_COMPAT_API_KEY / OPENAI_COMPAT_MODEL not set")

    agent = LLM_agent()
    agent.provider = "openai"
    agent.openai_api_key = api_key
    agent.openai_base_url = base_url
    agent.model = model

    messages = [{"role": "user", "content": "Reply with OK only."}]
    result = agent.run(messages=messages, payload={"max_output_tokens": 32}, max_iterations=1)

    assert _last_assistant_text(result) != ""
