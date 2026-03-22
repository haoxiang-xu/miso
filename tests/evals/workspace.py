from __future__ import annotations

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

    temp_root = Path(tempfile.mkdtemp(prefix=f"miso-eval-{case.id}-"))
    workspace_root = temp_root / "workspace"
    try:
        ignore = _repo_copy_ignore(repo_path) if case.workspace_mode == "repo_copy" else None
        shutil.copytree(source_path, workspace_root, ignore=ignore)
        yield workspace_root
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
