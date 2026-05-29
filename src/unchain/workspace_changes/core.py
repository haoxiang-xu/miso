from __future__ import annotations

import copy
import difflib
import hashlib
import os
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "unchain.artifact.v1"
DEFAULT_MAX_RESTORE_BYTES_PER_FILE = 256 * 1024
DEFAULT_MAX_RESTORE_TOTAL_BYTES = 1024 * 1024
DEFAULT_MAX_DIFF_LINES = 400
DEFAULT_MAX_DIFF_BYTES = 128 * 1024
DEFAULT_AUTO_TRACK_MAX_FILE_BYTES = 256 * 1024
DEFAULT_AUTO_TRACK_MAX_FILES = 5000
AUTO_TRACK_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".venv",
    "venv",
    "dist",
    "build",
    "coverage",
}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text_size(text: str | None) -> int:
    if text is None:
        return 0
    return len(text.encode("utf-8"))


def _snapshot(text: str | None) -> dict[str, Any]:
    exists = text is not None
    return {
        "exists": exists,
        "sha256": _sha256_text(text) if exists else "",
        "size_bytes": _text_size(text),
        "text": text if exists else None,
    }


def _status(before: dict[str, Any], after: dict[str, Any]) -> str:
    if not before.get("exists") and after.get("exists"):
        return "created"
    if before.get("exists") and not after.get("exists"):
        return "deleted"
    return "modified"


def _line_count(text: str) -> int:
    return len(text.splitlines()) if text else 0


def _truncate_text(text: str, *, max_lines: int, max_bytes: int) -> str:
    displayed = text
    lines = displayed.splitlines()
    if max_lines >= 0 and len(lines) > max_lines:
        displayed = "\n".join(lines[:max_lines])
    encoded = displayed.encode("utf-8")
    if max_bytes >= 0 and len(encoded) > max_bytes:
        displayed = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return displayed


def _diff_text(relative_path: str, before: str | None, after: str | None) -> str:
    before_lines = [] if before is None else before.splitlines()
    after_lines = [] if after is None else after.splitlines()
    lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
    )
    return "\n".join(lines) + ("\n" if lines else "")


def _count_plus_minus(unified_diff: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in unified_diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


class WorkspaceTextSnapshot:
    """Bounded UTF-8 text snapshot used for automatic workspace change detection."""

    def __init__(
        self,
        *,
        entries: dict[str, dict[str, Any]] | None = None,
        complete: bool = True,
    ) -> None:
        self.entries = entries or {}
        self.complete = bool(complete)

    @classmethod
    def capture(
        cls,
        workspace_roots: list[str | Path],
        *,
        max_file_bytes: int = DEFAULT_AUTO_TRACK_MAX_FILE_BYTES,
        max_files: int = DEFAULT_AUTO_TRACK_MAX_FILES,
    ) -> "WorkspaceTextSnapshot":
        entries: dict[str, dict[str, Any]] = {}
        complete = True
        resolved_roots = [Path(root).expanduser().resolve() for root in workspace_roots]

        for root in resolved_roots:
            if not root.exists():
                continue
            paths = [root] if root.is_file() else cls._iter_files(root)
            for target in paths:
                if len(entries) >= max(0, int(max_files)):
                    complete = False
                    break
                entry = cls._entry_for(target, resolved_roots, max_file_bytes=max_file_bytes)
                if entry is None:
                    continue
                entries[entry["path"]] = entry

        return cls(entries=entries, complete=complete)

    @staticmethod
    def _iter_files(root: Path):
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                name for name in dirnames if name not in AUTO_TRACK_SKIP_DIR_NAMES
            )
            for filename in sorted(filenames):
                yield Path(current_root) / filename

    @staticmethod
    def _relative_path(target: Path, workspace_roots: list[Path]) -> str:
        for root in workspace_roots:
            try:
                return target.relative_to(root).as_posix()
            except ValueError:
                continue
        return target.name

    @classmethod
    def _entry_for(
        cls,
        target: Path,
        workspace_roots: list[Path],
        *,
        max_file_bytes: int,
    ) -> dict[str, Any] | None:
        if target.is_symlink():
            return None
        try:
            resolved = target.expanduser().resolve()
        except Exception:
            return None
        if resolved.is_symlink() or not resolved.is_file():
            return None
        if not any(_is_relative_to(resolved, root) for root in workspace_roots):
            return None

        relative_path = cls._relative_path(resolved, workspace_roots)
        try:
            stat_result = resolved.stat()
        except OSError:
            return None
        if stat_result.st_size > max(0, int(max_file_bytes)):
            return {
                "path": str(resolved),
                "relative_path": relative_path,
                "exists": True,
                "trackable": False,
                "sha256": "",
                "size_bytes": int(stat_result.st_size),
                "text": None,
            }
        try:
            raw = resolved.read_bytes()
        except OSError:
            return None
        if b"\x00" in raw[:8192]:
            return {
                "path": str(resolved),
                "relative_path": relative_path,
                "exists": True,
                "trackable": False,
                "sha256": "",
                "size_bytes": len(raw),
                "text": None,
            }
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "path": str(resolved),
                "relative_path": relative_path,
                "exists": True,
                "trackable": False,
                "sha256": "",
                "size_bytes": len(raw),
                "text": None,
            }
        return {
            "path": str(resolved),
            "relative_path": relative_path,
            "exists": True,
            "trackable": True,
            "sha256": _sha256_text(text),
            "size_bytes": len(raw),
            "text": text,
        }


