# Agent and Team

Canonical English skill chapter for the `agent-and-team` topic.

## Role and boundaries

This chapter documents the high-level orchestration surface: how a single agent is configured and run, how teams coordinate, and how subagents extend the same execution model.

## Dependency view

- `Agent` owns tool normalization, memory coercion, and `Broth` construction.
- `Team` delegates each step to named `Agent` instances and only manages shared routing/scoring state.
- `ResponseFormat`, toolkit catalog state, and memory namespaces flow through the same run/resume surface.

## Core objects

- `Agent`
- `Team`
- `_SubagentConfig`
- `_SubagentCounters`
- `_SubagentRuntime`

## Execution and state flow

- `Agent.run()` merges tools, creates a fresh runtime, and executes until completion or suspension.
- `Agent.resume_human_input()` restores suspended catalog/runtime state and continues the same conversation.
- `Team.run()` publishes an initial task envelope, scores pending work, and lets agents publish/handoff/finalize until quiescent or complete.

## Configuration surface

- Agent identity/instructions/provider/model.
- Short-term and long-term memory configuration.
- Subagent limits, toolkit catalog settings, and per-run overrides such as `max_iterations`.

## Extension points

- Expose an agent as a tool via `Agent.as_tool()`.
- Enable nested delegation via `enable_subagents()`.
- Use custom callbacks to stream events from both solo and team runs.

## Common gotchas

- `Team` enforces unique agent names and a valid owner.
- Subagent depth/child counters prevent runaway recursion.
- Suspension state must be resumed with the continuation payload from the previous run.

## Related class references

- [Agents API](../api/agents.md)
- [Runtime API](../api/runtime.md)
- [Memory API](../api/memory.md)

## Source entry points

- `src/miso/agents/agent.py`
- `src/miso/agents/team.py`

## Detailed legacy reference

The original repository skill note is preserved below for continuity and extra examples. The canonical copy now lives in this docs tree.

> High-level `Agent` API, `Team` multi-agent coordination, subagent enablement, namespace isolation, and callback integration.

## Agent

`Agent` is the primary high-level interface. It owns tools, memory configuration, and runtime options, and creates a fresh `Broth` engine for each `run()`.

### Construction

```python
from miso import Agent
from miso.toolkits import WorkspaceToolkit, TerminalToolkit
from miso.memory import MemoryConfig

agent = Agent(
    name="coder",                            # Agent identity
    instructions="You are a code assistant.", # System prompt
    provider="openai",                       # LLM provider
    model="gpt-5",                           # Model identifier
    api_key=None,                            # Uses env var if None
    tools=[                                  # Tools, Toolkits, or callables
        WorkspaceToolkit(workspace_root="."),
        TerminalToolkit(workspace_root=".", terminal_strict_mode=True),
    ],
    short_term_memory=MemoryConfig(last_n_turns=10),
    long_term_memory=None,                   # LongTermMemoryConfig or dict
    defaults={"on_tool_confirm": my_handler},
    broth_options={"max_iterations": 8},
)
```

### `tools` Parameter Flexibility

The `tools` list accepts mixed types:

```python
tools=[
    WorkspaceToolkit(workspace_root="."),  # Toolkit instance → all its tools
    my_tool,                               # Tool object → single tool
    my_function,                           # Callable → auto-wrapped in Tool
]
```

All are merged into a single `Toolkit` before being passed to `Broth`.

### Running

```python
messages, bundle = agent.run(
    messages="Inspect the repo.",            # str or list[dict]
    session_id=None,                         # UUID auto-generated if None
    memory_namespace=None,                   # Defaults to session_id
    max_iterations=None,                     # Override broth_options
    payload=None,                            # Pass-through dict for context
    callback=None,                           # Event callback function
)
```

**Return value:**

```python
messages  # list[dict] — full conversation (system + user + assistant + tool messages)
bundle    # dict — metadata:
          #   consumed_tokens: int
          #   stop_reason: str ("complete" | "max_iterations" | "human_input" | ...)
          #   artifacts: list
          #   toolkit_catalog_token: str | None
```

### Resuming After Suspension

When Broth suspends (confirmation, human input):

```python
# First run suspends
messages, bundle = agent.run("Do something risky.")
# bundle["stop_reason"] == "human_input"

# User provides response
messages, bundle = agent.resume_human_input(
    response=ToolConfirmationResponse(approved=True),
    # or HumanInputResponse for ask_user
)
```

## Toolkit Catalog

Enable dynamic toolkit activation/deactivation at runtime:

```python
agent.enable_toolkit_catalog(
    managed_toolkit_ids=["workspace", "terminal", "external_api"],
    always_active_toolkit_ids=["workspace"],  # Cannot be deactivated
)
```

This injects 5 catalog management tools. The LLM can activate/deactivate toolkits as needed during a run. Always-active toolkits cannot be deactivated.

**State preservation**: Catalog state survives across `run()` suspensions via state tokens stored in `bundle["toolkit_catalog_token"]`.

## Subagents

Enable the agent to spawn child agents dynamically:

```python
agent.enable_subagents(
    tool_name="spawn_subagent",   # Tool name exposed to LLM
    max_depth=6,                   # Max nesting depth
    max_total_agents=20,           # Max total spawned agents
    child_tools=[...],             # Tools available to children (defaults to parent's)
)
```

### How it works

1. LLM calls `spawn_subagent(name, role, task)`
2. Framework creates a child `Agent` with:
   - Inherited tools and memory config
   - System prompt overlaid with role and depth context
   - Isolated memory namespace: `{parent_namespace}:{child_name}`
3. Child runs to completion
4. Result returned to parent as a tool result

