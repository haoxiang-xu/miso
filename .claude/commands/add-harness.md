# Add a New Kernel Harness

Create a new runtime harness for the unchain kernel loop.

## Arguments
- $ARGUMENTS: Harness name, phase(s), and description (e.g. "rate_limiter before_model throttle API calls based on token budget")

## Steps

1. Read the harness protocol: `src/unchain/kernel/harness.py`
2. Read `src/unchain/kernel/loop.py` to understand phase dispatch
3. Read an existing harness for reference:
   - Simple: `src/unchain/memory/bootstrap.py` (bootstrap phase)
   - Complex: `src/unchain/tools/execution.py` (on_tool_call + after_tool_batch)
   - Optimizer: `src/unchain/optimizers/last_n.py` (before_model)
4. Read `src/unchain/kernel/delta.py` for HarnessDelta operations

5. Create the harness class:
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

6. Register the harness in the appropriate module or builder
7. Write tests using the kernel test patterns from `tests/test_kernel_core.py`
8. Run: `PYTHONPATH=src pytest tests/ -q --tb=short`
