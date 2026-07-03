from __future__ import annotations

from pathlib import Path

from unchain.tools import ToolExecutionContext
from unchain.toolkits import WorkspaceToolkit


class _MemorySessionStore:
    def __init__(self) -> None:
        self._state: dict[str, dict] = {}

    def load(self, session_id: str) -> dict:
        return dict(self._state.get(session_id, {}))

    def save(self, session_id: str, state: dict) -> None:
        self._state[session_id] = dict(state)


def test_workspace_toolkit_exposes_legacy_file_tools(tmp_path: Path):
    toolkit = WorkspaceToolkit(workspace_root=tmp_path)
    target = tmp_path / "notes.txt"

    write_result = toolkit.execute(
        "write_file",
        {"path": str(target), "content": "hello\n"},
    )
    read_result = toolkit.execute("read_file", {"path": str(target)})

    assert write_result["operation"] == "create"
    assert read_result["path"] == str(target)
    assert "hello" in read_result["content"]


def test_workspace_toolkit_exposes_canonical_coding_tools(tmp_path: Path):
    toolkit = WorkspaceToolkit(workspace_root=tmp_path)

    assert {
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "shell",
        "lsp",
    }.issubset(toolkit.tools)
    assert toolkit.tools["write"].requires_confirmation is True
    assert toolkit.tools["edit"].requires_confirmation is True
    assert toolkit.tools["shell"].requires_confirmation is True


def test_workspace_toolkit_uses_workspace_backend_instead_of_core_toolkit(monkeypatch, tmp_path: Path):
    import unchain.toolkits.builtin.workspace.workspace as workspace_module

    def fail_core_toolkit(*args, **kwargs):
        raise AssertionError("WorkspaceToolkit should construct its workspace backend, not CoreToolkit directly")

    monkeypatch.setattr(workspace_module, "CoreToolkit", fail_core_toolkit, raising=False)

    toolkit = WorkspaceToolkit(workspace_root=tmp_path)

    assert type(toolkit._inner).__name__ == "WorkspaceToolkitBackend"


def test_workspace_toolkit_pins_file_context_in_session_store(tmp_path: Path):
    store = _MemorySessionStore()
    toolkit = WorkspaceToolkit(workspace_root=tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    context = ToolExecutionContext(
        session_id="session-1",
        run_id="run-1",
        provider="test",
        model="test",
        iteration=0,
        session_store=store,
    )

    toolkit.push_execution_context(context)
    try:
        result = toolkit.execute(
            "pin_file_context",
            {"path": str(target), "start": 1, "end": 1},
        )
    finally:
        toolkit.pop_execution_context()

    saved = store.load("session-1")
    assert result["duplicate"] is False
    assert result["pin"]["path"] == str(target)
    assert result["pin"]["content"] == "alpha\n"
    assert saved["workspace_pins"][0]["pin_id"] == result["pin_id"]
