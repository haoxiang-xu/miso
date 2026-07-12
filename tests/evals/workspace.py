from __future__ import annotations

import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .types import EvalCase


_COMMON_IGNORED_NAMES = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
    "htmlcov",
    "build",
    "dist",
}
_SNAPSHOT_IGNORED_NAMES = _COMMON_IGNORED_NAMES | {
    ".coverage",
    "node_modules",
}


def snapshot_workspace(workspace_root: str | Path) -> dict[str, str]:
    root = Path(workspace_root).resolve()
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in _SNAPSHOT_IGNORED_NAMES for part in relative.parts):
            continue
        if not path.is_file():
            continue
        try:
            snapshot[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return snapshot


def diff_workspace_snapshots(
    before: dict[str, str],
    after: dict[str, str],
) -> dict[str, list[str]]:
    before_paths = set(before)
    after_paths = set(after)
    return {
        "created_paths": sorted(after_paths - before_paths),
        "modified_paths": sorted(
            path
            for path in before_paths & after_paths
            if before[path] != after[path]
        ),
        "deleted_paths": sorted(before_paths - after_paths),
    }


def _repo_copy_ignore(repo_root: Path):
    resolved_repo_root = repo_root.resolve()

    def ignore(current_dir: str, names: list[str]) -> set[str]:
        current_path = Path(current_dir).resolve()
        ignored = {name for name in names if name in _COMMON_IGNORED_NAMES}
        for name in names:
            try:
                child_rel = (current_path / name).resolve().relative_to(resolved_repo_root)
            except Exception:
                continue
            if child_rel.parts[:3] == ("tests", "evals", "artifacts"):
                ignored.add(name)
        return ignored

    return ignore


@contextmanager
def prepare_workspace(case: EvalCase, *, repo_root: str | Path) -> Iterator[Path]:
    repo_path = Path(repo_root).resolve()
    source_path = (repo_path / case.workspace_source).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"workspace source not found for case '{case.id}': {source_path}")

    temp_root = Path(tempfile.mkdtemp(prefix=f"unchain-eval-{case.id}-"))
    workspace_root = temp_root / "workspace"
    try:
        ignore = _repo_copy_ignore(repo_path) if case.workspace_mode == "repo_copy" else None
        shutil.copytree(source_path, workspace_root, ignore=ignore)
        yield workspace_root
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
