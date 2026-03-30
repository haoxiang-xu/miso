# Add a New LLM Provider

Add support for a new LLM provider to the unchain framework.

## Arguments
- $ARGUMENTS: Provider name and SDK package (e.g. "deepseek deepseek-sdk")

## Steps

1. Read `src/unchain/providers/model_io.py` to understand the provider abstraction:
   - `_NativeModelIOBase` — base class with model capability resolution
   - `OpenAIModelIO`, `AnthropicModelIO`, `OllamaModelIO` — existing implementations
2. Read `src/unchain/agent/model_io.py` for `ModelIOFactoryRegistry`
3. Read `src/unchain/tools/messages.py` for provider-specific message builders

4. Create the new `ModelIO` class in `src/unchain/providers/model_io.py`:
   - Extend `_NativeModelIOBase`
   - Set `provider = "<name>"`
   - Implement `fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult`
   - Handle streaming with token_delta emission
   - Parse tool calls from the provider's format
   - Track input/output tokens

5. Register in `ModelIOFactoryRegistry` (`src/unchain/agent/model_io.py`)

6. Add provider-specific message builder in `src/unchain/tools/messages.py`:
   - `build_tool_result_message(tool_call, tool_result)` — format tool results for this provider

7. Add model entries to `src/miso/runtime/resources/model_capabilities.json`
8. Add default payloads to `src/miso/runtime/resources/model_default_payloads.json`

9. If the provider also needs support in the legacy Broth engine:
   - Add `_<provider>_fetch_once()` method to `src/miso/runtime/engine.py`
   - Add dispatch in `_fetch_once()` method

10. Add SDK to `pyproject.toml` dependencies
11. Write smoke test in `tests/test_<provider>_smoke.py` using fake client pattern
12. Run: `PYTHONPATH=src pytest tests/ -q --tb=short`
