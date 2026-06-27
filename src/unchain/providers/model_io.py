"""Legacy compatibility shim for provider model adapters.

Provider implementations live in provider-specific modules such as
``unchain.providers.openai``, ``unchain.providers.anthropic``, and
``unchain.providers.ollama``. This module keeps older imports working and
should not define provider classes directly.
"""

from __future__ import annotations

from .base import ModelAdapter, ModelIO, ModelTurnRequest
from .native import (
    _NativeModelIOBase,
    _translate_content_blocks_for_anthropic,
    _translate_content_blocks_for_openai,
)


def __getattr__(name: str):
    if name == "AnthropicModelIO":
        from .anthropic import AnthropicModelIO

        return AnthropicModelIO
    if name == "OpenAIModelIO":
        from .openai import OpenAIModelIO

        return OpenAIModelIO
    if name == "OllamaModelIO":
        from .ollama import OllamaModelIO

        return OllamaModelIO
    raise AttributeError(name)


__all__ = [
    "AnthropicModelIO",
    "ModelAdapter",
    "ModelIO",
    "ModelTurnRequest",
    "OllamaModelIO",
    "OpenAIModelIO",
]
