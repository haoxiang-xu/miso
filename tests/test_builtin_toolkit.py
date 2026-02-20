import tempfile
from pathlib import Path

from miso import agent as Agent, build_builtin_toolkit, python_workspace_toolkit


def test_builtin_toolkit_registers_expected_tools():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = python_workspace_toolkit(workspace_root=tmp, include_python_runtime=True)

        names = set(toolkit.tools.keys())
        # file-level
        assert "read_file" in names
        assert "write_file" in names
        assert "create_file" in names
        assert "delete_file" in names
        assert "copy_file" in names
        assert "move_file" in names
        assert "file_exists" in names
        # directory
        assert "list_directory" in names
        assert "create_directory" in names
        assert "search_text" in names
        # line-level
        assert "read_lines" in names
        assert "insert_lines" in names
        assert "replace_lines" in names
        assert "delete_lines" in names
        assert "copy_lines" in names
        assert "move_lines" in names
        assert "search_and_replace" in names
        # python runtime
        assert "python_runtime_init" in names
        assert "python_runtime_install" in names
        assert "python_runtime_run" in names
        assert "python_runtime_reset" in names


def test_builtin_toolkit_file_ops_and_search():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = python_workspace_toolkit(workspace_root=tmp, include_python_runtime=False)

        write_result = toolkit.execute(
            "write_file",
            {
                "path": "notes/test.txt",
                "content": "hello\nworld\nhello agent\n",
            },
        )
        assert "error" not in write_result

        read_result = toolkit.execute("read_file", {"path": "notes/test.txt"})
        assert "hello" in read_result["content"]

        search_result = toolkit.execute(
            "search_text",
            {"pattern": "hello", "path": "notes", "max_results": 10},
        )
        assert len(search_result["matches"]) == 2


def test_builtin_toolkit_python_runtime_run_code():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = python_workspace_toolkit(workspace_root=tmp, include_python_runtime=True)

        init_result = toolkit.execute("python_runtime_init", {"reset": True})
        assert init_result.get("created") is True

        run_result = toolkit.execute(
            "python_runtime_run",
            {"code": "print('runtime-ok')", "timeout_seconds": 10},
        )
        assert run_result.get("returncode") == 0
        assert "runtime-ok" in run_result.get("stdout", "")


def test_line_level_read_insert_replace_delete():
    with tempfile.TemporaryDirectory() as tmp:
        tk = python_workspace_toolkit(workspace_root=tmp, include_python_runtime=False)

        # Create a file with 5 lines
        tk.execute("write_file", {"path": "lines.txt", "content": "L1\nL2\nL3\nL4\nL5\n"})

        # read_lines range
        result = tk.execute("read_lines", {"path": "lines.txt", "start": 2, "end": 4})
        assert result["content"] == "L2\nL3\nL4\n"
        assert result["total_lines"] == 5

        # insert before line 3
        tk.execute("insert_lines", {"path": "lines.txt", "line": 3, "content": "NEW\n"})
        after_insert = tk.execute("read_file", {"path": "lines.txt"})
        lines = after_insert["content"].splitlines()
        assert lines[2] == "NEW"
        assert len(lines) == 6

        # replace lines 1-2 with single line
        tk.execute("replace_lines", {"path": "lines.txt", "start": 1, "end": 2, "content": "REPLACED\n"})
        after_replace = tk.execute("read_file", {"path": "lines.txt"})
        lines = after_replace["content"].splitlines()
        assert lines[0] == "REPLACED"
        assert after_replace["total_lines"] == 5

        # delete line 1
        tk.execute("delete_lines", {"path": "lines.txt", "start": 1, "end": 1})
        after_delete = tk.execute("read_file", {"path": "lines.txt"})
        assert after_delete["total_lines"] == 4


def test_copy_lines_and_move_lines():
    with tempfile.TemporaryDirectory() as tmp:
        tk = python_workspace_toolkit(workspace_root=tmp, include_python_runtime=False)

        tk.execute("write_file", {"path": "m.txt", "content": "A\nB\nC\nD\n"})

        # Copy lines 1-2 to before line 4
        tk.execute("copy_lines", {"path": "m.txt", "start": 1, "end": 2, "to_line": 5})
        result = tk.execute("read_file", {"path": "m.txt"})
        lines = result["content"].splitlines()
        assert lines == ["A", "B", "C", "D", "A", "B"]

        # Rewrite and test move
        tk.execute("write_file", {"path": "m.txt", "content": "A\nB\nC\nD\n"})
        tk.execute("move_lines", {"path": "m.txt", "start": 3, "end": 4, "to_line": 1})
        result = tk.execute("read_file", {"path": "m.txt"})
        lines = result["content"].splitlines()
        assert lines == ["C", "D", "A", "B"]


def test_search_and_replace():
    with tempfile.TemporaryDirectory() as tmp:
        tk = python_workspace_toolkit(workspace_root=tmp, include_python_runtime=False)

        tk.execute("write_file", {"path": "sr.txt", "content": "foo bar foo baz foo\n"})

        result = tk.execute("search_and_replace", {
            "path": "sr.txt",
            "search": "foo",
            "replace": "qux",
            "max_count": 2,
        })
        assert result["replacements_made"] == 2

        content = tk.execute("read_file", {"path": "sr.txt"})["content"]
        assert content.count("qux") == 2
        assert content.count("foo") == 1


def test_file_create_delete_copy_move_exists():
    with tempfile.TemporaryDirectory() as tmp:
        tk = python_workspace_toolkit(workspace_root=tmp, include_python_runtime=False)

        # create
        result = tk.execute("create_file", {"path": "new.txt", "content": "hello"})
        assert result["created"] is True

        # create again should error
        result = tk.execute("create_file", {"path": "new.txt"})
        assert "error" in result

        # exists
        result = tk.execute("file_exists", {"path": "new.txt"})
        assert result["exists"] is True
        assert result["type"] == "file"

        # copy
        result = tk.execute("copy_file", {"source": "new.txt", "destination": "copy.txt"})
        assert result["copied"] is True

        # move
        result = tk.execute("move_file", {"source": "copy.txt", "destination": "moved.txt"})
        assert result["moved"] is True

        # original copy gone
        result = tk.execute("file_exists", {"path": "copy.txt"})
        assert result["exists"] is False

        # delete
        result = tk.execute("delete_file", {"path": "moved.txt"})
        assert result["deleted"] is True

        result = tk.execute("file_exists", {"path": "moved.txt"})
        assert result["exists"] is False


def test_agent_uses_empty_toolkit_by_default():
    agent = Agent()
    assert agent.toolkit.tools == {}


def test_agent_add_multiple_toolkits():
    """Agent can hold multiple toolkits simultaneously."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        agent = Agent()
        ws = python_workspace_toolkit(workspace_root=tmp, include_python_runtime=False)
        ws_py = python_workspace_toolkit(workspace_root=tmp, include_python_runtime=True)

        agent.add_toolkit(ws)

        merged = agent.toolkit
        names = set(merged.tools.keys())
        assert "read_file" in names
        assert "write_file" in names
        assert "python_runtime_run" not in names

        agent.add_toolkit(ws_py)
        names = set(agent.toolkit.tools.keys())
        assert "python_runtime_run" in names

        agent.remove_toolkit(ws_py)
        names_after = set(agent.toolkit.tools.keys())
        assert "read_file" in names_after
        assert "python_runtime_run" not in names_after
