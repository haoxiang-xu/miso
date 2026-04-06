# Add a New Kernel Harness

This guide walks you through creating a new runtime harness for the unchain kernel loop. Harnesses are the primary extension point for injecting behavior at specific phases of the agent execution cycle.

## Prerequisites

- Familiarity with the kernel loop execution model (see [Architecture Overview](../skills/architecture-overview.md))
- Understanding of `HarnessDelta` immutable state mutations (see [Return Shapes and State Flow](../appendix/return-shapes-and-state-flow.md))

## Reference Files

| File | Role |
|------|------|
| `src/unchain/kernel/harness.py` | `BaseRuntimeHarness` protocol definition |
| `src/unchain/kernel/loop.py` | `KernelLoop` phase dispatch logic |
| `src/unchain/kernel/delta.py` | `HarnessDelta` operations (append, insert, replace, delete) |
| `src/unchain/kernel/state.py` | `RunState` mutable state with message versioning |

## Steps

1. **Study the harness protocol.** Read `src/unchain/kernel/harness.py` to understand the `BaseRuntimeHarness` interface and the contract each harness must fulfill.

2. **Understand phase dispatch.** Read `src/unchain/kernel/loop.py` to see how harnesses are dispatched at each phase of the kernel loop (e.g., `before_model`, `on_tool_call`, `after_tool_batch`).

3. **Review existing harnesses** for reference patterns:
   - **Simple (single phase):** `src/unchain/memory/bootstrap.py` -- runs at the `bootstrap` phase
   - **Complex (multi-phase):** `src/unchain/tools/execution.py` -- hooks into `on_tool_call` and `after_tool_batch`
   - **Optimizer:** `src/unchain/optimizers/last_n.py` -- runs at `before_model`

4. **Study HarnessDelta operations.** Read `src/unchain/kernel/delta.py` for the available delta operations: `state_only`, `append_messages`, `insert_messages`, `replace_messages`, and `delete_messages`.

5. **Create your harness class** (see Template below).

6. **Register the harness** in the appropriate module or agent builder.

7. **Write tests** using the kernel test patterns from `tests/test_kernel_core.py`.

## Template

```python
from unchain.kernel import BaseRuntimeHarness, HarnessContext, HarnessDelta


class MyHarness(BaseRuntimeHarness):
    name = "my_harness"
    phases = ("before_model",)  # Which phases to run in
    order = 100  # Execution order (lower = earlier)

    def build_delta(self, context: HarnessContext) -> HarnessDelta | None:
        # Access state: context.state, context.latest_messages()
        # Return None to skip, or a delta to apply
        return HarnessDelta.state_only(
            created_by=self.name,
            state_updates={"my_key": value},
        )
```

### Key points

- **`phases`**: Tuple of phase names this harness participates in. Common phases: `bootstrap`, `before_model`, `on_tool_call`, `after_tool_batch`.
- **`order`**: Integer controlling execution priority within a phase. Lower values run first.
- **`build_delta`**: The core method. Return `None` to be a no-op, or a `HarnessDelta` to mutate run state.

## Testing

Run tests using the standard test command:

```bash
PYTHONPATH=src pytest tests/ -q --tb=short
```

To run only kernel-related tests:

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "kernel"
```

## Related

- [Architecture Overview](../skills/architecture-overview.md) -- kernel loop execution model
- [Runtime Engine](../skills/runtime-engine.md) -- engine internals
- [Return Shapes and State Flow](../appendix/return-shapes-and-state-flow.md) -- delta and state flow details
- [Class Index](../appendix/class-index.md) -- full class reference
