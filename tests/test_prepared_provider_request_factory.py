from __future__ import annotations

import pytest

from unchain.providers import (
    AnthropicModelIO,
    HyperspaceModelIO,
    OllamaModelIO,
    OpenAIModelIO,
)
from unchain.providers.base import ModelTurnRequest
from unchain.providers.prepared_request_factory import (
    resolve_prepared_provider_request_payload,
)


class _ResponseFormat:
    def to_anthropic(self):
        return "Return strict JSON."

    def to_ollama(self):
        return {"type": "object"}


def test_model_turn_request_preserves_provider_context_mode() -> None:
    request = ModelTurnRequest(
        messages=[{"role": "user", "content": "hello"}],
        context_mode="remote_continuation",
    )

    assert request.context_mode == "remote_continuation"


def test_openai_resolves_effective_payload_format_and_continuation() -> None:
    model_io = OpenAIModelIO(
        model="frontier-model",
        api_key="test-key",
        client_factory=lambda **_kwargs: None,
        default_payloads={"frontier-model": {"store": True, "temperature": 1.0}},
        model_capabilities={
            "frontier-model": {
                "supports_reasoning": True,
                "allowed_payload_keys": ["store", "temperature", "include"],
            }
        },
    )
    request = ModelTurnRequest(
        messages=[{"role": "user", "content": "current delta"}],
        payload={"store": False, "temperature": 0.2},
        openai_text_format={"type": "json_object"},
        previous_response_id="response-previous",
        fallback_messages=[{"role": "user", "content": "complete replay"}],
        context_mode="remote_continuation",
    )

    prepared = resolve_prepared_provider_request_payload(
        model_io=model_io,
        request=request,
    )

    assert prepared["effective_payload"] == {
        "store": False,
        "temperature": 0.2,
        "include": ["reasoning.encrypted_content"],
    }
    assert prepared["response_format"] == {
        "kind": "openai_text",
        "value": {"type": "json_object"},
    }
    assert prepared["request_model"] == "frontier-model"
    assert prepared["context_mode"] == "remote_continuation"
    assert prepared["fallback_messages"] == [
        {"role": "user", "content": "complete replay"}
    ]


@pytest.mark.parametrize(
    ("provider", "adapter_class"),
    [
        ("anthropic", AnthropicModelIO),
        ("hyperspace", HyperspaceModelIO),
    ],
)
def test_anthropic_family_resolves_max_tokens_alias_and_instruction(
    provider,
    adapter_class,
) -> None:
    model_io = adapter_class(
        model="frontier-model",
        api_key="test-key",
        client_factory=lambda **_kwargs: None,
        default_payloads={"frontier-model": {"temperature": 0.8}},
        model_capabilities={
            "frontier-model": {
                "provider_model": "anthropic/claude-frontier",
                "max_output_tokens": 8192,
                "allowed_payload_keys": ["temperature", "max_tokens"],
            }
        },
    )
    request = ModelTurnRequest(
        messages=[{"role": "user", "content": "hello"}],
        payload={"temperature": 0.3},
        response_format=_ResponseFormat(),
        context_mode="semantic",
    )

    prepared = resolve_prepared_provider_request_payload(
        model_io=model_io,
        request=request,
    )

    assert prepared["provider"] == provider
    assert prepared["effective_payload"] == {
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    assert prepared["request_model"] == "anthropic/claude-frontier"
    assert prepared["response_format"] == {
        "kind": "anthropic_instruction",
        "value": "Return strict JSON.",
    }


def test_ollama_resolves_options_and_format() -> None:
    model_io = OllamaModelIO(
        model="frontier-model",
        default_payloads={"frontier-model": {"temperature": 0.8}},
        model_capabilities={
            "frontier-model": {
                "allowed_payload_keys": ["temperature", "num_ctx"],
            }
        },
    )
    request = ModelTurnRequest(
        messages=[{"role": "user", "content": "hello"}],
        payload={"temperature": 0.4, "num_ctx": 8192},
        response_format=_ResponseFormat(),
        context_mode="local_replay",
    )

    prepared = resolve_prepared_provider_request_payload(
        model_io=model_io,
        request=request,
    )

    assert prepared["effective_payload"] == {
        "temperature": 0.4,
        "num_ctx": 8192,
    }
    assert prepared["request_model"] == "frontier-model"
    assert prepared["response_format"] == {
        "kind": "ollama_format",
        "value": {"type": "object"},
    }
    assert prepared["context_mode"] == "local_replay"
