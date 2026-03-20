import os

import pytest

from miso.runtime import Broth
from miso.runtime import ProviderTurnResult


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

    agent = Broth()
    agent.provider = "openai"
    agent.api_key = api_key
    agent.model = model

    messages = [{"role": "user", "content": "Reply with OK only."}]
    messages_out, bundle = agent.run(messages=messages, payload={"max_output_tokens": 32}, max_iterations=1)

    assert _last_assistant_text(messages_out) != ""
    assert isinstance(bundle.get("consumed_tokens"), int)


def test_openai_run_multimodal_projection_unit():
    agent = Broth()
    agent.provider = "openai"
    agent.model = "gpt-5"

    captured_messages = []

    def fake_fetch_once(**kwargs):
        captured_messages.append(kwargs.get("messages"))
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "ok"}],
            tool_calls=[],
            final_text="ok",
            response_id="resp_mm_openai",
        )

    agent._fetch_once = fake_fetch_once
    agent.run(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "summarize"},
                    {"type": "image", "source": {"type": "url", "url": "https://example.com/a.jpg"}},
                    {
                        "type": "pdf",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": "JVBERi0xLjQK",
                        },
                    },
                ],
            }
        ],
        max_iterations=1,
    )

    assert len(captured_messages) == 1
    content = captured_messages[0][0]["content"]
    assert [b["type"] for b in content] == ["input_text", "input_image", "input_file"]
