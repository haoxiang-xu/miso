import tempfile

from miso import access_workspace_toolkit, broth as Broth, run_terminal_toolkit, terminal_toolkit


def test_terminal_tool_methods_are_declared_on_class_for_ui_discovery():
    assert "terminal_exec" in run_terminal_toolkit.__dict__
    assert "terminal_session_open" in run_terminal_toolkit.__dict__
    assert "terminal_session_write" in run_terminal_toolkit.__dict__
    assert "terminal_session_close" in run_terminal_toolkit.__dict__
    assert "read_file" in access_workspace_toolkit.__dict__


def test_run_terminal_toolkit_alias_is_preserved():
    assert terminal_toolkit is run_terminal_toolkit


def test_run_terminal_toolkit_registers_only_terminal_tools():
    with tempfile.TemporaryDirectory() as tmp:
        tk = run_terminal_toolkit(workspace_root=tmp)
        assert set(tk.tools.keys()) == {
            "terminal_exec",
            "terminal_session_open",
            "terminal_session_write",
            "terminal_session_close",
        }


def test_run_terminal_toolkit_exec_and_session_workflow():
    with tempfile.TemporaryDirectory() as tmp:
        tk = run_terminal_toolkit(workspace_root=tmp)

        exec_result = tk.execute("terminal_exec", {"command": "echo terminal-only"})
        assert exec_result["ok"] is True
        assert "terminal-only" in exec_result["stdout"]

        opened = tk.execute("terminal_session_open", {"shell": "/bin/sh"})
        assert opened["ok"] is True

        written = tk.execute(
            "terminal_session_write",
            {
                "session_id": opened["session_id"],
                "input": "echo session-only\n",
                "yield_time_ms": 300,
            },
        )
        assert written["ok"] is True
        assert "session-only" in written["stdout"]

        closed = tk.execute("terminal_session_close", {"session_id": opened["session_id"]})
        assert closed["ok"] is True


def test_run_terminal_toolkit_strict_mode_blocks_disallowed_commands():
    with tempfile.TemporaryDirectory() as tmp:
        tk = run_terminal_toolkit(workspace_root=tmp, terminal_strict_mode=True)
        result = tk.execute("terminal_exec", {"command": "curl https://example.com"})
        assert result["ok"] is False
        assert "blocked by strict mode" in result["error"]


def test_run_terminal_toolkit_can_be_composed_with_access_workspace_toolkit():
    with tempfile.TemporaryDirectory() as tmp:
        agent = Broth()
        files = access_workspace_toolkit(workspace_root=tmp)
        term = run_terminal_toolkit(workspace_root=tmp)

        agent.add_toolkit(files)
        agent.add_toolkit(term)

        names = set(agent.toolkit.tools.keys())
        assert "read_file" in names
        assert "terminal_exec" in names
        assert "python_runtime_run" not in names
