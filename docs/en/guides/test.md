# Run Tests

This guide covers how to run the unchain test suite, filter tests, and handle failures.

## Prerequisites

- Python 3.12+ installed
- Project dependencies installed
- Source tree available at `src/`

## Reference Files

| File / Directory | Role |
|-----------------|------|
| `tests/` | Test directory |
| `tests/test_kernel_core.py` | Kernel loop and harness tests |
| `pyproject.toml` | Test dependencies and pytest config |

## Running All Tests

Run the full test suite, excluding known flaky tests:

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "not read_file_ast and not relocate_non_python_ranges"
```

The two excluded tests are known to be flaky:
- `test_read_file_ast_parses_python_file`
- `test_pinned_prompt_messages_relocate_non_python_ranges_via_declaration_metadata`

## Running Filtered Tests

To run tests matching a specific keyword pattern:

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "<pattern>"
```

Common filter patterns:

| Pattern | What it runs |
|---------|-------------|
| `kernel` | Kernel loop and harness tests |
| `model` | Model capability and registry tests |
| `memory` | Memory system tests |
| `toolkit` | Toolkit tests |
| `anthropic` | Anthropic provider tests |
| `openai` | OpenAI provider tests |

## Handling Failures

When tests fail:

1. **Read the failing test file** to understand what is being asserted.
2. **Identify the root cause** -- is it a code change that broke the test, or a test that needs updating?
3. **Fix the issue** in either the source or the test.
4. **Re-run the specific failing test** to confirm the fix:
   ```bash
   PYTHONPATH=src pytest tests/<test_file>.py -v --tb=long -k "<test_name>"
   ```

## Test Patterns

The project uses fake clients for provider tests (`FakeOpenAIClient`, `FakeAnthropicClient`). These must accept `**kwargs` in their `__init__` to remain compatible with the real SDK signatures.

An evals framework is available in `tests/evals/` for more comprehensive evaluation-style tests.

## Related

- [Testing Conventions](../skills/testing-conventions.md) -- full testing conventions and patterns
- [Glossary](../appendix/glossary.md) -- terminology reference
