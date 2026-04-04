# Agents API Reference

Modular agent composition via the `Agent` class, `AgentBuilder` pipeline, immutable `AgentSpec`/`AgentState`, and pluggable `AgentModule` system (tools, memory, policies, optimizers, subagents).

| Metric | Value |
| --- | --- |
| Classes | 3 |
| Dataclasses | 5 |
| Protocols | 1 |
| Agent Modules | 5 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `AgentSpec` | `src/unchain/agent/spec.py` | subpackage | dataclass (frozen) |
| `AgentState` | `src/unchain/agent/spec.py` | subpackage | dataclass |
| `Agent` | `src/unchain/agent/agent.py` | top-level | class |
| `AgentCallContext` | `src/unchain/agent/builder.py` | subpackage | dataclass |
| `PreparedAgent` | `src/unchain/agent/builder.py` | subpackage | dataclass |
| `AgentBuilder` | `src/unchain/agent/builder.py` | subpackage | dataclass |
| `AgentModule` | `src/unchain/agent/modules/base.py` | subpackage | protocol |
| `BaseAgentModule` | `src/unchain/agent/modules/base.py` | subpackage | dataclass (frozen) |
| `ToolsModule` | `src/unchain/agent/modules/tools.py` | subpackage | dataclass (frozen) |
| `MemoryModule` | `src/unchain/agent/modules/memory.py` | subpackage | dataclass (frozen) |
| `PoliciesModule` | `src/unchain/agent/modules/policies.py` | subpackage | dataclass (frozen) |
| `OptimizersModule` | `src/unchain/agent/modules/optimizers.py` | subpackage | dataclass (frozen) |
| `SubagentModule` | `src/unchain/agent/modules/subagents.py` | subpackage | dataclass (frozen) |

### `src/unchain/agent/spec.py`

Immutable agent specification and mutable agent state.

## AgentSpec

Frozen dataclass holding the immutable configuration for an agent instance.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/spec.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.agent`. |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `name` | `str` | Required at construction time. |
| `instructions` | `str` | Default: `""`. |
| `provider` | `str` | Default: `"openai"`. |
| `model` | `str` | Default: `"gpt-5"`. |
| `api_key` | `str \| None` | Default: `None`. |
| `modules` | `tuple[Any, ...]` | Default: `()`. |
| `allowed_tools` | `tuple[str, ...] \| None` | Default: `None`. |

## AgentState

Mutable dataclass for per-agent runtime state.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/spec.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.agent`. |
| Kind | Dataclass. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `module_state` | `dict[str, Any]` | Default: `field(default_factory=dict)`. |

### `src/unchain/agent/agent.py`

Top-level `Agent` facade: owns configuration, normalizes messages, prepares the kernel loop via `AgentBuilder`, and exposes `run`/`resume_human_input`/`clone`/`fork_for_subagent`/`as_tool` entry points.

## Agent

