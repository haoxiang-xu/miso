from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from unchain.toolkits import GitToolkit
from unchain.tools.models import ToolConfirmationPolicy
from unchain.toolkits.builtin.git.git import _parse_staged_diff_files


# ════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    return tmp_path


@pytest.fixture
def toolkit(git_repo: Path) -> GitToolkit:
    return GitToolkit(workspace_root=str(git_repo))


# ════════════════════════════════════════════════════════════════════════════
#  git_status
# ════════════════════════════════════════════════════════════════════════════


def test_git_status_non_repo(tmp_path: Path) -> None:
    tk = GitToolkit(workspace_root=str(tmp_path))
    result = tk.git_status(cwd=".")
    assert result["ok"] is False
    assert "error" in result
    assert "not a git repository" in result["error"].lower() or result["error"]


def test_git_status_clean_repo(toolkit: GitToolkit, git_repo: Path) -> None:
    # Create an initial commit so the repo is non-empty but clean
    (git_repo / "hello.txt").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )

    result = toolkit.git_status(cwd=".")
    assert result["ok"] is True
    assert result["action"] == "status"
    assert result["repo_root"] is not None
    assert result["file_summary"] == []


def test_git_status_untracked_file(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "new.txt").write_text("content\n")
    result = toolkit.git_status(cwd=".")
    assert result["ok"] is True
    paths = [e["path"] for e in result["file_summary"]]
    assert any("new.txt" in p for p in paths)


def test_git_status_exclude_untracked(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "new.txt").write_text("content\n")
    result = toolkit.git_status(cwd=".", include_untracked=False)
    assert result["ok"] is True
    assert result["file_summary"] == []


def test_git_status_modified_file(toolkit: GitToolkit, git_repo: Path) -> None:
    f = git_repo / "a.txt"
    f.write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    f.write_text("v2\n")

    result = toolkit.git_status(cwd=".")
    assert result["ok"] is True
    paths = [e["path"] for e in result["file_summary"]]
    assert any("a.txt" in p for p in paths)


def test_git_status_staged_file(toolkit: GitToolkit, git_repo: Path) -> None:
    f = git_repo / "a.txt"
    f.write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    f.write_text("v2\n")
    subprocess.run(["git", "add", "a.txt"], cwd=str(git_repo), check=True, capture_output=True)

    result = toolkit.git_status(cwd=".")
    assert result["ok"] is True
    staged = [e for e in result["file_summary"] if e["index_status"] == "M"]
    assert staged


def test_git_status_truncation(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "x.txt").write_text("hello\n")
    result = toolkit.git_status(cwd=".", max_output_chars=5)
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["stdout"]) <= 5


# ════════════════════════════════════════════════════════════════════════════
#  git_diff
# ════════════════════════════════════════════════════════════════════════════


def test_git_diff_clean(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )

    result = toolkit.git_diff(cwd=".")
    assert result["ok"] is True
    assert result["stdout"] == ""
    assert result["file_summary"] == []


def test_git_diff_worktree_changes(toolkit: GitToolkit, git_repo: Path) -> None:
    f = git_repo / "a.txt"
    f.write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    f.write_text("v2\n")

    result = toolkit.git_diff(cwd=".")
    assert result["ok"] is True
    assert "a.txt" in result["stdout"]
    assert result["file_summary"]
    assert result["file_summary"][0]["path"] == "a.txt"


def test_git_diff_staged(toolkit: GitToolkit, git_repo: Path) -> None:
    f = git_repo / "a.txt"
    f.write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    f.write_text("v2\n")
    subprocess.run(["git", "add", "a.txt"], cwd=str(git_repo), check=True, capture_output=True)

    result = toolkit.git_diff(cwd=".", staged=True)
    assert result["ok"] is True
    assert "a.txt" in result["stdout"]
    assert "--cached" in result["argv"]


