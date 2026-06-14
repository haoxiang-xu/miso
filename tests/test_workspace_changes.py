from __future__ import annotations

import hashlib
from pathlib import Path

from unchain.workspace_changes import WorkspaceChangeTracker, restore_workspace_change_set


def test_tracker_collapses_multiple_changes_to_one_net_file(tmp_path: Path):
    target = tmp_path / "app.py"
    tracker = WorkspaceChangeTracker(run_id="run-1", workspace_roots=[tmp_path])

    tracker.record_text_file_change(
        str(target),
        None,
        "print('one')\n",
        operation="created",
        tool_name="write",
        call_id="call-1",
        turn_id="run-1:turn-0",
    )
    tracker.record_text_file_change(
        str(target),
        "print('one')\n",
        "print('two')\n",
        operation="modified",
        tool_name="edit",
        call_id="call-2",
        turn_id="run-1:turn-1",
    )

    state = tracker.to_state()
    assert state["change_set_id"] == "wcs_run-1"
    assert list(state["files"]) == [str(target)]

    file_state = state["files"][str(target)]
    assert file_state["relative_path"] == "app.py"
    assert file_state["status"] == "created"
    assert file_state["first_before"]["exists"] is False
    assert file_state["latest_after"]["exists"] is True
    assert file_state["latest_after"]["text"] == "print('two')\n"
    assert [op["tool_name"] for op in file_state["operations"]] == ["write", "edit"]


def test_tracker_records_automatic_snapshot_created_modified_and_deleted(tmp_path: Path):
    created = tmp_path / "created.txt"
    modified = tmp_path / "modified.txt"
    deleted = tmp_path / "deleted.txt"
    modified.write_text("before modified\n", encoding="utf-8")
    deleted.write_text("before deleted\n", encoding="utf-8")
    tracker = WorkspaceChangeTracker(run_id="run-1", workspace_roots=[tmp_path])
    before = tracker.capture_text_snapshot()

    created.write_text("created\n", encoding="utf-8")
    modified.write_text("after modified\n", encoding="utf-8")
    deleted.unlink()

    tracker.record_text_snapshot_changes(
        before,
        tool_name="shell",
        call_id="call-shell",
        turn_id="run-1:turn-0",
    )

    state = tracker.to_state()
    assert state["files"][str(created)]["status"] == "created"
    assert state["files"][str(modified)]["status"] == "modified"
    assert state["files"][str(deleted)]["status"] == "deleted"
    assert state["files"][str(deleted)]["latest_after"]["exists"] is False


def test_tracker_artifact_contains_run_level_net_diff_and_undo_metadata(tmp_path: Path):
    target = tmp_path / "app.py"
    tracker = WorkspaceChangeTracker(run_id="run-1", workspace_roots=[tmp_path])

    tracker.record_text_file_change(
        str(target),
        "print('old')\n",
        "print('new')\n",
        operation="modified",
        tool_name="edit",
        call_id="call-1",
        turn_id="run-1:turn-0",
    )

    artifact = tracker.to_artifact()
    assert artifact is not None
    assert artifact["artifact_id"] == "workspace_change_set:run-1"
    assert artifact["kind"] == "workspace_change_set"
    assert artifact["owner"]["run_id"] == "run-1"
    assert artifact["owner"]["turn_id"] is None
    assert artifact["presentation"]["surface"] == "run_summary"

    snapshot = artifact["snapshot"]
    assert snapshot["change_set_id"] == "wcs_run-1"
    assert snapshot["scope"] == "run"
    assert snapshot["totals"] == {"files": 1, "additions": 1, "deletions": 1}
    assert snapshot["undo"]["supported"] is True
    assert snapshot["files"][0]["relative_path"] == "app.py"
    assert snapshot["files"][0]["status"] == "modified"
    assert "-print('old')" in snapshot["files"][0]["unified_diff"]
    assert "+print('new')" in snapshot["files"][0]["unified_diff"]


