# Agent and Subagents

Canonical English skill chapter for the `agent-and-team` topic.

> **Status note**: The standalone `Team` class described in earlier versions of this doc is no longer part of the public API. Multi-agent coordination is now expressed via **subagents** (delegate / handoff / worker batch tools) under the `unchain.subagents` package, configured through `SubagentModule`.

## Role and boundaries

This chapter documents the high-level orchestration surface: how a single `Agent` is configured and run, and how subagents extend the same execution model for multi-agent work.

## Dependency view

- `Agent` owns identity, instructions, and module composition; it builds a `PreparedAgent` per call via `AgentBuilder`.
- `PreparedAgent` wraps the assembled `KernelLoop`, merged `Toolkit`, and resolved per-call defaults.
- Subagent tools (`delegate_to_subagent`, `handoff_to_subagent`, `spawn_worker_batch`) are registered through `SubagentModule` and dispatched by `SubagentExecutor`.

## Core objects

- `Agent`
- `AgentBuilder` / `PreparedAgent` / `AgentCallContext`
- `AgentSpec` / `AgentState`
- Module types: `ToolsModule`, `MemoryModule`, `PoliciesModule`, `OptimizersModule`, `SubagentModule`, `ToolDiscoveryModule`
- `SubagentExecutor`, `SubagentPolicy`, `SubagentTemplate` (under `unchain.subagents`)

## Execution and state flow

- `Agent.run()` normalizes messages, builds an `AgentCallContext`, lets every module configure a fresh `AgentBuilder`, and asks the builder for a `PreparedAgent`.
- `PreparedAgent.run()` drives `KernelLoop.run()` to completion or suspension and returns a `KernelRunResult`.
- `Agent.resume_human_input()` re-enters the same loop with a continuation payload after a confirmation or human-input pause.
- `Agent.fork_for_subagent()` creates a child `Agent` with overlaid delegation instructions, optionally stripping memory for ephemeral sub-runs.
- `Agent.as_tool()` wraps the agent as a `Tool` so it can be embedded inside another agent's toolset.

## Configuration surface

- Agent identity: `name`, `instructions`, `provider`, `model`, `api_key`.
- Module composition: `modules=(...)`.
- Allow-list filter: `allowed_tools=(...)` to restrict the merged toolkit.
- Per-call overrides on `Agent.run()`: `max_iterations`, `payload`, `callback`, `on_tool_confirm`, `on_human_input`, `on_max_iterations`, `session_id`, `memory_namespace`, `tool_runtime_config`.

## Common gotchas

- `Agent.run()` returns a single `KernelRunResult` (frozen dataclass), not a tuple of `(messages, bundle)`.
- The old `Agent(tools=[...], short_term_memory=..., long_term_memory=..., broth_options=...)` constructor signature is gone — pass everything through `modules=(...)`.
- `Team`, `enable_toolkit_catalog`, and `enable_subagents` no longer exist as runtime methods; use modules instead.
- Subagent depth/child counters (configured through `SubagentPolicy`) prevent runaway recursion; `SubagentExecutor` enforces them.
- Suspension state must be resumed with the `continuation` field returned in the previous `KernelRunResult`.

## Related class references

- [Agents API](../api/agents.md)
- [Runtime API](../api/runtime.md)
- [Memory API](../api/memory.md)

## Source entry points

- `src/unchain/agent/agent.py`
- `src/unchain/agent/builder.py`
- `src/unchain/agent/modules/`
- `src/unchain/subagents/`

## Agent In Practice

`Agent` is the high-level interface exposed to callers. It owns identity, default instructions, and the module set, but it does not execute the model loop itself. Each `run()` builds a fresh `PreparedAgent` (with a fresh `KernelLoop`) to perform the actual execution.

In other words, `Agent` is the configuration envelope and public entry surface, while `KernelLoop` is the single-run executor. `Agent` defines what the agent is and what defaults it carries; `KernelLoop` defines how one concrete request executes.

## Construction

```python
from unchain import Agent
from unchain.agent import ToolsModule, MemoryModule, PoliciesModule
from unchain.toolkits import CoreToolkit
from unchain.memory import MemoryConfig

agent = Agent(
    name="coder",
    instructions="You are a code assistant.",
    provider="openai",
    model="gpt-5",
    api_key=None,
    modules=(
        ToolsModule(tools=(CoreToolkit(workspace_root="."),)),
        MemoryModule(memory=MemoryConfig(last_n_turns=10)),
        PoliciesModule(max_iterations=8, on_tool_confirm=my_handler),
    ),
)
```

The `tools` field of `ToolsModule` accepts a mix of `Toolkit`, `Tool`, or callables — they all get merged into one `Toolkit` during `AgentBuilder.build()`.

## Running

```python
result = agent.run(
    "Inspect the repo.",
    payload={},                              # Pass-through dict for context
    callback=None,                           # Event callback function
    max_iterations=None,                     # Overrides the policies default
    session_id=None,                         # UUID auto-generated if None
    memory_namespace=None,                   # Defaults to session_id
    on_tool_confirm=None,                    # Override per call
)
```

Return value is a `KernelRunResult` (frozen dataclass) with these fields among others:

| Field | Notes |
| --- | --- |
| `messages` | Full conversation (system + user + assistant + tool messages). |
| `status` | Run outcome (`"completed"`, `"awaiting_human_input"`, ...). |
| `continuation` | Continuation payload to pass back into `resume_human_input()` if suspended. |
| `human_input_request` | Populated when status indicates a pending human input. |
| `consumed_tokens` / `input_tokens` / `output_tokens` | Token accounting for the whole run. |
| `last_turn_tokens` / `last_turn_input_tokens` / `last_turn_output_tokens` | Token accounting for the last turn only. |
| `iteration` | How many iterations the loop ran. |

