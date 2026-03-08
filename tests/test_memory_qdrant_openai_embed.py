import sys
import types

import pytest

from miso.memory_qdrant import build_openai_embed_fn


class _FakeEmbeddingsAPI:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        data = [
            types.SimpleNamespace(embedding=[float(i), float(i) + 0.5])
            for i, _ in enumerate(kwargs.get("input", []), start=1)
        ]
        return types.SimpleNamespace(data=data)


class _FakeOpenAI:
    instances = []

    def __init__(self, api_key):
        self.api_key = api_key
        self.embeddings = _FakeEmbeddingsAPI()
        type(self).instances.append(self)


@pytest.fixture
def fake_openai_module(monkeypatch):
    _FakeOpenAI.instances.clear()
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    return _FakeOpenAI


def test_build_openai_embed_fn_uses_broth_api_key_and_allowed_payload(monkeypatch, fake_openai_module):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    broth_instance = types.SimpleNamespace(api_key="broth-key")

    embed_fn, vector_size = build_openai_embed_fn(
        model="text-embedding-3-large",
        broth_instance=broth_instance,
        payload={
            "dimensions": 1024,
            "encoding_format": "base64",
            "not_allowed": "ignored",
        },
    )

    assert vector_size == 1024
    vectors = embed_fn(["alpha", "beta"])

    client = fake_openai_module.instances[-1]
    assert client.api_key == "broth-key"

    call = client.embeddings.calls[-1]
    assert call["model"] == "text-embedding-3-large"
    assert call["input"] == ["alpha", "beta"]
    assert call["dimensions"] == 1024
    assert call["encoding_format"] == "base64"
    assert "not_allowed" not in call
    assert vectors == [[1.0, 1.5], [2.0, 2.5]]


def test_build_openai_embed_fn_falls_back_to_openai_env_key(monkeypatch, fake_openai_module):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    broth_instance = types.SimpleNamespace(api_key="   ")

    embed_fn, vector_size = build_openai_embed_fn(
        model="text-embedding-3-small",
        broth_instance=broth_instance,
    )

    assert vector_size == 1536
    embed_fn(["hello"])
    assert fake_openai_module.instances[-1].api_key == "env-key"


def test_build_openai_embed_fn_raises_when_api_key_missing(monkeypatch, fake_openai_module):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    broth_instance = types.SimpleNamespace(api_key="")

    with pytest.raises(ValueError, match="broth.api_key or OPENAI_API_KEY"):
        build_openai_embed_fn(
            model="text-embedding-3-small",
            broth_instance=broth_instance,
        )


def test_build_openai_embed_fn_rejects_dimensions_for_ada_002(monkeypatch, fake_openai_module):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    broth_instance = types.SimpleNamespace(api_key="")

    with pytest.raises(ValueError, match="does not support dimensions"):
        build_openai_embed_fn(
            model="text-embedding-ada-002",
            broth_instance=broth_instance,
            payload={"dimensions": 1024},
        )


def test_build_openai_embed_fn_rejects_unknown_model(monkeypatch, fake_openai_module):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    with pytest.raises(ValueError, match="is not configured"):
        build_openai_embed_fn(
            model="text-embedding-unknown",
            broth_instance=None,
        )