User-facing agent class that composes modules, builds a `PreparedAgent`, and delegates execution to the `KernelLoop`.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/agent.py` |
| Inheritance | `-` |
| Exposure | Top-level export via `unchain`. |
| Kind | Class; public-facing. |

### Constructor surface

- `__init__(self, *, name: str, instructions: str='', provider: str='openai', model: str='gpt-5', api_key: str | None=None, modules: tuple[Any, ...]=(), allowed_tools: tuple[str, ...] | None=None, model_io_factory: Callable[..., Any] | None=None)`

### Properties

- `@property name` -> `str`
- `@property instructions` -> `str`
- `@property provider` -> `str`
- `@property model` -> `str`
- `@property allowed_tools` -> `tuple[str, ...] | None`

### Public methods

#### `__init__(self, *, name: str, instructions: str='', provider: str='openai', model: str='gpt-5', api_key: str | None=None, modules: tuple[Any, ...]=(), allowed_tools: tuple[str, ...] | None=None, model_io_factory: Callable[..., Any] | None=None)`

Initializes the agent, creates an `AgentSpec`, `AgentState`, and `ModelIOFactoryRegistry`.

- Category: Constructor
- Errors: raises `ValueError` if `name` is empty or not a string.

#### `run(self, messages: str | list[dict[str, Any]], *, payload: dict[str, Any] | None=None, response_format: Any=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, max_iterations: int | None=None, max_context_window_tokens: int | None=None, previous_response_id: str | None=None, on_tool_confirm: Callable[..., Any] | None=None, on_human_input: Callable[..., Any] | None=None, on_max_iterations: Callable[..., Any] | None=None, session_id: str | None=None, memory_namespace: str | None=None, run_id: str | None=None, tool_runtime_config: dict[str, Any] | None=None) -> KernelRunResult`

Main entry point. Normalizes messages, prepares a `PreparedAgent` via `AgentBuilder`, and runs the kernel loop.

- Category: Method
- Returns: `KernelRunResult`

#### `resume_human_input(self, *, conversation: list[dict[str, Any]], continuation: dict[str, Any], response: dict[str, Any] | Any, payload: dict[str, Any] | None=None, response_format: Any=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, on_tool_confirm: Callable[..., Any] | None=None, on_human_input: Callable[..., Any] | None=None, on_max_iterations: Callable[..., Any] | None=None, session_id: str | None=None, memory_namespace: str | None=None, run_id: str | None=None, tool_runtime_config: dict[str, Any] | None=None) -> KernelRunResult`

Resumes a suspended run after human input has been collected.

- Category: Method
- Returns: `KernelRunResult`

#### `clone(self, *, name: str | None=None, instructions: str | None=None, modules: tuple[Any, ...] | None=None, model: str | None=None, allowed_tools: tuple[str, ...] | None=None) -> Agent`

Creates a new `Agent` with selectively overridden fields.

- Category: Method
- Returns: new `Agent` instance.

#### `fork_for_subagent(self, *, subagent_name: str, mode: str, parent_name: str, lineage: list[str], task: str, instructions: str, expected_output: str, memory_policy: str, model: str | None=None, allowed_tools: tuple[str, ...] | None=None) -> Agent`

Creates a child agent for subagent execution. Overlays delegation instructions and optionally strips the `MemoryModule` when `memory_policy` is `"ephemeral"`.

- Category: Method
- Returns: new `Agent` instance.

#### `as_tool(self, *, name: str | None=None, description: str | None=None, max_iterations: int | None=None) -> Tool`

Wraps this agent as a callable `Tool` that delegates to `self.run()`.

- Category: Method
- Returns: `Tool`

### Lifecycle and runtime role

- Construction validates identity, builds an `AgentSpec` and `AgentState`.
- `run()` normalizes messages, creates an `AgentCallContext`, calls `_prepare()` which invokes each module's `configure()` on an `AgentBuilder`, then calls `builder.build()` to get a `PreparedAgent`, and finally calls `prepared.run()`.
- Modules compose behaviour: `ToolsModule` registers tools, `MemoryModule` attaches memory, `PoliciesModule` sets defaults, `OptimizersModule` adds harnesses, `SubagentModule` adds delegation tools.

### Minimal usage example

```python
from unchain import Agent
from unchain.agent import ToolsModule, MemoryModule

agent = Agent(
    name="assistant",
    instructions="You are a helpful assistant.",
    modules=(
        ToolsModule(tools=(my_tool,)),
        MemoryModule(memory=my_memory_config),
    ),
)
result = agent.run("Hello!")
```

### `src/unchain/agent/builder.py`

Agent preparation pipeline: `AgentCallContext` captures call-site options, `AgentBuilder` accumulates module contributions, and `PreparedAgent` holds the final assembled kernel loop.

## AgentCallContext

Dataclass capturing the per-call options passed to `Agent.run()` or `Agent.resume_human_input()`.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/builder.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.agent`. |
| Kind | Dataclass. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `mode` | `str` | `"run"` or `"resume_human_input"`. |
| `input_messages` | `list[dict[str, Any]] \| None` | Default: `None`. |
| `conversation` | `list[dict[str, Any]] \| None` | Default: `None`. |
| `continuation` | `dict[str, Any] \| None` | Default: `None`. |
| `response` | `dict[str, Any] \| Any` | Default: `None`. |
| `payload` | `dict[str, Any] \| None` | Default: `None`. |
| `response_format` | `ResponseFormat \| None` | Default: `None`. |
| `callback` | `Callable[[dict[str, Any]], None] \| None` | Default: `None`. |
| `verbose` | `bool` | Default: `False`. |
| `max_iterations` | `int \| None` | Default: `None`. |
| `max_context_window_tokens` | `int \| None` | Default: `None`. |
| `previous_response_id` | `str \| None` | Default: `None`. |
| `on_tool_confirm` | `Callable[..., Any] \| None` | Default: `None`. |
| `on_human_input` | `Callable[..., Any] \| None` | Default: `None`. |
| `on_max_iterations` | `Callable[..., Any] \| None` | Default: `None`. |
| `session_id` | `str \| None` | Default: `None`. |
| `memory_namespace` | `str \| None` | Default: `None`. |
| `run_id` | `str \| None` | Default: `None`. |
| `tool_runtime_config` | `dict[str, Any] \| None` | Default: `None`. |

