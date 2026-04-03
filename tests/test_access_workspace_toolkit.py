import tempfile
from pathlib import Path

import pytest

from unchain.memory import InMemorySessionStore
from unchain.toolkits import TerminalToolkit, WorkspaceToolkit
from unchain.workspace.syntax import is_language_supported
from unchain.workspace import (
    MAX_FULL_FILE_PIN_CHARS,
    WorkspacePinExecutionContext,
    load_workspace_pins,
)


EXPECTED_WORKSPACE_TOOLS = {
    "delete_lines",
    "insert_lines",
    "list_directories",
    "pin_file_context",
    "read_files",
    "read_lines",
    "replace_lines",
    "search_text",
    "unpin_file_context",
    "write_file",
}

EXPECTED_TERMINAL_TOOLS = {
    "terminal_exec",
    "terminal_session_close",
    "terminal_session_open",
    "terminal_session_write",
}


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


def _read_files_ast(toolkit: WorkspaceToolkit, path: str, ast_threshold: int = 1) -> dict:
    result = toolkit.execute(
        "read_files",
        {"paths": [path], "max_chars_per_file": 50000, "max_total_chars": 50000, "ast_threshold": ast_threshold},
    )
    return result["files"][0]


def _assert_ast_upgraded_result(item: dict, language: str):
    assert item.get("ast_upgraded") is True
    assert item["language"] == language
    assert item["node_count"] >= item["returned_node_count"] >= 1
    assert isinstance(item["ast"], dict)
    assert isinstance(item["ast"]["type"], str)
    assert "content" not in item


def _require_ast_support(language: str) -> None:
    try:
        supported = is_language_supported(language)
    except RuntimeError:
        supported = False
    if not supported:
        pytest.skip(f"tree-sitter support for {language} is not available in this environment")


def test_workspace_toolkit_registers_expected_tools():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)

        assert set(toolkit.tools.keys()) == EXPECTED_WORKSPACE_TOOLS
        assert "create_file" not in toolkit.tools
        assert "delete_file" not in toolkit.tools
        assert "copy_file" not in toolkit.tools
        assert "move_file" not in toolkit.tools
        assert "file_exists" not in toolkit.tools
        assert "create_directory" not in toolkit.tools
        assert "copy_lines" not in toolkit.tools
        assert "move_lines" not in toolkit.tools
        assert "search_and_replace" not in toolkit.tools
        assert "read_file_ast" not in toolkit.tools


def test_terminal_toolkit_registers_expected_tools():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = TerminalToolkit(workspace_root=tmp)

        assert set(toolkit.tools.keys()) == EXPECTED_TERMINAL_TOOLS


def test_workspace_toolkit_methods_are_declared_on_class_for_ui_discovery():
    assert EXPECTED_WORKSPACE_TOOLS.issubset(set(WorkspaceToolkit.__dict__.keys()))
    assert "create_file" not in WorkspaceToolkit.__dict__
    assert "delete_file" not in WorkspaceToolkit.__dict__
    assert "copy_lines" not in WorkspaceToolkit.__dict__
    assert "search_and_replace" not in WorkspaceToolkit.__dict__


def test_confirmation_flags_match_workspace_and_terminal_contract():
    with tempfile.TemporaryDirectory() as tmp:
        workspace = WorkspaceToolkit(workspace_root=tmp)
        terminal = TerminalToolkit(workspace_root=tmp)

        assert workspace.tools["write_file"].requires_confirmation is True
        assert workspace.tools["insert_lines"].requires_confirmation is True
        assert workspace.tools["replace_lines"].requires_confirmation is True
        assert workspace.tools["delete_lines"].requires_confirmation is True

        assert workspace.tools["read_files"].requires_confirmation is False
        assert workspace.tools["list_directories"].requires_confirmation is False
        assert workspace.tools["search_text"].requires_confirmation is False
        assert workspace.tools["read_lines"].requires_confirmation is False
        assert workspace.tools["pin_file_context"].requires_confirmation is False
        assert workspace.tools["unpin_file_context"].requires_confirmation is False

        for tool in terminal.tools.values():
            assert tool.requires_confirmation is False


def test_workspace_pin_tools_use_explicit_descriptions():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)

        pin_description = tk.tools["pin_file_context"].description
        unpin_description = tk.tools["unpin_file_context"].description

        assert "Prefer the smallest necessary line range" in pin_description
        assert "unpin as soon as you no longer need it" in pin_description
        assert "pin_id" in unpin_description


def test_workspace_search_text_description_clarifies_local_scope():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)

        description = tk.tools["search_text"].description

        assert "workspace" in description
        assert "does not search the web" in description


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


