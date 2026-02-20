import os

import pytest

from miso import agent as Agent


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

    agent = Agent()
    agent.provider = "openai"
    agent.api_key = api_key
    agent.model = model

    messages = [{"role": "user", "content": "Reply with OK only."}]
    messages_out, bundle = agent.run(messages=messages, payload={"max_output_tokens": 32}, max_iterations=1)

    assert _last_assistant_text(messages_out) != ""
    assert isinstance(bundle.get("consumed_tokens"), int)