def test_git_diff_path_filter(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    (git_repo / "b.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    (git_repo / "a.txt").write_text("v2\n")
    (git_repo / "b.txt").write_text("v2\n")

    result = toolkit.git_diff(cwd=".", paths=["a.txt"])
    assert result["ok"] is True
    assert "a.txt" in result["stdout"]
    assert "b.txt" not in result["stdout"]


def test_git_diff_truncation(toolkit: GitToolkit, git_repo: Path) -> None:
    f = git_repo / "a.txt"
    f.write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    f.write_text("v2\n")

    result = toolkit.git_diff(cwd=".", max_output_chars=10)
    assert result["ok"] is True
    assert result["truncated"] is True


# ════════════════════════════════════════════════════════════════════════════
#  Security / validation
# ════════════════════════════════════════════════════════════════════════════


def test_cwd_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    subprocess.run(["git", "init"], cwd=str(outside), check=True, capture_output=True)

    tk = GitToolkit(workspace_root=str(workspace))
    result = tk.git_status(cwd=str(outside))
    assert result["ok"] is False
    assert "error" in result


def test_path_traversal_rejected(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    (git_repo / "a.txt").write_text("v2\n")

    result = toolkit.git_stage(paths=["../escape.txt"], cwd=".")
    assert result["ok"] is False
    assert "error" in result


def test_flag_injection_rejected(toolkit: GitToolkit, git_repo: Path) -> None:
    result = toolkit.git_stage(paths=["-flag"], cwd=".")
    assert result["ok"] is False
    assert "error" in result


def test_pathspec_magic_rejected(toolkit: GitToolkit, git_repo: Path) -> None:
    result = toolkit.git_stage(paths=[":path"], cwd=".")
    assert result["ok"] is False
    assert "error" in result


def test_empty_paths_rejected(toolkit: GitToolkit, git_repo: Path) -> None:
    result = toolkit.git_stage(paths=[], cwd=".")
    assert result["ok"] is False
    assert "error" in result


def test_dot_path_rejected(toolkit: GitToolkit, git_repo: Path) -> None:
    result = toolkit.git_stage(paths=["."], cwd=".")
    assert result["ok"] is False
    assert "error" in result


def test_no_git_binary(toolkit: GitToolkit, git_repo: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
        result = toolkit.git_status(cwd=".")
    # The first subprocess call (_get_repo_root) raises FileNotFoundError
    assert result["ok"] is False
    assert "error" in result


# ════════════════════════════════════════════════════════════════════════════
#  git_stage / git_unstage
# ════════════════════════════════════════════════════════════════════════════


def test_stage_file(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    (git_repo / "a.txt").write_text("v2\n")

    result = toolkit.git_stage(paths=["a.txt"], cwd=".")
    assert result["ok"] is True

    status = toolkit.git_status(cwd=".")
    staged = [e for e in status["file_summary"] if e["index_status"] == "M"]
    assert staged


def test_unstage_file(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    (git_repo / "a.txt").write_text("v2\n")
    subprocess.run(["git", "add", "a.txt"], cwd=str(git_repo), check=True, capture_output=True)

    unstage_result = toolkit.git_unstage(paths=["a.txt"], cwd=".")
    assert unstage_result["ok"] is True

    status = toolkit.git_status(cwd=".")
    staged = [e for e in status["file_summary"] if e["index_status"] == "M"]
    assert not staged


def test_stage_then_unstage(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )
    (git_repo / "a.txt").write_text("v2\n")

    stage = toolkit.git_stage(paths=["a.txt"], cwd=".")
    assert stage["ok"] is True

    unstage = toolkit.git_unstage(paths=["a.txt"], cwd=".")
    assert unstage["ok"] is True

    status = toolkit.git_status(cwd=".")
    staged = [e for e in status["file_summary"] if e["index_status"] not in (" ", "?")]
    assert not staged


# ════════════════════════════════════════════════════════════════════════════
#  git_commit
# ════════════════════════════════════════════════════════════════════════════


def test_commit_nothing_staged(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    result = toolkit.git_commit(message="test commit", cwd=".")
    assert result["ok"] is False
    assert "nothing to commit" in (result.get("error") or "").lower()


def test_commit_staged_content(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)

    result = toolkit.git_commit(message="my commit", cwd=".")
    assert result["ok"] is True
    assert result["action"] == "commit"


def test_commit_empty_message_rejected(toolkit: GitToolkit, git_repo: Path) -> None:
    result = toolkit.git_commit(message="", cwd=".")
    assert result["ok"] is False
    assert "error" in result


def test_commit_only_commits_staged(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(git_repo), check=True, capture_output=True
    )

    # Stage a.txt changes but leave b.txt unstaged
    (git_repo / "a.txt").write_text("v2\n")
    (git_repo / "b.txt").write_text("new\n")
    subprocess.run(["git", "add", "a.txt"], cwd=str(git_repo), check=True, capture_output=True)

    result = toolkit.git_commit(message="only a", cwd=".")
    assert result["ok"] is True

    # b.txt should still be untracked
    status = toolkit.git_status(cwd=".")
    untracked = [e for e in status["file_summary"] if "?" in e["worktree_status"]]
    assert any("b.txt" in e["path"] for e in untracked)


# ════════════════════════════════════════════════════════════════════════════
#  Confirmation resolver
# ════════════════════════════════════════════════════════════════════════════


def test_commit_resolver_code_diff(toolkit: GitToolkit, git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True)

    policy = toolkit._resolve_commit_confirmation(
        {"message": "my commit", "cwd": "."}, None
    )
    assert isinstance(policy, ToolConfirmationPolicy)
    assert policy.requires_confirmation is True
    assert policy.interact_type == "code_diff"
    assert isinstance(policy.interact_config, list)
    assert policy.interact_config


def test_commit_resolver_fallback_no_staged(toolkit: GitToolkit, git_repo: Path) -> None:
    # Nothing staged → resolver should fall back to plain confirmation
    (git_repo / "a.txt").write_text("v1\n")

    policy = toolkit._resolve_commit_confirmation(
        {"message": "my commit", "cwd": "."}, None
    )
    assert isinstance(policy, ToolConfirmationPolicy)
    assert policy.requires_confirmation is True
    # No staged diff → falls back (interact_type defaults to "confirmation")
    assert policy.interact_type == "confirmation"


def test_commit_resolver_fallback_bad_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    tk = GitToolkit(workspace_root=str(workspace))
    policy = tk._resolve_commit_confirmation(
        {"message": "x", "cwd": str(outside)}, None
    )
    assert isinstance(policy, ToolConfirmationPolicy)
    assert policy.requires_confirmation is True
    assert policy.interact_type == "confirmation"


# ════════════════════════════════════════════════════════════════════════════
#  _parse_staged_diff_files unit tests
# ════════════════════════════════════════════════════════════════════════════


def test_parse_diff_single_edit() -> None:
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "index abc..def 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    entries = _parse_staged_diff_files(diff)
    assert len(entries) == 1
    assert entries[0]["path"] == "foo.py"
    assert entries[0]["sub_operation"] == "edit"
    assert "-old" in entries[0]["unified_diff"]
    assert entries[0]["truncated"] is False


def test_parse_diff_new_file() -> None:
    diff = (
        "diff --git a/new.txt b/new.txt\n"
        "new file mode 100644\n"
        "index 000..abc\n"
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1 @@\n"
        "+hello\n"
    )
    entries = _parse_staged_diff_files(diff)
    assert len(entries) == 1
    assert entries[0]["sub_operation"] == "create"


def test_parse_diff_deleted_file() -> None:
    diff = (
        "diff --git a/old.txt b/old.txt\n"
        "deleted file mode 100644\n"
        "index abc..000\n"
        "--- a/old.txt\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-bye\n"
    )
    entries = _parse_staged_diff_files(diff)
    assert len(entries) == 1
    assert entries[0]["sub_operation"] == "delete"


def test_parse_diff_multiple_files() -> None:
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1 +1 @@\n"
        "-p\n"
        "+q\n"
    )
    entries = _parse_staged_diff_files(diff)
    assert len(entries) == 2
    assert entries[0]["path"] == "a.py"
    assert entries[1]["path"] == "b.py"


def test_parse_diff_empty() -> None:
    assert _parse_staged_diff_files("") == []


# ════════════════════════════════════════════════════════════════════════════
#  Registry / export
# ════════════════════════════════════════════════════════════════════════════


def test_git_toolkit_registry() -> None:
    from unchain.tools import ToolkitRegistry

    registry = ToolkitRegistry()
    toolkit_ids = {item["id"] for item in registry.list_toolkits(include_tools=False)}
    assert "git" in toolkit_ids
    assert registry.require("git").to_summary()["tool_count"] == 5


def test_git_toolkit_importable_from_unchain_toolkits() -> None:
    from unchain.toolkits import GitToolkit as GT  # noqa: F401

    assert GT is GitToolkit