def test_workspace_write_read_search_and_append():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)

        first_write = toolkit.execute(
            "write_file",
            {
                "path": "notes/test.txt",
                "content": "hello\nworld\n",
            },
        )
        second_write = toolkit.execute(
            "write_file",
            {
                "path": "notes/test.txt",
                "content": "hello agent\n",
                "append": True,
            },
        )

        assert first_write["created"] is True
        assert second_write["append"] is True

        read_result = _read_single_file(toolkit, "notes/test.txt")
        assert read_result["content"] == "hello\nworld\nhello agent\n"
        assert read_result["total_lines"] == 3

        search_result = toolkit.execute(
            "search_text",
            {"pattern": "hello", "path": "notes", "max_results": 10, "context_lines": 1},
        )
        assert len(search_result["matches"]) == 2
        assert search_result["matches"][0]["path"] == "notes/test.txt"
        assert search_result["matches"][0]["context_after"] == ["world"]


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
        notes_tree = list_result["directories"][0]["tree"]
        notes_names = [e["name"] for e in notes_tree]
        assert "a.txt" in notes_names
        docs_tree = list_result["directories"][1]["tree"]
        docs_names = [e["name"] for e in docs_tree]
        assert "nested/" in docs_names
        assert "b.txt" in docs_names
        nested_node = next(e for e in docs_tree if e["name"] == "nested/")
        assert any(c["name"] == "c.txt" for c in nested_node.get("children", []))


def test_list_directories_reject_file_path():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        file_path = Path(tmp, "single.txt")
        file_path.write_text("hello\n", encoding="utf-8")

        result = _list_single_directory(toolkit, "single.txt")

        assert result["error"] == f"not a directory: {file_path.resolve()}"


def test_read_files_auto_ast_parses_python_file():
    _require_ast_support("python")
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "sample.py").write_text(
            "import os\n\nclass Greeter:\n    def hello(self, name: str) -> str:\n        return f'hi {name}'\n",
            encoding="utf-8",
        )

        item = _read_files_ast(toolkit, "sample.py")

        _assert_ast_upgraded_result(item, "python")
        assert item["ast"]["type"] == "module"
        children = item["ast"]["children"]
        assert children[0]["type"] == "import_statement"
        assert children[1]["type"] == "class_definition"


def test_read_files_auto_ast_parses_typescript_file():
    _require_ast_support("typescript")
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "sample.ts").write_text(
            "export function hello(name: string): string {\n  return `hi ${name}`;\n}\n",
            encoding="utf-8",
        )

        item = _read_files_ast(toolkit, "sample.ts")

        _assert_ast_upgraded_result(item, "typescript")
        assert item["ast"]["type"] == "program"


def test_read_files_auto_ast_parses_rust_file():
    _require_ast_support("rust")
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "sample.rs").write_text(
            "fn hello(name: &str) -> String {\n    format!(\"hi {}\", name)\n}\n",
            encoding="utf-8",
        )

        item = _read_files_ast(toolkit, "sample.rs")

        _assert_ast_upgraded_result(item, "rust")
        assert item["ast"]["type"] == "source_file"


def test_read_files_auto_ast_parses_go_file():
    _require_ast_support("go")
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "sample.go").write_text(
            "package main\n\nfunc hello(name string) string {\n    return \"hi \" + name\n}\n",
            encoding="utf-8",
        )

        item = _read_files_ast(toolkit, "sample.go")

        _assert_ast_upgraded_result(item, "go")
        assert item["ast"]["type"] == "source_file"


def test_read_files_auto_ast_detects_language_from_shebang():
    _require_ast_support("bash")
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "script").write_text(
            "#!/usr/bin/env bash\necho hello\n",
            encoding="utf-8",
        )

        item = _read_files_ast(toolkit, "script")

        _assert_ast_upgraded_result(item, "bash")


def test_read_files_no_ast_for_unsupported_language():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "notes.txt").write_text("hello world " * 30, encoding="utf-8")

        item = _read_files_ast(toolkit, "notes.txt")

        assert item.get("ast_upgraded") is not True
        assert "content" in item


def test_read_files_no_ast_below_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "tiny.py").write_text("x = 1\n", encoding="utf-8")

        item = _read_files_ast(toolkit, "tiny.py", ast_threshold=256)

        assert item.get("ast_upgraded") is not True
        assert "content" in item
        assert item["content"] == "x = 1\n"


def test_read_files_ast_disabled_when_threshold_zero():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = WorkspaceToolkit(workspace_root=tmp)
        Path(tmp, "big.py").write_text("x = 1\n" * 100, encoding="utf-8")

        item = _read_files_ast(toolkit, "big.py", ast_threshold=0)

        assert item.get("ast_upgraded") is not True
        assert "content" in item


