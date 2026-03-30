# Add a New Model

Add a new LLM model to the unchain/miso framework.

## Arguments
- $ARGUMENTS: Model name and provider info (e.g. "claude-opus-4 anthropic" or "gpt-4.1-mini openai")

## Steps

1. Read `src/miso/runtime/resources/model_capabilities.json` to understand existing model entries
2. Read `src/miso/runtime/resources/model_default_payloads.json` for default payload patterns
3. Parse the model name and provider from $ARGUMENTS
4. Add a new entry to `model_capabilities.json` with appropriate:
   - `provider` (openai, anthropic, gemini, ollama)
   - `provider_model` (actual API model ID, if different from the key name)
   - `max_context_window_tokens`
   - `supports_tools`, `supports_response_format`, `supports_previous_response_id`, `supports_reasoning`
   - `input_modalities` and `input_source_types`
   - `allowed_payload_keys` (provider-specific parameters)
5. Add default payload entry to `model_default_payloads.json` if needed
6. Check if `src/miso/schemas/models.py` needs a named constant
7. Run tests: `PYTHONPATH=src pytest tests/ -q -k "model" --tb=short`
8. Show the user what was added
