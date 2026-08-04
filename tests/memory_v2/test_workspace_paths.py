from __future__ import annotations

import pytest

from unchain.journal import ModelValidationError
from unchain.memory.workspace.paths import (
    canonical_entry_path,
    canonical_parent_path,
    path_key,
    virtual_name,
    virtual_parent,
)


def test_virtual_paths_are_canonical_stable_posix_paths() -> None:
    assert canonical_entry_path("/notes/Ａgent Design.md") == "/notes/Agent Design.md"
    assert canonical_parent_path("/") == "/"
    assert virtual_parent("/notes/design.md") == "/notes"
    assert virtual_name("/notes/design.md") == "design.md"
    assert path_key("/Notes/Design.md") == "/notes/design.md"


@pytest.mark.parametrize(
    "value",
    [
        "notes/design.md",
        "/",
        "/notes/../secret.md",
        "/notes/./secret.md",
        "/notes//secret.md",
        "/notes/%2e%2e/secret.md",
        "/Users/red/secret.md",
        "/etc/passwd",
        "/C:/Users/red/secret.md",
        "C:\\Users\\red\\secret.md",
        "file:///tmp/secret.md",
        "/notes/secret\x00.md",
    ],
)
def test_entry_paths_reject_traversal_host_paths_and_noncanonical_input(value: str) -> None:
    with pytest.raises((ModelValidationError, TypeError)):
        canonical_entry_path(value)


@pytest.mark.parametrize(
    "value",
    ["/notes/untitled.md", "/notes/tmp.md", "/notes/a.md"],
)
def test_entry_paths_require_a_meaningful_name(value: str) -> None:
    with pytest.raises(ModelValidationError):
        canonical_entry_path(value)