def test_line_level_read_insert_replace_delete():
    with tempfile.TemporaryDirectory() as tmp:
        tk = WorkspaceToolkit(workspace_root=tmp)

        tk.execute("write_file", {"path": "lines.txt", "content": "L1\nL2\nL3\nL4\nL5\n"})

        result = tk.execute("read_lines", {"path": "lines.txt", "start": 2, "end": 4})
        assert result["content"] == "L2\nL3\nL4\n"
        assert result["total_lines"] == 5

        tk.execute("insert_lines", {"path": "lines.txt", "line": 3, "content": "NEW\n"})
        after_insert = _read_single_file(tk, "lines.txt")
        lines = after_insert["content"].splitlines()
        assert lines[2] == "NEW"
        assert len(lines) == 6

        tk.execute("replace_lines", {"path": "lines.txt", "start": 1, "end": 2, "content": "REPLACED\n"})
        after_replace = _read_single_file(tk, "lines.txt")
        lines = after_replace["content"].splitlines()
        assert lines[0] == "REPLACED"
        assert after_replace["total_lines"] == 5

        tk.execute("delete_lines", {"path": "lines.txt", "start": 1, "end": 1})
        after_delete = _read_single_file(tk, "lines.txt")
        assert after_delete["total_lines"] == 4


def test_workspace_and_terminal_support_multiple_roots():
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        first = Path(tmp1)
        second = Path(tmp2)
        target = second / "hello.txt"
        target.write_text("hello\n", encoding="utf-8")

        workspace = WorkspaceToolkit(workspace_roots=[first, second])
        terminal = TerminalToolkit(workspace_roots=[first, second])

        read_result = workspace.execute(
            "read_files",
            {"paths": [str(target)], "max_chars_per_file": 100, "max_total_chars": 100},
        )
        exec_result = terminal.execute("terminal_exec", {"command": "pwd", "cwd": str(second)})

        assert read_result["files"][0]["content"] == "hello\n"
        assert exec_result["ok"] is True
        assert exec_result["stdout"].strip() == str(second.resolve())


def test_terminal_exec_supports_real_shell_features():
    with tempfile.TemporaryDirectory() as tmp:
        tk = TerminalToolkit(workspace_root=tmp)
        subdir = Path(tmp, "subdir")
        subdir.mkdir()

        result = tk.execute(
            "terminal_exec",
            {
                "command": "printf 'hello' | tr '[:lower:]' '[:upper:]' && printf ' done'",
                "cwd": "subdir",
            },
        )

        assert result["ok"] is True
        assert result["stdout"] == "HELLO done"
        assert result["cwd"] == str(subdir.resolve())
        assert result["command"].startswith("printf")
        assert result["shell"] == "/bin/bash"


def test_terminal_exec_reports_nonzero_exit():
    with tempfile.TemporaryDirectory() as tmp:
        tk = TerminalToolkit(workspace_root=tmp)

        result = tk.execute(
            "terminal_exec",
            {"command": "printf 'boom' >&2; exit 7"},
        )

        assert result["ok"] is False
        assert result["returncode"] == 7
        assert "boom" in result["stderr"]


def test_terminal_exec_enforces_workspace_cwd_and_strict_mode():
    with tempfile.TemporaryDirectory() as tmp:
        tk = TerminalToolkit(workspace_root=tmp)

        blocked = tk.execute("terminal_exec", {"command": "curl https://example.com"})
        outside = tk.execute("terminal_exec", {"command": "pwd", "cwd": "/"})

        assert "blocked by strict mode" in blocked["error"]
        assert blocked["command"] == "curl https://example.com"
        assert blocked["cwd"] == "."
        assert blocked["shell"] == "/bin/bash"
        assert outside["error"] == "cwd is outside all workspace roots"
        assert outside["command"] == "pwd"
        assert outside["cwd"] == "/"
        assert outside["shell"] == "/bin/bash"


def test_terminal_session_lifecycle_and_blocking():
    with tempfile.TemporaryDirectory() as tmp:
        tk = TerminalToolkit(workspace_root=tmp)

        opened = tk.execute("terminal_session_open", {})
        assert opened["ok"] is True

        session_id = opened["session_id"]
        first = tk.execute("terminal_session_write", {"session_id": session_id, "input": "printf 'hi'\n"})
        blocked = tk.execute("terminal_session_write", {"session_id": session_id, "input": "curl https://example.com\n"})
        closed = tk.execute("terminal_session_close", {"session_id": session_id})

        assert first["ok"] is True
        assert "hi" in first["stdout"]
        assert "blocked by strict mode" in blocked["error"]
        assert closed["ok"] is True
