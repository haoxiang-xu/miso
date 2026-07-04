from __future__ import annotations

from pathlib import Path

from unchain.tools import ToolExecutionContext
from unchain.toolkits.builtin.core.coding_backend import CoreCodingBackend
from unchain.toolkits.builtin.workspace import WorkspaceToolkit
from unchain.toolkits.builtin.workspace.backend import WorkspaceToolkitBackend


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


def test_workspace_toolkit_uses_legacy_backend_adapter(tmp_path: Path):
    toolkit = WorkspaceToolkit(workspace_root=tmp_path)

    assert type(toolkit._inner).__name__ == "WorkspaceToolkitBackend"
    assert isinstance(toolkit._inner, CoreCodingBackend)


def test_workspace_toolkit_wraps_core_coding_backend(monkeypatch, tmp_path: Path):
    calls: list[str] = []
    original_read = CoreCodingBackend.read

    def tracked_read(self, *args, **kwargs):
        calls.append("read")
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(CoreCodingBackend, "read", tracked_read)

    toolkit = WorkspaceToolkit(workspace_root=tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha\n", encoding="utf-8")
    result = toolkit.execute("read", {"path": str(target)})

    assert isinstance(toolkit._inner, CoreCodingBackend)
    assert calls == ["read"]
    assert result["content"] == "1\talpha"


def test_workspace_backend_is_legacy_adapter_over_core_coding_backend():
    assert issubclass(WorkspaceToolkitBackend, CoreCodingBackend)
    assert "__getattr__" not in WorkspaceToolkitBackend.__dict__
    assert {"glob", "grep", "shell", "lsp"}.issubset(CoreCodingBackend.__dict__)
    assert "lsp" not in WorkspaceToolkitBackend.__dict__


def test_workspace_backend_owns_read_state_and_helpers(tmp_path: Path):
    backend = WorkspaceToolkitBackend(workspace_root=tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    result = backend.read(str(target))

    assert result["content"] == "1\talpha\n2\tbeta"
    assert "_read_snapshots" in backend.__dict__
    assert backend._read_snapshots
    assert "read" in CoreCodingBackend.__dict__
    assert "_read_text_file" in CoreCodingBackend.__dict__
    assert "_record_read_snapshot" in CoreCodingBackend.__dict__
    assert "_resolve_absolute_path" in CoreCodingBackend.__dict__


def test_workspace_read_keeps_overwrite_safe_in_backend_snapshot_flow(tmp_path: Path):
    toolkit = WorkspaceToolkit(workspace_root=tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha\n", encoding="utf-8")

    read_result = toolkit.execute("read", {"path": str(target)})
    write_result = toolkit.execute("write", {"path": str(target), "content": "beta\n"})

    assert read_result["truncated"] is False
    assert write_result["operation"] == "update"
    assert target.read_text(encoding="utf-8") == "beta\n"


def test_workspace_backend_owns_write_edit_snapshot_flow(monkeypatch, tmp_path: Path):
    backend = WorkspaceToolkitBackend(workspace_root=tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    def fail_core_file_mutation(*args, **kwargs):
        raise AssertionError("workspace backend should not depend on core file mutation state")

    monkeypatch.setattr(backend._core, "_record_read_snapshot", fail_core_file_mutation)
    monkeypatch.setattr(backend._core, "write", fail_core_file_mutation)
    monkeypatch.setattr(backend._core, "edit", fail_core_file_mutation)

    read_result = backend.read(str(target))
    write_result = backend.write(str(target), "alpha\nBETA\n")
    edit_result = backend.edit(str(target), "BETA", "gamma")

    assert read_result["truncated"] is False
    assert write_result["operation"] == "update"
    assert edit_result["replacement_count"] == 1
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"
    assert "_check_snapshot_freshness" in CoreCodingBackend.__dict__
    assert "_file_diff_artifact_descriptor" in CoreCodingBackend.__dict__


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