def _is_relative_to(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


class WorkspaceChangeTracker:
    """Run-scoped text-file change tracker used to build net workspace diffs."""

    def __init__(
        self,
        *,
        run_id: str,
        workspace_roots: list[str | Path],
        state: dict[str, Any] | None = None,
        max_restore_bytes_per_file: int = DEFAULT_MAX_RESTORE_BYTES_PER_FILE,
        max_restore_total_bytes: int = DEFAULT_MAX_RESTORE_TOTAL_BYTES,
        max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
        max_diff_bytes: int = DEFAULT_MAX_DIFF_BYTES,
    ) -> None:
        self.run_id = str(run_id)
        self.change_set_id = f"wcs_{self.run_id}"
        self.workspace_roots = [Path(root).expanduser().resolve() for root in workspace_roots]
        self.max_restore_bytes_per_file = max(0, int(max_restore_bytes_per_file))
        self.max_restore_total_bytes = max(0, int(max_restore_total_bytes))
        self.max_diff_lines = max(0, int(max_diff_lines))
        self.max_diff_bytes = max(0, int(max_diff_bytes))
        self._files: dict[str, dict[str, Any]] = {}
        if isinstance(state, dict):
            self._load_state(state)

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any] | None,
        *,
        run_id: str,
        workspace_roots: list[str | Path],
    ) -> "WorkspaceChangeTracker":
        return cls(run_id=run_id, workspace_roots=workspace_roots, state=state)

    def record_text_file_change(
        self,
        path: str,
        before_text: str | None,
        after_text: str | None,
        *,
        operation: str,
        tool_name: str,
        call_id: str,
        turn_id: str,
    ) -> None:
        target = Path(path).expanduser().resolve()
        if self.workspace_roots and not self._is_workspace_path(target):
            return
        if before_text == after_text:
            return

        path_key = str(target)
        relative_path = self._relative_path(target)
        entry = self._files.get(path_key)
        if entry is None:
            entry = {
                "path": path_key,
                "relative_path": relative_path,
                "first_before": _snapshot(before_text),
                "latest_after": _snapshot(after_text),
                "status": "",
                "operations": [],
                "undo_supported": True,
                "undo_unavailable_reason": "",
            }
            self._files[path_key] = entry
        else:
            entry["latest_after"] = _snapshot(after_text)

        entry["status"] = _status(entry["first_before"], entry["latest_after"])
        entry["operations"].append(
            {
                "turn_id": str(turn_id or ""),
                "tool_name": str(tool_name or ""),
                "call_id": str(call_id or ""),
                "operation": str(operation or entry["status"]),
            }
        )
        self._refresh_undo_support()

    def capture_text_snapshot(self) -> WorkspaceTextSnapshot:
        return WorkspaceTextSnapshot.capture(self.workspace_roots)

    def record_text_snapshot_changes(
        self,
        before: WorkspaceTextSnapshot | None,
        *,
        tool_name: str,
        call_id: str,
        turn_id: str,
    ) -> None:
        if before is None:
            return
        after = self.capture_text_snapshot()
        path_keys = sorted(set(before.entries) | set(after.entries))
        for path_key in path_keys:
            before_entry = before.entries.get(path_key)
            after_entry = after.entries.get(path_key)
            if before_entry is None and not before.complete:
                continue
            if after_entry is None and not after.complete:
                continue
            if before_entry is not None and before_entry.get("trackable") is not True:
                continue
            if after_entry is not None and after_entry.get("trackable") is not True:
                continue

            before_text = (
                str(before_entry.get("text"))
                if isinstance(before_entry, dict) and before_entry.get("exists")
                else None
            )
            after_text = (
                str(after_entry.get("text"))
                if isinstance(after_entry, dict) and after_entry.get("exists")
                else None
            )
            if before_text == after_text:
                continue
            after_sha256 = (
                str(after_entry.get("sha256") or "")
                if isinstance(after_entry, dict) and after_entry.get("exists")
                else ""
            )
            if self.latest_after_sha256(path_key) == after_sha256:
                continue
            operation = _status(_snapshot(before_text), _snapshot(after_text))
            self.record_text_file_change(
                path_key,
                before_text,
                after_text,
                operation=operation,
                tool_name=tool_name,
                call_id=call_id,
                turn_id=turn_id,
            )

    def latest_after_sha256(self, path: str) -> str | None:
        target = Path(path).expanduser().resolve()
        entry = self._files.get(str(target))
        if not isinstance(entry, dict):
            return None
        latest = entry.get("latest_after")
        if not isinstance(latest, dict):
            return None
        return str(latest.get("sha256") or "")

    def to_state(self) -> dict[str, Any]:
        self._refresh_undo_support()
        return {
            "change_set_id": self.change_set_id,
            "run_id": self.run_id,
            "files": copy.deepcopy(self._files),
        }

    def has_changes(self) -> bool:
        return bool(self._files)

    def to_artifact(self) -> dict[str, Any] | None:
        if not self._files:
            return None
        self._refresh_undo_support()
        files = []
        total_additions = 0
        total_deletions = 0
        full_diff_parts: list[str] = []
        displayed_diff_parts: list[str] = []
        remaining_lines = self.max_diff_lines
        remaining_bytes = self.max_diff_bytes

        for entry in self._files.values():
            before = entry["first_before"].get("text") if entry["first_before"].get("exists") else None
            after = entry["latest_after"].get("text") if entry["latest_after"].get("exists") else None
            full_diff = _diff_text(entry["relative_path"], before, after)
            displayed_diff = _truncate_text(
                full_diff,
                max_lines=remaining_lines,
                max_bytes=remaining_bytes,
            )
            remaining_lines = max(0, remaining_lines - _line_count(displayed_diff))
            remaining_bytes = max(0, remaining_bytes - len(displayed_diff.encode("utf-8")))
            additions, deletions = _count_plus_minus(full_diff)
            total_additions += additions
            total_deletions += deletions
            full_diff_parts.append(full_diff)
            displayed_diff_parts.append(displayed_diff)
            files.append(
                {
                    "path": entry["path"],
                    "relative_path": entry["relative_path"],
                    "status": entry["status"],
                    "before_sha256": entry["first_before"].get("sha256", ""),
                    "after_sha256": entry["latest_after"].get("sha256", ""),
                    "unified_diff": displayed_diff,
                    "truncated": displayed_diff != full_diff,
                    "total_lines": _line_count(full_diff),
                    "displayed_lines": _line_count(displayed_diff),
                    "additions": additions,
                    "deletions": deletions,
                    "undo_supported": bool(entry.get("undo_supported")),
                    "undo_unavailable_reason": str(entry.get("undo_unavailable_reason") or ""),
                }
            )

        full_diff_text = "".join(full_diff_parts)
        displayed_diff_text = "".join(displayed_diff_parts)
        undo_supported = all(bool(entry.get("undo_supported")) for entry in self._files.values())
        summary = f"{len(files)} file{'s' if len(files) != 1 else ''} changed, +{total_additions} -{total_deletions}"
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact_id": f"workspace_change_set:{self.run_id}",
            "kind": "workspace_change_set",
            "title": "Workspace changes",
            "summary": summary,
            "revision": 1,
            "status": "completed",
            "owner": {
                "run_id": self.run_id,
                "turn_id": None,
                "tool_name": "workspace_change_tracker",
                "call_id": None,
            },
            "snapshot": {
                "change_set_id": self.change_set_id,
                "run_id": self.run_id,
                "scope": "run",
                "files": files,
                "totals": {
                    "files": len(files),
                    "additions": total_additions,
                    "deletions": total_deletions,
                },
                "undo": {
                    "supported": undo_supported,
                    "requires_current_after_hash": True,
                },
                "truncated": displayed_diff_text != full_diff_text,
                "total_lines": _line_count(full_diff_text),
                "displayed_lines": _line_count(displayed_diff_text),
                "total_bytes": len(full_diff_text.encode("utf-8")),
                "displayed_bytes": len(displayed_diff_text.encode("utf-8")),
                "sha256": _sha256_text(full_diff_text),
            },
            "source": {},
            "presentation": {
                "surface": "run_summary",
                "group": "files",
                "collapsed": False,
            },
        }

    def restore(self) -> dict[str, Any]:
        self._refresh_undo_support()
        unsupported = [
            {
                "path": entry["path"],
                "relative_path": entry["relative_path"],
                "reason": str(entry.get("undo_unavailable_reason") or "undo is not supported"),
            }
            for entry in self._files.values()
            if not entry.get("undo_supported")
        ]
        for entry in self._files.values():
            target = Path(entry["path"]).expanduser().resolve()
            if self.workspace_roots and not self._is_workspace_path(target):
                unsupported.append(
                    {
                        "path": entry["path"],
                        "relative_path": entry["relative_path"],
                        "reason": "path is outside workspace roots",
                    }
                )
        if unsupported:
            return {
                "status": "unsupported",
                "restored": [],
                "conflicts": [],
                "unsupported": unsupported,
            }

        conflicts = []
        for entry in self._files.values():
            current = self._read_current_snapshot(Path(entry["path"]))
            expected = entry["latest_after"]
            if current.get("exists") != expected.get("exists") or current.get("sha256") != expected.get("sha256"):
                conflicts.append(
                    {
                        "path": entry["path"],
                        "relative_path": entry["relative_path"],
                        "expected_after_sha256": expected.get("sha256", ""),
                        "current_sha256": current.get("sha256", ""),
                    }
                )
        if conflicts:
            return {
                "status": "conflict",
                "restored": [],
                "conflicts": conflicts,
                "unsupported": [],
            }

        restored = []
        for entry in self._files.values():
            target = Path(entry["path"])
            before = entry["first_before"]
            if before.get("exists"):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(before.get("text") or ""), encoding="utf-8")
            else:
                if target.exists():
                    target.unlink()
            restored.append({"path": entry["path"], "relative_path": entry["relative_path"]})
        return {
            "status": "restored",
            "restored": restored,
            "conflicts": [],
            "unsupported": [],
        }

    def _load_state(self, state: dict[str, Any]) -> None:
        files = state.get("files")
        if isinstance(files, dict):
            self._files = copy.deepcopy(files)
        change_set_id = state.get("change_set_id")
        if isinstance(change_set_id, str) and change_set_id:
            self.change_set_id = change_set_id

    def _relative_path(self, target: Path) -> str:
        for root in self.workspace_roots:
            try:
                return target.relative_to(root).as_posix()
            except ValueError:
                continue
        return target.name

    def _is_workspace_path(self, target: Path) -> bool:
        for root in self.workspace_roots:
            try:
                target.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _read_current_snapshot(self, target: Path) -> dict[str, Any]:
        if not target.exists():
            return _snapshot(None)
        try:
            return _snapshot(target.read_text(encoding="utf-8"))
        except Exception:
            return {
                "exists": True,
                "sha256": "",
                "size_bytes": target.stat().st_size if target.exists() else 0,
                "text": None,
                "unreadable": True,
            }

    def _refresh_undo_support(self) -> None:
        total_bytes = 0
        for entry in self._files.values():
            before = entry.get("first_before") if isinstance(entry.get("first_before"), dict) else {}
            after = entry.get("latest_after") if isinstance(entry.get("latest_after"), dict) else {}
            entry_bytes = int(before.get("size_bytes") or 0) + int(after.get("size_bytes") or 0)
            total_bytes += entry_bytes
            if entry_bytes > self.max_restore_bytes_per_file:
                entry["undo_supported"] = False
                entry["undo_unavailable_reason"] = "file exceeds undo snapshot size limit"
            else:
                entry["undo_supported"] = True
                entry["undo_unavailable_reason"] = ""

        if total_bytes > self.max_restore_total_bytes:
            for entry in self._files.values():
                entry["undo_supported"] = False
                entry["undo_unavailable_reason"] = "change set exceeds undo snapshot size limit"


def restore_workspace_change_set(
    state: dict[str, Any] | None,
    *,
    run_id: str,
    workspace_roots: list[str | Path],
) -> dict[str, Any]:
    tracker = WorkspaceChangeTracker.from_state(
        state,
        run_id=run_id,
        workspace_roots=workspace_roots,
    )
    if not tracker.has_changes():
        return {
            "status": "missing",
            "restored": [],
            "conflicts": [],
            "unsupported": [],
        }
    return tracker.restore()
