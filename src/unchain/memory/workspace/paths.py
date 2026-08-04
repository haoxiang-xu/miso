from __future__ import annotations

import unicodedata

from unchain.journal import ModelValidationError

from .models import canonical_virtual_path


_PLACEHOLDER_NAMES = frozenset(
    {
        "file",
        "folder",
        "memory",
        "new",
        "new file",
        "note",
        "temp",
        "tmp",
        "untitled",
    }
)
_MAX_WORKSPACE_PATH_CHARS = 1024


def canonical_parent_path(value: object) -> str:
    path = canonical_virtual_path(value, "parent_path")
    if len(path) > _MAX_WORKSPACE_PATH_CHARS:
        raise ModelValidationError("parent_path is too long")
    return path


def canonical_entry_path(value: object) -> str:
    path = canonical_virtual_path(value, "path")
    if path == "/" or len(path) > _MAX_WORKSPACE_PATH_CHARS:
        raise ModelValidationError("path must name a workspace entry")
    name = path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0].strip().casefold()
    if len(stem) < 2 or stem in _PLACEHOLDER_NAMES:
        raise ModelValidationError("path must use a specific, meaningful name")
    return path


def path_key(value: object) -> str:
    return canonical_entry_path(value).casefold()


def virtual_name(value: object) -> str:
    return canonical_entry_path(value).rsplit("/", 1)[-1]


def virtual_parent(value: object) -> str:
    path = canonical_entry_path(value)
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


def meaningful_description(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("description must be text")
    description = unicodedata.normalize("NFC", value.strip())
    if (
        not description
        or len(description) > 8192
        or "\x00" in description
        or any(ord(character) < 32 and character not in "\n\t" for character in description)
        or description.casefold() in _PLACEHOLDER_NAMES
    ):
        raise ModelValidationError(
            "description must explain what the entry contains and when it is useful"
        )
    return description


__all__ = [
    "canonical_entry_path",
    "canonical_parent_path",
    "meaningful_description",
    "path_key",
    "virtual_name",
    "virtual_parent",
]
