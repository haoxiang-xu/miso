import os

import httpx
import pytest

from miso.runtime import Broth


def _ollama_tags(timeout=2.0):
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _model_available(tags, model):
    for m in tags.get("models", []):
        name = m.get("name") or m.get("model")
        if name == model:
            return True
    return False


def _last_assistant_text(messages):
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return (msg.get("content") or "").strip()
    return ""


def test_ollama_smoke():
    tags = _ollama_tags()
    if tags is None:
        pytest.skip("ollama not running on http://localhost:11434")

    model = os.environ.get("OLLAMA_MODEL", "deepseek-r1:14b")
    if not _model_available(tags, model):
        pytest.skip(f"ollama model not installed: {model}")

    agent = Broth()
    agent.provider = "ollama"
    agent.model = model

    messages = [{"role": "user", "content": "只回复 OK"}]
    messages_out, bundle = agent.run(messages=messages, payload={"num_predict": 32}, verbose=False)

    assert _last_assistant_text(messages_out) != ""
    assert isinstance(bundle.get("consumed_tokens"), int)
