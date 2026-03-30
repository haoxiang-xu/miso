# Explore Architecture

Explore and explain a specific part of the unchain/miso architecture.

## Arguments
- $ARGUMENTS: Area to explore (e.g. "kernel loop", "memory system", "tool execution", "provider abstraction", "subagents", "character system")

## Steps

1. Based on $ARGUMENTS, identify the relevant source files:

   | Area | Key Files |
   |------|-----------|
   | kernel loop | `src/unchain/kernel/loop.py`, `state.py`, `harness.py`, `delta.py` |
   | memory | `src/unchain/memory/runtime.py`, `manager.py`, `config.py`, `stores.py`, `long_term.py` |
   | tool execution | `src/unchain/tools/execution.py`, `tool.py`, `toolkit.py`, `confirmation.py` |
   | providers | `src/unchain/providers/model_io.py`, `src/unchain/agent/model_io.py` |
   | agent builder | `src/unchain/agent/agent.py`, `builder.py`, `spec.py`, `modules/` |
   | subagents | `src/unchain/subagents/executor.py`, `plugin.py`, `types.py` |
   | optimizers | `src/unchain/optimizers/base.py`, `last_n.py`, `llm_summary.py` |
   | characters | `src/miso/characters/character.py` |
   | workspace pins | `src/miso/workspace/pins.py` |
   | legacy broth | `src/miso/runtime/engine.py` |
   | pupu integration | `PuPu/miso_runtime/server/miso_adapter.py`, `routes.py` |

2. Read the relevant files
3. Explain the architecture with:
   - Data flow diagram (text-based)
   - Key classes and their responsibilities
   - How components interact
   - Extension points for new development
