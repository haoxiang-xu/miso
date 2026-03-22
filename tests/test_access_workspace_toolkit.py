import tempfile
from pathlib import Path

from miso.runtime import Broth
from miso.toolkits import TerminalToolkit, WorkspaceToolkit
from miso.memory import InMemorySessionStore
from miso.workspace import (
    MAX_FULL_FILE_PIN_CHARS,
    WorkspacePinExecutionContext,
    load_workspace_pins,
)


def _read_single_file(toolkit: WorkspaceToolkit, path: str, max_chars_per_file: int = 20000) -> dict:
    result = toolkit.execute(
        "read_files",
        {
            "paths": [path],
            "max_chars_per_file": max_chars_per_file,
            "max_total_chars": max_chars_per_file,
        },
    )
    return result["files"][0]


def _list_single_directory(toolkit: WorkspaceToolkit, path: str, recursive: bool = False, max_entries: int = 200) -> dict:
    result = toolkit.execute(
        "list_directories",
        {
            "paths": [path],
            "recursive": recursive,
            "max_entries_per_directory": max_entries,
            "max_total_entries": max_entries,
        },
    )
    return result["directories"][0]


def test_workspace_toolkit_class_is_exposed():
    assert WorkspaceToolkit.__name__ == "WorkspaceToolkit"


def test_access_workspace_toolkit_registers_expected_tools():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)

        names = set(toolkit.tools.keys())
        # file-level
        assert "read_files" in names
        assert "read_file_ast" in names
        assert "write_file" in names
        assert "create_file" in names
        assert "delete_file" in names
        assert "copy_file" in names
        assert "move_file" in names
        assert "file_exists" in names
        # directory
        assert "list_directories" in names
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


def test_workspace_toolkit_builds_without_terminal_tools():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)
        assert isinstance(tk, WorkspaceToolkit)
        assert "read_files" in tk.tools
        assert "read_file_ast" in tk.tools
        assert "terminal_exec" not in tk.tools


def test_workspace_toolkit_does_not_expose_legacy_single_read_tools():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)

        assert "read_file" not in tk.tools
        assert "list_directory" not in tk.tools
        assert tk.execute("read_file", {"path": "demo.txt"})["error"] == "tool not found: read_file"
        assert tk.execute("list_directory", {"path": "."})["error"] == "tool not found: list_directory"


def test_workspace_toolkit_methods_are_declared_on_class_for_ui_discovery():
    assert "read_files" in WorkspaceToolkit.__dict__
    assert "read_file_ast" in WorkspaceToolkit.__dict__
    assert "write_file" in WorkspaceToolkit.__dict__
    assert "list_directories" in WorkspaceToolkit.__dict__
    assert "read_lines" in WorkspaceToolkit.__dict__
    assert "search_and_replace" in WorkspaceToolkit.__dict__
    assert "pin_file_context" in WorkspaceToolkit.__dict__
    assert "unpin_file_context" in WorkspaceToolkit.__dict__


def test_workspace_pin_tools_use_explicit_descriptions():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)

        pin_description = tk.tools["pin_file_context"].description
        unpin_description = tk.tools["unpin_file_context"].description

        assert "Prefer the smallest necessary line range" in pin_description
        assert "unpin as soon as you no longer need it" in pin_description
        assert "pin_id" in unpin_description


def test_workspace_pin_tools_require_active_session_id():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "demo.py").write_text("print('hello')\n", encoding="utf-8")

        pin_result = tk.execute("pin_file_context", {"path": "demo.py"})
        unpin_result = tk.execute("unpin_file_context", {"all": True})

        assert "session_id" in pin_result["error"]
        assert "session_id" in unpin_result["error"]


def test_workspace_pin_file_context_deduplicates_and_unpins():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)
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
        tk = WorkspaceToolkit(workspace_root=tmp)
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


def test_access_workspace_toolkit_file_ops_and_search():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)

        write_result = toolkit.execute(
            "write_file",
            {
                "path": "notes/test.txt",
                "content": "hello\nworld\nhello agent\n",
            },
        )
        assert "error" not in write_result

        read_result = _read_single_file(toolkit, "notes/test.txt")
        assert "hello" in read_result["content"]

        search_result = toolkit.execute(
            "search_text",
            {"pattern": "hello", "path": "notes", "max_results": 10},
        )
        assert len(search_result["matches"]) == 2


