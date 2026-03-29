from __future__ import annotations

import os
from typing import Any, Callable

from ..model_io import AnthropicModelIO, ModelIO, OllamaModelIO, OpenAIModelIO


class ModelIOFactoryRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., ModelIO]] = {
            "openai": self._create_openai,
            "anthropic": self._create_anthropic,
            "ollama": self._create_ollama,
        }

    def register(self, provider: str, factory: Callable[..., ModelIO]) -> None:
        normalized = str(provider or "").strip().lower()
        if not normalized:
            raise ValueError("provider is required")
        self._factories[normalized] = factory

    def create(
        self,
        *,
        provider: str,
        model: str,
        api_key: str | None,
    ) -> ModelIO:
        normalized = str(provider or "").strip().lower()
        factory = self._factories.get(normalized)
        if factory is None:
            raise NotImplementedError(f"no model io factory registered for provider={provider!r}")
        return factory(model=model, api_key=api_key)

    def _create_openai(self, *, model: str, api_key: str | None) -> ModelIO:
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        return OpenAIModelIO(model=model, api_key=resolved_api_key or "")

    def _create_anthropic(self, *, model: str, api_key: str | None) -> ModelIO:
        resolved_api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        return AnthropicModelIO(model=model, api_key=resolved_api_key or "")

    def _create_ollama(self, *, model: str, api_key: str | None) -> ModelIO:
        del api_key
        return OllamaModelIO(
            model=model,
            base_url=str(os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434"),
        )
