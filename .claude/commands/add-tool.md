# Add a New Tool

Add a new tool to an existing toolkit or create a standalone tool.

## Arguments
- $ARGUMENTS: Tool name, target toolkit (or "standalone"), and description (e.g. "read_pdf workspace Read PDF files and extract text content")

## Steps

1. Parse tool name, target toolkit, and description from $ARGUMENTS

2. If adding to an existing toolkit:
   - Read the toolkit source in `src/unchain/toolkits/builtin/<toolkit>/`
   - Add the tool function with proper type hints and docstring
   - Register via `self.register()` in the toolkit's `__init__`
   - Mirror in `src/miso/toolkits/builtin/<toolkit>/` for legacy compat

3. If standalone:
   - Create the tool using the `@tool` decorator pattern:
     ```python
     from unchain.tools import tool

     @tool
     def my_tool(param1: str, param2: int = 10) -> dict:
         """Tool description. First line becomes the tool description for the LLM.

         Args:
             param1: Description of param1
             param2: Description of param2
         """
         return {"result": "..."}
     ```

4. For tools that need confirmation gates:
   ```python
   Tool(
       name="dangerous_tool",
       func=my_func,
       requires_confirmation=True,
   )
   ```

5. For tools whose output should be injected as observation:
   ```python
   Tool(
       name="observe_tool",
       func=my_func,
       observe=True,
   )
   ```

6. Write a test for the new tool
7. Run: `PYTHONPATH=src pytest tests/ -q --tb=short -k "<tool_name>"`