def test_workspace_toolkit_batch_reads_and_directory_listing():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "notes").mkdir()
        Path(tmp, "docs").mkdir()
        Path(tmp, "notes", "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        Path(tmp, "docs", "b.txt").write_text("gamma\n", encoding="utf-8")
        Path(tmp, "docs", "nested").mkdir()
        Path(tmp, "docs", "nested", "c.txt").write_text("delta\n", encoding="utf-8")

        read_result = toolkit.execute(
            "read_files",
            {
                "paths": ["notes/a.txt", "docs/b.txt", "missing.txt"],
                "max_chars_per_file": 32,
                "max_total_chars": 64,
            },
        )
        assert read_result["requested_paths"] == 3
        assert read_result["returned_files"] == 3
        assert read_result["files"][0]["content"] == "alpha\nbeta\n"
        assert read_result["files"][1]["content"] == "gamma\n"
        assert "file not found" in read_result["files"][2]["error"]

        list_result = toolkit.execute(
            "list_directories",
            {
                "paths": ["notes", "docs"],
                "recursive": True,
                "max_entries_per_directory": 10,
                "max_total_entries": 10,
            },
        )
        assert list_result["requested_paths"] == 2
        assert list_result["returned_directories"] == 2
        assert "notes/a.txt" in list_result["directories"][0]["entries"]
        assert "docs/b.txt" in list_result["directories"][1]["entries"]
        assert "docs/nested/" in list_result["directories"][1]["entries"]
        assert "docs/nested/c.txt" in list_result["directories"][1]["entries"]


def test_list_directories_reject_file_path():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        file_path = Path(tmp, "single.txt")
        file_path.write_text("hello\n", encoding="utf-8")

        result = _list_single_directory(toolkit, "single.txt")

        assert result["error"] == f"not a directory: {file_path.resolve()}"


def _assert_syntax_tree_result(result: dict, language: str):
    assert result["language"] == language
    assert result["parser"] == "tree_sitter"
    assert result["tree_kind"] == "concrete_syntax_tree"
    assert result["node_count"] >= result["returned_node_count"] >= 1
    assert result["truncated"] is False
    assert result["has_syntax_errors"] is False
    assert result["syntax_errors"] == []
    assert isinstance(result["ast"], dict)
    assert isinstance(result["ast"]["type"], str)


def test_read_file_ast_parses_python_file():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "sample.py").write_text(
            "import os\n\nclass Greeter:\n    def hello(self, name: str) -> str:\n        return f'hi {name}'\n",
            encoding="utf-8",
        )

        result = toolkit.execute("read_file_ast", {"path": "sample.py"})

        _assert_syntax_tree_result(result, "python")
        assert result["ast"]["type"] == "module"
        children = result["ast"]["children"]
        assert children[0]["type"] == "import_statement"
        assert children[1]["type"] == "class_definition"


def test_read_file_ast_parses_typescript_file():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "sample.ts").write_text(
            "export function hello(name: string): string {\n  return `hi ${name}`;\n}\n",
            encoding="utf-8",
        )

        result = toolkit.execute("read_file_ast", {"path": "sample.ts"})

        _assert_syntax_tree_result(result, "typescript")
        assert result["ast"]["type"] == "program"


def test_read_file_ast_parses_rust_file():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "sample.rs").write_text(
            "fn hello(name: &str) -> String {\n    format!(\"hi {}\", name)\n}\n",
            encoding="utf-8",
        )

        result = toolkit.execute("read_file_ast", {"path": "sample.rs"})

        _assert_syntax_tree_result(result, "rust")
        assert result["ast"]["type"] == "source_file"


def test_read_file_ast_parses_go_file():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "sample.go").write_text(
            "package main\n\nfunc hello(name string) string {\n    return \"hi \" + name\n}\n",
            encoding="utf-8",
        )

        result = toolkit.execute("read_file_ast", {"path": "sample.go"})

        _assert_syntax_tree_result(result, "go")
        assert result["ast"]["type"] == "source_file"


def test_read_file_ast_detects_language_from_shebang():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "script").write_text(
            "#!/usr/bin/env bash\necho hello\n",
            encoding="utf-8",
        )

        result = toolkit.execute("read_file_ast", {"path": "script"})

        _assert_syntax_tree_result(result, "bash")


def test_read_file_ast_supports_language_override():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "snippet.custom").write_text(
            "function greet(name) {\n  return `hi ${name}`;\n}\n",
            encoding="utf-8",
        )

        result = toolkit.execute(
            "read_file_ast",
            {"path": "snippet.custom", "language": "javascript"},
        )

        _assert_syntax_tree_result(result, "javascript")


def test_read_file_ast_rejects_unsupported_text_files():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "notes.txt").write_text("hello\n", encoding="utf-8")

        result = toolkit.execute("read_file_ast", {"path": "notes.txt"})

        assert result["language"] is None
        assert result["error"] == "read_file_ast could not detect a supported language for this file"
        assert "python" in result["supported_languages"]


