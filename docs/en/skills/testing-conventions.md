# Testing Conventions

Canonical English skill chapter for the `testing-conventions` topic.

## Role and boundaries

This chapter explains how the repository tests orchestration, tool execution, memory, registry behavior, and eval fixtures without relying on live providers.

## Dependency view

- Unit tests largely monkeypatch provider fetches or use fake adapters.
- Toolkit discovery tests use temporary manifest packages.
- Notebook/eval fixtures live beside but outside the main unit-test tree.

## Core objects

- `ProviderTurnResult`
- `ToolCall`
- `MemoryManager` fakes
- `ToolkitRegistry` fixtures
- `EvalCase`/`JudgeReport` in `tests/evals/`

## Execution and state flow

- Patch provider fetches to simulate tool turns.
- Use fake stores/adapters for deterministic memory behavior.
- Run eval fixtures separately from unit tests.
- Validate new toolkit manifests and safety constraints with temporary packages and tmp paths.

## Configuration surface

- Python 3.12 virtualenv and editable install.
- pytest discovery settings in `pyproject.toml`.
- Specific `pytest -k` filters or single-file runs for focused debugging.

## Extension points

- Add new fake adapters instead of calling live services.
- Add eval cases under `tests/evals/` for end-to-end behavioral checks.
- Keep test helper naming and fixture layout consistent.

## Common gotchas

- `tests/evals/fixtures` is intentionally excluded from pytest discovery.
- State dictionaries are frequently used to model multi-turn provider behavior.
- Callback assertions are often the only reliable way to verify event ordering.

## Related class references

- [Runtime API](../api/runtime.md)
- [Memory API](../api/memory.md)
- [Tool System API](../api/tools.md)

## Source entry points

- `tests/`
- `tests/evals/`
- `pyproject.toml`

## Detailed legacy reference

The original repository skill note is preserved below for continuity and extra examples. The canonical copy now lives in this docs tree.

> Test organization, naming, mock patterns, agent testing without LLM, the eval framework, and how to run tests.

## Running Tests

```bash
# One-time setup
./scripts/init_python312_venv.sh   # Creates .venv with Python 3.12
pip install -e ".[dev]"            # Editable install with dev deps

# Run all tests
./run_tests.sh

# Run specific test
python -m pytest tests/test_agent_core.py -v

# Run tests matching a pattern
python -m pytest -k "test_tool_parameter" -v
```

`run_tests.sh` validates Python 3.12, pytest, and that unchain is installed before running.

### pytest Configuration

From `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
norecursedirs = [".git", ".pytest_cache", ".venv", "tests/evals/fixtures"]
```

Note: `tests/evals/fixtures/` is excluded — those are workspace fixtures for eval cases, not tests.

## File & Function Naming

| Convention      | Pattern                          | Example                                       |
| --------------- | -------------------------------- | --------------------------------------------- |
| Test file       | `test_<feature>.py`              | `test_agent_core.py`, `test_memory.py`        |
| Test function   | `test_<what_it_tests>()`         | `test_tool_parameter_inference_and_execute()` |
| Helper function | `_<name>()` (leading underscore) | `_tool_turn()`, `_final_turn()`               |
| Fake class      | `_Fake<Interface>`               | `_FakeVectorAdapter`, `_FakeProfileStore`     |

All test files are in the top-level `tests/` directory (flat structure, no subdirectories for unit tests).

## Mock Patterns

### Pattern 1: Mock `_fetch_once` for Agent Testing

The most common pattern — test agent logic without making LLM calls:

```python
from unchain.runtime import ProviderTurnResult, ToolCall

def _tool_turn(tool_name, arguments):
    """Simulate an LLM turn that calls a tool."""
    return ProviderTurnResult(
        assistant_message={"role": "assistant", "content": None},
        tool_calls=[ToolCall(id="call_1", name=tool_name, arguments=arguments)],
        token_usage={"prompt_tokens": 100, "completion_tokens": 50},
    )

def _final_turn(content):
    """Simulate an LLM turn that returns a final answer."""
    return ProviderTurnResult(
        assistant_message={"role": "assistant", "content": content},
        tool_calls=[],
        token_usage={"prompt_tokens": 100, "completion_tokens": 50},
    )

def test_agent_calls_tool(monkeypatch):
    agent = Agent(name="test", provider="openai", model="gpt-5", tools=[...])

    state = {"turn": 0}
    def fake_fetch(messages, tools, **kwargs):
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("read_files", {"paths": ["main.py"]})
        return _final_turn("Done reading the file.")

    monkeypatch.setattr(agent, "_fetch_once", fake_fetch)
    messages, bundle = agent.run("Read main.py")
    assert "Done reading" in messages[-1]["content"]
```

