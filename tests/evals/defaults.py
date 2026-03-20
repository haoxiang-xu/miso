from __future__ import annotations

from .types import ModelSpec


def get_default_judge_model_spec() -> ModelSpec:
    return ModelSpec(
        provider="anthropic",
        model="claude-opus-4-6",
        label="claude-opus-4-6-judge",
        api_key_env="ANTHROPIC_API_KEY",
    )


DEFAULT_JUDGE_MODEL_SPEC = get_default_judge_model_spec()


__all__ = ["DEFAULT_JUDGE_MODEL_SPEC", "get_default_judge_model_spec"]
