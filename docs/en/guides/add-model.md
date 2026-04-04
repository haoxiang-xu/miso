# Add a New Model

This guide explains how to register a new LLM model in the unchain framework so it can be used by agents.

## Prerequisites

- Knowledge of the model's capabilities (context window size, tool support, modalities, etc.)
- The model must be served by a provider already supported in unchain (OpenAI, Anthropic, Gemini, Ollama). If not, follow [Add a New Provider](add-provider.md) first.

## Reference Files

| File | Role |
|------|------|
| `src/unchain/runtime/resources/model_capabilities.json` | Model registry with capabilities and constraints |
| `src/unchain/runtime/resources/model_default_payloads.json` | Default request payloads per model |
| `src/unchain/schemas/models.py` | Named model constants |

## Steps

1. **Read the model capabilities registry.** Open `src/unchain/runtime/resources/model_capabilities.json` and study the structure of existing entries to understand the required fields.

2. **Read the default payloads.** Open `src/unchain/runtime/resources/model_default_payloads.json` to see how default request parameters are defined per model.

3. **Add a new entry to `model_capabilities.json`** with the following fields:

   - `provider` -- one of: `openai`, `anthropic`, `gemini`, `ollama`
   - `provider_model` -- the actual API model ID, if it differs from the key name
   - `max_context_window_tokens` -- maximum context window size
   - `supports_tools` -- whether the model supports tool/function calling
   - `supports_response_format` -- whether it supports structured response format
   - `supports_previous_response_id` -- whether it supports response chaining
   - `supports_reasoning` -- whether extended thinking / chain-of-thought is available
   - `input_modalities` -- list of supported input types (e.g., `["text", "image"]`)
   - `input_source_types` -- input source formats (e.g., `["base64", "url"]`)
   - `allowed_payload_keys` -- provider-specific parameters the model accepts

4. **Add a default payload entry** to `model_default_payloads.json` if the model needs custom default parameters (e.g., temperature, max_tokens).

5. **Add a named constant** (optional). If the model should have a convenient alias, add it to `src/unchain/schemas/models.py`.

## Example

Adding a new entry to `model_capabilities.json`:

```json
{
  "my-new-model": {
    "provider": "openai",
    "provider_model": "my-new-model-2025-04",
    "max_context_window_tokens": 128000,
    "supports_tools": true,
    "supports_response_format": true,
    "supports_previous_response_id": false,
    "supports_reasoning": false,
    "input_modalities": ["text", "image"],
    "input_source_types": ["base64", "url"],
    "allowed_payload_keys": ["temperature", "max_tokens", "top_p"]
  }
}
```

## Testing

Run model-related tests:

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "model"
```

## Related

- [Add a New Provider](add-provider.md) -- if the model requires a new provider
- [Runtime Engine](../skills/runtime-engine.md) -- how model capabilities are resolved at runtime
- [Runtime API Reference](../api/runtime.md) -- runtime resource loading