### Constraints

- Depth tracking prevents infinite recursion
- Total agent count prevents resource exhaustion
- Each child gets its own `session_id` and `memory_namespace`

## Team

`Team` coordinates multiple agents via channel-based async messaging.

### Construction

```python
from miso import Agent, Team

analyst = Agent(name="analyst", provider="openai", model="gpt-5", instructions="...")
coder = Agent(name="coder", provider="openai", model="gpt-5", instructions="...")

team = Team(
    agents=[analyst, coder],
    channels={
        "main": {"subscribers": ["analyst", "coder"]},
        "code_review": {"subscribers": ["coder"]},
    },
    owner="analyst",              # The agent that can finalize the team run
    max_steps=20,                 # Max total agent turns across all agents
)
```

### Execution

```python
result = team.run(
    messages="Build a web scraper.",
    session_id=None,
    callback=None,
)
```

**Return value** (different from `Agent.run()`):

```python
result = {
    "transcript": [...],       # Ordered list of all agent messages
    "events": [...],           # Event log (scheduled, handoff, idle, finalized)
    "stop_reason": str,        # "quiescent" | "finalized" | "max_steps"
    "agent_bundles": {...},    # Per-agent bundle dicts
}
```

### Agent Selection Scoring

When multiple agents could act next, Team uses a scoring system:

| Signal       | Points   | Description                               |
| ------------ | -------- | ----------------------------------------- |
| Handoff      | 3        | Agent A explicitly handed off to Agent B  |
| Mention      | 2        | Agent A mentioned @AgentB in its message  |
| User input   | 1        | Initial user message delivered to channel |
| Owner bonus  | +0.5     | Tie-breaking bias toward the owner agent  |
| Alphabetical | tiebreak | Final tiebreak by name                    |

The highest-scoring agent acts next.

### Channel-Based Communication

```text
User: "Build a web scraper."
  ↓ published to "main" channel

analyst (subscribed to "main") → scores highest → runs
  ↓ publishes response to "main"
  ↓ publishes handoff to "coder" via "main"

coder (subscribed to "main") → scores 3 (handoff) → runs
  ↓ publishes code to "main"
  ↓ mentions @analyst

analyst (subscribed to "main") → scores 2 (mention) → runs
  ↓ publishes "Looks good" + finalizes

Team stops (stop_reason="finalized")
```

### Handoffs

An agent can explicitly transfer control:

```text
Agent response: "Handing off to @coder for implementation."
```

The framework detects the handoff pattern and gives coder a 3-point score.

### Finalization

Only the **owner** can finalize (end the team run). Non-owner agents that try to finalize are given an error and the team continues.

```text
analyst (owner): "All tasks complete. [FINALIZE]"
→ Team stops with stop_reason="finalized"
```

### Stop Conditions

| Condition       | `stop_reason` | Description                     |
| --------------- | ------------- | ------------------------------- |
| Owner finalizes | `"finalized"` | Owner agent signals completion  |
| No agent scored | `"quiescent"` | All agents idle, no work left   |
| Step limit hit  | `"max_steps"` | `max_steps` total turns reached |

## Memory Namespace Isolation

| Context    | Pattern                           | Example                |
| ---------- | --------------------------------- | ---------------------- |
| Solo agent | `session_id`                      | `abc-123`              |
| Team agent | `{session_id}:{agent_name}`       | `abc-123:coder`        |
| Subagent   | `{parent_namespace}:{child_name}` | `abc-123:coder:helper` |

This ensures each agent's long-term memory is isolated while still allowing shared session stores.

## Callback Integration

Both `Agent.run()` and `Team.run()` accept callbacks:

```python
def my_callback(event: dict) -> None:
    match event["type"]:
        case "message_published":
            print(f"[{event.get('agent', 'system')}] {event['data']}")
        case "tool_result":
            print(f"  Tool: {event['tool_name']} → {event['result']}")
        case "handoff":
            print(f"  Handoff: {event['from']} → {event['to']}")
```

Team adds these extra event types:

| Event Type       | When                       |
| ---------------- | -------------------------- |
| `scheduled`      | Agent selected to act next |
| `handoff`        | Agent hands off to another |
| `idle`           | Agent has nothing to do    |
| `finalized`      | Owner ends the team run    |
| `step_completed` | One agent turn finished    |

## Common Gotchas

1. **`Agent.run()` returns `(messages, bundle)`; `Team.run()` returns a dict** — These are different shapes. Don't destructure team results as a tuple.

2. **Fresh Broth per run** — Agent state (tools, memory config) persists, but the runtime engine is recreated. No conversation state leaks between runs unless memory is configured.

3. **Owner is required for finalization** — If no `owner` is set on Team, no agent can finalize, and the team runs until `max_steps` or quiescence.

4. **Subagent namespace accumulates** — A subagent of a subagent gets namespace `root:parent:child`. Deep nesting creates long namespace strings.

5. **`max_steps` is total turns, not per-agent** — A team with 3 agents and `max_steps=20` allows ~6-7 turns per agent on average.

6. **Catalog state must be passed across suspensions** — If the agent has a toolkit catalog and the run suspends, the `toolkit_catalog_token` from the bundle must be preserved for resumption.

7. **Team agents share nothing by default** — Each agent has its own tools, memory, and instructions. Communication happens only through channels.

## Related Skills

- [architecture-overview.md](architecture-overview.md) — System-level component relationships
- [runtime-engine.md](runtime-engine.md) — How Broth executes under Agent
- [memory-system.md](memory-system.md) — Memory namespace conventions
- [tool-system-patterns.md](tool-system-patterns.md) — Tool registration for agents