### Pattern 2: Fake Adapters for Memory Testing

```python
class _FakeVectorAdapter:
    def __init__(self):
        self.added = []         # Track what was indexed
        self.searches = []      # Track what was searched

    def add(self, texts, metadatas, namespace):
        self.added.append({"texts": texts, "metadatas": metadatas, "namespace": namespace})

    def search(self, query, top_k, namespace):
        self.searches.append({"query": query, "top_k": top_k})
        return []  # Return empty for test

class _FakeLongTermProfileStore:
    def __init__(self):
        self.profiles = {}

    def load(self, namespace):
        return self.profiles.get(namespace, {})

    def save(self, namespace, profile):
        self.profiles[namespace] = profile
```

### Pattern 3: State Dict for Multi-Turn Sequencing

```python
def test_multi_turn_flow(monkeypatch):
    state = {"turn": 0}

    def fake_fetch(messages, tools, **kwargs):
        state["turn"] += 1
        if state["turn"] == 1:
            return _tool_turn("list_directories", {"paths": ["."]})
        elif state["turn"] == 2:
            return _tool_turn("read_files", {"paths": ["README.md"]})
        else:
            return _final_turn("Here is the summary...")

    # ... test code ...
```

### Pattern 4: Event Callback Assertions

```python
def test_events_emitted(monkeypatch):
    events = []

    def capture(event):
        events.append(event)

    messages, bundle = agent.run("task", callback=capture)

    event_types = [e["type"] for e in events]
    assert "run_started" in event_types
    assert event_types.count("tool_result") == 2
    assert "run_completed" in event_types
```

### Pattern 5: Filesystem Setup with `tmp_path`

```python
def test_workspace_tool(tmp_path):
    # Create test files
    (tmp_path / "hello.txt").write_text("world")
    (tmp_path / "subdir").mkdir()

    tk = CodeToolkit(workspace_root=str(tmp_path))
    result = tk.execute("read_files", {"paths": ["hello.txt"]})
    assert result["files"][0]["content"] == "world"
```

### Pattern 6: Toolkit Discovery with Temp Packages

```python
def _write_toolkit_package(root, toolkit_id, tools):
    """Helper: create a minimal toolkit package for discovery tests."""
    pkg_dir = root / toolkit_id
    pkg_dir.mkdir()

    # Write toolkit.toml
    toml_content = f"""
[toolkit]
id = "{toolkit_id}"
name = "Test {toolkit_id}"
description = "Test toolkit"
factory = "test_pkg.{toolkit_id}:Factory"
readme = "README.md"
"""
    for tool_name in tools:
        toml_content += f"""
[[tools]]
name = "{tool_name}"
description = "Test tool"
"""
    (pkg_dir / "toolkit.toml").write_text(toml_content)
    (pkg_dir / "README.md").write_text("# Test")
    # ... write factory module ...
```

## What to Test for Each Component

### New Builtin Toolkit

- [ ] All tools registered correctly (names match manifest)
- [ ] Parameter inference produces correct JSON schema
- [ ] Each tool returns expected dict shape on success
- [ ] Each tool returns `{"error": ...}` on failure
- [ ] Path safety: traversal attempts raise `ValueError`
- [ ] `shutdown()` cleans up resources
- [ ] History optimizers reduce payload size

### New Tool

- [ ] Parameter types inferred correctly from signature
- [ ] Docstring description extracted
- [ ] Required vs optional parameters correct
- [ ] `observe` and `requires_confirmation` flags propagated
- [ ] `execute()` handles edge cases (empty args, wrong types)

### Memory Configuration

