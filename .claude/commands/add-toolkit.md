# Add a New Built-in Toolkit

Create a new built-in toolkit for the unchain framework.

## Arguments
- $ARGUMENTS: Toolkit name and brief description (e.g. "database SQL query and schema inspection tools")

## Steps

1. Parse toolkit name from $ARGUMENTS
2. Read an existing toolkit for reference pattern:
   - `src/unchain/toolkits/builtin/workspace/` (complex example)
   - `src/unchain/toolkits/builtin/ask_user/` (simple example)
3. Create the toolkit directory: `src/unchain/toolkits/builtin/<name>/`
4. Create `toolkit.toml` manifest with:
   ```toml
   [toolkit]
   name = "<name>"
   description = "<description>"
   version = "0.1.0"
   ```
5. Create `__init__.py` with the toolkit class:
   - Extend `Toolkit` from `unchain.tools`
   - Register tools in `__init__` via `self.register()`
   - Use `@tool` decorator or direct `Tool()` construction
   - Add proper type hints and docstrings for tool parameters
6. Mirror the toolkit in `src/miso/toolkits/builtin/<name>/` for legacy compat
7. Export from `src/unchain/toolkits/__init__.py` and `src/miso/toolkits/__init__.py`
8. Write a basic test in `tests/test_<name>_toolkit.py`
9. Run tests: `PYTHONPATH=src pytest tests/test_<name>_toolkit.py -v --tb=short`