def test_read_file_ast_rejects_binary_files():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "blob.bin").write_bytes(b"\x00\x01\x02")

        result = toolkit.execute("read_file_ast", {"path": "blob.bin"})

        assert result["language"] is None
        assert result["error"] == "read_file_ast does not support binary files"
        assert "javascript" in result["supported_languages"]


def test_read_file_ast_reports_python_syntax_errors():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

        result = toolkit.execute("read_file_ast", {"path": "broken.py"})

        assert result["language"] == "python"
        assert result["parser"] == "tree_sitter"
        assert result["has_syntax_errors"] is True
        assert result["syntax_errors"][0]["start_line"] == 1
        assert isinstance(result["ast"], dict)


def test_read_file_ast_reports_javascript_syntax_errors():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "broken.js").write_text("function broken( {\n  return 1;\n}\n", encoding="utf-8")

        result = toolkit.execute("read_file_ast", {"path": "broken.js"})

        assert result["language"] == "javascript"
        assert result["has_syntax_errors"] is True
        assert result["syntax_errors"]
        assert isinstance(result["ast"], dict)


def test_line_level_read_insert_replace_delete():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)

        # Create a file with 5 lines
        tk.execute("write_file", {"path": "lines.txt", "content": "L1\nL2\nL3\nL4\nL5\n"})

        # read_lines range
        result = tk.execute("read_lines", {"path": "lines.txt", "start": 2, "end": 4})
        assert result["content"] == "L2\nL3\nL4\n"
        assert result["total_lines"] == 5

        # insert before line 3
        tk.execute("insert_lines", {"path": "lines.txt", "line": 3, "content": "NEW\n"})
        after_insert = _read_single_file(tk, "lines.txt")
        lines = after_insert["content"].splitlines()
        assert lines[2] == "NEW"
        assert len(lines) == 6

        # replace lines 1-2 with single line
        tk.execute("replace_lines", {"path": "lines.txt", "start": 1, "end": 2, "content": "REPLACED\n"})
        after_replace = _read_single_file(tk, "lines.txt")
        lines = after_replace["content"].splitlines()
        assert lines[0] == "REPLACED"
        assert after_replace["total_lines"] == 5

        # delete line 1
        tk.execute("delete_lines", {"path": "lines.txt", "start": 1, "end": 1})
        after_delete = _read_single_file(tk, "lines.txt")
        assert after_delete["total_lines"] == 4


def test_copy_lines_and_move_lines():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)

        tk.execute("write_file", {"path": "m.txt", "content": "A\nB\nC\nD\n"})

        # Copy lines 1-2 to before line 4
        tk.execute("copy_lines", {"path": "m.txt", "start": 1, "end": 2, "to_line": 5})
        result = _read_single_file(tk, "m.txt")
        lines = result["content"].splitlines()
        assert lines == ["A", "B", "C", "D", "A", "B"]

        # Rewrite and test move
        tk.execute("write_file", {"path": "m.txt", "content": "A\nB\nC\nD\n"})
        tk.execute("move_lines", {"path": "m.txt", "start": 3, "end": 4, "to_line": 1})
        result = _read_single_file(tk, "m.txt")
        lines = result["content"].splitlines()
        assert lines == ["C", "D", "A", "B"]


def test_search_and_replace():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)

        tk.execute("write_file", {"path": "sr.txt", "content": "foo bar foo baz foo\n"})

        result = tk.execute("search_and_replace", {
            "path": "sr.txt",
            "search": "foo",
            "replace": "qux",
            "max_count": 2,
        })
        assert result["replacements_made"] == 2

        content = _read_single_file(tk, "sr.txt")["content"]
        assert content.count("qux") == 2
        assert content.count("foo") == 1


def test_file_create_delete_copy_move_exists():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)

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
        ws = WorkspaceToolkit(workspace_root=tmp)
        term = TerminalToolkit(workspace_root=tmp)

        agent.add_toolkit(ws)

        merged = agent.toolkit
        names = set(merged.tools.keys())
        assert "read_files" in names
        assert "write_file" in names
        assert "python_runtime_run" not in names
        assert "terminal_exec" not in names

        agent.add_toolkit(term)
        names = set(agent.toolkit.tools.keys())
        assert "terminal_exec" in names

        agent.remove_toolkit(term)
        names_after = set(agent.toolkit.tools.keys())
        assert "read_files" in names_after
        assert "terminal_exec" not in names_after