## AgentBuilder

Mutable builder that modules call into to register tools, harnesses, memory, defaults, and hooks.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/builder.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.agent`. |
| Kind | Dataclass. |

### Public methods

| Method | Description |
| --- | --- |
| `add_tool(entry)` | Register a `Tool`, `Toolkit`, or callable. |
| `add_harness(harness)` | Attach a runtime harness. |
| `attach_memory_runtime(memory_runtime)` | Attach a `KernelMemoryRuntime`. |
| `set_model_io(model_io)` | Override the `ModelIO` instance. |
| `set_model_io_factory(factory)` | Set a factory for deferred `ModelIO` creation. |
| `add_run_hook(hook)` | Append a post-run hook. |
| `add_tool_runtime_plugin(plugin)` | Append a tool runtime plugin. |
| `set_payload_defaults(payload)` | Merge default payload. |
| `set_response_format_default(response_format)` | Set default response format. |
| `set_max_iterations_default(max_iterations)` | Set default max iterations. |
| `set_max_context_window_tokens_default(tokens)` | Set default context window limit. |
| `set_on_tool_confirm_default(on_tool_confirm)` | Set default tool confirm callback. |
| `set_on_human_input_default(on_human_input)` | Set default human input callback. |
| `set_on_max_iterations_default(on_max_iterations)` | Set default max-iterations callback. |
| `build()` | Finalize and return a `PreparedAgent`. |

## PreparedAgent

Assembled agent ready for execution. Holds the `KernelLoop`, merged `Toolkit`, and resolved defaults.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/builder.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.agent`. |
| Kind | Dataclass. |

### Public methods

| Method | Returns | Description |
| --- | --- | --- |
| `run()` | `KernelRunResult` | Execute the kernel loop. |
| `resume_human_input()` | `KernelRunResult` | Resume from human input suspension. |

### `src/unchain/agent/modules/`

Pluggable modules that configure the `AgentBuilder` during preparation.

## AgentModule (Protocol)

Protocol that all agent modules must satisfy.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/modules/base.py` |
| Kind | Protocol. |

### Required interface

| Attribute/Method | Type | Description |
| --- | --- | --- |
| `name` | `str` | Module identifier. |
| `configure(builder)` | `-> None` | Called during agent preparation. |

## ToolsModule

Registers tools, toolkits, and callables into the builder.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/modules/tools.py` |
| Inheritance | `BaseAgentModule` |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `tools` | `tuple[Any, ...]` | Default: `()`. |
| `name` | `str` | Default: `"tools"`. |

## MemoryModule

Attaches memory to the builder. Accepts `KernelMemoryRuntime`, `MemoryManager`, `MemoryConfig`, or a raw dict.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/modules/memory.py` |
| Inheritance | `BaseAgentModule` |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `memory` | `KernelMemoryRuntime \| MemoryManager \| MemoryConfig \| dict[str, Any] \| None` | Default: `None`. |
| `store` | `SessionStore \| None` | Default: `None`. |
| `name` | `str` | Default: `"memory"`. |

## PoliciesModule

Sets default payload, response format, max iterations, context window tokens, and tool confirmation callback.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/modules/policies.py` |
| Inheritance | `BaseAgentModule` |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `payload` | `dict[str, Any]` | Default: `{}`. |
| `response_format` | `ResponseFormat \| None` | Default: `None`. |
| `max_iterations` | `int \| None` | Default: `None`. |
| `max_context_window_tokens` | `int \| None` | Default: `None`. |
| `on_tool_confirm` | `Callable[..., Any] \| None` | Default: `None`. |
| `name` | `str` | Default: `"policies"`. |

## OptimizersModule

Attaches runtime harnesses (e.g., context-window optimizers) to the builder.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/modules/optimizers.py` |
| Inheritance | `BaseAgentModule` |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `harnesses` | `tuple[object, ...]` | Default: `()`. |
| `name` | `str` | Default: `"optimizers"`. |

## SubagentModule

Registers delegation, handoff, and worker-batch tools plus the `SubagentToolPlugin`.

| Item | Details |
| --- | --- |
| Source | `src/unchain/agent/modules/subagents.py` |
| Inheritance | `BaseAgentModule` |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `templates` | `tuple[SubagentTemplate, ...]` | Default: `()`. |
| `policy` | `SubagentPolicy` | Default: `SubagentPolicy()`. |
| `executor` | `SubagentExecutor \| None` | Default: `None`. |
| `name` | `str` | Default: `"subagents"`. |
