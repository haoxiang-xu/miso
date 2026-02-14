<div align="center">
  <img src="assets/miso_logo.png" alt="miso logo" width="160" />
  <h1>miso</h1>
  <p>A lightweight Python Agent Builder for OpenAI and Ollama.</p>
</div>

---

## <h1>Table of Contents</h1>

- [What is miso](#what-is-miso)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Core Usage](#core-usage)
- [Structured Output](#structured-output)
- [Event Callback](#event-callback)
- [Builtin Toolkit](#builtin-toolkit)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Roadmap Notes](#roadmap-notes)

---

## <h1>What is miso</h1> <a id="what-is-miso"></a>

`miso` is a compact agent framework focused on:

- Multi-step tool calling loops (`agent.run`)
- OpenAI Responses API
- Local Ollama chat support
- Custom tool registration (`tool`, `toolkit`, `@tool_decorator`)
- Predefined workspace tools (read/write/search files, optional isolated Python runtime)
- JSON-schema response formatting (`response_format`)

If your goal is "agent builder but not heavy framework", this repo is exactly that.

---

## <h1>Prerequisites</h1> <a id="prerequisites"></a>

- Python 3.9+ (recommend 3.11)
- `pip`
- Optional:
  - OpenAI API key (for `provider="openai"`)
  - Ollama running locally at `http://localhost:11434` (for `provider="ollama"`)

---

## <h1>Quick Start</h1> <a id="quick-start"></a>

```bash
# 1) create and activate virtual env
python3 -m venv venv
source venv/bin/activate

# 2) install deps
pip install -r requirements.txt

# 3) run tests
./run_tests.sh
```

Default payloads are loaded from:

- `miso/model_default_payloads.json`
- `miso/model_capabilities.json`

Merge rule:

- Start from model default payload
- Only keys that already exist in model defaults can be overridden by user payload
- User payload keys not present in model defaults are ignored
- Then payload is filtered by `allowed_payload_keys` from model capabilities

For GPT-5 / GPT-5-Codex models, defaults include `reasoning`, `include`, and `store`,
so you can override those keys from user payload.

---

## <h1>Core Usage</h1> <a id="core-usage"></a>

### 1) OpenAI provider

```python
from miso import agent as Agent

agent = Agent()
agent.provider = "openai"
agent.model = "gpt-4.1"
agent.openai_api_key = "YOUR_OPENAI_API_KEY"

messages = [{"role": "user", "content": "Reply with OK only."}]
messages_out, bundle = agent.run(messages=messages, payload={"max_output_tokens": 32}, max_iterations=1)

last_assistant = [m for m in messages_out if m.get("role") == "assistant"][-1]
print(last_assistant["content"])
print(bundle["consumed_tokens"])
```

### 2) Ollama provider

```python
from miso import agent as Agent

agent = Agent()
agent.provider = "ollama"
agent.model = "deepseek-r1:14b"

messages = [{"role": "user", "content": "只回复 OK"}]
messages_out, bundle = agent.run(messages=messages, payload={"num_predict": 32}, max_iterations=1)
print([m for m in messages_out if m.get("role") == "assistant"][-1]["content"])
print(bundle["consumed_tokens"])
```

### 3) Register your own tools

```python
from miso import agent as Agent, toolkit as Toolkit

def add(a: int, b: int = 2):
    return a + b

agent = Agent()
agent.provider = "openai"
agent.openai_api_key = "YOUR_OPENAI_API_KEY"

tk = Toolkit()
tk.register(add, observe=True)  # observe=True enables review pass after tool execution
agent.toolkit = tk
```

### 4) GPT-5 reasoning + previous response chaining

```python
from miso import agent as Agent

agent = Agent()
agent.provider = "openai"
agent.model = "gpt-5"
agent.openai_api_key = "YOUR_OPENAI_API_KEY"

messages_out, bundle = agent.run(
    messages=[{"role": "user", "content": "Analyze and answer briefly."}],
    payload={
        "reasoning": {"effort": "medium"},
        "include": ["reasoning.encrypted_content"],
        "store": False,
    },
    max_iterations=1,
)

print("last response id:", agent.last_response_id)
print("reasoning blocks:", len(agent.last_reasoning_items))
print("consumed_tokens:", bundle["consumed_tokens"])
```

---

## <h1>Structured Output</h1> <a id="structured-output"></a>

```python
from miso import agent as Agent, response_format

fmt = response_format(
    name="answer_format",
    schema={
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
        },
        "required": ["answer"],
        "additionalProperties": False,
    },
)

agent = Agent()
agent.provider = "openai"
agent.openai_api_key = "YOUR_OPENAI_API_KEY"

messages = [{"role": "user", "content": 'Return JSON: {"answer":"ok"}'}]
messages_out, bundle = agent.run(messages=messages, response_format=fmt, max_iterations=1)

print([m for m in messages_out if m.get("role") == "assistant"][-1]["content"])
print(bundle["consumed_tokens"])
```

`response_format` will parse and normalize the last assistant message according to the schema.

---

## <h1>Event Callback</h1> <a id="event-callback"></a>

`run(..., callback=...)` emits events such as:

- `run_started`
- `iteration_started`
- `token_delta`
- `tool_call`
- `tool_result`
- `reasoning`
- `observation`
- `final_message`
- `run_completed`
- `run_max_iterations`

Example:

```python
def on_event(evt: dict):
    if evt["type"] in ("tool_call", "tool_result", "final_message"):
        print(evt["type"], evt.get("tool_name"), evt.get("content", ""))

messages_out, bundle = agent.run(messages=messages, callback=on_event)
print(bundle["consumed_tokens"])
```

---

## <h1>Builtin Toolkit</h1> <a id="builtin-toolkit"></a>

Use built-in workspace tools directly:

```python
from miso import agent as Agent, builtin_toolkit

agent = Agent()
agent.toolkit = builtin_toolkit(
    workspace_root=".",
    include_python_runtime=True,
)
toolkit = agent.toolkit

toolkit.execute("write_text_file", {"path": "notes/demo.txt", "content": "hello\nworld\n"})
toolkit.execute("create_minimal_demo", {"path": "demo_minimal.py"})
print(toolkit.execute("search_text", {"pattern": "hello", "path": "notes"}))
```

Registered builtin tools:

- `read_text_file`
- `write_text_file`
- `list_directory`
- `search_text`
- `create_minimal_demo`
- `python_runtime_init`
- `python_runtime_install`
- `python_runtime_run`
- `python_runtime_reset`

`workspace_root` safety rule: file operations are constrained inside the workspace root.

---

## <h1>Project Structure</h1> <a id="project-structure"></a>

```text
miso/
  __init__.py
  agent.py               # agent core loop + provider adapters
  tool.py                # tool schema/inference/registry
  builtin_tools.py    # workspace and isolated python runtime tools
  response_format.py     # JSON-schema response format helper
tests/
  test_agent_core.py
  test_openai_family_smoke.py
  test_ollama_smoke.py
  test_builtin_toolkit.py
  test_toolkit_design.py
run_tests.sh
requirements.txt
```

---

## <h1>Testing</h1> <a id="testing"></a>

```bash
./run_tests.sh
```

Optional smoke tests depend on environment variables:

- OpenAI:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL`
- Ollama:
  - `OLLAMA_MODEL` (default: `deepseek-r1:14b`)

---

## <h1>Roadmap Notes</h1> <a id="roadmap-notes"></a>

- Use `agent` as the single entry class.