## Resuming After Suspension

When the kernel suspends (confirmation or `ask_user_question`):

```python
first = agent.run("Do something risky.")
# first.status == "awaiting_human_input"

# User provides response
final = agent.resume_human_input(
    conversation=first.messages,
    continuation=first.continuation,
    response={"approved": True},  # or a HumanInputResponse for ask_user_question
)
```

## Tool Exposure (Module-driven)

Toolkit catalog and per-tool deferred discovery are wired through modules, not runtime methods. See [tool-system-patterns.md](tool-system-patterns.md) for the full rundown; in short:

```python
# Catalog mode (toolkit-level lazy)
from unchain.tools import ToolkitCatalogRuntime, ToolkitCatalogConfig
catalog = ToolkitCatalogRuntime(
    config=ToolkitCatalogConfig(
        managed_toolkit_ids=("code", "external_api"),
        always_active_toolkit_ids=("code",),
    ),
    eager_toolkits=[],
)
agent = Agent(name="...", modules=(ToolsModule(tools=(catalog,)),))

# Discovery mode (tool-level deferred)
from unchain.agent import ToolDiscoveryModule
from unchain.tools import ToolDiscoveryConfig
agent = Agent(
    name="...",
    modules=(ToolDiscoveryModule(
        config=ToolDiscoveryConfig(managed_toolkit_ids=("code", "external_api")),
    ),),
)
```

## Subagents

Subagents are dynamically-spawned child agents controlled by tools the LLM calls. Configure them via `SubagentModule`:

```python
from unchain.agent import SubagentModule
from unchain.subagents import SubagentPolicy, SubagentTemplate

agent = Agent(
    name="planner",
    instructions="...",
    modules=(
        SubagentModule(
            templates=(
                SubagentTemplate(
                    name="researcher",
                    instructions="Research the question and return a summary.",
                    model="gpt-5",
                ),
            ),
            policy=SubagentPolicy(max_depth=6, max_total_agents=20),
        ),
    ),
)
```

The module registers three tools by default:

| Tool | Behaviour |
| --- | --- |
| `delegate_to_subagent` | Spawns a named child agent for a task; result returned as a tool message. |
| `handoff_to_subagent` | Hands off the conversation to a different agent (control transfer). |
| `spawn_worker_batch` | Fans out N worker agents in parallel, returns results as a batch. |

### How a delegate run works

1. LLM calls `delegate_to_subagent(name=..., task=...)`.
2. `SubagentExecutor` looks up the matching `SubagentTemplate`, calls `parent.fork_for_subagent(...)` to build the child.
3. Child runs to completion (depth/child counters enforced by `SubagentPolicy`).
4. Result returned to the parent as the tool result for that call.

### Constraints

- `SubagentPolicy.max_depth` prevents infinite recursion.
- `SubagentPolicy.max_total_agents` bounds resource use across the whole tree.
- Each child gets its own `session_id` and `memory_namespace`. Memory policy (`"shared"`, `"ephemeral"`, etc.) decides whether the child retains memory at all.

## Agent as a Tool

Wrap an agent as a `Tool` to plug it into another agent's toolkit:

```python
researcher_tool = researcher_agent.as_tool(
    name="research",
    description="Investigate a question and return a summary.",
)
planner = Agent(
    name="planner",
    modules=(ToolsModule(tools=(researcher_tool,)),),
)
```

`as_tool()` produces a `Tool` whose `execute()` calls the wrapped agent's `run()` with the provided arguments.

## Memory Namespace Isolation

| Context     | Default `memory_namespace`            | Example                |
| ----------- | -------------------------------------- | ---------------------- |
| Solo agent  | `session_id`                           | `abc-123`              |
| Subagent    | `{parent_namespace}:{subagent_name}`   | `abc-123:researcher`   |
| Nested sub. | `{root}:{parent}:{child}` (recursive)  | `abc-123:planner:scout`|

This ensures each agent's long-term memory is isolated while still allowing shared session stores.

## Callback Integration

`Agent.run()` accepts a `callback` that receives every event emitted by the kernel and harnesses (see `runtime-engine.md` for the event catalog):

```python
def my_callback(event: dict) -> None:
    match event["type"]:
        case "message_published":
            print(f"[{event.get('agent', 'system')}] {event['data']}")
        case "tool_result":
            print(f"  Tool: {event['tool_name']} → {event['result']}")
```

## Common Gotchas

1. **`Agent.run()` returns `KernelRunResult`, not `(messages, bundle)`** — Use `.messages`, `.status`, `.continuation`, etc. on the dataclass.

2. **Fresh kernel per run** — Module configuration (tools, memory) persists in `AgentSpec`, but the `KernelLoop` is rebuilt every time. State only crosses runs through `MemoryModule`.

3. **Old constructor kwargs are gone** — `tools=`, `short_term_memory=`, `long_term_memory=`, `broth_options=` no longer exist. Use modules.

4. **Subagent namespace accumulates** — A subagent of a subagent gets namespace `root:parent:child`. Deep nesting creates long namespace strings.

5. **Catalog/discovery state must be passed across suspensions** — Both runtimes checkpoint via the harness `on_suspend` phase, but you still have to pass `continuation` back into `resume_human_input()`.

6. **`Team` is no longer the right primitive** — If you need multi-agent flow, model it as a planner agent that uses `delegate_to_subagent` / `spawn_worker_batch` over `SubagentTemplate`s.

## Related Skills

- [architecture-overview.md](architecture-overview.md) — System-level component relationships
- [runtime-engine.md](runtime-engine.md) — How `KernelLoop` runs under `Agent`
- [memory-system.md](memory-system.md) — Memory namespace conventions for subagents
- [tool-system-patterns.md](tool-system-patterns.md) — Tool registration and exposure modes
