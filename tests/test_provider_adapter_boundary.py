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


def test_openai_adapter_lives_in_provider_specific_module_with_legacy_reexports():
    from unchain.providers import OpenAIModelIO
    from unchain.providers.model_io import OpenAIModelIO as LegacyOpenAIModelIO
    from unchain.providers.openai import OpenAIModelIO as ProviderOpenAIModelIO
    from unchain.providers.registry import get_model_adapter_class

    assert ProviderOpenAIModelIO.__module__ == "unchain.providers.openai"
    assert OpenAIModelIO is ProviderOpenAIModelIO
    assert LegacyOpenAIModelIO is ProviderOpenAIModelIO
    assert get_model_adapter_class("openai") is ProviderOpenAIModelIO


def test_anthropic_adapter_lives_in_provider_specific_module_with_legacy_reexports():
    from unchain.providers import AnthropicModelIO, HyperspaceModelIO
    from unchain.providers.anthropic import AnthropicModelIO as ProviderAnthropicModelIO
    from unchain.providers.model_io import AnthropicModelIO as LegacyAnthropicModelIO
    from unchain.providers.registry import get_model_adapter_class

    assert ProviderAnthropicModelIO.__module__ == "unchain.providers.anthropic"
    assert AnthropicModelIO is ProviderAnthropicModelIO
    assert LegacyAnthropicModelIO is ProviderAnthropicModelIO
    assert get_model_adapter_class("anthropic") is ProviderAnthropicModelIO
    assert issubclass(HyperspaceModelIO, ProviderAnthropicModelIO)


def test_native_provider_substrate_lives_in_dedicated_module_with_legacy_reexports():
    from unchain.providers.model_io import _NativeModelIOBase as LegacyNativeModelIOBase
    from unchain.providers.model_io import (
        _translate_content_blocks_for_openai as legacy_translate_for_openai,
    )
    from unchain.providers.native import _NativeModelIOBase, _translate_content_blocks_for_openai
    from unchain.providers.openai import _NativeModelIOBase as OpenAINativeModelIOBase

    assert _NativeModelIOBase.__module__ == "unchain.providers.native"
    assert LegacyNativeModelIOBase is _NativeModelIOBase
    assert OpenAINativeModelIOBase is _NativeModelIOBase
    assert legacy_translate_for_openai is _translate_content_blocks_for_openai


def test_provider_resources_reexport_runtime_model_resource_loaders():
    from unchain.providers import resources
    from unchain.runtime import payloads

    assert resources.load_model_capabilities is payloads.load_model_capabilities
    assert resources.load_default_payloads is payloads.load_default_payloads
    assert "gpt-5" in resources.load_model_capabilities()
