# Run Tests

Run the project test suite with optional filtering.

## Arguments
- $ARGUMENTS: Optional test filter pattern (e.g. "anthropic", "kernel", "memory", "toolkit")

## Steps

1. If $ARGUMENTS is provided, run filtered tests:
   ```bash
   PYTHONPATH=src pytest tests/ -q --tb=short -k "$ARGUMENTS"
   ```
2. If no arguments, run all tests:
   ```bash
   PYTHONPATH=src pytest tests/ -q --tb=short -k "not read_file_ast and not relocate_non_python_ranges"
   ```
3. Report results. If there are failures:
   - Read the failing test file
   - Identify the root cause
   - Suggest or apply a fix
   - Re-run the failing test to confirm
