import tempfile
from pathlib import Path

from miso import agent as Agent, builtin_toolkit


def test_builtin_toolkit_registers_expected_tools():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = builtin_toolkit(workspace_root=tmp, include_python_runtime=True)

        names = set(toolkit.tools.keys())
        assert "read_text_file" in names
        assert "write_text_file" in names
        assert "list_directory" in names
        assert "search_text" in names
        assert "create_minimal_demo" in names
        assert "python_runtime_init" in names
        assert "python_runtime_install" in names
        assert "python_runtime_run" in names
        assert "python_runtime_reset" in names


def test_builtin_toolkit_file_ops_and_search():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = builtin_toolkit(workspace_root=tmp, include_python_runtime=False)

        write_result = toolkit.execute(
            "write_text_file",
            {
                "path": "notes/test.txt",
                "content": "hello\nworld\nhello agent\n",
            },
        )
        assert "error" not in write_result

        read_result = toolkit.execute("read_text_file", {"path": "notes/test.txt"})
        assert "hello" in read_result["content"]

        search_result = toolkit.execute(
            "search_text",
            {"pattern": "hello", "path": "notes", "max_results": 10},
        )
        assert len(search_result["matches"]) == 2


def test_builtin_toolkit_python_runtime_run_code():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = builtin_toolkit(workspace_root=tmp, include_python_runtime=True)

        init_result = toolkit.execute("python_runtime_init", {"reset": True})
        assert init_result.get("created") is True

        run_result = toolkit.execute(
            "python_runtime_run",
            {"code": "print('runtime-ok')", "timeout_seconds": 10},
        )
        assert run_result.get("returncode") == 0
        assert "runtime-ok" in run_result.get("stdout", "")


def test_builtin_toolkit_create_minimal_demo_file():
    with tempfile.TemporaryDirectory() as tmp:
        tk = builtin_toolkit(workspace_root=tmp, include_python_runtime=False)

        created = tk.execute("create_minimal_demo", {"path": "demo_minimal.py"})
        assert created.get("created") is True

        demo_file = Path(tmp) / "demo_minimal.py"
        assert demo_file.exists()
        demo_content = demo_file.read_text(encoding="utf-8")
        assert "def greet(name: str) -> str:" in demo_content
        assert "print(greet(\"miso\"))" in demo_content

        skipped = tk.execute("create_minimal_demo", {"path": "demo_minimal.py"})
        assert skipped.get("created") is False


def test_agent_use_builtin_toolkit_shortcut():
    with tempfile.TemporaryDirectory() as tmp:
        agent = Agent()
        toolkit = agent.use_builtin_toolkit(workspace_root=tmp, include_python_runtime=False)

        assert isinstance(toolkit, builtin_toolkit)
        assert "read_text_file" in agent.toolkit.tools

        workspace_file = Path(tmp) / "a.txt"
        workspace_file.write_text("abc", encoding="utf-8")

        result = agent.toolkit.execute("read_text_file", {"path": "a.txt"})
        assert result["content"] == "abc"
