import tempfile
from pathlib import Path

from miso import broth as Broth, build_builtin_toolkit, terminal_toolkit, workspace_toolkit
from miso.memory import InMemorySessionStore
from miso.workspace_pins import (
    MAX_FULL_FILE_PIN_CHARS,
    WorkspacePinExecutionContext,
    load_workspace_pins,
)


def test_workspace_toolkit_registers_expected_tools():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = workspace_toolkit(workspace_root=tmp)

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
        assert "pin_file_context" in names
        assert "unpin_file_context" in names
        assert "terminal_exec" not in names
        assert "terminal_session_open" not in names
        assert "terminal_session_write" not in names
        assert "terminal_session_close" not in names
        assert "python_runtime_init" not in names
        assert "python_runtime_install" not in names
        assert "python_runtime_run" not in names
        assert "python_runtime_reset" not in names


def test_build_builtin_toolkit_returns_workspace_toolkit():
    with tempfile.TemporaryDirectory() as tmp:
        tk = build_builtin_toolkit(workspace_root=tmp)
        assert isinstance(tk, workspace_toolkit)
        assert "read_file" in tk.tools
        assert "terminal_exec" not in tk.tools


def test_workspace_toolkit_methods_are_declared_on_class_for_ui_discovery():
    assert "read_file" in workspace_toolkit.__dict__
    assert "write_file" in workspace_toolkit.__dict__
    assert "read_lines" in workspace_toolkit.__dict__
    assert "search_and_replace" in workspace_toolkit.__dict__
    assert "pin_file_context" in workspace_toolkit.__dict__
    assert "unpin_file_context" in workspace_toolkit.__dict__


def test_workspace_pin_tools_use_explicit_descriptions():
    with tempfile.TemporaryDirectory() as tmp:
        tk = workspace_toolkit(workspace_root=tmp)

        pin_description = tk.tools["pin_file_context"].description
        unpin_description = tk.tools["unpin_file_context"].description

        assert "Prefer the smallest necessary line range" in pin_description
        assert "unpin as soon as you no longer need it" in pin_description
        assert "pin_id" in unpin_description


def test_workspace_pin_tools_require_active_session_id():
    with tempfile.TemporaryDirectory() as tmp:
        tk = workspace_toolkit(workspace_root=tmp)
        Path(tmp, "demo.py").write_text("print('hello')\n", encoding="utf-8")

        pin_result = tk.execute("pin_file_context", {"path": "demo.py"})
        unpin_result = tk.execute("unpin_file_context", {"all": True})

        assert "session_id" in pin_result["error"]
        assert "session_id" in unpin_result["error"]


def test_workspace_pin_file_context_deduplicates_and_unpins():
    with tempfile.TemporaryDirectory() as tmp:
        tk = workspace_toolkit(workspace_root=tmp)
        file_path = Path(tmp, "demo.py")
        file_path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        store = InMemorySessionStore()
        context = WorkspacePinExecutionContext(session_id="pin-session", session_store=store)
        tk.push_execution_context(context)
        try:
            first = tk.execute("pin_file_context", {"path": "demo.py", "start": 2, "end": 3})
            second = tk.execute("pin_file_context", {"path": "demo.py", "start": 2, "end": 3})
            removed = tk.execute("unpin_file_context", {"pin_id": first["pin_id"]})
        finally:
            tk.pop_execution_context()

        assert first["created"] is True
        assert second["duplicate"] is True
        assert second["pin_id"] == first["pin_id"]
        assert removed["removed"] == 1

        _, pins = load_workspace_pins(store, "pin-session")
        assert pins == []


def test_workspace_pin_file_context_rejects_oversized_whole_file_pin():
    with tempfile.TemporaryDirectory() as tmp:
        tk = workspace_toolkit(workspace_root=tmp)
        file_path = Path(tmp, "large.txt")
        file_path.write_text("A" * (MAX_FULL_FILE_PIN_CHARS + 1), encoding="utf-8")

        store = InMemorySessionStore()
        context = WorkspacePinExecutionContext(session_id="pin-session", session_store=store)
        tk.push_execution_context(context)
        try:
            result = tk.execute("pin_file_context", {"path": "large.txt"})
        finally:
            tk.pop_execution_context()

        assert result["error"] == "file too large to pin as a whole"
        assert result["max_chars"] == MAX_FULL_FILE_PIN_CHARS
        assert "Pin a smaller line range" in result["suggestion"]


def test_workspace_toolkit_file_ops_and_search():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = workspace_toolkit(workspace_root=tmp)

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


def test_line_level_read_insert_replace_delete():
    with tempfile.TemporaryDirectory() as tmp:
        tk = workspace_toolkit(workspace_root=tmp)

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
        tk = workspace_toolkit(workspace_root=tmp)

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
        tk = workspace_toolkit(workspace_root=tmp)

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
        tk = workspace_toolkit(workspace_root=tmp)

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
    agent = Broth()
    assert agent.toolkit.tools == {}


def test_agent_add_multiple_toolkits():
    """Agent can hold multiple toolkits simultaneously."""
    with tempfile.TemporaryDirectory() as tmp:
        agent = Broth()
        ws = workspace_toolkit(workspace_root=tmp)
        term = terminal_toolkit(workspace_root=tmp)

        agent.add_toolkit(ws)

        merged = agent.toolkit
        names = set(merged.tools.keys())
        assert "read_file" in names
        assert "write_file" in names
        assert "python_runtime_run" not in names
        assert "terminal_exec" not in names

        agent.add_toolkit(term)
        names = set(agent.toolkit.tools.keys())
        assert "terminal_exec" in names

        agent.remove_toolkit(term)
        names_after = set(agent.toolkit.tools.keys())
        assert "read_file" in names_after
        assert "terminal_exec" not in names_after
