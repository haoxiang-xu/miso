"""Provider dispatch tests for build_observation_payload."""

from unchain.tools.observation import (
    OBSERVATION_MAX_OUTPUT_TOKENS,
    build_observation_payload,
)


def test_anthropic_uses_max_tokens():
    payload = build_observation_payload({"max_output_tokens": 9}, provider="anthropic")
    assert payload["max_tokens"] == OBSERVATION_MAX_OUTPUT_TOKENS
    assert "max_output_tokens" not in payload
    assert "num_predict" not in payload


def test_hyperspace_uses_anthropic_wire_params():
    payload = build_observation_payload({"max_output_tokens": 9}, provider="hyperspace")
    assert payload["max_tokens"] == OBSERVATION_MAX_OUTPUT_TOKENS
    assert "max_output_tokens" not in payload
    assert "num_predict" not in payload


def test_ollama_uses_num_predict():
    payload = build_observation_payload({}, provider="ollama")
    assert payload["num_predict"] == OBSERVATION_MAX_OUTPUT_TOKENS
    assert "max_tokens" not in payload


def test_openai_uses_max_output_tokens():
    payload = build_observation_payload({"max_tokens": 9}, provider="openai")
    assert payload["max_output_tokens"] == OBSERVATION_MAX_OUTPUT_TOKENS
    assert "max_tokens" not in payload