def test_restore_refuses_when_current_file_no_longer_matches_after_hash(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    tracker = WorkspaceChangeTracker(run_id="run-1", workspace_roots=[tmp_path])

    tracker.record_text_file_change(
        str(target),
        "before\n",
        "after\n",
        operation="modified",
        tool_name="edit",
        call_id="call-1",
        turn_id="run-1:turn-0",
    )
    target.write_text("user edit\n", encoding="utf-8")

    result = tracker.restore()
    assert result["status"] == "conflict"
    assert result["restored"] == []
    assert result["conflicts"][0]["path"] == str(target)
    assert target.read_text(encoding="utf-8") == "user edit\n"


def test_restore_reverts_created_modified_and_deleted_files(tmp_path: Path):
    created = tmp_path / "created.txt"
    modified = tmp_path / "modified.txt"
    deleted = tmp_path / "deleted.txt"
    modified.write_text("after modified\n", encoding="utf-8")

    tracker = WorkspaceChangeTracker(run_id="run-1", workspace_roots=[tmp_path])
    tracker.record_text_file_change(
        str(created),
        None,
        "created\n",
        operation="created",
        tool_name="write",
        call_id="call-1",
        turn_id="run-1:turn-0",
    )
    tracker.record_text_file_change(
        str(modified),
        "before modified\n",
        "after modified\n",
        operation="modified",
        tool_name="edit",
        call_id="call-2",
        turn_id="run-1:turn-0",
    )
    tracker.record_text_file_change(
        str(deleted),
        "before deleted\n",
        None,
        operation="deleted",
        tool_name="delete",
        call_id="call-3",
        turn_id="run-1:turn-0",
    )
    created.write_text("created\n", encoding="utf-8")

    result = tracker.restore()
    assert result["status"] == "restored"
    assert sorted(item["relative_path"] for item in result["restored"]) == [
        "created.txt",
        "deleted.txt",
        "modified.txt",
    ]
    assert not created.exists()
    assert modified.read_text(encoding="utf-8") == "before modified\n"
    assert deleted.read_text(encoding="utf-8") == "before deleted\n"


def test_restore_workspace_change_set_from_serialized_state(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("after\n", encoding="utf-8")
    tracker = WorkspaceChangeTracker(run_id="run-1", workspace_roots=[tmp_path])
    tracker.record_text_file_change(
        str(target),
        "before\n",
        "after\n",
        operation="modified",
        tool_name="edit",
        call_id="call-1",
        turn_id="run-1:turn-0",
    )

    result = restore_workspace_change_set(
        tracker.to_state(),
        run_id="run-1",
        workspace_roots=[tmp_path],
    )

    assert result["status"] == "restored"
    assert target.read_text(encoding="utf-8") == "before\n"


def test_restore_refuses_serialized_paths_outside_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    workspace.mkdir()
    outside.write_text("after\n", encoding="utf-8")
    outside_state = {
        "change_set_id": "wcs_run-1",
        "run_id": "run-1",
        "files": {
            str(outside): {
                "path": str(outside),
                "relative_path": "../outside.txt",
                "first_before": {
                    "exists": True,
                    "sha256": "unused",
                    "size_bytes": len("before\n"),
                    "text": "before\n",
                },
                "latest_after": {
                    "exists": True,
                    "sha256": hashlib.sha256("after\n".encode("utf-8")).hexdigest(),
                    "size_bytes": len("after\n"),
                    "text": "after\n",
                },
                "status": "modified",
                "operations": [],
                "undo_supported": True,
                "undo_unavailable_reason": "",
            }
        },
    }

    result = restore_workspace_change_set(
        outside_state,
        run_id="run-1",
        workspace_roots=[workspace],
    )

    assert result["status"] == "unsupported"
    assert result["unsupported"][0]["reason"] == "path is outside workspace roots"
    assert outside.read_text(encoding="utf-8") == "after\n"