- [ ] MemoryConfig validates field ranges
- [ ] Context strategy selects correct messages
- [ ] Session store persists/loads correctly
- [ ] Tool history compaction shrinks payloads
- [ ] Namespace isolation works across agents

### Agent Integration

- [ ] Tools are merged correctly from mixed inputs
- [ ] Memory config is converted from dict to dataclass
- [ ] Callbacks receive expected events in order
- [ ] Suspension and resumption preserves state
- [ ] Catalog state tokens survive across suspensions

## Eval Framework (`tests/evals/`)

The eval framework benchmarks agent performance with LLM-based judging.

### Core Types

```python
from tests.evals.types import EvalCase, RunArtifact, JudgeReport

case = EvalCase(
    task_id="file_inspection",
    prompt="Read main.py and explain it.",
    workspace_config={"root": "./fixtures/simple_repo"},
    allowed_toolkits=["workspace", "terminal"],
    rules={
        "required_paths": ["main.py"],
        "required_tool_names": ["read_files"],
        "forbidden_tool_names": ["write_file", "delete_lines"],
        "min_tool_calls": 1,
        "min_final_chars": 100,
    },
    rubric_weights={
        "correctness": 40,
        "debugging": 25,
        "tool_strategy": 20,
        "efficiency": 15,
    },
)
```

### Rule-Based Checks

| Rule                       | What It Checks                             |
| -------------------------- | ------------------------------------------ |
| `required_paths`           | Agent must reference these file paths      |
| `required_substrings`      | Final answer must contain these strings    |
| `required_regexes`         | Final answer must match these patterns     |
| `required_tool_names`      | These tools must be called                 |
| `required_tool_any_of`     | At least one of these tools must be called |
| `forbidden_tool_names`     | These tools must NOT be called             |
| `min_tool_calls`           | Minimum number of tool calls               |
| `min_final_chars`          | Minimum length of final answer             |
| `min_file_reference_count` | Minimum file references in answer          |

### Workflow

```text
1. Define EvalCase with task, workspace, rules, rubric
2. Runner builds agent with case config
3. Agent executes, collecting messages + events
4. RunArtifact created with execution metadata + rule scores
5. Judge agent (LLM) evaluates using rubric weights
6. JudgeReport returned with scores + recommendations
```

### Notebook-Based Evals

Eval cases can also be run as Jupyter notebooks:

```text
tests/evals/
├── notebooks/           # Executable notebooks
│   └── tetris_beginner_game_test/
├── templates/
│   └── single_test_template.ipynb    # Copy to create new evals
├── fixtures/            # Workspace fixtures for eval cases
│   ├── fixture_debug/
│   └── multi_file_plan/
└── artifacts/           # Output from eval runs
    └── <test_id>/<timestamp>/
```

To create a new eval: copy `single_test_template.ipynb` into `notebooks/`, then configure `MODEL_SPEC`, `EVAL_RULES`, `TASK_PROMPT`, `WORKSPACE_CONFIG`, and `TOOLKIT_CONFIG` in the notebook cells.

## Common Test Gotchas

1. **Don't forget `monkeypatch`** — Agent tests should mock `_fetch_once`, not make real API calls. Real calls go to smoke tests (`test_*_smoke.py`).

2. **`tmp_path` for filesystem tests** — Always use pytest's `tmp_path` fixture, never the real workspace.

3. **State dict for turn sequencing** — Use `state = {"turn": 0}` and increment in the mock to control multi-turn behavior.

4. **Eval fixtures are excluded from pytest** — `norecursedirs` skips `tests/evals/fixtures/`. Don't put test files there.

5. **Smoke tests need API keys** — Files like `test_openai_family_smoke.py` and `test_anthropic_smoke.py` require real API keys. They should be skipped in CI unless keys are configured.

6. **Toolkit manifest validation runs during discovery** — If your test creates a toolkit with a wrong manifest, the error happens at discovery time, not execution time.

## Related Skills

- [creating-builtin-toolkits.md](creating-builtin-toolkits.md) — What to test when adding a toolkit
- [tool-system-patterns.md](tool-system-patterns.md) — Tool parameter inference to verify
- [memory-system.md](memory-system.md) — Memory adapter fake patterns
- [agent-and-team.md](agent-and-team.md) — Agent integration test patterns
