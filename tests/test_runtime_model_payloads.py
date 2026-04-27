from unchain.runtime.payloads import load_default_payloads, load_model_capabilities


def test_gpt_55_runtime_resources_are_registered():
    capabilities = load_model_capabilities()
    payloads = load_default_payloads()

    assert capabilities["gpt-5.5"]["provider"] == "openai"
    assert capabilities["gpt-5.5"]["supports_tools"] is True
    assert capabilities["gpt-5.5"]["supports_response_format"] is True
    assert capabilities["gpt-5.5"]["supports_previous_response_id"] is True
    assert capabilities["gpt-5.5"]["supports_reasoning"] is True
    assert "reasoning" in capabilities["gpt-5.5"]["allowed_payload_keys"]
    assert payloads["gpt-5.5"]["max_output_tokens"] == 128000
    assert payloads["gpt-5.5"]["reasoning"]["effort"] == "medium"


def test_claude_haiku_35_runtime_resources_are_removed():
    capabilities = load_model_capabilities()
    payloads = load_default_payloads()

    assert "claude-haiku-3.5" not in capabilities
    assert "claude-haiku-3.5" not in payloads
