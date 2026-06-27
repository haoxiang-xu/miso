from __future__ import annotations


def test_provider_base_is_the_single_model_adapter_contract():
    from unchain import kernel, providers
    from unchain.providers import base
    from unchain.providers import model_io as legacy_model_io

    assert base.ModelIO is kernel.ModelIO
    assert base.ModelAdapter is kernel.ModelAdapter
    assert base.ModelTurnRequest is kernel.ModelTurnRequest
    assert providers.ModelIO is base.ModelIO
    assert providers.ModelAdapter is base.ModelAdapter
    assert legacy_model_io.ModelIO is base.ModelIO
    assert legacy_model_io.ModelTurnRequest is base.ModelTurnRequest


def test_provider_registry_exposes_default_adapter_classes_and_custom_registration():
    from unchain.providers import AnthropicModelIO, HyperspaceModelIO, OllamaModelIO, OpenAIModelIO
    from unchain.providers.registry import ProviderAdapterRegistry, get_model_adapter_class

    assert get_model_adapter_class("openai") is OpenAIModelIO
    assert get_model_adapter_class("anthropic") is AnthropicModelIO
    assert get_model_adapter_class("ollama") is OllamaModelIO
    assert get_model_adapter_class("hyperspace") is HyperspaceModelIO

    class CustomAdapter:
        provider = "custom"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    registry = ProviderAdapterRegistry()
    registry.register("custom", CustomAdapter)
    adapter = registry.create("custom", model="demo-model", api_key="test-key")

    assert registry.get("custom") is CustomAdapter
    assert adapter.kwargs == {"model": "demo-model", "api_key": "test-key"}


def test_provider_resources_reexport_runtime_model_resource_loaders():
    from unchain.providers import resources
    from unchain.runtime import payloads

    assert resources.load_model_capabilities is payloads.load_model_capabilities
    assert resources.load_default_payloads is payloads.load_default_payloads
    assert "gpt-5" in resources.load_model_capabilities()
