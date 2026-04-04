# Add a New LLM Provider

This guide walks you through adding support for a new LLM provider to the unchain framework. A provider is the SDK-level integration that allows unchain agents to communicate with a specific LLM service.

## Prerequisites

- The provider's Python SDK installed and accessible
- Understanding of the provider's streaming API and tool-calling format
- Familiarity with the `ModelIO` abstraction (see [Architecture Overview](../skills/architecture-overview.md))

## Reference Files

| File | Role |
|------|------|
| `src/unchain/providers/model_io.py` | `_NativeModelIOBase` and existing provider implementations (OpenAI, Anthropic, Ollama) |
| `src/unchain/agent/model_io.py` | `ModelIOFactoryRegistry` -- maps provider names to `ModelIO` factories |
| `src/unchain/tools/messages.py` | Provider-specific message builders for tool results |
| `src/unchain/runtime/resources/model_capabilities.json` | Model registry |
| `src/unchain/runtime/resources/model_default_payloads.json` | Default payloads per model |
| `pyproject.toml` | Project dependencies |

## Steps

1. **Study the provider abstraction.** Read `src/unchain/providers/model_io.py` to understand:
   - `_NativeModelIOBase` -- base class with model capability resolution
   - Existing implementations: `OpenAIModelIO`, `AnthropicModelIO`, `OllamaModelIO`

2. **Study the registry.** Read `src/unchain/agent/model_io.py` to see how `ModelIOFactoryRegistry` maps provider names to factory functions.

3. **Study message builders.** Read `src/unchain/tools/messages.py` to see how tool results are formatted for each provider.

4. **Create the new `ModelIO` class** in `src/unchain/providers/model_io.py`:
   - Extend `_NativeModelIOBase`
   - Set `provider = "<provider_name>"`
   - Implement `fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult`
   - Handle streaming with `token_delta` emission
   - Parse tool calls from the provider's response format
   - Track input/output token usage

5. **Register in `ModelIOFactoryRegistry`** in `src/unchain/agent/model_io.py` so that the provider name is resolved to your new class.

6. **Add a provider-specific message builder** in `src/unchain/tools/messages.py`:
   - Implement `build_tool_result_message(tool_call, tool_result)` to format tool results in the provider's expected format.

7. **Add model entries** to `src/unchain/runtime/resources/model_capabilities.json` for the models served by this provider.

8. **Add default payloads** to `src/unchain/runtime/resources/model_default_payloads.json`.

9. **Add the SDK dependency** to `pyproject.toml`.

10. **Write a smoke test** in `tests/test_<provider>_smoke.py` using the fake client pattern (see `FakeOpenAIClient` / `FakeAnthropicClient` in the existing tests for reference).

## Template

```python
# In src/unchain/providers/model_io.py

class MyProviderModelIO(_NativeModelIOBase):
    provider = "my_provider"

    async def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult:
        # 1. Build the provider-specific request from request.messages + request.tools
        # 2. Call the provider SDK (streaming)
        # 3. Yield token_delta events for each streamed chunk
        # 4. Parse tool_calls from the response
        # 5. Return ModelTurnResult with messages, tool_calls, and token counts
        ...
```

## Testing

Run the smoke test:

```bash
PYTHONPATH=src pytest tests/ -q --tb=short
```

## Related

- [Add a New Model](add-model.md) -- register models after adding the provider
- [Architecture Overview](../skills/architecture-overview.md) -- how providers fit into the execution flow
- [Tools API Reference](../api/tools.md) -- tool message format details
